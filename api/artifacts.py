"""The analysis artifact workspace REST surface -- see
`docs/conversational-intelligence-ui.md`.

Five routes under `/artifacts`: save, list, open, refresh, delete. Every
one requires a local account (`require_local_user`) and then calls
`artifacts.policy.authorize_artifact_action`, acting only on an
`allowed=True` decision -- the same discipline `api/bi_integration.py`/
`api/governance.py` already follow, including the
cross-tenant-denial-is-a-404 anti-enumeration precedent, extended here to
cross-*user* denial for the reason `artifacts/policy.py`'s own docstring
gives.

**Three properties are worth knowing before changing anything here.**

1. *A stored snapshot is re-governed on every read, never trusted because
   it was governed when it was captured.* `governance.result_policy
   .govern_rows_for_caller` runs against the **current** caller's roles
   each time an artifact is opened. An analyst who saved an artifact over
   a restricted column and later lost `view_restricted_columns` sees
   `[restricted]` on reopen -- an artifact cannot become a durable copy of
   data its owner is no longer allowed to see (requirement 8).

2. *A refresh reuses `POST /execute`'s own governed path verbatim* --
   `validate_sql` -> `enforce_row_limit` -> `qualify_table_schema` ->
   `execute_readonly_sql` -> `govern_rows_for_caller`. The recorded SQL is
   re-validated fresh every single time, never trusted because it was
   valid when the artifact was created ("SQL is untrusted output,
   always"), and the route accepts no SQL from the caller at all. A
   refresh also requires `Permission.EXECUTE_SQL` -- the permission
   `POST /execute` itself requires -- so a `viewer` who may ask but may
   not run queries cannot make an artifact run one for them.

3. *A refresh never writes back into the referenced conversation turn.*
   It writes into the artifact's own row. Mutating
   `ai_outputs.metadata.result_snapshot` would make a saved conversation
   display numbers that were never the answer to that question at that
   time -- the mirror image of chat history's own "opening a saved
   conversation never re-executes SQL" invariant.

An operational refresh failure is a 200 with the failure recorded on the
artifact (`last_refresh_outcome`/`last_refresh_error`), never an HTTP
error code -- the identical shape `POST /execute`'s own
`ExecuteResponse(status="failed", ...)` and `POST /bi/connectors/{id}
/refresh` already use. An HTTP error code is reserved for a request-level
problem: authorization, validation, rate limit, not-found.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from identity.models import AiOutput, AnalysisArtifact, User
from identity.repositories.artifacts import (
    artifact_to_snapshot,
    count_active_artifacts,
    create_artifact,
    find_artifact_for_output,
    get_artifact_by_id,
    get_owned_prompt,
    list_artifacts,
    load_source_turn,
    record_refresh_result,
    soft_delete_artifact,
)
from identity.repositories.history import get_ai_output, get_conversation
from identity.repositories.users import get_user_roles
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent.authz import Permission, has_role_permission
from agent.sql_validator import (
    enforce_row_limit,
    find_restricted_column_references,
    qualify_table_schema,
    summarize_sql,
    validate_sql,
)
from api.artifact_schemas import (
    ArtifactDetailOut,
    ArtifactFreshnessOut,
    ArtifactListOut,
    ArtifactOut,
    ArtifactResultOut,
    CreateArtifactRequest,
    RefreshArtifactRequest,
)
from api.identity_authz import require_local_user
from api.rate_limit import enforce_api_action_rate_limit
from api.result_serialization import rows_to_json
from api.schemas import ChartRecommendationOut
from artifacts.models import ArtifactSnapshot
from artifacts.policy import ArtifactAction, authorize_artifact_action
from artifacts.staleness import (
    assess_freshness,
    fingerprint_result,
    result_changed_on_refresh,
    standing_result,
)
from config.sensitive_columns import load_sensitive_columns
from config.settings import Settings, get_settings
from db.connection import get_read_only_engine, get_sqlglot_dialect
from db.execution import execute_readonly_sql
from embeddings.schema_indexer import get_last_schema_fingerprint
from governance.domain_detection import detect_domain
from governance.result_policy import govern_rows_for_caller, narrative_withhold_reason
from security.audit_log import log_security_event
from security.redaction import redact_secrets
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_DEFAULT_REFRESH_REQUEST = RefreshArtifactRequest()

_TITLE_MAX_LENGTH = 300

#: Fixed sentences, chosen by cause. Never a validator rule name, a raw
#: driver error, or the name of a restricted column -- the same reason
#: `frontend/src/components/sql/QueryLifecycle.tsx` renders fixed stop
#: reasons rather than the server's own error text.
_NO_SQL_RECORDED = "This artifact has no query to re-run."
_NO_DATABASE_RECORDED = "This artifact does not record which database it came from."
_DATABASE_UNAVAILABLE = "The database this artifact came from is no longer available to you."
_SQL_WITHHELD_RESTRICTED = (
    "The query is not shown because it references a field your role may not view."
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")


def _authorize(
    user: User,
    session: Session,
    action: ArtifactAction,
    artifact: AnalysisArtifact | None,
) -> tuple[str, list[str]]:
    """Returns `(actor_tenant_id, actor_roles)` on success; raises on denial.

    A denial that names the resource's tenant *or* its owner becomes a 404
    -- a caller must not be able to tell "no such artifact" from "someone
    else's artifact", which would make this route an existence oracle for
    other people's saved analyses.
    """
    actor_tenant_id = resolve_actor_tenant_id(user)
    actor_roles = get_user_roles(session, user.id)
    decision = authorize_artifact_action(
        actor_roles=actor_roles,
        actor_user_id=str(user.id),
        actor_tenant_id=actor_tenant_id,
        resource_tenant_id=artifact.tenant_id if artifact is not None else None,
        resource_owner_user_id=(str(artifact.owner_user_id) if artifact is not None else None),
        action=action,
    )
    if not decision.allowed:
        if artifact is not None and decision.reason in {
            "artifact belongs to a different tenant",
            "artifact belongs to a different user",
        }:
            raise _not_found()
        raise _forbidden()
    if actor_tenant_id is None:  # pragma: no cover - policy already refused this
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not resolve a tenant for this account.",
        )
    return actor_tenant_id, list(actor_roles)


def _require_artifact(session: Session, artifact_id: uuid.UUID) -> AnalysisArtifact:
    artifact = get_artifact_by_id(session, artifact_id)
    if artifact is None or artifact.deleted_at is not None:
        # A soft-deleted artifact is gone as far as every route is
        # concerned -- `deleted_at` is retention bookkeeping, not a
        # recoverable state this API exposes.
        raise _not_found()
    return artifact


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _snapshot_from_turn(ai_output: AiOutput | None) -> dict[str, Any]:
    """The source turn's own persisted `result_snapshot`, or `{}`.

    This is the dict `api.chat_persistence.persist_execute_result` wrote
    when the user clicked "Confirm and Run" -- already bounded, already
    redacted, already the rows that turn displays. Reading it is why an
    artifact stores no result of its own (requirement 3).
    """
    if ai_output is None:
        return {}
    snapshot = (ai_output.metadata_json or {}).get("result_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _fingerprint_of_snapshot(snapshot: dict[str, Any], settings: Settings) -> str | None:
    """Fingerprints a stored snapshot's rows, capped identically to the
    way a refresh caps its own before fingerprinting -- otherwise the two
    hashes would differ for an unchanged result purely because one side
    saw more rows than the other."""
    columns = snapshot.get("columns")
    rows = snapshot.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return None
    return fingerprint_result(columns, rows[: settings.chat_history_max_result_rows])


def _result_out(
    snapshot: dict[str, Any], actor_roles: list[str], settings: Settings
) -> ArtifactResultOut | None:
    """One stored snapshot, **re-governed for the caller asking now**.

    This is the single most important line in this module: the rows were
    governed for whoever executed them, and roles change. Re-running
    `govern_rows_for_caller` here means an artifact can never serve as a
    durable copy of data its owner is no longer permitted to see.
    """
    columns = snapshot.get("columns")
    rows = snapshot.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return None
    if settings.enable_result_governance:
        rows = govern_rows_for_caller(columns, rows, actor_roles)
    chart = snapshot.get("chart_recommendation")
    return ArtifactResultOut(
        columns=[str(column) for column in columns],
        rows=[list(row) for row in rows],
        row_count=snapshot.get("row_count"),
        returned_rows=len(rows),
        truncated=bool(snapshot.get("truncated")),
        column_types=snapshot.get("column_types"),
        chart_recommendation=(
            ChartRecommendationOut.model_validate(chart) if isinstance(chart, dict) else None
        ),
        captured_at=_as_datetime(snapshot.get("captured_at")),
    )


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _database_available(snapshot: ArtifactSnapshot, settings: Settings) -> bool:
    """Whether this artifact's database is still configured *for this
    tenant* -- `Settings.databases_for_tenant`, the same per-tenant view
    `POST /execute` and `GET /schema/tables` resolve names through
    (Prompt 20). A tenant that loses access to a database must not keep
    refreshing artifacts against it.

    An artifact that never recorded a database (every non-SQL source --
    a document, policy, web or media answer) is `True`: the question
    this answers is "has this artifact's database gone away", and for an
    artifact that never had one the honest answer is "nothing has gone
    away", not "unavailable". Returning `False` here put
    `DATABASE_NOT_AVAILABLE` in the freshness reasons of every non-SQL
    artifact, which reads as something having broken. Refusing a refresh
    is handled separately and earlier by `_refresh_block_reason`, which
    checks `source_sql`/`database_id` before it ever consults this."""
    if not snapshot.database_id:
        return True
    return any(
        db.name == snapshot.database_id for db in settings.databases_for_tenant(snapshot.tenant_id)
    )


def _refresh_block_reason(snapshot: ArtifactSnapshot, database_available: bool) -> str | None:
    if not snapshot.source_sql:
        return _NO_SQL_RECORDED
    if not snapshot.database_id:
        return _NO_DATABASE_RECORDED
    if not database_available:
        return _DATABASE_UNAVAILABLE
    return None


def _sql_for_caller(
    snapshot: ArtifactSnapshot, actor_roles: list[str], settings: Settings
) -> tuple[str | None, str | None]:
    """The artifact's recorded SQL, or `(None, reason)` when this caller
    may not be shown it.

    Checked against the **current** roles, exactly like the rows: if the
    query selects a column `config/sensitive_columns.yaml` classifies
    "restricted" and the caller no longer holds
    `Permission.VIEW_RESTRICTED_COLUMNS`, the text is withheld. Reuses
    `agent.sql_validator.find_restricted_column_references` -- the same
    function `validate_sql_node`'s own pre-execution gate uses, so there
    is one answer to "does this query touch a restricted field", not two.
    """
    sql = snapshot.source_sql
    if not sql:
        return None, None
    if has_role_permission(actor_roles, Permission.VIEW_RESTRICTED_COLUMNS):
        return sql, None
    restricted_pairs = {
        pair for pair, tier in load_sensitive_columns().items() if tier == "restricted"
    }
    if not restricted_pairs:
        return sql, None
    dialect = _dialect_for(snapshot, settings)
    try:
        hits = find_restricted_column_references(sql, restricted_pairs, set(), dialect=dialect)
    except Exception:  # noqa: BLE001 - an unparseable stored query withholds, never leaks
        logger.warning("[artifacts] could not parse stored SQL for a restricted-column check")
        return None, _SQL_WITHHELD_RESTRICTED
    if hits:
        return None, _SQL_WITHHELD_RESTRICTED
    return sql, None


def _narrative_for_caller(
    snapshots: Sequence[dict[str, Any] | None],
    actor_roles: list[str],
    settings: Settings,
) -> str | None:
    """`None` when this caller may be shown the artifact's prose, or the
    reason it is withheld -- the narrative counterpart to
    `_sql_for_caller`, and the same decision `api.shares
    ._narrative_withheld_reason` applies to a shared turn.

    `_result_out` re-governs the rows for the caller asking now, but the
    recorded `answer`/`insight` were written by
    `agent.nodes.generate_insight_node` from those rows *before*
    governing, under the runner's permissions. So an artifact whose
    table correctly reads `[restricted]` could still state the figure in
    its own summary sentence. Checked against the current roles for the
    same reason the rows and the SQL are: roles change.

    Judged over every snapshot this detail view serves (origin and
    refreshed), since one withheld column anywhere in it is enough to
    make the single shared narrative undisclosable.
    """
    if not settings.enable_result_governance:
        return None
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        columns = snapshot.get("columns")
        if not isinstance(columns, list) or not columns:
            continue
        column_names = [str(column) for column in columns]
        stored_sql = snapshot.get("normalized_sql") or ""
        domain = detect_domain(str(stored_sql), column_names).domain
        reason = narrative_withhold_reason(column_names, actor_roles, domain)
        if reason is not None:
            return reason
    return None


def _dialect_for(snapshot: ArtifactSnapshot, settings: Settings) -> str | None:
    """The sqlglot dialect of the artifact's own database.

    Passes `config.db_type`, never the config object -- the exact mistake
    `api/main.py::_sql_summary_out` made, where a `DatabaseConnectionConfig`
    reached `get_sqlglot_dialect` and silently resolved to the generic
    dialect, under which T-SQL does not parse at all (see CLAUDE.md's
    note on it).
    """
    for db in settings.databases_for_tenant(snapshot.tenant_id):
        if db.name == snapshot.database_id:
            return get_sqlglot_dialect(db.db_type)
    return None


def _freshness_out(
    snapshot: ArtifactSnapshot,
    *,
    settings: Settings,
    source_turn_available: bool,
    database_available: bool,
) -> ArtifactFreshnessOut:
    live_fingerprint = (
        get_last_schema_fingerprint(snapshot.database_id, settings)
        if snapshot.database_id
        else None
    )
    assessment = assess_freshness(
        snapshot,
        as_of=datetime.now(UTC),
        aging_after_seconds=settings.artifact_aging_after_hours * 3600,
        stale_after_seconds=settings.artifact_stale_after_hours * 3600,
        source_turn_available=source_turn_available,
        live_schema_fingerprint=live_fingerprint,
        database_available=database_available,
    )
    return ArtifactFreshnessOut(
        state=assessment.state.value,
        reasons=[reason.value for reason in assessment.reasons],
        result_age_seconds=assessment.result_age_seconds,
        result_as_of=assessment.result_as_of,
    )


def _out(
    artifact: AnalysisArtifact,
    *,
    session: Session,
    settings: Settings,
) -> ArtifactOut:
    """One list row. Loads the source turn only to answer "is it still
    there" -- the rows themselves are never read for a list."""
    snapshot = artifact_to_snapshot(artifact)
    _prompt, ai_output = load_source_turn(session, artifact)
    source_available = _prompt is not None or ai_output is not None
    database_available = _database_available(snapshot, settings)
    standing = standing_result(snapshot)
    return ArtifactOut(
        id=snapshot.id,
        artifact_type=snapshot.artifact_type.value,
        title=snapshot.title,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
        conversation_id=snapshot.conversation_id,
        prompt_id=snapshot.prompt_id,
        message_id=snapshot.ai_output_id,
        database=snapshot.lineage.database,
        model=snapshot.lineage.model,
        sources_used=list(snapshot.lineage.sources_used),
        row_count=standing.row_count,
        freshness=_freshness_out(
            snapshot,
            settings=settings,
            source_turn_available=source_available,
            database_available=database_available,
        ),
        is_refreshable=snapshot.is_refreshable and database_available,
        refresh_blocked_reason=_refresh_block_reason(snapshot, database_available),
        last_refresh_at=snapshot.last_refresh_at,
        last_refresh_outcome=snapshot.last_refresh_outcome.value,
        last_refresh_error=snapshot.last_refresh_error,
    )


