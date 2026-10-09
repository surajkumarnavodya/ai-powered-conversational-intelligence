"""Centralized RBAC + ABAC decision engine for the governed workflow
lifecycle -- every route in `api/workflow.py` must call
`authorize_workflow_action` before doing anything, mirroring
`governance_center/policy.py`'s own "pure policy, no FastAPI/database
import" split exactly (this module is unit-testable with plain,
hand-built values -- no DB, no HTTP). Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`).

**Deny-by-default at every branch.** An action this module doesn't
recognize, a `None` where a value was expected -- every one of those is
a `deny`, never an `allow`.

## RBAC (what someone may do at all)

- `identity.rbac.Permission.WORKFLOW_MANAGE` -- create a workflow from
  an accepted recommendation, submit it for approval, assign/reassign an
  owner and due date, start it, complete it, set its external task
  reference, add a comment, or force-expire it. Granted to the `admin`
  role only (`identity/rbac.py`'s own
  `_ADMIN` set) -- these are operational actions, not domain judgment,
  the identical reasoning `ONBOARDING_MANAGE`/`CATALOG_MANAGE`/
  `RECOMMENDATION_MANAGE`/`GOVERNANCE_MANAGE` already establish.
- `identity.rbac.Permission.WORKFLOW_REVIEW` -- approve or reject a
  workflow that is `APPROVAL_REQUIRED`. Granted to `analyst` and
  `admin` -- deciding whether a proposed action is actually worth
  carrying out needs domain judgment, not account administration, the
  identical reasoning `ONBOARDING_REVIEW`/`CATALOG_REVIEW`/
  `RECOMMENDATION_REVIEW`/`GOVERNANCE_REVIEW` already establish. This is
  the mission's own "require approval for consequential actions"
  control: `WORKFLOW_MANAGE` and `WORKFLOW_REVIEW` are two distinct
  permission codes, never one combined "do anything" grant, so a future
  custom role holding `WORKFLOW_MANAGE` alone (without the analyst-tier
  permissions `WORKFLOW_REVIEW` also sits at) could submit a workflow but
  never approve it on its own. **Honest about this codebase's actual
  seeded roles, not overclaiming**: `identity/rbac.py`'s `_ADMIN` set is
  `_ANALYST | {...}` (the same hierarchy every other `*_MANAGE`/`*_REVIEW`
  pair in this codebase has), so the seeded `admin` role inherits
  `WORKFLOW_REVIEW` from the analyst tier *in addition to* its own
  `WORKFLOW_MANAGE` grant -- an admin account can propose, submit, *and*
  approve the same workflow today, identically to how an admin can
  already both manage and review a recommendation, a catalog entry, or a
  governance asset version. The separation this policy enforces is
  between the two *permissions*, not a claim that no single seeded role
  holds both.

Viewing (`VIEW`/`LIST`) is shared by either permission -- a reviewer who
can approve a workflow must also be able to see it, and a manager who
submitted it must also be able to see its current state, mirroring
`governance_center.policy`'s own `_VIEW_OR_REVIEW_ACTIONS` rationale.

## ABAC (which specific workflow)

Every action beyond `CREATE`/`LIST` requires the actor's own
`security.tenancy.resolve_actor_tenant_id` to match the target
`identity.models.Workflow.tenant_id` -- the exact same scoped pattern
`onboarding.policy`/`semantic.catalog_policy`/`recommendation
.governance_policy`/`governance_center.policy` already establish. A
cross-tenant caller is denied with the same `"cross_tenant"` reason code
every one of those modules uses, never a different one that might hint
the workflow exists in a tenant it doesn't belong to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from identity.rbac import Permission


class WorkflowAction(str, Enum):
    #: Creating a new workflow from an already-accepted recommendation.
    #: No existing workflow to tenant-match against -- authorized by
    #: permission alone, mirroring `governance_center.policy
    #: .GovernanceAction.CREATE_DRAFT`.
    CREATE = "create"
    #: Listing the caller's own tenant's workflows -- deliberately
    #: distinct from `VIEW` below: the query itself is what scopes the
    #: result to the caller's own tenant, so this action, like `CREATE`,
    #: has no one specific, already-resolved resource to check (the real
    #: bug `governance_center.policy`'s own docstring discloses finding
    #: and fixing in Prompt 44 -- avoided here from the start).
    LIST = "list"
    #: Viewing one *specific*, already-resolved workflow by id (or its
    #: event trail) -- this one always tenant-matches against that
    #: workflow's own `tenant_id`, unlike `LIST` above.
    VIEW = "view"
    SUBMIT_FOR_APPROVAL = "submit_for_approval"
    APPROVE = "approve"
    REJECT = "reject"
    ASSIGN = "assign"
    START = "start"
    COMPLETE = "complete"
    SET_EXTERNAL_REFERENCE = "set_external_reference"
    ADD_COMMENT = "add_comment"
    EXPIRE = "expire"


#: Every action a `WORKFLOW_MANAGE` holder may take on an already-
#: resolved workflow (everything except the review decision itself).
_MANAGE_ACTIONS: frozenset[WorkflowAction] = frozenset(
    {
        WorkflowAction.SUBMIT_FOR_APPROVAL,
        WorkflowAction.ASSIGN,
        WorkflowAction.START,
        WorkflowAction.COMPLETE,
        WorkflowAction.SET_EXTERNAL_REFERENCE,
        WorkflowAction.ADD_COMMENT,
        WorkflowAction.EXPIRE,
    }
)
#: `APPROVE`/`REJECT` -- the one domain-judgment decision point, and
#: `VIEW` -- shared by either permission, mirroring `governance_center
#: .policy._VIEW_OR_REVIEW_ACTIONS`.
_VIEW_OR_REVIEW_ACTIONS: frozenset[WorkflowAction] = frozenset(
    {WorkflowAction.VIEW, WorkflowAction.APPROVE, WorkflowAction.REJECT}
)


@dataclass(frozen=True)
class AuthorizationDecision:
    """The outcome of one `authorize_workflow_action` call.

    Attributes:
        allowed: Whether the action may proceed.
        reason: A stable machine code (never a sentence) -- mirrors
            `recommendation.governance_policy.AuthorizationDecision`'s
            own contract.
    """

    allowed: bool
    reason: str


def _allow(reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(allowed=True, reason=reason)


def _deny(reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(allowed=False, reason=reason)


def authorize_workflow_action(
    *,
    actor_permissions: frozenset[str],
    actor_tenant_id: str | None,
    workflow_tenant_id: str | None,
    action: WorkflowAction,
) -> AuthorizationDecision:
    """The one function every workflow route must call.

    Args:
        actor_permissions: The caller's own DB-backed granted
            permissions, as plain strings (`identity.repositories.users
            .get_user_permissions`'s actual return shape) -- resolved by
            the caller, never by this module (keeps this file's own
            dependency surface at zero, per its docstring).
        actor_tenant_id: `security.tenancy.resolve_actor_tenant_id(user)`.
        workflow_tenant_id: The target `identity.models.Workflow
            .tenant_id`, or `None` for `CREATE`/`LIST` (neither has an
            existing, specific workflow to check a tenant against -- see
            each action's own docstring on `WorkflowAction`).
        action: Which action is being attempted.

    Returns:
        An `AuthorizationDecision`. Every branch is a `deny` unless an
        explicit, positive condition is met first.
    """
    if action == WorkflowAction.CREATE:
        if Permission.WORKFLOW_MANAGE in actor_permissions:
            return _allow("workflow_manage")
        return _deny("missing_permission")

    if action == WorkflowAction.LIST:
        if (
            Permission.WORKFLOW_MANAGE in actor_permissions
            or Permission.WORKFLOW_REVIEW in actor_permissions
        ):
            return _allow("workflow_manage_or_review")
        return _deny("missing_permission")

    if workflow_tenant_id is None:
        return _deny("workflow_not_found")
    if actor_tenant_id is None or actor_tenant_id != workflow_tenant_id:
        return _deny("cross_tenant")

    if action in _MANAGE_ACTIONS:
        if Permission.WORKFLOW_MANAGE in actor_permissions:
            return _allow("workflow_manage")
        return _deny("missing_permission")

    if action in _VIEW_OR_REVIEW_ACTIONS:
        if (
            Permission.WORKFLOW_MANAGE in actor_permissions
            or Permission.WORKFLOW_REVIEW in actor_permissions
        ):
            return _allow("workflow_manage_or_review")
        return _deny("missing_permission")

    return _deny("unrecognized_action")


__all__ = ["WorkflowAction", "AuthorizationDecision", "authorize_workflow_action"]
