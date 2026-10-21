"""Request and response models for the asynchronous question queue.

Kept in its own module, beside `api/jobs.py`, following the
`*_schemas.py` convention every other router pair in this package uses
(`api/artifact_schemas.py`, `api/governance_schemas.py`, ...).

**These are new contracts, so they are allowed to be precise.**
`api/schemas.py`'s `AskResponse` is frozen and widely consumed
(CLAUDE.md 6.3), which is why its `status` still has four values. A job's
status has seven, because nothing was consuming it before now.

**What never appears in any model here.** No provider name, no API key,
no internal model configuration, no raw prompt, no routing rule, and no
system instruction (brief §3, §9). `model_used` is the one model-related
field, and it carries only the operator-configured Ollama model name that
`AskResponse.model` already returns on the synchronous path -- so this
exposes nothing `/ask` did not. `sources_used` carries the same source
names the existing "Sources used" panel already shows. Neither is a
secret; both are required for the auditing the brief asks for.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from api.schemas import ConversationExchangeIn
from jobs.models import JobStatus

#: The seven job statuses, as a Literal, so OpenAPI documents the exact
#: set a client must handle rather than a bare string. Derived from
#: `JobStatus` at import time would lose the literal type, so this is
#: written out -- and `tests/test_question_jobs_api.py` pins the two
#: against each other, which is what stops them drifting.
JobStatusOut = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timeout",
    "blocked_by_guardrail",
]


class SubmitQuestionRequest(BaseModel):
    """One question submitted for asynchronous execution.

    Deliberately a near-superset of `AskRequest`'s own fields rather than
    a reuse of it: `AskRequest` is the synchronous contract and adding a
    queue-only field (`idempotency_key`) to it would change a frozen,
    widely-consumed model. The fields that *are* shared keep their exact
    names and meanings, so a frontend can build one object and send it to
    either endpoint.

    `extra="forbid"` matches `AskRequest`: a misspelled field is a
    client bug worth surfacing, not a value to silently ignore.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question: str = Field(..., min_length=1, description="Natural-language question.")
    conversation_history: list[ConversationExchangeIn] = Field(
        default_factory=list,
        description="Recent prior turns (oldest first), for follow-up resolution.",
    )
    enable_insight: bool = Field(default=True)
    session_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    attachment_ids: list[str] = Field(default_factory=list)
    model: str | None = Field(
        default=None,
        description=(
            "An Ollama model from this deployment's configured allowlist. Validated "
            "before a queue slot is spent, exactly as POST /ask validates it."
        ),
    )
    forecast_horizon: int | None = Field(default=None, gt=0)
    sources: list[str] | None = Field(
        default=None,
        description=(
            "Restrict this question to these sources. A filter, never a grant -- a "
            "source the caller is not authorized for stays unreachable."
        ),
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Optional client-chosen key that makes a resubmission return the original "
            "job instead of starting a second one. Input to a server-side, "
            "owner-and-tenant-scoped hash -- never the stored key itself, so it cannot "
            "collide with another user's. Omit it and a key is derived from the "
            "question and options, which already makes a double-submitted form safe."
        ),
    )


class SubmitQuestionResponse(BaseModel):
    """The handle a caller gets back immediately.

    `status` is `queued` on a fresh admission. On a duplicate it is
    whatever the original job's status is now, which may already be
    terminal -- a client that resubmits after an answer arrived gets that
    answer's job back rather than a new run.
    """

    job_id: str
    status: JobStatusOut
    #: True when this submission matched an existing job instead of
    #: creating one. Reported rather than hidden so a client can tell
    #: "my retry was absorbed" from "my retry started new work".
    duplicate: bool = False
    #: This caller's own position/load at admission time. Counts only.
    queued_for_caller: int = 0
    running_for_caller: int = 0
    #: Echoed so a client can show "n of m" without a second call.
    max_concurrent_per_user: int = 0


class QuestionJobOut(BaseModel):
    """One job's current state, scoped to its owner.

    Only ever returned for a job the authenticated caller owns. A job
    belonging to anyone else produces the same 404 a nonexistent job id
    does (CLAUDE.md 5.3's anti-enumeration rule), so this model is never
    a vehicle for another user's data.
    """

    job_id: str
    status: JobStatusOut
    question: str
    #: `AgentState["status"]`'s four-value vocabulary, for a client that
    #: already renders that and wants one code path. The precise status
    #: is `status` above.
    agent_status: str
    attempts: int
    submitted_at: float
    queue_wait_seconds: float
    run_seconds: float | None = None
    #: Present only once the job is COMPLETED. Re-governed against the
    #: reader's *current* roles on every read, never served as captured
    #: (CLAUDE.md 5.4).
    result: dict[str, Any] | None = None
    #: A caller-safe message. Never internal detail or a stack trace.
    error_message: str | None = None
    #: Which guardrail refused, when status is `blocked_by_guardrail`. A
    #: fixed constant, never built from the question.
    guardrail_reason: str | None = None
    #: Observability, safe to show: the same source names the existing
    #: "Sources used" panel displays, and the same model name
    #: `AskResponse.model` already returns.
    sources_used: list[str] = Field(default_factory=list)
    model_used: str | None = None
    source_confidence: dict[str, float] = Field(default_factory=dict)
    correlation_id: str | None = None


class QuestionJobListOut(BaseModel):
    """This caller's jobs, newest first."""

    jobs: list[QuestionJobOut]
    total: int


class QueueStatsOut(BaseModel):
    """Capacity counts for the queue. Counts only -- never content.

    The global numbers are deliberately included for an authenticated
    caller, not just an admin: they are what a client needs to explain a
    queued question honestly ("the system is busy" versus "you are at
    your own limit"), and a count of running jobs reveals nothing about
    who is running them or what they asked. The per-caller numbers are
    this caller's own, so one user cannot read another's load.
    """

    running_global: int
    queued_global: int
    max_concurrent_global: int
    max_concurrent_per_user: int
    max_queue_depth: int
    running_for_caller: int
    queued_for_caller: int
    #: False when `ENABLE_QUESTION_QUEUE` is off, so a client can hide the
    #: asynchronous UI instead of discovering it through a 503.
    enabled: bool = True


def job_status_literal(status: JobStatus) -> JobStatusOut:
    """`JobStatus` as the `Literal` the response models declare.

    A one-line cast that exists so `api/jobs.py` has a single, typed
    place where the enum becomes the wire value, rather than a bare
    `str(...)` at each construction site.
    """
    # `cast`, not `# type: ignore`: the value genuinely is one of the
    # Literal's members (the test pins the enum against it), and a bare
    # ignore would also silence an unrelated future error here.
    return cast("JobStatusOut", status.value)


__all__ = [
    "JobStatusOut",
    "QuestionJobListOut",
    "QuestionJobOut",
    "QueueStatsOut",
    "SubmitQuestionRequest",
    "SubmitQuestionResponse",
    "job_status_literal",
]
