"""Repository layer for the governed workflow lifecycle --
`identity.models.Workflow`/`WorkflowEvent`, Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`), mirroring
`identity/repositories/recommendation_governance.py`'s own shape
exactly.

**This module never makes an authorization decision itself** -- every
function here is a plain data operation; `workflow.policy
.authorize_workflow_action` is the only place whether a caller may or
may not do something is decided, mirroring `identity/repositories
/recommendation_governance.py`'s own identical split.

**Status transitions are defended here, not only at the API layer.**
`_assert_transition_valid` checks the legal transition table against
this session's own, possibly stale, loaded status. That alone is not
enough: two reviewers acting on the same workflow at once can both pass
it, and a client retrying a request after a timeout must never
double-apply a transition it already succeeded at. So every status
change is one conditional `UPDATE ... WHERE id = :id AND status =
:expected`, and only the caller whose `UPDATE` actually matched a row
succeeds -- the same race/retry fix `identity/repositories
/recommendation_governance.py::_apply_conditional_status_update`/
`identity/repositories/semantic_catalog.py::_apply_transition` already
establish, written here against this table's own columns.

**At most one *active* (non-terminal) workflow may exist per
recommendation at a time.** `create_workflow` enforces this --
the mission's own "test ... duplicate events" requirement: proposing a
second workflow for a recommendation that already has one in flight is
refused (`DuplicateActiveWorkflowError`), not silently allowed to
coexist. A recommendation may get a fresh workflow after an earlier one
completed, was rejected, or expired.

**Comments, owner/due-date changes, and external-reference changes
never touch `status`.** They append a `WorkflowEvent` with the matching
`event_type`, so the one append-only audit trail records every governed
action -- the mission's own "test ... audit" requirement.

**Nothing in this module executes anything.** Every function here is a
plain database read/write -- no SQL execution, no external HTTP call, no
other engine invocation. See `workflow/models.py`'s own module docstring
for the full "never permit uncontrolled autonomous execution" rationale.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from recommendation.governance import RecommendationStatus
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from identity.models import RecommendationRecord, User, Workflow, WorkflowEvent
from identity.rbac import Permission
from identity.repositories.users import get_user_permissions
from workflow.models import ACTIVE_STATUSES, VALID_STATUS_TRANSITIONS, WorkflowStatus

#: A source recommendation must already be judged worth acting on before
#: it can become a workflow -- the literal "turn recommendations into
#: governed workflows" mission statement, enforced as a real precondition
#: rather than an implied one.
_ELIGIBLE_RECOMMENDATION_STATUSES: frozenset[RecommendationStatus] = frozenset(
    {RecommendationStatus.ACCEPTED, RecommendationStatus.PARTIALLY_USEFUL}
)

#: `title` is truncated to this length when derived from a
#: recommendation's own `claim_text` (which has no length cap itself) --
#: `Workflow.title` is `String(300)`.
_TITLE_MAX_LENGTH = 300


class InvalidWorkflowStatusTransitionError(Exception):
    """Raised when a caller attempts a status transition
    `workflow.models.VALID_STATUS_TRANSITIONS` doesn't allow from the
    workflow's current status (e.g. starting a still-`approved`
    workflow, transitioning out of a terminal status), or when a
    concurrent caller -- or a retried request -- already moved the
    workflow (the conditional `UPDATE` matched no row)."""


class InvalidWorkflowOwnerError(Exception):
    """Raised when an owner assignment names a user who is not an active
    account in the workflow's own tenant, or who cannot act on workflows.
    Callers map this to the same not-found response an unknown user
    would get -- a cross-tenant user id must never be distinguishable
    from a nonexistent one."""


class RecommendationNotEligibleError(Exception):
    """Raised when `create_workflow` is attempted against a
    `RecommendationRecord` that isn't `ACCEPTED`/`PARTIALLY_USEFUL`."""


class DuplicateActiveWorkflowError(Exception):
    """Raised when `create_workflow` is attempted against a
    recommendation that already has a non-terminal workflow -- see this
    module's own docstring for why this is enforced, not merely
    discouraged."""