def _detail_out(
    artifact: AnalysisArtifact,
    *,
    session: Session,
    settings: Settings,
    actor_roles: list[str],
) -> ArtifactDetailOut:
    snapshot = artifact_to_snapshot(artifact)
    prompt, ai_output = load_source_turn(session, artifact)
    source_available = prompt is not None or ai_output is not None
    sql, sql_withheld_reason = _sql_for_caller(snapshot, actor_roles, settings)

    origin_result = (
        _result_out(_snapshot_from_turn(ai_output), actor_roles, settings)
        if source_available
        else None
    )
    refreshed_raw = (artifact.metadata_json or {}).get("refreshed_snapshot")
    refreshed_result = (
        _result_out(refreshed_raw, actor_roles, settings)
        if isinstance(refreshed_raw, dict)
        else None
    )

    narrative_withheld_reason = _narrative_for_caller(
        [_snapshot_from_turn(ai_output) if source_available else None, refreshed_raw],
        actor_roles,
        settings,
    )
    if narrative_withheld_reason is not None:
        logger.info(
            "[artifacts] narrative text withheld from an artifact detail (%s)",
            narrative_withheld_reason,
        )

    base = _out(artifact, session=session, settings=settings)
    return ArtifactDetailOut(
        **base.model_dump(),
        question=prompt.final_content if prompt is not None else None,
        answer=(None if narrative_withheld_reason else _answer_from_turn(ai_output)),
        insight=(None if narrative_withheld_reason else snapshot.lineage.insight),
        narrative_withheld_reason=narrative_withheld_reason,
        insight_numbers_checked=snapshot.lineage.insight_numbers_checked,
        tables_referenced=list(snapshot.lineage.tables_referenced),
        governed_metrics=list(snapshot.lineage.governed_metrics),
        truth_level=snapshot.lineage.truth_level.value,
        sql=sql,
        sql_withheld_reason=sql_withheld_reason,
        origin_result=origin_result,
        refreshed_result=refreshed_result,
        result_changed_since_creation=result_changed_on_refresh(snapshot),
    )


