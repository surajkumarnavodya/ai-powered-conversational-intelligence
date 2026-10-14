"""Repository layer for analysis artifacts -- `AnalysisArtifact`,
the analysis artifact workspace (`docs/conversational-intelligence-ui.md`).

**This module never makes an authorization decision itself** --
`artifacts.policy.authorize_artifact_action` is the only place that
happens, mirroring `identity/repositories/bi_integration.py`'s own split.
`get_artifact_by_id` is deliberately *unscoped*, which is what lets
`api/artifacts.py` return one indistinguishable 404 for "no such
artifact", "another tenant's artifact" and "another user's artifact" --
the anti-enumeration convention `api/semantic_catalog.py` established and
every tenant-scoped router here has followed since. It is the only
unscoped read in this module: `list_artifacts`/`count_active_artifacts`/
`find_artifact_for_output` all fold tenant **and** owner into the `WHERE`
clause itself, never a post-hoc filter.

**No result rows are stored by anything in this module except
`record_refresh_result`**, and that one is bounded by the caller before it
arrives. See `identity.models.AnalysisArtifact`'s own docstring for why an
artifact references the turn's existing snapshot instead of copying it,
and why a refresh must never write back into that turn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artifacts.models import (
    ArtifactLineage,
    ArtifactSnapshot,
    ArtifactType,
    RefreshOutcome,
    ResultSnapshotRef,
)
from identity.models import AiOutput, AnalysisArtifact, Prompt
from identity.repositories.history import get_ai_output, get_conversation


def create_artifact(
    session: Session,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    artifact_type: str,
    title: str,
    prompt_id: uuid.UUID | None = None,
    ai_output_id: uuid.UUID | None = None,
    database_id: str | None = None,
    source_sql: str | None = None,
    schema_fingerprint: str | None = None,
    origin_row_count: int | None = None,
    origin_captured_at: datetime | None = None,
    origin_fingerprint: str | None = None,
    lineage: dict[str, Any] | None = None,
) -> AnalysisArtifact:
    """Creates one artifact. Every field is supplied by the caller from
    the already-persisted turn -- this function derives nothing, so there
    is no way for it to record something the turn did not actually say."""
    artifact = AnalysisArtifact(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        conversation_id=conversation_id,
        prompt_id=prompt_id,
        ai_output_id=ai_output_id,
        artifact_type=artifact_type,
        title=title,
        database_id=database_id,
        source_sql=source_sql,
        schema_fingerprint=schema_fingerprint,
        origin_row_count=origin_row_count,
        origin_captured_at=origin_captured_at,
        origin_fingerprint=origin_fingerprint,
        last_refresh_outcome=RefreshOutcome.NEVER_REFRESHED.value,
        metadata_json={"lineage": lineage or {}, "refreshed_snapshot": None},
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def get_artifact_by_id(session: Session, artifact_id: uuid.UUID) -> AnalysisArtifact | None:
    """Unscoped by design -- see this module's own docstring. The caller
    must call `artifacts.policy.authorize_artifact_action` with this row's
    `tenant_id`/`owner_user_id` before acting on it."""
    return session.get(AnalysisArtifact, artifact_id)


def list_artifacts(
    session: Session,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[AnalysisArtifact]:
    """The caller's own artifacts, newest first. Soft-deleted rows are
    never returned."""
    stmt = (
        select(AnalysisArtifact)
        .where(
            AnalysisArtifact.tenant_id == tenant_id,
            AnalysisArtifact.owner_user_id == owner_user_id,
            AnalysisArtifact.deleted_at.is_(None),
        )
        .order_by(AnalysisArtifact.created_at.desc())
        .limit(limit)
    )
    if conversation_id is not None:
        stmt = stmt.where(AnalysisArtifact.conversation_id == conversation_id)
    return list(session.execute(stmt).scalars().all())


def count_active_artifacts(session: Session, *, tenant_id: str, owner_user_id: uuid.UUID) -> int:
    """How many live artifacts this caller already has -- the retention
    cap (`Settings.artifact_max_per_user`) is enforced against this, by
    the caller, before a create."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(AnalysisArtifact)
            .where(
                AnalysisArtifact.tenant_id == tenant_id,
                AnalysisArtifact.owner_user_id == owner_user_id,
                AnalysisArtifact.deleted_at.is_(None),
            )
        )
        or 0
    )


def find_artifact_for_output(
    session: Session,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    ai_output_id: uuid.UUID,
) -> AnalysisArtifact | None:
    """The caller's existing live artifact for one answer row, if any.

    Lets a save be idempotent per turn rather than accumulating a second,
    identical artifact every time the button is pressed -- the same
    "re-clicking upserts rather than duplicating" property
    `embeddings.golden_examples` already gives its own deterministic ids.
    """
    return session.scalar(
        select(AnalysisArtifact).where(
            AnalysisArtifact.tenant_id == tenant_id,
            AnalysisArtifact.owner_user_id == owner_user_id,
            AnalysisArtifact.ai_output_id == ai_output_id,
            AnalysisArtifact.deleted_at.is_(None),
        )
    )


def soft_delete_artifact(session: Session, artifact: AnalysisArtifact) -> AnalysisArtifact:
    """Sets `deleted_at` -- never a real `DELETE`, matching
    `identity.repositories.history.soft_delete_conversation`'s own
    retention posture for the turn this artifact references. Idempotent:
    deleting an already-deleted artifact keeps the original timestamp, so
    a retried request cannot rewrite when it happened."""
    if artifact.deleted_at is None:
        artifact.deleted_at = datetime.now(UTC)
        session.commit()
        session.refresh(artifact)
    return artifact