def _assert_transition_valid(workflow: Workflow, target: WorkflowStatus) -> None:
    current = WorkflowStatus(workflow.status)
    if target not in VALID_STATUS_TRANSITIONS.get(current, frozenset()):
        raise InvalidWorkflowStatusTransitionError(
            f"Cannot move a {current.value!r} workflow to {target.value!r}."
        )


def create_workflow(
    session: Session,
    *,
    recommendation: RecommendationRecord,
    created_by_user_id: uuid.UUID | None,
    title: str | None = None,
    description: str | None = None,
) -> Workflow:
    """Creates a new `Workflow` in `"proposed"` status from an
    already-accepted `recommendation`, and records the initial
    `WorkflowEvent` (`from_status=None`, `to_status="proposed"`) that
    establishes the audit trail's own starting point.

    `title`/`description` default to the recommendation's own
    `claim_text` (truncated to `Workflow.title`'s own column length) and
    `action` fields when not explicitly given -- a caller may override
    either at creation time, but neither is ever edited afterward (see
    `identity.models.Workflow`'s own docstring).

    Raises:
        RecommendationNotEligibleError: `recommendation.status` is not
            `"accepted"`/`"partially_useful"`.
        DuplicateActiveWorkflowError: `recommendation` already has a
            non-terminal workflow.
    """
    if RecommendationStatus(recommendation.status) not in _ELIGIBLE_RECOMMENDATION_STATUSES:
        raise RecommendationNotEligibleError(
            f"Recommendation {recommendation.id} is {recommendation.status!r}, not "
            "'accepted'/'partially_useful' -- only an already-accepted recommendation "
            "may become a workflow."
        )

    existing_active = session.execute(
        select(Workflow.id)
        .where(Workflow.recommendation_id == recommendation.id)
        .where(Workflow.status.in_([s.value for s in ACTIVE_STATUSES]))
        .limit(1)
    ).first()
    if existing_active is not None:
        raise DuplicateActiveWorkflowError(
            f"Recommendation {recommendation.id} already has an active workflow "
            f"({existing_active[0]}) -- complete, reject, or expire it first."
        )

    resolved_title = (title or recommendation.claim_text or "Untitled workflow")[:_TITLE_MAX_LENGTH]
    resolved_description = description if description is not None else recommendation.action

    workflow = Workflow(
        tenant_id=recommendation.tenant_id,
        recommendation_id=recommendation.id,
        title=resolved_title,
        description=resolved_description,
        status=WorkflowStatus.PROPOSED.value,
        created_by_user_id=created_by_user_id,
    )
    session.add(workflow)
    session.flush()

    event = WorkflowEvent(
        workflow_id=workflow.id,
        from_status=None,
        to_status=WorkflowStatus.PROPOSED.value,
        event_type="status_change",
        actor_user_id=created_by_user_id,
        actor_label=None if created_by_user_id is not None else "system:unspecified",
        reason="Created from an accepted recommendation.",
    )
    session.add(event)
    session.commit()
    return workflow


def get_workflow_by_id(session: Session, workflow_id: uuid.UUID) -> Workflow | None:
    return session.get(Workflow, workflow_id)