def _referenced_tables(sql: str | None, dialect: str | None) -> list[str]:
    """The tables the artifact's query actually references.

    Deliberately **not** the turn's persisted `schema_tables`: those are
    the top-k candidates schema retrieval offered, routinely a superset of
    what the query uses, so labelling them "tables referenced" would be
    wrong -- the exact trap `agent.sql_validator.summarize_sql`'s own
    docstring was written about, and which a real saved artifact here
    showed with 7 consulted tables for a 3-table query.

    So this reuses `summarize_sql`, the one existing answer to that
    question, rather than a second extraction. SQL that does not parse
    yields an empty list: showing nothing is right, and showing the
    consulted set instead would reintroduce the mislabel.
    """
    if not sql:
        return []
    summary = summarize_sql(sql, dialect=dialect)
    return list(summary[1]) if summary is not None else []


def _answer_from_turn(ai_output: AiOutput | None) -> str | None:
    """The answer this turn actually gave, whatever produced it.

    Read from the turn's own persisted fields, never re-derived, so an
    artifact can never disagree with the conversation it was saved from.

    Two fields, in this order, because neither alone is right:

    `synthesized_answer` first. It is the *whole* answer for a run that
    used more than one source -- `agent.orchestrator.nodes.synthesis_node`
    writes one labelled section per source into it -- and it is absent
    for a single-source SQL run, where synthesis is a pass-through.

    `AiOutput.content` otherwise. That is chat history's own canonical
    pick (`api.chat_persistence._answer_text_for_history`), which covers
    every source, so this adds no second answer-picking convention.

    Taking `content` alone was wrong, and a real multi-source run showed
    it: that function prefers the SQL `insight` over everything else, so
    an artifact saved from an attachments+sql turn carried only the
    database half ("Australia, 9,061,000") while the attachment half --
    the part the question was actually about ("East, 143500") -- was
    silently dropped. Taking `synthesized_answer` alone would be wrong
    too: a plain SQL turn has none.

    `insight` on its own stays what it is -- the SQL pipeline's narrative,
    `None` for every other source -- and is reported separately.
    """
    if ai_output is None:
        return None
    synthesized = (ai_output.metadata_json or {}).get("synthesized_answer")
    if isinstance(synthesized, str) and synthesized.strip():
        return synthesized
    return ai_output.content


