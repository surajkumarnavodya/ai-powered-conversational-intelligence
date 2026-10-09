"""Deny-by-default RBAC+ABAC for `bi_integration/` -- Prompt 48. Mirrors
`eval_platform.policy`/`semantic.catalog_policy`/`onboarding.policy`'s own
identical shape: a pure function, no I/O, taking already-loaded
permissions/tenant ids and returning one decision -- the real enforcement
point is `api/bi_integration.py`'s own call site, not this module acting
alone.

A single permission (`Permission.BI_INTEGRATION_MANAGE`), not a
`*_MANAGE`/`*_REVIEW` pair -- see that permission's own docstring in
`identity/rbac.py` for why this feature has no domain-judgment review
tier the way onboarding/catalog/governance do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from identity.rbac import Permission


class BiAction(str, Enum):
    """One governable action against a connector.

    `LIST`/`CREATE` have no single resource to tenant-match against yet
    -- the identical reasoning `eval_platform.policy.EvalAction.LIST`/
    `governance_center.policy.GovernanceAction.LIST`'s own docstrings
    already name (and the real bug `44_AI_GOVERNANCE_CENTER_CONTRACT.md`
    found and fixed for the identical shape).
    """

    LIST = "list"
    CREATE = "create"
    GET = "get"
    REFRESH = "refresh"
    EXPORT = "export"
    DELETE = "delete"


@dataclass(frozen=True)
class BiAuthzDecision:
    allowed: bool
    reason: str


def authorize_bi_action(
    *,
    actor_permissions: frozenset[str],
    actor_tenant_id: str | None,
    resource_tenant_id: str | None,
    action: BiAction,
) -> BiAuthzDecision:
    """Deny-by-default: an unrecognized action, a missing actor tenant,
    or a resource belonging to a different tenant are all refused before
    any permission check runs.

    `actor_permissions` is a plain set of permission-code strings (never
    `Permission` enum members) -- the exact shape
    `identity.repositories.users.get_user_permissions` actually returns,
    the identical convention `eval_platform.policy.authorize_eval_action`
    already establishes (and the real `mypy`-caught bug fixed there,
    applied here from the start rather than rediscovering it).
    """
    if actor_tenant_id is None:
        return BiAuthzDecision(False, "actor has no resolved tenant")
    if resource_tenant_id is not None and resource_tenant_id != actor_tenant_id:
        return BiAuthzDecision(False, "resource belongs to a different tenant")

    if action not in BiAction:
        return BiAuthzDecision(False, "unrecognized action")

    if Permission.BI_INTEGRATION_MANAGE.value in actor_permissions:
        return BiAuthzDecision(True, "actor holds BI_INTEGRATION_MANAGE")
    return BiAuthzDecision(False, "actor lacks BI_INTEGRATION_MANAGE")
