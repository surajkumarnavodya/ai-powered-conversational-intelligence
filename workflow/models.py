"""The governed workflow lifecycle's typed vocabulary -- Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`).

**Deliberately ORM-independent and pure**, mirroring `recommendation
.governance`'s own "no identity/SQLAlchemy import" split exactly, for
the identical reason: this vocabulary (and the transition table below)
needs to be unit-testable with plain enum values, with no database in
the loop, and reusable by both the repository layer
(`identity/repositories/workflow.py`, defense in depth) and the API
layer (`api/workflow.py`, the user-facing 409 before a repository call
is even attempted) -- the exact two-layer enforcement
`recommendation.governance.VALID_STATUS_TRANSITIONS`/`semantic.catalog
.VALID_STATUS_TRANSITIONS` already establish.

## Never permit uncontrolled autonomous execution

This is the mission's own named mandatory control, and it is structural
here, not a runtime check that could be forgotten:

- A workflow's `status` only ever changes because a human caller hit one
  of `api/workflow.py`'s routes -- there is no scheduled sweep, no
  LangGraph node, and no multi-agent specialist anywhere in this
  codebase that creates, transitions, or completes a `Workflow` row.
- `COMPLETED` records a human's own report (`outcome_summary`/
  `outcome_measured_impact`) that the real-world work was done -- it
  never triggers, verifies, or re-runs anything. The "action" a workflow
  describes (copied from the source recommendation's own `action` field
  at creation time) is **display text a human reads and acts on
  themselves**, never a string this package parses or executes --
  mirrors `optimization.models`/`analytics.scenario.models`'s own
  "no field a caller or an LLM could turn into an executed expression"
  structural guarantee, applied here to free-text instructions instead
  of a formula.
- `external_system`/`external_reference_id`/`external_reference_url`
  (the mission's own "external task references") are plain,
  caller-supplied metadata -- a label and a link a human can click to
  open a ticket in whatever system they already use. Nothing in this
  codebase calls an external ticketing API to create, update, or close
  anything there; see `49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`'s
  own inspection notes for why (`bi_integration/push.py` is the one real
  outbound-push precedent in this codebase, and it targets a BI
  dataset/webhook, not a task-tracking system -- there is no existing
  connector this prompt could reuse without inventing a new,
  unauthenticated, unverified integration).
- `APPROVAL_REQUIRED -> APPROVED` is a **separate, explicitly
  permissioned action** (`Permission.WORKFLOW_REVIEW`) from
  `PROPOSED -> APPROVAL_REQUIRED` (`Permission.WORKFLOW_MANAGE`) -- the
  literal "require approval for consequential actions" control: whoever
  proposes a workflow can never also be the one whose click alone makes
  it `APPROVED` unless they separately hold the review permission too
  (see `workflow/policy.py`).

See `tests/security/test_workflow_never_autonomously_executes.py` for
the regression test proving this by parsing this package's own source
for a forbidden import (`agent.graph`, `agent.nodes`, `db.execution`,
`db.connection`, `bi_integration.push`, `agent.multiagent`) or a call
to a known execution entry point.
"""

from __future__ import annotations

from enum import Enum


class WorkflowStatus(str, Enum):
    """One persisted `identity.models.Workflow`'s own lifecycle state --
    the exact chain the mission specifies: proposed -> approval_required
    -> approved -> assigned -> in_progress -> completed/rejected/expired.

    Attributes:
        PROPOSED: The initial state every workflow starts in, created
            from an already-`ACCEPTED`/`PARTIALLY_USEFUL`
            `recommendation.governance.RecommendationStatus` record (see
            `identity/repositories/workflow.py::create_workflow`). Not
            yet submitted for a decision -- the creator may still add a
            comment before doing so (the proposal's own title/
            description are fixed at creation, see
            `identity.models.Workflow`'s own docstring).
        APPROVAL_REQUIRED: Explicitly submitted for a decision -- the
            mission's own "require approval for consequential actions"
            gate. Only reachable from `PROPOSED`, by a caller holding
            `Permission.WORKFLOW_MANAGE`.
        APPROVED: A reviewer (`Permission.WORKFLOW_REVIEW`) judged this
            workflow worth carrying out. Not yet assigned to anyone.
        REJECTED: A reviewer judged this workflow not worth carrying out
            -- a legitimate, non-error disagreement, decided at the same
            point `APPROVED` is -- terminal. Only reachable from
            `APPROVAL_REQUIRED` (the one state where a decision is
            actually pending); there is no "reject" action from
            `PROPOSED` itself, mirroring `recommendation.governance
            .RecommendationStatus`'s own "a recommendation can't be
            resolved if it was never judged accepted in the first
            place" shape, applied here to rejection instead.
        ASSIGNED: An owner and (optionally) a due date have been set --
            the mission's own "support owners, due dates" requirement.
            Only reachable from `APPROVED`.
        IN_PROGRESS: The assigned owner has started the work. Only
            reachable from `ASSIGNED`.
        COMPLETED: The work is reported done, with an outcome summary --
            the mission's own "outcome measurement" requirement. Only
            reachable from `IN_PROGRESS` -- terminal.
        EXPIRED: No longer relevant -- stale, superseded, or
            administratively retired. Reachable from any non-terminal
            state (see `VALID_STATUS_TRANSITIONS`), the identical
            "expiry is always available" shape `recommendation
            .governance.RecommendationStatus.EXPIRED` already
            establishes -- terminal.
    """

    PROPOSED = "proposed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"


