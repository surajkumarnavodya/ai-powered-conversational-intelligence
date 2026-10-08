"""Centralized RBAC + ABAC decision engine for the AI Governance Center
-- every route in `api/governance.py` must call
`authorize_governance_action` before doing anything, mirroring
`onboarding/policy.py`'s own "pure policy, no FastAPI/database import"
split exactly (this module is unit-testable with plain, hand-built
values -- no DB, no HTTP).

**Deny-by-default at every branch.** An action this module doesn't
recognize, a `None` where a value was expected -- every one of those is
a `deny`, never an `allow`.

## RBAC (what someone may do at all)

- `identity.rbac.Permission.GOVERNANCE_MANAGE` -- create/edit a draft,
  submit for testing/evaluation, record test/evaluation results, deploy,
  roll back, retire. Granted to the `admin` role only (`identity/rbac
  .py`'s own `_ADMIN` set) -- releasing or rolling back a production
  model/prompt/tool/retrieval-source/policy configuration is an
  operational, admin-tier action, the identical reasoning
  `ONBOARDING_MANAGE`/`CATALOG_MANAGE` already establish for their own
  domains.
- `identity.rbac.Permission.GOVERNANCE_REVIEW` -- approve or reject a
  version. Granted to `analyst` and `admin` -- a governance reviewer
  needs domain judgment (does this test/evaluation evidence actually
  support production use), not account administration, the identical
  reasoning `ONBOARDING_REVIEW`/`CATALOG_REVIEW`/`RECOMMENDATION_REVIEW`
  already establish.

## ABAC (which specific asset version)

Every action beyond creating a brand-new draft requires the actor's own
`security.tenancy.resolve_actor_tenant_id` to match the target
`GovernanceAssetVersion.tenant_id` -- the exact same scoped pattern
`onboarding.policy`/`semantic.catalog_policy`/`recommendation
.governance_policy` already establish. A cross-tenant caller is denied
with the same `"cross_tenant"` reason code every one of those modules
uses, never a different one that might hint the version exists in a
tenant it doesn't belong to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from identity.rbac import Permission


class GovernanceAction(str, Enum):
    CREATE_DRAFT = "create_draft"
    #: Listing this tenant's own versions, or looking up the currently-
    #: deployed version by `(asset_type, asset_key)` -- deliberately
    #: distinct from `VIEW` below: neither has one specific, already-
    #: resolved resource to tenant-match against (the query itself is
    #: what scopes the result to the caller's own tenant), so this
    #: action is authorized by permission alone, the identical
    #: "no target yet" shape `CREATE_DRAFT` already has.
    LIST = "list"
    #: Viewing one *specific*, already-resolved version by id -- this
    #: one always tenant-matches against that version's own
    #: `tenant_id`, unlike `LIST` above.
    VIEW = "view"
    EDIT_DRAFT = "edit_draft"
    SUBMIT_FOR_TESTING = "submit_for_testing"
    RECORD_TEST_RESULTS = "record_test_results"
    SUBMIT_FOR_EVALUATION = "submit_for_evaluation"
    RECORD_EVALUATION_RESULTS = "record_evaluation_results"
    APPROVE = "approve"
    REJECT = "reject"
    DEPLOY = "deploy"
    ROLLBACK = "rollback"
    RETIRE = "retire"
    RECORD_USAGE = "record_usage"


_MANAGE_ACTIONS: frozenset[GovernanceAction] = frozenset(
    {
        GovernanceAction.CREATE_DRAFT,
        GovernanceAction.EDIT_DRAFT,
        GovernanceAction.SUBMIT_FOR_TESTING,
        GovernanceAction.RECORD_TEST_RESULTS,
        GovernanceAction.SUBMIT_FOR_EVALUATION,
        GovernanceAction.RECORD_EVALUATION_RESULTS,
        GovernanceAction.DEPLOY,
        GovernanceAction.ROLLBACK,
        GovernanceAction.RETIRE,
        GovernanceAction.RECORD_USAGE,
    }
)
# VIEW is deliberately readable by either permission -- a reviewer who
# can approve/reject a version must also be able to see it (and its own
# test/evaluation evidence), the identical "view is shared" rationale
# `onboarding.policy`'s own `_VIEW_OR_REVIEW_ACTIONS` establishes.
_VIEW_OR_REVIEW_ACTIONS: frozenset[GovernanceAction] = frozenset(
    {GovernanceAction.VIEW, GovernanceAction.APPROVE, GovernanceAction.REJECT}
)


@dataclass(frozen=True)
class AuthorizationDecision:
    """The outcome of one `authorize_governance_action` call.

    Attributes:
        allowed: Whether the action may proceed.
        reason: A stable machine code (never a sentence) -- mirrors
            `identity.share_policy.AuthorizationDecision`'s own contract.
    """

    allowed: bool
    reason: str


def _allow(reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(allowed=True, reason=reason)


def _deny(reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(allowed=False, reason=reason)


def authorize_governance_action(
    *,
    actor_permissions: frozenset[str],
    actor_tenant_id: str | None,
    asset_tenant_id: str | None,
    action: GovernanceAction,
) -> AuthorizationDecision:
    """The one function every governance route must call.

    Args:
        actor_permissions: The caller's own DB-backed granted
            permissions, as plain strings (`identity.repositories.users
            .get_user_permissions`'s actual return shape) -- resolved by
            the caller, never by this module (keeps this file's own
            dependency surface at zero, per its docstring).
        actor_tenant_id: `security.tenancy.resolve_actor_tenant_id(user)`.
        asset_tenant_id: The target `GovernanceAssetVersion.tenant_id`,
            or `None` for `CREATE_DRAFT`/`LIST` (neither has an existing,
            specific version to check a tenant against -- see each
            action's own docstring on `GovernanceAction`).
        action: Which action is being attempted.

    Returns:
        An `AuthorizationDecision`. Every branch is a `deny` unless an
        explicit, positive condition is met first.
    """
    if action == GovernanceAction.CREATE_DRAFT:
        if Permission.GOVERNANCE_MANAGE in actor_permissions:
            return _allow("governance_manage")
        return _deny("missing_permission")

    if action == GovernanceAction.LIST:
        if (
            Permission.GOVERNANCE_MANAGE in actor_permissions
            or Permission.GOVERNANCE_REVIEW in actor_permissions
        ):
            return _allow("governance_manage_or_review")
        return _deny("missing_permission")

    if asset_tenant_id is None:
        return _deny("asset_not_found")
    if actor_tenant_id is None or actor_tenant_id != asset_tenant_id:
        return _deny("cross_tenant")

    if action in _MANAGE_ACTIONS:
        if Permission.GOVERNANCE_MANAGE in actor_permissions:
            return _allow("governance_manage")
        return _deny("missing_permission")

    if action in _VIEW_OR_REVIEW_ACTIONS:
        if (
            Permission.GOVERNANCE_MANAGE in actor_permissions
            or Permission.GOVERNANCE_REVIEW in actor_permissions
        ):
            return _allow("governance_manage_or_review")
        return _deny("missing_permission")

    return _deny("unrecognized_action")


__all__ = ["GovernanceAction", "AuthorizationDecision", "authorize_governance_action"]