def list_workflows(
    session: Session,
    tenant_id: str,
    *,
    status: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    unassigned: bool = False,
    recommendation_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[Workflow]:
    stmt = select(Workflow).where(Workflow.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(Workflow.status == status)
    if recommendation_id is not None:
        stmt = stmt.where(Workflow.recommendation_id == recommendation_id)
    if owner_user_id is not None:
        stmt = stmt.where(Workflow.owner_user_id == owner_user_id)
    if unassigned:
        stmt = stmt.where(Workflow.owner_user_id.is_(None))
    stmt = stmt.order_by(Workflow.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def list_workflow_events(session: Session, workflow_id: uuid.UUID) -> list[WorkflowEvent]:
    """Every lifecycle/comment/metadata event for one workflow, oldest
    first -- the full, append-only audit trail `GET /workflows/{id}/events`
    reports."""
    stmt = (
        select(WorkflowEvent)
        .where(WorkflowEvent.workflow_id == workflow_id)
        .order_by(WorkflowEvent.created_at.asc())
    )
    return list(session.scalars(stmt))


def owner_display_names(session: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Display names for a batch of owner ids in one query -- identical
    shape to `identity.repositories.recommendation_governance
    .owner_display_names` (a plain `User` lookup unrelated to any one
    governed-lifecycle table, duplicated here rather than imported
    cross-module since neither module depends on the other today; both
    are one-line wrappers over the same `User` table)."""
    if not user_ids:
        return {}
    rows = session.execute(select(User.id, User.display_name).where(User.id.in_(user_ids))).all()
    return {user_id: name for user_id, name in rows if name}


def _apply_conditional_status_update(
    session: Session,
    workflow: Workflow,
    *,
    expected_status: str,
    new_status: str,
    extra_values: dict[str, Any] | None = None,
) -> None:
    """Atomically moves `workflow` from `expected_status` to
    `new_status`, optionally setting further columns (e.g. outcome
    fields on `complete`) in the same `UPDATE`.

    Raises:
        InvalidWorkflowStatusTransitionError: if the row's committed
            status is no longer `expected_status` (rowcount 0) -- another
            caller's transition already landed, or this is a retried
            request that already succeeded once. The session is rolled
            back so nothing from this attempt is written.
    """
    values: dict[str, Any] = {"status": new_status, "updated_at": datetime.now(UTC)}
    if extra_values:
        values.update(extra_values)
    result = session.execute(
        sa_update(Workflow)
        .where(Workflow.id == workflow.id)
        .where(Workflow.status == expected_status)
        .values(**values)
    )
    if result.rowcount == 0:
        session.rollback()
        raise InvalidWorkflowStatusTransitionError(
            f"Workflow {workflow.id} is no longer {expected_status!r} -- another "
            "decision was already recorded, or this request was already applied."
        )


def _transition(
    session: Session,
    workflow: Workflow,
    *,
    target_status: WorkflowStatus,
    actor_user_id: uuid.UUID | None,
    actor_label: str | None = None,
    reason: str | None = None,
    extra_values: dict[str, Any] | None = None,
) -> Workflow:
    """The shared implementation behind every named transition function
    below -- one event per call, never a batch, so each is independently
    attributable to one actor/reason/timestamp."""
    _assert_transition_valid(workflow, target_status)
    from_status = workflow.status
    _apply_conditional_status_update(
        session,
        workflow,
        expected_status=from_status,
        new_status=target_status.value,
        extra_values=extra_values,
    )
    event = WorkflowEvent(
        workflow_id=workflow.id,
        from_status=from_status,
        to_status=target_status.value,
        event_type="status_change",
        actor_user_id=actor_user_id,
        actor_label=actor_label if actor_user_id is None else None,
        reason=reason,
    )
    session.add(event)
    session.commit()
    session.refresh(workflow)
    return workflow


def submit_for_approval(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    reason: str | None = None,
) -> Workflow:
    """`proposed` -> `approval_required` -- the mission's own "require
    approval for consequential actions" gate: nothing may be assigned or
    started until a reviewer has explicitly decided on it."""
    return _transition(
        session,
        workflow,
        target_status=WorkflowStatus.APPROVAL_REQUIRED,
        actor_user_id=actor_user_id,
        reason=reason,
    )


def approve_workflow(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    reason: str | None = None,
) -> Workflow:
    """`approval_required` -> `approved`."""
    return _transition(
        session,
        workflow,
        target_status=WorkflowStatus.APPROVED,
        actor_user_id=actor_user_id,
        reason=reason,
    )


def reject_workflow(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    reason: str | None = None,
) -> Workflow:
    """`approval_required` -> `rejected` -- terminal."""
    return _transition(
        session,
        workflow,
        target_status=WorkflowStatus.REJECTED,
        actor_user_id=actor_user_id,
        reason=reason,
    )


def start_workflow(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    reason: str | None = None,
) -> Workflow:
    """`assigned` -> `in_progress`."""
    return _transition(
        session,
        workflow,
        target_status=WorkflowStatus.IN_PROGRESS,
        actor_user_id=actor_user_id,
        reason=reason,
    )


def complete_workflow(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    outcome_summary: str,
    outcome_measured_impact: dict[str, Any] | None = None,
    reason: str | None = None,
) -> Workflow:
    """`in_progress` -> `completed`, recording the mission's own
    "outcome measurement" requirement in the same atomic update as the
    status change -- a human's own report that the work was done and
    what it achieved, never a value this codebase independently verifies
    (see `identity.models.Workflow`'s own docstring)."""
    return _transition(
        session,
        workflow,
        target_status=WorkflowStatus.COMPLETED,
        actor_user_id=actor_user_id,
        reason=reason,
        extra_values={
            "outcome_summary": outcome_summary,
            "outcome_measured_impact": outcome_measured_impact,
        },
    )


def expire_workflow(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    reason: str | None = None,
) -> Workflow:
    """Any non-terminal status -> `expired`. Unlike every other
    transition here, `actor_user_id` may legitimately be `None` with a
    real `actor_label` -- mirrors `identity.repositories
    .recommendation_governance.expire_record`'s own forward-compatible
    shape, though no automated caller exists anywhere in this codebase
    today (see `workflow/models.py`'s own module docstring)."""
    return _transition(
        session,
        workflow,
        target_status=WorkflowStatus.EXPIRED,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        reason=reason,
    )


def add_comment(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    comment: str,
) -> WorkflowEvent:
    """Appends a free-text comment to `workflow`'s audit trail. Never
    changes `status`."""
    session.refresh(workflow)
    event = WorkflowEvent(
        workflow_id=workflow.id,
        from_status=workflow.status,
        to_status=workflow.status,
        event_type="comment",
        actor_user_id=actor_user_id,
        actor_label=None,
        reason=comment,
    )
    session.add(event)
    session.commit()
    return event


def assign_workflow(
    session: Session,
    workflow: Workflow,
    *,
    owner_user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    due_date: datetime | None = None,
) -> Workflow:
    """Sets the owner (and, optionally, the due date) of `workflow`.

    From `"approved"`, this is the `approved -> assigned` transition
    itself (the mission's own chain). From `"assigned"`/`"in_progress"`,
    this is a **reassignment** -- the owner/due date change without any
    status transition, so a workflow already being worked on can move to
    a different owner without losing its progress.

    The owner must be an **active** user whose `tenant_id` equals the
    workflow's own, and who holds a workflow permission (so the person
    named is actually able to act on the item) -- all checked here, not
    only in the API layer, mirroring `identity.repositories
    .recommendation_governance.assign_owner`'s own identical discipline.

    Raises:
        InvalidWorkflowOwnerError: the named user doesn't exist, isn't
            active, belongs to another tenant, or can't act on workflows.
        InvalidWorkflowStatusTransitionError: `workflow.status` is
            neither `"approved"` (first assignment) nor `"assigned"`/
            `"in_progress"` (reassignment).
    """
    owner = session.get(User, owner_user_id)
    if owner is None or owner.status != "active" or owner.tenant_id != workflow.tenant_id:
        raise InvalidWorkflowOwnerError("Owner is not an active user in this tenant.")
    owner_permissions = get_user_permissions(session, owner.id)
    if not (
        Permission.WORKFLOW_MANAGE in owner_permissions
        or Permission.WORKFLOW_REVIEW in owner_permissions
    ):
        raise InvalidWorkflowOwnerError("Owner cannot act on workflows.")

    current = WorkflowStatus(workflow.status)
    previous_owner = workflow.owner_user_id
    extra_values: dict[str, Any] = {"owner_user_id": owner_user_id}
    if due_date is not None:
        extra_values["due_date"] = due_date

    if current == WorkflowStatus.APPROVED:
        _apply_conditional_status_update(
            session,
            workflow,
            expected_status=current.value,
            new_status=WorkflowStatus.ASSIGNED.value,
            extra_values=extra_values,
        )
        from_status = current.value
        to_status = WorkflowStatus.ASSIGNED.value
    elif current in (WorkflowStatus.ASSIGNED, WorkflowStatus.IN_PROGRESS):
        # A reassignment -- status doesn't change, so there is no
        # expected-status race to defend against via a conditional
        # status transition; the owner/due_date columns are simply
        # overwritten. Two concurrent reassignments still can't corrupt
        # anything -- the last `UPDATE` to commit wins, and the event
        # it appends truthfully records what it set (a disclosed,
        # last-writer-wins limitation, matching Prompt 31's own
        # recommendation-owner-assignment precedent).
        session.execute(
            sa_update(Workflow)
            .where(Workflow.id == workflow.id)
            .values(updated_at=datetime.now(UTC), **extra_values)
        )
        from_status = current.value
        to_status = current.value
    else:
        raise InvalidWorkflowStatusTransitionError(
            f"Cannot assign an owner to a {current.value!r} workflow."
        )

    event = WorkflowEvent(
        workflow_id=workflow.id,
        from_status=from_status,
        to_status=to_status,
        event_type="owner_assigned",
        actor_user_id=actor_user_id,
        actor_label=None,
        reason=None,
        detail={
            "owner_user_id": str(owner_user_id),
            "previous_owner_user_id": str(previous_owner) if previous_owner else None,
            "due_date": due_date.isoformat() if due_date else None,
        },
    )
    session.add(event)
    session.commit()
    session.refresh(workflow)
    return workflow


def set_due_date(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    due_date: datetime | None,
) -> Workflow:
    """Sets (or, with `due_date=None`, clears) `workflow`'s due date
    without touching owner or status -- independent of `assign_workflow`
    so a reviewer can push back a deadline without reassigning the work."""
    session.refresh(workflow)
    previous_due_date = workflow.due_date
    session.execute(
        sa_update(Workflow)
        .where(Workflow.id == workflow.id)
        .values(due_date=due_date, updated_at=datetime.now(UTC))
    )
    event = WorkflowEvent(
        workflow_id=workflow.id,
        from_status=workflow.status,
        to_status=workflow.status,
        event_type="due_date_set",
        actor_user_id=actor_user_id,
        actor_label=None,
        reason=None,
        detail={
            "due_date": due_date.isoformat() if due_date else None,
            "previous_due_date": previous_due_date.isoformat() if previous_due_date else None,
        },
    )
    session.add(event)
    session.commit()
    session.refresh(workflow)
    return workflow


def set_external_reference(
    session: Session,
    workflow: Workflow,
    *,
    actor_user_id: uuid.UUID,
    external_system: str | None,
    external_reference_id: str | None,
    external_reference_url: str | None,
) -> Workflow:
    """Sets (or clears, with every field `None`) `workflow`'s external
    task reference -- the mission's own "external task references"
    requirement. Plain metadata only; never calls an external system
    (see `workflow/models.py`'s own module docstring)."""
    session.refresh(workflow)
    previous = {
        "external_system": workflow.external_system,
        "external_reference_id": workflow.external_reference_id,
        "external_reference_url": workflow.external_reference_url,
    }
    session.execute(
        sa_update(Workflow)
        .where(Workflow.id == workflow.id)
        .values(
            external_system=external_system,
            external_reference_id=external_reference_id,
            external_reference_url=external_reference_url,
            updated_at=datetime.now(UTC),
        )
    )
    event = WorkflowEvent(
        workflow_id=workflow.id,
        from_status=workflow.status,
        to_status=workflow.status,
        event_type="external_reference_set",
        actor_user_id=actor_user_id,
        actor_label=None,
        reason=None,
        detail={
            "external_system": external_system,
            "external_reference_id": external_reference_id,
            "external_reference_url": external_reference_url,
            "previous": previous,
        },
    )
    session.add(event)
    session.commit()
    session.refresh(workflow)
    return workflow
