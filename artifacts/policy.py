"""Deny-by-default RBAC+ABAC for analysis artifacts -- the analysis artifact workspace.

A pure function with no I/O, taking already-resolved roles/tenant/owner
and returning one decision -- the same shape `bi_integration.policy`,
`eval_platform.policy`, `semantic.catalog_policy` and `onboarding.policy`
already establish. The real enforcement point is `api/artifacts.py`'s own
call site; this module alone enforces nothing.

**Two checks, not one, and the second one is the point.** A BI connector
or a governed asset version is tenant-scoped: anyone in the tenant with
the right permission may act on it. An artifact is **not**: it points at
a `Conversation`/`Prompt`/`AiOutput` row that
`identity/repositories/history.py` deliberately scopes to one `user_id`,
so a tenant-wide artifact would be a back door around chat history's own
ownership boundary -- a colleague's private question and its answer,
readable because someone saved it. So an artifact is matched on tenant
*and* owner, and requirement 8 ("do not allow artifacts to bypass
authorization") is satisfied by the artifact being strictly narrower than
the thing it references, never wider.

**No new permission code was invented.** The base-role vocabulary
(`agent.authz.Permission`) already draws the distinction this feature
needs: `ASK` to own and read analyses of your own questions, and
`EXECUTE_SQL` -- the permission `POST /execute` itself requires -- to
make an artifact re-run its SQL. A `viewer`, who may ask but may not run
queries, therefore cannot refresh an artifact either, which is the whole
substance of "artifacts must not bypass authorization" for this feature.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from agent.authz import Permission, has_role_permission


class ArtifactAction(str, Enum):
    """One governable action against an artifact.

    `LIST`/`CREATE` have no single resource to tenant- or owner-match
    against yet -- the identical reasoning `bi_integration.policy
    .BiAction.LIST`/`governance_center.policy.GovernanceAction.LIST`'s own
    docstrings already name (and the real bug
    `44_AI_GOVERNANCE_CENTER_CONTRACT.md` found and fixed for exactly this
    shape, avoided here from the start).
    """

    LIST = "list"
    CREATE = "create"
    VIEW = "view"
    REFRESH = "refresh"
    DELETE = "delete"


#: Which base-role permission each action requires. `REFRESH` really does
#: execute SQL against a customer database, so it is gated on the same
#: permission `POST /execute` is -- not on the weaker one that merely lets
#: a caller read their own saved analyses.
_REQUIRED_PERMISSION: dict[ArtifactAction, Permission] = {
    ArtifactAction.LIST: Permission.ASK,
    ArtifactAction.CREATE: Permission.ASK,
    ArtifactAction.VIEW: Permission.ASK,
    ArtifactAction.REFRESH: Permission.EXECUTE_SQL,
    ArtifactAction.DELETE: Permission.ASK,
}


@dataclass(frozen=True)
class ArtifactAuthzDecision:
    allowed: bool
    reason: str


def authorize_artifact_action(
    *,
    actor_roles: Sequence[str],
    actor_user_id: str | None,
    actor_tenant_id: str | None,
    resource_tenant_id: str | None,
    resource_owner_user_id: str | None,
    action: ArtifactAction,
) -> ArtifactAuthzDecision:
    """Deny-by-default.

    A missing actor tenant, a missing actor id, a resource in another
    tenant, a resource owned by another user, or an unrecognized action
    are all refused before any permission check runs -- so a bug in the
    permission table can never widen access beyond the caller's own
    tenant and their own rows.

    `actor_roles` is the caller's base role names
    (`identity.repositories.users.get_user_roles`), which bridge straight
    into `agent.authz.ROLE_PERMISSIONS` -- the same bridge
    `api/bi_integration.py` already uses when it hands roles to
    `governance.result_policy.govern_rows_for_caller`.
    """
    if actor_tenant_id is None:
        return ArtifactAuthzDecision(False, "actor has no resolved tenant")
    if actor_user_id is None:
        return ArtifactAuthzDecision(False, "actor has no resolved user id")
    if resource_tenant_id is not None and resource_tenant_id != actor_tenant_id:
        return ArtifactAuthzDecision(False, "artifact belongs to a different tenant")
    if resource_owner_user_id is not None and resource_owner_user_id != actor_user_id:
        return ArtifactAuthzDecision(False, "artifact belongs to a different user")

    required = _REQUIRED_PERMISSION.get(action)
    if required is None:
        return ArtifactAuthzDecision(False, "unrecognized action")
    if not has_role_permission(list(actor_roles), required):
        return ArtifactAuthzDecision(False, f"actor lacks {required.value}")
    return ArtifactAuthzDecision(True, f"actor holds {required.value}")
