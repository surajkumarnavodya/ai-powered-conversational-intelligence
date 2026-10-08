"""Deny-by-default RBAC+ABAC for the AI Evaluation Benchmarking platform
-- Prompt 46. Mirrors `semantic.catalog_policy`/`onboarding.policy`'s own
identical shape: a pure function, no I/O, taking already-loaded
permissions/tenant ids and returning one decision -- the real
enforcement point is `api/eval_platform.py`'s own call site, not this
module acting alone.

`EVAL_REVIEW` (submitting a human review verdict on a non-deterministic
case, or reading another tenant's runs for audit purposes) sits at the
analyst tier, the identical domain-judgment reasoning
`ONBOARDING_REVIEW`/`CATALOG_REVIEW`/`RECOMMENDATION_REVIEW`/
`GOVERNANCE_REVIEW` already establish. `EVAL_MANAGE` (recording a new
run, computing a release-gate verdict) sits at the admin tier, the
identical operational-action reasoning `ONBOARDING_MANAGE`/
`CATALOG_MANAGE`/`GOVERNANCE_MANAGE` already establish.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from identity.rbac import Permission


class EvalAction(str, Enum):
    """One governable action against this platform's own data.

    `LIST`/`CREATE_RUN` have no single resource to tenant-match against
    yet (the identical reasoning `governance_center.policy
    .GovernanceAction.LIST`'s own docstring already names, and the exact
    bug that action was added to fix -- see `44_AI_GOVERNANCE_CENTER
    _CONTRACT.md`'s "two real bugs" section) -- authorized by permission
    alone, never compared against a resource tenant id.
    """

    LIST = "list"
    CREATE_RUN = "create_run"
    GET_RUN = "get_run"
    COMPUTE_RELEASE_GATE = "compute_release_gate"
    SUBMIT_CASE_REVIEW = "submit_case_review"
    LIST_CASE_REVIEWS = "list_case_reviews"


#: Actions requiring EVAL_MANAGE (operational, not domain judgment).
_MANAGE_ACTIONS: frozenset[EvalAction] = frozenset(
    {EvalAction.CREATE_RUN, EvalAction.COMPUTE_RELEASE_GATE}
)
#: Actions requiring EVAL_REVIEW (domain judgment: human review of a
#: non-deterministic case's outcome).
_REVIEW_ACTIONS: frozenset[EvalAction] = frozenset(
    {EvalAction.SUBMIT_CASE_REVIEW, EvalAction.LIST_CASE_REVIEWS}
)
#: Read-only actions either permission alone satisfies.
_EITHER_PERMISSION_ACTIONS: frozenset[EvalAction] = frozenset({EvalAction.LIST, EvalAction.GET_RUN})


@dataclass(frozen=True)
class EvalAuthzDecision:
    allowed: bool
    reason: str


def authorize_eval_action(
    *,
    actor_permissions: frozenset[str],
    actor_tenant_id: str | None,
    resource_tenant_id: str | None,
    action: EvalAction,
) -> EvalAuthzDecision:
    """Deny-by-default: an unrecognized action, a missing actor tenant,
    or a resource belonging to a different tenant are all refused before
    any permission check runs -- the identical ordering
    `onboarding.policy.authorize_onboarding_action`'s own docstring
    documents ("cross-tenant denial checked before any RBAC branch").

    `actor_permissions` is a plain set of permission-code strings (never
    `Permission` enum members) -- the exact shape `identity.repositories
    .users.get_user_permissions` already returns and
    `governance_center.policy.authorize_governance_action` already
    accepts, kept identical here rather than introducing a second,
    enum-typed convention this one module alone would use.
    """
    if actor_tenant_id is None:
        return EvalAuthzDecision(False, "actor has no resolved tenant")
    if resource_tenant_id is not None and resource_tenant_id != actor_tenant_id:
        return EvalAuthzDecision(False, "resource belongs to a different tenant")

    has_manage = Permission.EVAL_MANAGE.value in actor_permissions
    has_review = Permission.EVAL_REVIEW.value in actor_permissions

    if action in _MANAGE_ACTIONS:
        if has_manage:
            return EvalAuthzDecision(True, "actor holds EVAL_MANAGE")
        return EvalAuthzDecision(False, "actor lacks EVAL_MANAGE")
    if action in _REVIEW_ACTIONS:
        if has_manage or has_review:
            return EvalAuthzDecision(True, "actor holds EVAL_MANAGE or EVAL_REVIEW")
        return EvalAuthzDecision(False, "actor lacks EVAL_MANAGE/EVAL_REVIEW")
    if action in _EITHER_PERMISSION_ACTIONS:
        if has_manage or has_review:
            return EvalAuthzDecision(True, "actor holds EVAL_MANAGE or EVAL_REVIEW")
        return EvalAuthzDecision(False, "actor lacks EVAL_MANAGE/EVAL_REVIEW")
    return EvalAuthzDecision(False, "unrecognized action")
