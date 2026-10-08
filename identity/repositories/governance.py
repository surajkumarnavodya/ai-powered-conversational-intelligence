"""Repository layer for the AI Governance Center --
`GovernanceAssetVersion`/`GovernanceEvent`, Prompt 44
(`44_AI_GOVERNANCE_CENTER_CONTRACT.md`).

**This module never makes an authorization decision itself** --
`governance_center.policy.authorize_governance_action` is the only place
a caller may or may not do something is decided, mirroring
`identity/repositories/onboarding.py`'s own identical split.

**Status transitions are defended here, not only at the API layer** --
a wrong stage transition here would corrupt an asset's own version/
deployment history, so `_assert_transition_valid` raises
`InvalidGovernanceStageTransitionError` against `governance_center
.models.VALID_STAGE_TRANSITIONS` as a second, defense-in-depth check
behind `api/governance.py`'s own pre-flight validation, and every write
goes through `_apply_transition`'s atomic, conditional `UPDATE ... WHERE
id = ... AND stage = expected_current` -- the exact race-safety fix
`identity/repositories/semantic_catalog.py::_apply_transition`'s own
docstring documents finding and closing for a concurrent-publish race
(Prompt 27), applied here from the start rather than rediscovering it.

**`deploy`/`rollback` are the two functions that touch a *second* row**
(the asset's own previously-deployed version, if any) -- both apply
their two rows' updates inside one atomic unit: `deploy`'s own
conditional `UPDATE` on the new version is what actually commits, and
the previous version's `stage` is mutated on the same ORM object
*before* that commit, so a lost race on the new version's own `UPDATE`
(rowcount 0) rolls the previous version's change back too, via
`_apply_transition`'s existing rollback-on-conflict path -- never a
half-deployed state where two versions are simultaneously "deployed."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from governance_center.models import (
    ROLLBACK_ELIGIBLE_STAGES,
    VALID_STAGE_TRANSITIONS,
    GovernanceAssetVersionSnapshot,
    GovernanceStage,
    GovernedAssetType,
)
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from identity.models import GovernanceAssetVersion, GovernanceEvent


class InvalidGovernanceStageTransitionError(Exception):
    """Raised when a caller attempts a stage transition `governance_center
    .models.VALID_STAGE_TRANSITIONS` doesn't allow from the version's
    current stage, *or* when `_apply_transition`'s own conditional
    `UPDATE` finds the row's real, committed stage no longer matches
    what this caller's in-memory object expected -- see that function's
    own docstring for why both checks exist."""


def _assert_transition_valid(version: GovernanceAssetVersion, target: GovernanceStage) -> None:
    """A fast, friendly first-pass check against the already-loaded
    in-memory `version.stage` -- correct for the overwhelmingly common
    non-concurrent case, but **not** by itself a safe guard against a
    race between two concurrent callers (see `_apply_transition`, which
    is what actually prevents that)."""
    current = GovernanceStage(version.stage)
    if target not in VALID_STAGE_TRANSITIONS.get(current, frozenset()):
        raise InvalidGovernanceStageTransitionError(
            f"Cannot move a {current.value!r} asset version to {target.value!r}."
        )


def _apply_transition(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    expected_current: GovernanceStage,
    values: dict[str, Any],
) -> None:
    """Atomically applies `values` to `version`'s row via one conditional
    `UPDATE ... WHERE id = version.id AND stage = expected_current.value`
    -- see this module's own docstring for why this, not "read, mutate
    attributes, commit," is the only safe pattern here.

    Raises:
        InvalidGovernanceStageTransitionError: if the row's real,
            currently-committed stage no longer equals `expected_current`
            (rowcount 0) -- the race was caught.
    """
    result = session.execute(
        sa_update(GovernanceAssetVersion)
        .where(GovernanceAssetVersion.id == version.id)
        .where(GovernanceAssetVersion.stage == expected_current.value)
        .values(**values)
    )
    if result.rowcount == 0:
        session.rollback()
        raise InvalidGovernanceStageTransitionError(
            f"Asset version {version.id} is no longer {expected_current.value!r} -- another "
            "decision was already recorded."
        )
    session.commit()
    session.refresh(version)


def _log_event(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    event_type: str,
    from_stage: str | None,
    to_stage: str | None,
    actor_user_id: uuid.UUID | None,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> GovernanceEvent:
    event = GovernanceEvent(
        asset_version_id=version.id,
        tenant_id=version.tenant_id,
        event_type=event_type,
        from_stage=from_stage,
        to_stage=to_stage,
        actor_user_id=actor_user_id,
        reason=reason,
        payload=payload,
    )
    session.add(event)
    session.commit()
    return event


def next_version_for_asset(
    session: Session, *, tenant_id: str, asset_type: str, asset_key: str
) -> int:
    """The next version number for `asset_key` within this `(tenant_id,
    asset_type)` scope -- `1` for a brand-new asset."""
    current_max = session.scalar(
        select(GovernanceAssetVersion.version)
        .where(GovernanceAssetVersion.tenant_id == tenant_id)
        .where(GovernanceAssetVersion.asset_type == asset_type)
        .where(GovernanceAssetVersion.asset_key == asset_key)
        .order_by(GovernanceAssetVersion.version.desc())
        .limit(1)
    )
    return (current_max or 0) + 1


def create_draft(
    session: Session,
    *,
    tenant_id: str,
    asset_type: str,
    asset_key: str,
    display_name: str,
    description: str,
    config: dict[str, Any],
    owner: str | None,
    created_by_user_id: uuid.UUID | None,
) -> GovernanceAssetVersion:
    """Creates one new `"draft"` version of `asset_key` -- version `1`
    for a brand-new asset, or the next version if one already exists."""
    version_number = next_version_for_asset(
        session, tenant_id=tenant_id, asset_type=asset_type, asset_key=asset_key
    )
    version = GovernanceAssetVersion(
        tenant_id=tenant_id,
        asset_type=asset_type,
        asset_key=asset_key,
        display_name=display_name,
        description=description,
        owner=owner,
        version=version_number,
        stage="draft",
        config=config,
        created_by_user_id=created_by_user_id,
    )
    session.add(version)
    session.commit()
    _log_event(
        session,
        version,
        event_type="stage_change",
        from_stage=None,
        to_stage="draft",
        actor_user_id=created_by_user_id,
    )
    return version


def get_version_by_id(session: Session, version_id: uuid.UUID) -> GovernanceAssetVersion | None:
    return session.get(GovernanceAssetVersion, version_id)


def list_versions(
    session: Session,
    tenant_id: str,
    *,
    asset_type: str | None = None,
    asset_key: str | None = None,
    stage: str | None = None,
) -> list[GovernanceAssetVersion]:
    stmt = select(GovernanceAssetVersion).where(GovernanceAssetVersion.tenant_id == tenant_id)
    if asset_type is not None:
        stmt = stmt.where(GovernanceAssetVersion.asset_type == asset_type)
    if asset_key is not None:
        stmt = stmt.where(GovernanceAssetVersion.asset_key == asset_key)
    if stage is not None:
        stmt = stmt.where(GovernanceAssetVersion.stage == stage)
    return list(session.scalars(stmt.order_by(GovernanceAssetVersion.created_at.desc())))


def get_current_deployed(
    session: Session, tenant_id: str, asset_type: str, asset_key: str
) -> GovernanceAssetVersion | None:
    """The single version of `(tenant_id, asset_type, asset_key)`
    currently holding `stage == "deployed"`, or `None` if nothing is
    currently deployed for it."""
    return session.scalar(
        select(GovernanceAssetVersion)
        .where(GovernanceAssetVersion.tenant_id == tenant_id)
        .where(GovernanceAssetVersion.asset_type == asset_type)
        .where(GovernanceAssetVersion.asset_key == asset_key)
        .where(GovernanceAssetVersion.stage == "deployed")
    )


def update_draft(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    display_name: str | None = None,
    description: str | None = None,
    config: dict[str, Any] | None = None,
    owner: str | None = None,
) -> GovernanceAssetVersion:
    """Edits a `"draft"` version in place -- the one stage this table
    ever allows a real in-place edit on, mirroring `SemanticCatalogEntry
    .status == "draft"`'s own identical exception."""
    if GovernanceStage(version.stage) != GovernanceStage.DRAFT:
        raise InvalidGovernanceStageTransitionError(
            f"Asset version {version.id} is {version.stage!r}, not 'draft' -- only a draft "
            "version may be edited in place."
        )
    values: dict[str, Any] = {}
    if display_name is not None:
        values["display_name"] = display_name
    if description is not None:
        values["description"] = description
    if config is not None:
        values["config"] = config
    if owner is not None:
        values["owner"] = owner
    if not values:
        session.refresh(version)
        return version
    _apply_transition(session, version, expected_current=GovernanceStage.DRAFT, values=values)
    return version