def record_refresh_result(
    session: Session,
    artifact: AnalysisArtifact,
    *,
    success: bool,
    row_count: int | None = None,
    fingerprint: str | None = None,
    error: str | None = None,
    refreshed_snapshot: dict[str, Any] | None = None,
) -> AnalysisArtifact:
    """Records one refresh attempt in a single commit.

    `error` must already be redacted by the caller
    (`security.redaction.redact_secrets`) -- the identical contract
    `identity.repositories.bi_integration.record_refresh_result` states;
    this function stores it verbatim and does not re-check.

    `refreshed_snapshot` must already be row-bounded by the caller. It is
    written into this artifact's own `metadata`, **never** back into the
    referenced turn: see `identity.models.AnalysisArtifact`'s docstring.
    A failed refresh clears neither the previous successful snapshot nor
    its fingerprint, so a transient failure does not destroy the result
    the artifact was legitimately standing on.
    """
    artifact.last_refresh_at = datetime.now(UTC)
    artifact.last_refresh_outcome = (
        RefreshOutcome.SUCCESS.value if success else RefreshOutcome.FAILED.value
    )
    artifact.last_refresh_error = error  # type: ignore[assignment]
    if success:
        artifact.last_refresh_row_count = row_count  # type: ignore[assignment]
        artifact.last_refresh_fingerprint = fingerprint  # type: ignore[assignment]
        metadata = dict(artifact.metadata_json or {})
        metadata["refreshed_snapshot"] = refreshed_snapshot
        artifact.metadata_json = metadata
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def get_owned_prompt(
    session: Session, *, prompt_id: uuid.UUID | None, user_id: uuid.UUID
) -> Prompt | None:
    """Ownership-scoped lookup of one question row, the counterpart to
    `identity.repositories.history.get_ai_output` (which that module
    provides for the answer row but not for the question). A forged or
    borrowed `prompt_id` simply does not match."""
    if prompt_id is None:
        return None
    return session.scalar(
        select(Prompt).where(
            Prompt.id == prompt_id,
            Prompt.user_id == user_id,
            Prompt.deleted_at.is_(None),
        )
    )


def load_source_turn(
    session: Session, artifact: AnalysisArtifact
) -> tuple[Prompt | None, AiOutput | None]:
    """The question and answer rows this artifact references, each read
    through the **ownership-scoped** lookup chat history itself uses.

    Returns `(None, None)` the moment the conversation is no longer
    readable by this artifact's owner (soft-deleted, or its tenant no
    longer matches its owner's -- `get_conversation` folds both into its
    own `WHERE` clause), so an artifact can never act as a way to read a
    turn that chat history would refuse. A deleted conversation cascades
    the artifact away anyway; this covers the soft-delete case, which does
    not cascade.
    """
    conversation = get_conversation(
        session, conversation_id=artifact.conversation_id, user_id=artifact.owner_user_id
    )
    if conversation is None:
        return None, None
    prompt = get_owned_prompt(session, prompt_id=artifact.prompt_id, user_id=artifact.owner_user_id)
    ai_output = (
        get_ai_output(session, ai_output_id=artifact.ai_output_id, user_id=artifact.owner_user_id)
        if artifact.ai_output_id is not None
        else None
    )
    return prompt, ai_output


def artifact_to_snapshot(artifact: AnalysisArtifact) -> ArtifactSnapshot:
    """The one place an `AnalysisArtifact` row becomes the ORM-independent
    `ArtifactSnapshot` -- mirroring `identity.repositories.bi_integration
    .connector_to_snapshot`'s own identical role."""
    metadata = artifact.metadata_json or {}
    refreshed = metadata.get("refreshed_snapshot") or {}
    return ArtifactSnapshot(
        id=str(artifact.id),
        tenant_id=artifact.tenant_id,
        owner_user_id=str(artifact.owner_user_id),
        conversation_id=str(artifact.conversation_id),
        prompt_id=str(artifact.prompt_id) if artifact.prompt_id else None,
        ai_output_id=str(artifact.ai_output_id) if artifact.ai_output_id else None,
        artifact_type=ArtifactType(artifact.artifact_type),
        title=artifact.title,
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
        deleted_at=artifact.deleted_at,
        database_id=artifact.database_id,
        source_sql=artifact.source_sql,
        schema_fingerprint=artifact.schema_fingerprint,
        origin=ResultSnapshotRef(
            row_count=artifact.origin_row_count,
            captured_at=artifact.origin_captured_at,
            fingerprint=artifact.origin_fingerprint,
        ),
        lineage=ArtifactLineage.model_validate(metadata.get("lineage") or {}),
        last_refresh_at=artifact.last_refresh_at,
        last_refresh_outcome=RefreshOutcome(artifact.last_refresh_outcome),
        last_refresh_error=artifact.last_refresh_error,
        refreshed=ResultSnapshotRef(
            row_count=artifact.last_refresh_row_count,
            captured_at=_parse_captured_at(refreshed.get("captured_at")),
            fingerprint=artifact.last_refresh_fingerprint,
        ),
    )


def _parse_captured_at(value: Any) -> datetime | None:
    """A refreshed snapshot's own `captured_at` is stored as an ISO string
    inside a JSON column (there is no `DateTime` inside JSONB), so it
    comes back as text. An unparseable value yields `None` rather than
    raising -- which `artifacts.staleness` then reports as "no result
    captured", the honest reading, instead of failing the whole read."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