def _lineage_from_turn(
    ai_output: AiOutput | None, *, referenced_tables: list[str]
) -> dict[str, Any]:
    """The lineage an artifact retains, copied from the turn's own already-
    persisted metadata -- never re-derived, so an artifact can never claim
    something the turn did not say. The one exception is
    `tables_referenced`, which the turn never recorded accurately: see
    `_referenced_tables` above."""
    metadata = (ai_output.metadata_json or {}) if ai_output is not None else {}
    governed = metadata.get("governing_metrics") or metadata.get("governed_metrics")
    return {
        "database": metadata.get("database"),
        "model": metadata.get("model"),
        "sources_used": list(metadata.get("sources_used") or []),
        "tables_referenced": referenced_tables,
        "governed_metrics": [str(item) for item in governed] if isinstance(governed, list) else [],
        "insight": metadata.get("insight"),
        "insight_numbers_checked": metadata.get("insight_numbers_checked"),
    }


def _derive_title(question: str | None) -> str:
    """The source question, trimmed and capped -- the same convention
    `identity.repositories.history._derive_title` already uses for a
    conversation name."""
    collapsed = " ".join((question or "").split())
    if not collapsed:
        return "Saved analysis"
    return collapsed[:_TITLE_MAX_LENGTH]


@router.post("", response_model=ArtifactDetailOut, status_code=status.HTTP_201_CREATED)
def save_artifact(
    payload: CreateArtifactRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ArtifactDetailOut:
    """Saves an already-answered turn as a durable artifact.

    Nothing about the result comes from the request: the turn is looked up
    through `identity.repositories.history.get_ai_output`, whose own
    `user_id` scoping means a forged or borrowed `message_id` simply does
    not resolve. Saving the same turn twice returns the existing artifact
    rather than accumulating duplicates.
    """
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "artifact_create", settings, identity=None)
    tenant_id, actor_roles = _authorize(user, session, ArtifactAction.CREATE, None)

    conversation_id = _parse_uuid(payload.conversation_id)
    ai_output_id = _parse_uuid(payload.message_id)
    if conversation_id is None or ai_output_id is None:
        raise _not_found()

    conversation = get_conversation(session, conversation_id=conversation_id, user_id=user.id)
    ai_output = get_ai_output(session, ai_output_id=ai_output_id, user_id=user.id)
    if conversation is None or ai_output is None:
        raise _not_found()
    if ai_output.conversation_id != conversation.id:
        # Defense in depth behind the two ownership-scoped lookups above:
        # both rows are this caller's, but they must also belong together.
        raise _not_found()

    existing = find_artifact_for_output(
        session, tenant_id=tenant_id, owner_user_id=user.id, ai_output_id=ai_output_id
    )
    if existing is not None:
        return _detail_out(existing, session=session, settings=settings, actor_roles=actor_roles)

    if count_active_artifacts(session, tenant_id=tenant_id, owner_user_id=user.id) >= (
        settings.artifact_max_per_user
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have {settings.artifact_max_per_user} saved analyses. "
                "Delete one to save another."
            ),
        )

    metadata = ai_output.metadata_json or {}
    turn_snapshot = _snapshot_from_turn(ai_output)
    database_id = metadata.get("database")
    recorded_sql = metadata.get("sql") if isinstance(metadata.get("sql"), str) else None
    # Parsed in the artifact's own database's dialect: T-SQL does not parse
    # under sqlglot's generic dialect at all, so resolving this from the
    # config (never passing the config object itself) matters -- the exact
    # bug `api/main.py::_sql_summary_out` once had.
    dialect = next(
        (
            get_sqlglot_dialect(db.db_type)
            for db in settings.databases_for_tenant(tenant_id)
            if db.name == database_id
        ),
        None,
    )
    referenced_tables = _referenced_tables(recorded_sql, dialect)
    prompt_id = ai_output.prompt_id
    prompt = get_owned_prompt(session, prompt_id=prompt_id, user_id=user.id)
    question = prompt.final_content if prompt is not None else None

    artifact = create_artifact(
        session,
        tenant_id=tenant_id,
        owner_user_id=user.id,
        conversation_id=conversation.id,
        prompt_id=prompt_id,
        ai_output_id=ai_output.id,
        artifact_type=payload.artifact_type,
        title=payload.title or _derive_title(question),
        database_id=database_id if isinstance(database_id, str) else None,
        # The SQL actually recorded for the turn: `persist_execute_result`
        # overwrites `metadata["sql"]` with the statement that was really
        # run, so a hand-edited query is what gets saved -- the same "save
        # what was actually run" principle `embeddings.golden_examples`
        # applies.
        source_sql=recorded_sql,
        schema_fingerprint=(
            get_last_schema_fingerprint(database_id, settings)
            if isinstance(database_id, str)
            else None
        ),
        origin_row_count=turn_snapshot.get("row_count"),
        origin_captured_at=_as_datetime(turn_snapshot.get("captured_at")),
        origin_fingerprint=_fingerprint_of_snapshot(turn_snapshot, settings),
        lineage=_lineage_from_turn(ai_output, referenced_tables=referenced_tables),
    )
    log_security_event(
        "analysis_artifact_created",
        "info",
        "An analysis artifact was saved.",
        artifact_id=str(artifact.id),
        artifact_type=artifact.artifact_type,
        actor_user_id=str(user.id),
    )
    return _detail_out(artifact, session=session, settings=settings, actor_roles=actor_roles)