def submit_for_testing(
    session: Session, version: GovernanceAssetVersion, *, actor_user_id: uuid.UUID | None
) -> GovernanceAssetVersion:
    """`"draft"` -> `"testing"`."""
    _assert_transition_valid(version, GovernanceStage.TESTING)
    _apply_transition(
        session,
        version,
        expected_current=GovernanceStage.DRAFT,
        values={"stage": GovernanceStage.TESTING.value},
    )
    _log_event(
        session,
        version,
        event_type="stage_change",
        from_stage="draft",
        to_stage="testing",
        actor_user_id=actor_user_id,
    )
    return version


def record_test_results(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    test_results: dict[str, Any],
    actor_user_id: uuid.UUID | None,
) -> GovernanceAssetVersion:
    """Records `test_results` on a `"testing"` version -- does not itself
    change `stage` (see `submit_for_evaluation` for that)."""
    if GovernanceStage(version.stage) != GovernanceStage.TESTING:
        raise InvalidGovernanceStageTransitionError(
            f"Asset version {version.id} is {version.stage!r}, not 'testing'."
        )
    _apply_transition(
        session,
        version,
        expected_current=GovernanceStage.TESTING,
        values={
            "test_results": test_results,
            "tested_by_user_id": actor_user_id,
            "tested_at": datetime.now(UTC),
        },
    )
    _log_event(
        session,
        version,
        event_type="note",
        from_stage="testing",
        to_stage="testing",
        actor_user_id=actor_user_id,
        payload=test_results,
    )
    return version


