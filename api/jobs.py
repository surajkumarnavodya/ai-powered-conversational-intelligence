"""`POST /questions` and friends: the asynchronous question surface.

This is the layer that lets one authenticated user have up to
`MAX_CONCURRENT_QUESTIONS_PER_USER` questions in flight. `POST /ask`
keeps its existing synchronous contract untouched -- a caller who wants
one answer and is willing to wait for it should keep using it, and
nothing about its behaviour changes here (CLAUDE.md 4.7: additive, not a
rewrite).

**The queue runs the same governed pipeline, not a second one.**
`_question_job_runner` calls `agent.orchestrator.graph.run_orchestrated`
-- the exact entry point `/ask` calls -- and serializes the result with
`api.main._ask_response_from_state`, the exact serializer `/ask` uses. So
the SQL validator, the twice-enforced row cap, the driver-level query
timeout, the restricted-column gate, the result governance engine,
provenance and the per-database concurrency limiter all apply identically,
because they are literally the same code (CLAUDE.md 4.5). There is no
queue-specific execution path to drift.

**Authorization and ownership.**

  - Every route requires authentication. Submission additionally requires
    `Permission.ASK`, the same permission `/ask` requires, so the queue
    can never be a way to reach the pipeline without it.
  - A job is readable, listable and cancellable **only by its owner**.
    Someone else's job id produces the same 404 a nonexistent one does,
    never a distinguishable 403 (CLAUDE.md 5.3's anti-enumeration rule).
  - The owner key is `security.oidc.real_caller_subject(identity)` --
    the subject this server resolved. A deployment in `none` or
    `static_token` auth mode has no distinct per-caller subject (every
    caller is literally indistinguishable, see `AuthIdentity`), so
    ownership cannot be enforced and the queue **refuses** rather than
    putting every caller in one shared bucket where they could read each
    other's results. Deny-by-default, and a 403 that says what to enable.
  - The tenant id comes from `security.tenancy.resolve_tenant_id_for_identity`
    and nothing else. A client-supplied `X-Tenant-Id` is already refused
    before authentication by `api.auth.verify_api_key`, which every route
    here depends on.

**A stored result is re-governed on every read.** `_regovern_job_result`
re-runs result-level governance against the *reader's current* roles,
exactly as `api.artifacts._result_out` and
`api.shares._governed_result_snapshot` do, and for the same reason: roles
change, and a result governed for an analyst must not come back
unredacted for a viewer an hour later (CLAUDE.md 5.4). The job store
deliberately holds no opinion about policy; this is where policy lives.

**Nothing here leaks configuration.** No provider name, no key, no raw
prompt, no routing rule and no system instruction appears in any
response. `model_used` and `sources_used` carry only what `AskResponse`
and the existing "Sources used" panel already show.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from guardrails.engine import evaluate_question

from agent.authz import Permission
from agent.exceptions import (
    AgentError,
    MalformedLLMOutputError,
    OllamaUnavailableError,
    SchemaRetrievalError,
    SqlExecutionTimeoutError,
)
from agent.harness.failures import FailureClass
from agent.model_registry import InvalidModelSelectionError, validate_model_selection
from agent.state import ConversationExchange
from api.authz import require_permission
from api.chat_history import require_owned_conversation
from api.jobs_schemas import (
    QuestionJobListOut,
    QuestionJobOut,
    QueueStatsOut,
    SubmitQuestionRequest,
    SubmitQuestionResponse,
    job_status_literal,
)
from config.settings import Settings, get_settings
from governance.domain_detection import detect_domain
from governance.result_policy import govern_result_for_domain
from jobs.models import JobRecord, JobStatus
from jobs.queue import (
    QUEUE_DISABLED_MESSAGE,
    QUEUE_FULL_MESSAGE,
    JobOutcome,
    get_question_queue,
)
from jobs.store import AdmissionOutcome
from security.audit_log import get_correlation_id, log_security_event
from security.oidc import AuthIdentity, real_caller_subject
from security.tenancy import resolve_tenant_id_for_identity

logger = logging.getLogger(__name__)

router = APIRouter()

#: Fixed refusal text. A module constant, never built from the question,
#: so a refusal cannot be used as an oracle for what matched
#: (CLAUDE.md 5.5).
_NO_OWNER_MESSAGE = (
    "Asynchronous questions require an authenticated user account. This deployment's "
    "authentication mode does not identify individual callers, so a question could not "
    "be attributed to you. Enable LOCAL_AUTH_ENABLED or OIDC to use this feature."
)
_GUARDRAIL_REFUSED_REASON = "input_guardrail"
_RATE_LIMITED_MESSAGE = (
    "The system is handling a lot of requests right now -- please submit this question "
    "again in a moment."
)


# ---- ownership ------------------------------------------------------


def _require_owner_key(identity: AuthIdentity) -> str:
    """The authenticated subject that owns this caller's jobs.

    403s when the auth mode provides no distinct subject. That is a
    deliberate refusal rather than a fallback to a shared key: a shared
    key would make every caller the owner of every job, which is a
    cross-user data leak dressed as a convenience. See this module's
    docstring.
    """
    owner_key = real_caller_subject(identity)
    if owner_key is None:
        log_security_event(
            "question_queue_unattributable_caller",
            "info",
            "An asynchronous question was refused because the auth mode does not "
            "identify individual callers.",
            subject=identity.subject,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NO_OWNER_MESSAGE)
    return owner_key


def _require_queue(settings: Settings) -> Any:
    """The process-wide queue, or a 503 explaining that it is off.

    A 503 rather than a 404 because the route genuinely exists and the
    capability is simply disabled -- a client can show a clear message
    instead of concluding its own URL is wrong.
    """
    queue = get_question_queue(settings, _question_job_runner)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_DISABLED_MESSAGE
        )
    return queue


# ---- the runner -----------------------------------------------------


def _failure_class_for(exc: BaseException) -> FailureClass:
    """The harness classification for an exception the runner saw.

    Only this mapping decides whether a job is retried, so it is
    deliberately narrow: an exception nobody mapped becomes `UNKNOWN`,
    which `agent.harness.failures` does not treat as retryable. That
    direction matters -- an unrecognized failure retried three times is a
    retry storm, while one surfaced once is a bug report (that module's
    own reasoning).
    """
    if isinstance(exc, OllamaUnavailableError):
        return FailureClass.MODEL_UNAVAILABLE
    if isinstance(exc, SqlExecutionTimeoutError):
        return FailureClass.DATABASE_TIMEOUT
    if isinstance(exc, MalformedLLMOutputError):
        return FailureClass.MODEL_MALFORMED_OUTPUT
    if isinstance(exc, SchemaRetrievalError):
        return FailureClass.TOOL_FAILED
    return FailureClass.UNKNOWN


def _outcome_for_state(state: Any, result: dict[str, Any]) -> JobOutcome:
    """Map a finished `AgentState["status"]` onto a terminal `JobStatus`.

    The interesting cases:

      - `"rejected"` becomes BLOCKED_BY_GUARDRAIL, not FAILED. The input
        guard, the off-topic gate and the restricted-column gate all end
        here, and every one of them is the system working as configured.
        Reporting that as a failure would make a correct refusal
        indistinguishable from an outage, both to the user and to whoever
        is alerting on failure rates.
      - `"needs_clarification"` becomes COMPLETED. The job did finish;
        the answer happens to be a question back. Calling it a failure
        would be wrong and would also make the frontend hide the
        clarification it needs to show.
      - `"rate_limited"` becomes FAILED and is deliberately **not**
        retryable. The retry would re-enter the same limiter it just
        tripped, which is the retry storm `agent.harness.failures`'
        conservative `_RETRYABLE` set exists to avoid. The queue already
        gives the caller a clean way to resubmit.
    """
    is_mapping = isinstance(state, dict)
    agent_status = state.get("status") if is_mapping else None
    sources_used: list[str] = (
        [str(s) for s in (state.get("sources_used") or [])] if is_mapping else []
    )
    model_used: str | None = state.get("selected_model") if is_mapping else None
    confidence: dict[str, float] = (
        {str(k): float(v) for k, v in (state.get("source_confidence") or {}).items()}
        if is_mapping
        else {}
    )

    # The three observability fields are passed explicitly at each call
    # site rather than splatted from one dict. A `**kwargs` dict holding
    # heterogeneous value types widens to a union that cannot match each
    # keyword's own declared type, so the terser version cost four type
    # errors per call site and bought nothing.
    if agent_status in ("succeeded", "needs_clarification"):
        return JobOutcome(
            status=JobStatus.COMPLETED,
            result=result,
            sources_used=sources_used,
            model_used=model_used,
            source_confidence=confidence,
        )
    if agent_status == "rejected":
        return JobOutcome(
            status=JobStatus.BLOCKED_BY_GUARDRAIL,
            result=result,
            guardrail_reason=_GUARDRAIL_REFUSED_REASON,
            sources_used=sources_used,
            model_used=model_used,
            source_confidence=confidence,
        )
    if agent_status == "rate_limited":
        return JobOutcome(
            status=JobStatus.FAILED,
            result=result,
            error_message=_RATE_LIMITED_MESSAGE,
            sources_used=sources_used,
            model_used=model_used,
            source_confidence=confidence,
        )
    # "failed", or any non-terminal status that somehow survived -- the
    # serialized response already carries the safe error text.
    errors = result.get("error_history") or []
    return JobOutcome(
        status=JobStatus.FAILED,
        result=result,
        error_message=errors[0] if errors else "This question could not be completed.",
        failure=None,
        sources_used=sources_used,
        model_used=model_used,
        source_confidence=confidence,
    )


def _question_job_runner(job: JobRecord, envelope: Any) -> JobOutcome:
    """Execute one job through the same pipeline `POST /ask` uses.

    Imports `api.main` lazily. `api.main` registers this router, so a
    module-level import would be circular -- and the laziness is not a
    workaround but the correct direction: `jobs/` must not depend on
    `api/` at all (it takes this function as an injected `JobRunner`),
    and `api/jobs.py` may depend on `api/main.py` because they are the
    same layer.

    Persistence is fail-open (CLAUDE.md 4.2): a chat-history write that
    fails is logged and the answer is still returned, exactly as `/ask`
    treats it. An answer the user can read is worth more than a tidy
    history row.
    """
    from agent.orchestrator.graph import run_orchestrated
    from api.main import _ask_response_from_state

    settings = get_settings()
    request = job.request
    session_id = str(request.get("session_id") or job.job_id)

    history = [
        ConversationExchange(
            question=turn["question"],
            sql=turn.get("sql"),
            tables=list(turn.get("tables") or []),
            status=turn.get("status", "succeeded"),
        )
        for turn in request.get("conversation_history", [])
    ]

    try:
        final_state = run_orchestrated(
            job.question,
            history,
            enable_insight=bool(request.get("enable_insight", True)),
            session_id=session_id,
            caller_roles=tuple(job.caller_roles),
            caller_subject=job.owner_key,
            attachment_ids=list(request.get("attachment_ids") or []) or None,
            selected_sources=request.get("sources"),
            model=request.get("model"),
            tenant_id=job.tenant_id,
            forecast_horizon=request.get("forecast_horizon"),
            model_explicitly_selected=bool(request.get("model_explicitly_selected")),
            envelope=envelope,
        )
    except AgentError as exc:
        # Same shape `/ask` produces for the identical case: the safe
        # message to the caller, the full detail to the log only
        # (CLAUDE.md 5.6). Classified so a transient provider outage is
        # retried and a validation refusal is not.
        logger.error(
            "[jobs] job_id=%s failed with %s: %s",
            job.job_id,
            type(exc).__name__,
            exc,
            exc_info=exc,
        )
        return JobOutcome(
            status=JobStatus.FAILED,
            error_message=exc.safe_message,
            failure=_failure_class_for(exc),
        )

    response = _ask_response_from_state(final_state, session_id)
    result = response.model_dump(mode="json")
    outcome = _outcome_for_state(final_state, result)

    try:
        from api.chat_persistence import persist_ask_turn

        identity = _identity_for_persistence(job)
        if identity is not None:
            conversation_id, message_id = persist_ask_turn(
                identity=identity,
                conversation_id=request.get("conversation_id"),
                question=job.question,
                ask_response=response,
                attachment_ids=list(request.get("attachment_ids") or []),
                settings=settings,
            )
            if conversation_id or message_id:
                result["conversation_id"] = conversation_id
                result["message_id"] = message_id
    except Exception as exc:  # noqa: BLE001 - see api/chat_persistence.py's docstring
        logger.warning(
            "[jobs] job_id=%s chat-history persistence failed unexpectedly: %s",
            job.job_id,
            exc,
        )

    return outcome


def _identity_for_persistence(job: JobRecord) -> AuthIdentity | None:
    """Rebuild the submitting identity, for chat-history persistence only.

    `persist_ask_turn` takes an `AuthIdentity` because on the synchronous
    path it has the live one. A worker thread does not, so this
    reconstructs it from what the job already stored -- the subject and
    roles *this server* resolved at submission, never anything the client
    sent.

    Returns `None` when local auth is off, which short-circuits
    persistence entirely rather than constructing an identity that
    `persist_ask_turn` would reject anyway. Chat history covers
    locally-authenticated users only, which is a standing, documented
    limitation (CLAUDE.md 13), not something this reconstruction changes.
    """
    settings = get_settings()
    if not settings.local_auth_enabled:
        return None
    return AuthIdentity(
        subject=job.owner_key,
        roles=tuple(job.caller_roles),
        mode="local",
    )


# ---- result re-governance -------------------------------------------


def _regovern_job_result(
    result: dict[str, Any] | None,
    reader_roles: Sequence[str],
    settings: Settings,
) -> dict[str, Any] | None:
    """One stored job result, re-governed for whoever is reading it now.

    The same control `api.shares._governed_result_snapshot` and
    `api.artifacts._result_out` apply, on a third surface that serves a
    persisted result. A job's result was governed once, for the roles the
    submitter held at submission; by the time it is read those roles may
    have narrowed. Serving the stored rows as-is would make a completed
    job a durable copy of data the reader is no longer entitled to.

    Two of the protections re-applied here are not the submitter's to
    waive even for their own job: a credential or payment instrument is
    withheld from every caller including an admin, and small-group
    suppression protects the individual in the group rather than the
    sensitivity of the column (CLAUDE.md 5.4).

    The domain is detected from the stored SQL with the result's column
    names as schema evidence -- weaker evidence than `POST /execute` has,
    and deliberately so: a worse guess falls back to `GENERAL`, the
    strictest policy in the registry, so the degradation direction is
    toward more protection, never less.
    """
    if result is None or not settings.enable_result_governance:
        return result

    columns = result.get("result_columns")
    rows = result.get("result_rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or not rows:
        return result

    column_names = [str(column) for column in columns]
    stored_sql = result.get("sql") or ""
    domain = detect_domain(str(stored_sql), column_names).domain
    governed = govern_result_for_domain(
        column_names,
        rows,
        reader_roles,
        domain,
        settings.governance_min_group_size,
    )
    if not governed.changed:
        return result

    logger.info(
        "[jobs] result-level governance re-applied on read (domain=%s, actions=%d)",
        domain.value,
        len(governed.actions),
    )
    protected = dict(result)
    protected["result_rows"] = [list(row) for row in governed.rows]
    # The export ceiling can remove rows outright, so the row bookkeeping
    # has to follow -- otherwise a reader is told "150 rows" above a table
    # holding 100 and reads the shortfall as a rendering bug rather than a
    # policy decision. `row_count` keeps its original value: it is what
    # the query matched, which is still true and is what makes the
    # shortfall legible (the identical reasoning `api/shares.py` applies).
    if len(governed.rows) != len(rows):
        protected["truncated"] = True
    return protected


def _job_out(job: JobRecord, reader_roles: Sequence[str], settings: Settings) -> QuestionJobOut:
    """Serialize one job for its owner, re-governing its result."""
    return QuestionJobOut(
        job_id=job.job_id,
        status=job_status_literal(job.status),
        question=job.question,
        agent_status=job.status.to_agent_status(),
        attempts=job.attempts,
        submitted_at=job.submitted_at_epoch,
        queue_wait_seconds=round(job.queue_wait_seconds, 3),
        run_seconds=None if job.run_seconds is None else round(job.run_seconds, 3),
        result=_regovern_job_result(job.result, reader_roles, settings),
        error_message=job.error_message,
        guardrail_reason=job.guardrail_reason,
        sources_used=job.sources_used,
        model_used=job.model_used,
        source_confidence=job.source_confidence,
        correlation_id=job.correlation_id,
    )


# ---- routes ---------------------------------------------------------


@router.post(
    "/questions",
    response_model=SubmitQuestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_question(
    payload: SubmitQuestionRequest,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> SubmitQuestionResponse:
    """Submit one question for asynchronous execution.

    Returns 202 with a job id immediately. The job is QUEUED, and becomes
    RUNNING as soon as this user is under
    `MAX_CONCURRENT_QUESTIONS_PER_USER` and the process is under
    `MAX_CONCURRENT_QUESTIONS_GLOBAL`.

    **Queued, not dropped.** Exceeding the concurrency limit queues the
    question rather than rejecting it -- the brief's requirement, and the
    right behaviour here because the caller is not blocking on an answer.
    The one case that still rejects is a *full queue* (429), which is
    genuine backpressure: accepting work with no realistic prospect of
    running it would be a memory leak with a progress bar.

    **A duplicate submission is absorbed, not duplicated.** A resubmitted
    identical question returns the original job id with `duplicate=true`,
    so a double-clicked button or a client retry costs nothing.

    Requires `Permission.ASK`, the same permission `POST /ask` requires.
    """
    settings = get_settings()
    owner_key = _require_owner_key(identity)
    queue = _require_queue(settings)

    # Same pre-check `POST /ask` runs, for the same reason: a job that
    # would be filed under someone else's conversation is refused before
    # it is ever queued, not after it has run.
    require_owned_conversation(identity, payload.conversation_id, settings)

    # Validated before a queue slot is spent, mirroring `/ask`'s own
    # "validate before any admission-control slot is acquired" posture: an
    # invalid or disallowed model is a malformed request (400), not a
    # reason to occupy capacity or to produce a FAILED job.
    try:
        selected_model = validate_model_selection(payload.model, settings)
    except InvalidModelSelectionError as exc:
        log_security_event(
            "invalid_model_selection",
            "info",
            "An asynchronous question specified an Ollama model outside the allowlist.",
            subject=identity.subject,
            requested_model=payload.model,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if (
        payload.forecast_horizon is not None
        and payload.forecast_horizon > settings.forecast_max_horizon
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"forecast_horizon {payload.forecast_horizon} exceeds the configured "
                f"maximum of {settings.forecast_max_horizon}."
            ),
        )

    # Input-side guardrails, run before admission (guardrails/).
    #
    # The graph's own `sanitize_input_node` already applies
    # `agent.input_guard` and would refuse this question with
    # `status="rejected"` -- which `_outcome_for_state` maps to
    # BLOCKED_BY_GUARDRAIL. Running the façade here as well is not a
    # duplicate decision: it adds the abuse layer the graph has no
    # equivalent of, and it means a refusable question never occupies a
    # worker slot or a database connection to be told no.
    #
    # A refusal is still recorded as a **job**, not returned as a 4xx.
    # The caller asked for a job id and a status, so giving them a
    # terminal job whose status is `blocked_by_guardrail` keeps one code
    # path on the client and leaves an auditable record. QUEUED ->
    # BLOCKED_BY_GUARDRAIL is a legal transition precisely for this.
    guardrail_verdict = evaluate_question(payload.question, settings)

    request_payload: dict[str, Any] = {
        "conversation_history": [turn.model_dump() for turn in payload.conversation_history],
        "enable_insight": payload.enable_insight,
        "session_id": payload.session_id,
        "conversation_id": payload.conversation_id,
        "attachment_ids": list(payload.attachment_ids),
        "model": selected_model,
        # `selected_model` above is already resolved (an omitted model
        # becomes Settings.ollama_model), so this flag is the only thing
        # that still distinguishes "the caller asked for this model" from
        # "the caller asked for nothing". Model routing never overrides an
        # explicit choice.
        "model_explicitly_selected": payload.model is not None,
        "forecast_horizon": payload.forecast_horizon,
        "sources": payload.sources,
    }

    admission = queue.submit(
        owner_key=owner_key,
        question=payload.question,
        tenant_id=resolve_tenant_id_for_identity(identity),
        caller_roles=tuple(identity.roles),
        request=request_payload,
        client_idempotency_key=payload.idempotency_key,
        correlation_id=get_correlation_id(),
    )

    if admission.outcome is AdmissionOutcome.QUEUE_FULL:
        # The detail is counts and a fixed phrase -- never the question.
        logger.warning("[jobs] submission refused: %s", admission.detail)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=QUEUE_FULL_MESSAGE,
            headers={"Retry-After": "5"},
        )

    job = admission.job
    if job is None:  # pragma: no cover - ADMITTED/DUPLICATE always carry a job
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_DISABLED_MESSAGE
        )

    if guardrail_verdict.blocked and admission.outcome is AdmissionOutcome.ADMITTED:
        queue.store.finish(
            job.job_id,
            JobStatus.BLOCKED_BY_GUARDRAIL,
            error_message=guardrail_verdict.message,
            guardrail_reason=_GUARDRAIL_REFUSED_REASON,
        )
        log_security_event(
            "question_blocked_by_guardrail",
            "warning",
            "A submitted question was refused by an input guardrail before any " "source ran.",
            subject=identity.subject,
            job_id=job.job_id,
            # Layer names only -- never the question, and never which
            # pattern matched (CLAUDE.md 5.5: a refusal must not be an
            # oracle). The pattern names are in the guardrail logs, where
            # an investigator can see them and a caller cannot.
            layers=[layer.value for layer in guardrail_verdict.fired_layers],
        )
        refused = queue.store.get(job.job_id, owner_key=owner_key)
        if refused is not None:
            job = refused

    stats = queue.stats(owner_key=owner_key)
    return SubmitQuestionResponse(
        job_id=job.job_id,
        status=job_status_literal(job.status),
        duplicate=admission.outcome is AdmissionOutcome.DUPLICATE,
        queued_for_caller=stats.queued_for_caller,
        running_for_caller=stats.running_for_caller,
        max_concurrent_per_user=stats.max_concurrent_per_user,
    )


@router.get("/questions/stats", response_model=QueueStatsOut)
def question_queue_stats(
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> QueueStatsOut:
    """Capacity counts for this queue, scoped to this caller where it matters.

    Declared *before* `GET /questions/{job_id}` deliberately: FastAPI
    matches routes in declaration order, so the literal path has to come
    first or "stats" would be captured as a job id.

    Returns `enabled=false` rather than a 503 when the queue is off, so a
    client can hide the asynchronous UI instead of discovering the
    capability is missing through an error.
    """
    settings = get_settings()
    owner_key = _require_owner_key(identity)
    queue = get_question_queue(settings, _question_job_runner)
    if queue is None:
        return QueueStatsOut(
            running_global=0,
            queued_global=0,
            max_concurrent_global=0,
            max_concurrent_per_user=0,
            max_queue_depth=0,
            running_for_caller=0,
            queued_for_caller=0,
            enabled=False,
        )
    stats = queue.stats(owner_key=owner_key)
    return QueueStatsOut(
        running_global=stats.running_global,
        queued_global=stats.queued_global,
        max_concurrent_global=stats.max_concurrent_global,
        max_concurrent_per_user=stats.max_concurrent_per_user,
        max_queue_depth=stats.max_queue_depth,
        running_for_caller=stats.running_for_caller,
        queued_for_caller=stats.queued_for_caller,
        enabled=True,
    )


@router.get("/questions", response_model=QuestionJobListOut)
def list_questions(
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Comma-separated job statuses to include. Omit for every status.",
    ),
    limit: int = Query(default=50, gt=0, le=200),
) -> QuestionJobListOut:
    """This caller's own jobs, newest first. Never anyone else's.

    An unrecognized status in `status` is ignored rather than a 400: the
    filter is a convenience, and a client sending a status this server
    does not know should get the jobs it *can* match rather than an
    error. An all-unrecognized filter therefore returns nothing, which is
    the honest answer to "show me jobs in a state that does not exist".
    """
    settings = get_settings()
    owner_key = _require_owner_key(identity)
    queue = _require_queue(settings)

    statuses: frozenset[JobStatus] | None = None
    if status_filter:
        wanted = {part.strip().lower() for part in status_filter.split(",") if part.strip()}
        statuses = frozenset(s for s in JobStatus if s.value in wanted)

    jobs = queue.store.list_for_owner(owner_key, statuses=statuses, limit=limit)
    return QuestionJobListOut(
        jobs=[_job_out(job, identity.roles, settings) for job in jobs],
        total=len(jobs),
    )


@router.get("/questions/{job_id}", response_model=QuestionJobOut)
def get_question(
    job_id: str,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> QuestionJobOut:
    """One of this caller's jobs, with its result once it has one.

    Another user's job id returns the same 404 a nonexistent id does --
    never a distinguishable 403 -- so a caller cannot probe for the
    existence of someone else's job (CLAUDE.md 5.3).

    The result is re-governed against this reader's *current* roles on
    every call, never served as captured.
    """
    settings = get_settings()
    owner_key = _require_owner_key(identity)
    queue = _require_queue(settings)

    job = queue.store.get(job_id, owner_key=owner_key)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such question.")
    return _job_out(job, identity.roles, settings)


@router.post("/questions/{job_id}/cancel", response_model=QuestionJobOut)
def cancel_question(
    job_id: str,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> QuestionJobOut:
    """Cancel one of this caller's jobs.

    The status flips to CANCELLED immediately, so the very next poll is
    truthful. A job already RUNNING is additionally signalled through its
    execution envelope and stops at its next node boundary -- not
    mid-query, and not instantly: Python cannot pre-empt a thread inside
    a C extension, the same limitation `db.execution` documents. What the
    signal buys is that the wasted work is bounded to roughly one node
    rather than a whole graph run.

    Cancelling an already-terminal job is a 409 rather than a silent
    success: a client that thinks it cancelled a completed answer has a
    bug worth seeing. A job that is not this caller's is still a 404.
    """
    settings = get_settings()
    owner_key = _require_owner_key(identity)
    queue = _require_queue(settings)

    existing = queue.store.get(job_id, owner_key=owner_key)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such question.")

    cancelled = queue.cancel(job_id, owner_key=owner_key)
    if cancelled is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This question already finished ({existing.status.value}).",
        )
    logger.info("[jobs] job_id=%s cancelled by its owner", job_id)
    return _job_out(cancelled, identity.roles, settings)


__all__ = ["router"]