@router.get("", response_model=ArtifactListOut)
def list_saved_artifacts(
    conversation_id: str | None = Query(default=None, max_length=64),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ArtifactListOut:
    """The caller's own artifacts, newest first, each with its freshness
    computed now. Never another user's and never another tenant's -- both
    are folded into the query itself, not checked afterwards."""
    user, session = user_and_session
    settings = get_settings()
    tenant_id, _roles = _authorize(user, session, ArtifactAction.LIST, None)
    scoped_conversation = _parse_uuid(conversation_id) if conversation_id else None
    rows = list_artifacts(
        session,
        tenant_id=tenant_id,
        owner_user_id=user.id,
        conversation_id=scoped_conversation,
        limit=settings.artifact_max_per_user,
    )
    return ArtifactListOut(
        items=[_out(row, session=session, settings=settings) for row in rows],
        total=len(rows),
        limit_per_user=settings.artifact_max_per_user,
    )


@router.get("/{artifact_id}", response_model=ArtifactDetailOut)
def open_artifact(
    artifact_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ArtifactDetailOut:
    """Opens one artifact: its source question, lineage, SQL (when this
    caller may see it) and result snapshots -- **re-governed for this
    caller, now**, never served as captured. Executes nothing."""
    user, session = user_and_session
    settings = get_settings()
    artifact = _require_artifact(session, artifact_id)
    _tenant_id, actor_roles = _authorize(user, session, ArtifactAction.VIEW, artifact)
    return _detail_out(artifact, session=session, settings=settings, actor_roles=actor_roles)


@router.post("/{artifact_id}/refresh", response_model=ArtifactDetailOut)
def refresh_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    payload: RefreshArtifactRequest = _DEFAULT_REFRESH_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ArtifactDetailOut:
    """Re-runs the artifact's recorded SQL through the governed path and
    records the result on the artifact.

    Requires `Permission.EXECUTE_SQL` (enforced by
    `artifacts.policy`) -- the same permission `POST /execute` requires,
    because this is an execution. The SQL is re-validated fresh; the
    caller supplies none. An execution failure is recorded on the artifact
    and returned as a 200, per this module's own docstring.
    """
    del payload  # No options yet -- see `RefreshArtifactRequest`.
    user, session = user_and_session
    settings = get_settings()
    artifact = _require_artifact(session, artifact_id)
    _tenant_id, actor_roles = _authorize(user, session, ArtifactAction.REFRESH, artifact)
    enforce_api_action_rate_limit(request, "artifact_refresh", settings, identity=None)

    snapshot = artifact_to_snapshot(artifact)
    database_available = _database_available(snapshot, settings)
    blocked = _refresh_block_reason(snapshot, database_available)
    if blocked is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked)

    db_config = next(
        db
        for db in settings.databases_for_tenant(snapshot.tenant_id)
        if db.name == snapshot.database_id
    )
    dialect = get_sqlglot_dialect(db_config.db_type)
    sql = snapshot.source_sql or ""

    # "SQL is untrusted output, always" -- a stored statement is exactly as
    # untrusted as a freshly generated one, so it is re-validated here even
    # though it validated when the artifact was created.
    validation = validate_sql(sql, dialect=dialect)
    if not validation.is_valid or validation.normalized_sql is None:
        updated = record_refresh_result(
            session, artifact, success=False, error=f"Rejected: {validation.error}"
        )
        return _detail_out(updated, session=session, settings=settings, actor_roles=actor_roles)

    safe_sql = enforce_row_limit(
        validation.normalized_sql, settings.max_result_rows, dialect=dialect
    )
    execution_sql = qualify_table_schema(safe_sql, db_config.db_schema, dialect=dialect)
    try:
        columns, rows = execute_readonly_sql(
            execution_sql,
            settings.query_timeout_seconds,
            settings.max_result_rows,
            engine=get_read_only_engine(db_config),
        )
    except (SQLAlchemyError, TimeoutError) as exc:
        safe_detail = redact_secrets(str(exc), db_config)
        updated = record_refresh_result(
            session, artifact, success=False, error=f"Execution failed: {safe_detail}"
        )
        return _detail_out(updated, session=session, settings=settings, actor_roles=actor_roles)

    row_count = len(rows)
    governed_rows: list[Any] = list(rows)
    if settings.enable_result_governance:
        governed_rows = govern_rows_for_caller(columns, rows, actor_roles)
    # Convert to plain JSON values **once**, before anything stores or
    # hashes them. Two real failures, both found by refreshing a real SQL
    # Server result whose `decimal` columns arrive as `decimal.Decimal`:
    # the JSONB write raised `TypeError: Object of type Decimal is not
    # JSON serializable` outright, and the fingerprint below (which falls
    # back to `str` for an unknown type) hashed `"66302381.557"` where the
    # origin snapshot -- already JSON, read back as a float -- hashed
    # `66302381.557`, so an unchanged result reported "the numbers moved"
    # on every single refresh. Both sides now hash the same shape, which
    # is what makes `artifacts.staleness.result_changed_on_refresh`
    # mean what it says.
    bounded = rows_to_json(governed_rows[: settings.chat_history_max_result_rows]) or []
    refreshed_snapshot = {
        "columns": [str(column) for column in columns],
        "rows": bounded,
        "row_count": row_count,
        "returned_rows": len(bounded),
        "truncated": len(bounded) < row_count,
        "captured_at": datetime.now(UTC).isoformat(),
    }
    updated = record_refresh_result(
        session,
        artifact,
        success=True,
        row_count=row_count,
        fingerprint=fingerprint_result(columns, bounded),
        error=None,
        refreshed_snapshot=refreshed_snapshot,
    )
    log_security_event(
        "analysis_artifact_refreshed",
        "info",
        "An analysis artifact was refreshed.",
        artifact_id=str(artifact.id),
        row_count=row_count,
        actor_user_id=str(user.id),
    )
    return _detail_out(updated, session=session, settings=settings, actor_roles=actor_roles)


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> Response:
    """Soft-deletes one artifact. The conversation turn it referenced is
    untouched -- deleting a saved analysis must never delete the question
    and answer it was made from."""
    user, session = user_and_session
    settings = get_settings()
    artifact = _require_artifact(session, artifact_id)
    _authorize(user, session, ArtifactAction.DELETE, artifact)
    enforce_api_action_rate_limit(request, "artifact_delete", settings, identity=None)
    soft_delete_artifact(session, artifact)
    log_security_event(
        "analysis_artifact_deleted",
        "info",
        "An analysis artifact was deleted.",
        artifact_id=str(artifact.id),
        actor_user_id=str(user.id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