def submit_for_evaluation(
    session: Session, version: GovernanceAssetVersion, *, actor_user_id: uuid.UUID | None
) -> GovernanceAssetVersion:
    """`"testing"` -> `"evaluating"`."""
    _assert_transition_valid(version, GovernanceStage.EVALUATING)
    _apply_transition(
        session,
        version,
        expected_current=GovernanceStage.TESTING,
        values={"stage": GovernanceStage.EVALUATING.value},
    )
    _log_event(
        session,
        version,
        event_type="stage_change",
        from_stage="testing",
        to_stage="evaluating",
        actor_user_id=actor_user_id,
    )
    return version


def record_evaluation_results(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    evaluation_results: dict[str, Any],
    actor_user_id: uuid.UUID | None,
) -> GovernanceAssetVersion:
    """Records `evaluation_results` on an `"evaluating"` version -- does
    not itself change `stage` (see `approve`/`reject` for that)."""
    if GovernanceStage(version.stage) != GovernanceStage.EVALUATING:
        raise InvalidGovernanceStageTransitionError(
            f"Asset version {version.id} is {version.stage!r}, not 'evaluating'."
        )
    _apply_transition(
        session,
        version,
        expected_current=GovernanceStage.EVALUATING,
        values={
            "evaluation_results": evaluation_results,
            "evaluated_by_user_id": actor_user_id,
            "evaluated_at": datetime.now(UTC),
        },
    )
    _log_event(
        session,
        version,
        event_type="note",
        from_stage="evaluating",
        to_stage="evaluating",
        actor_user_id=actor_user_id,
        payload=evaluation_results,
    )
    return version