#: Transitions `identity/repositories/workflow.py` enforces -- the single
#: source of truth for "which status can move to which," checked by both
#: the repository layer (defense in depth, via a race-safe conditional
#: `UPDATE`) and `api/workflow.py` (the user-facing 409 before a
#: repository call is even attempted). Mirrors `recommendation.governance
#: .VALID_STATUS_TRANSITIONS`'s own dict shape exactly.
VALID_STATUS_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.PROPOSED: frozenset({WorkflowStatus.APPROVAL_REQUIRED, WorkflowStatus.EXPIRED}),
    WorkflowStatus.APPROVAL_REQUIRED: frozenset(
        {WorkflowStatus.APPROVED, WorkflowStatus.REJECTED, WorkflowStatus.EXPIRED}
    ),
    WorkflowStatus.APPROVED: frozenset({WorkflowStatus.ASSIGNED, WorkflowStatus.EXPIRED}),
    WorkflowStatus.ASSIGNED: frozenset({WorkflowStatus.IN_PROGRESS, WorkflowStatus.EXPIRED}),
    WorkflowStatus.IN_PROGRESS: frozenset({WorkflowStatus.COMPLETED, WorkflowStatus.EXPIRED}),
    WorkflowStatus.COMPLETED: frozenset(),
    WorkflowStatus.REJECTED: frozenset(),
    WorkflowStatus.EXPIRED: frozenset(),
}

#: Every status with no outgoing transition at all -- a workflow in one
#: of these states is permanently done, for whichever reason its own
#: value names.
TERMINAL_STATUSES: frozenset[WorkflowStatus] = frozenset(
    status for status, targets in VALID_STATUS_TRANSITIONS.items() if not targets
)

#: Statuses a workflow may legitimately sit in while still "active" --
#: i.e. not yet terminal. Used by `identity/repositories/workflow.py
#: ::create_workflow` to refuse creating a second workflow for a
#: recommendation that already has one of these -- the mission's own
#: "test ... duplicate events" requirement, enforced as "at most one
#: active workflow per recommendation at a time," not "at most one
#: workflow ever" (a recommendation may get a fresh workflow after an
#: earlier one completed, was rejected, or expired).
ACTIVE_STATUSES: frozenset[WorkflowStatus] = frozenset(
    status for status in WorkflowStatus if status not in TERMINAL_STATUSES
)


class WorkflowEventType(str, Enum):
    """What kind of append-only event one `identity.models.WorkflowEvent`
    row records -- mirrors `identity.models.RecommendationFeedbackEvent
    .event_type`'s own Prompt-31 vocabulary (`status_change`/`note`/
    `owner_assigned`), extended with the two further mutable-but-not-
    status-changing facts this mission names: a due date and an external
    task reference.
    """

    STATUS_CHANGE = "status_change"
    COMMENT = "comment"
    OWNER_ASSIGNED = "owner_assigned"
    DUE_DATE_SET = "due_date_set"
    EXTERNAL_REFERENCE_SET = "external_reference_set"


def is_terminal_status(status: WorkflowStatus) -> bool:
    """True if `status` has no legal outgoing transition at all."""
    return status in TERMINAL_STATUSES


def is_active_status(status: WorkflowStatus) -> bool:
    """True if `status` is not yet terminal -- see `ACTIVE_STATUSES`."""
    return status in ACTIVE_STATUSES