def approve(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    actor_user_id: uuid.UUID | None,
    notes: str | None = None,
) -> GovernanceAssetVersion:
    """`"evaluating"` -> `"approved"` -- the moment this version's own
    `truth_level` (`governance_center.models.stage_to_truth_level`)
    becomes `CONFIRMED_BUSINESS_TRUTH`."""
    _assert_transition_valid(version, GovernanceStage.APPROVED)
    _apply_transition(
        session,
        version,
        expected_current=GovernanceStage.EVALUATING,
        values={
            "stage": GovernanceStage.APPROVED.value,
            "approved_by_user_id": actor_user_id,
            "approved_at": datetime.now(UTC),
            "approval_notes": notes,
        },
    )
    _log_event(
        session,
        version,
        event_type="stage_change",
        from_stage="evaluating",
        to_stage="approved",
        actor_user_id=actor_user_id,
        reason=notes,
    )
    return version


def reject(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    actor_user_id: uuid.UUID | None,
    reason: str,
) -> GovernanceAssetVersion:
    """`"draft"`/`"testing"`/`"evaluating"`/`"approved"` -> `"rejected"` --
    terminal. A caller wanting another attempt creates a brand-new draft
    version instead (this table is append-only past `draft`, the same
    discipline `SemanticCatalogEntry` already establishes)."""
    current = GovernanceStage(version.stage)
    _assert_transition_valid(version, GovernanceStage.REJECTED)
    _apply_transition(
        session,
        version,
        expected_current=current,
        values={
            "stage": GovernanceStage.REJECTED.value,
            "rejection_reason": reason,
        },
    )
    _log_event(
        session,
        version,
        event_type="stage_change",
        from_stage=current.value,
        to_stage="rejected",
        actor_user_id=actor_user_id,
        reason=reason,
    )
    return version


def deploy(
    session: Session, version: GovernanceAssetVersion, *, actor_user_id: uuid.UUID | None
) -> tuple[GovernanceAssetVersion, GovernanceAssetVersion | None]:
    """`"approved"` -> `"deployed"` -- the mission's own "release" action.

    If a different version of the same `(tenant_id, asset_type,
    asset_key)` already holds `"deployed"`, it is flipped to `"retired"`
    (never deleted -- it remains a legal `rollback` target) in the same
    atomic unit that promotes this one.

    Returns:
        `(deployed_version, retired_version_or_none)`.
    """
    _assert_transition_valid(version, GovernanceStage.DEPLOYED)

    previously_deployed = get_current_deployed(
        session, version.tenant_id, version.asset_type, version.asset_key
    )
    if previously_deployed is not None and previously_deployed.id == version.id:
        previously_deployed = None  # defensive; cannot happen given the transition table

    if previously_deployed is not None:
        # Mutated on the same ORM object *before* the conditional commit
        # below -- a lost race on `version`'s own UPDATE rolls this back
        # too (see this module's own docstring).
        previously_deployed.stage = GovernanceStage.RETIRED.value
        # The pre-existing `Mapped[uuid.UUID]`-declared-but-`nullable=True`
        # convention `identity/models.py` already uses throughout (e.g.
        # `SemanticCatalogEntry.created_by_user_id`) -- mypy sees the
        # column's own declared (non-Optional) type, not its real
        # nullability, for a direct attribute assignment specifically
        # (every other repository in this codebase sets these only via
        # `_apply_transition`'s `.values(**values)` dict, which doesn't
        # trigger this).
        previously_deployed.retired_by_user_id = actor_user_id  # type: ignore[assignment]
        previously_deployed.retired_at = datetime.now(UTC)

    _apply_transition(
        session,
        version,
        expected_current=GovernanceStage.APPROVED,
        values={
            "stage": GovernanceStage.DEPLOYED.value,
            "deployed_by_user_id": actor_user_id,
            "deployed_at": datetime.now(UTC),
        },
    )
    _log_event(
        session,
        version,
        event_type="stage_change",
        from_stage="approved",
        to_stage="deployed",
        actor_user_id=actor_user_id,
        payload=(
            {"retired_version_id": str(previously_deployed.id)}
            if previously_deployed is not None
            else None
        ),
    )
    return version, previously_deployed


class RollbackTargetIneligibleError(Exception):
    """Raised by `rollback` when `target_version` isn't a legal rollback
    target for `current_deployed` -- see that function's own docstring
    for every condition checked."""


def rollback(
    session: Session,
    current_deployed: GovernanceAssetVersion,
    target_version: GovernanceAssetVersion,
    *,
    actor_user_id: uuid.UUID | None,
    reason: str,
) -> tuple[GovernanceAssetVersion, GovernanceAssetVersion]:
    """Rolls back one asset's current deployment to an earlier version.

    Deliberately **not** a plain entry in `VALID_STAGE_TRANSITIONS` --
    `retired`/`rolled_back` -> `deployed` is not a transition any
    *ordinary* forward flow should ever reach for a version on its own
    (a version that was simply superseded has no business becoming
    "deployed" again except through this one, explicitly-audited
    action). This function instead independently validates every
    condition and raises a distinctly-named error so a caller can tell
    "this isn't a legal transition at all" apart from "this specific
    rollback target is ineligible":

      - `target_version` must belong to the exact same `(tenant_id,
        asset_type, asset_key)` as `current_deployed` -- rolling back to
        a version of a *different* asset would silently swap what's
        running.
      - `target_version.stage` must be in `governance_center.models
        .ROLLBACK_ELIGIBLE_STAGES` (`retired`/`rolled_back`) -- a version
        that was never deployed (still `draft`/`testing`/`evaluating`/
        `approved`/`rejected`) has no production history to "roll back"
        to; deploying it for the first time is `deploy`'s own job.
      - `current_deployed.stage` must actually be `"deployed"` -- the
        caller's own, already-loaded row, re-validated here rather than
        trusted blindly.

    Both rows are updated in the same atomic unit as `deploy` above:
    `current_deployed`'s own conditional `UPDATE` (`deployed` ->
    `rolled_back`) is what actually commits; `target_version`'s promotion
    to `deployed` is applied on the same ORM object beforehand, so a
    lost race on `current_deployed` rolls `target_version`'s change back
    too.

    Returns:
        `(rolled_back_version, redeployed_version)`.
    """
    if (
        target_version.tenant_id != current_deployed.tenant_id
        or target_version.asset_type != current_deployed.asset_type
        or target_version.asset_key != current_deployed.asset_key
    ):
        raise RollbackTargetIneligibleError(
            "The rollback target must be a prior version of the exact same asset."
        )
    if GovernanceStage(target_version.stage) not in ROLLBACK_ELIGIBLE_STAGES:
        raise RollbackTargetIneligibleError(
            f"Version {target_version.id} is {target_version.stage!r} -- only a previously "
            "deployed (now retired/rolled-back) version may be a rollback target."
        )
    if GovernanceStage(current_deployed.stage) != GovernanceStage.DEPLOYED:
        raise InvalidGovernanceStageTransitionError(
            f"Asset version {current_deployed.id} is {current_deployed.stage!r}, not "
            "'deployed' -- nothing to roll back."
        )

    # Applied on the same ORM object before the conditional commit below
    # -- see this function's own docstring.
    target_version.stage = GovernanceStage.DEPLOYED.value
    target_version.deployed_by_user_id = actor_user_id  # type: ignore[assignment]
    target_version.deployed_at = datetime.now(UTC)

    _apply_transition(
        session,
        current_deployed,
        expected_current=GovernanceStage.DEPLOYED,
        values={"stage": GovernanceStage.ROLLED_BACK.value},
    )
    _log_event(
        session,
        current_deployed,
        event_type="rollback",
        from_stage="deployed",
        to_stage="rolled_back",
        actor_user_id=actor_user_id,
        reason=reason,
        payload={"redeployed_version_id": str(target_version.id)},
    )
    _log_event(
        session,
        target_version,
        event_type="rollback",
        from_stage=target_version.stage,
        to_stage="deployed",
        actor_user_id=actor_user_id,
        reason=reason,
        payload={"rolled_back_version_id": str(current_deployed.id)},
    )
    return current_deployed, target_version


def retire(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    actor_user_id: uuid.UUID | None,
    reason: str | None = None,
) -> GovernanceAssetVersion:
    """`"approved"`/`"deployed"` -> `"retired"` -- a manual, intentional
    decommission (never used for the automatic supersession `deploy`
    already handles internally)."""
    current = GovernanceStage(version.stage)
    _assert_transition_valid(version, GovernanceStage.RETIRED)
    _apply_transition(
        session,
        version,
        expected_current=current,
        values={
            "stage": GovernanceStage.RETIRED.value,
            "retired_by_user_id": actor_user_id,
            "retired_at": datetime.now(UTC),
        },
    )
    _log_event(
        session,
        version,
        event_type="stage_change",
        from_stage=current.value,
        to_stage="retired",
        actor_user_id=actor_user_id,
        reason=reason,
    )
    return version


def record_usage(
    session: Session,
    version: GovernanceAssetVersion,
    *,
    actor_user_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> GovernanceEvent:
    """Appends one caller/operator-reported usage/cost/latency sample --
    see `identity.models.GovernanceEvent`'s own docstring for why this is
    reported, never auto-instrumented, in this pass."""
    return _log_event(
        session,
        version,
        event_type="usage_recorded",
        from_stage=version.stage,
        to_stage=version.stage,
        actor_user_id=actor_user_id,
        payload=payload,
    )


def list_events(session: Session, asset_version_id: uuid.UUID) -> list[GovernanceEvent]:
    return list(
        session.scalars(
            select(GovernanceEvent)
            .where(GovernanceEvent.asset_version_id == asset_version_id)
            .order_by(GovernanceEvent.created_at.desc())
        )
    )


def usage_summary(session: Session, asset_version_id: uuid.UUID) -> dict[str, Any]:
    """A deterministic aggregation (`DataTruthLevel.DATABASE_FACT` -- a
    plain sum/mean over already-recorded numbers, never an inference)
    over every `"usage_recorded"` event for one asset version.

    Each sample's own `payload` is a free-form dict; only numeric
    `request_count`/`total_cost`/`p50_latency_ms`/`p95_latency_ms` keys,
    when present, are aggregated -- a sample missing one of these simply
    doesn't contribute to that particular total, never raises.
    """
    events = [
        e
        for e in list_events(session, asset_version_id)
        if e.event_type == "usage_recorded" and e.payload
    ]
    total_requests = sum(float(e.payload.get("request_count") or 0) for e in events)
    total_cost = sum(float(e.payload.get("total_cost") or 0) for e in events)
    p95_samples = [
        float(e.payload["p95_latency_ms"])
        for e in events
        if e.payload.get("p95_latency_ms") is not None
    ]
    return {
        "sample_count": len(events),
        "total_requests": total_requests,
        "total_cost": total_cost,
        "mean_p95_latency_ms": (sum(p95_samples) / len(p95_samples)) if p95_samples else None,
    }


def version_to_snapshot(version: GovernanceAssetVersion) -> GovernanceAssetVersionSnapshot:
    """Converts a real ORM row into the ORM-independent
    `GovernanceAssetVersionSnapshot` shape -- see `governance_center
    .models`'s own module docstring for why this module never imports
    `identity.models`/SQLAlchemy directly."""
    return GovernanceAssetVersionSnapshot(
        id=str(version.id),
        tenant_id=version.tenant_id,
        asset_type=GovernedAssetType(version.asset_type),
        asset_key=version.asset_key,
        display_name=version.display_name,
        description=version.description,
        owner=version.owner,
        version=version.version,
        stage=GovernanceStage(version.stage),
        config=version.config or {},
        test_results=version.test_results,
        evaluation_results=version.evaluation_results,
        approval_notes=version.approval_notes,
        rejection_reason=version.rejection_reason,
        supersedes_id=str(version.supersedes_id) if version.supersedes_id else None,
    )


__all__ = [
    "InvalidGovernanceStageTransitionError",
    "RollbackTargetIneligibleError",
    "next_version_for_asset",
    "create_draft",
    "get_version_by_id",
    "list_versions",
    "get_current_deployed",
    "update_draft",
    "submit_for_testing",
    "record_test_results",
    "submit_for_evaluation",
    "record_evaluation_results",
    "approve",
    "reject",
    "deploy",
    "rollback",
    "retire",
    "record_usage",
    "list_events",
    "usage_summary",
    "version_to_snapshot",
]
