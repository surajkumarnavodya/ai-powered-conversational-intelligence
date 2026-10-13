"""Tests for `artifacts/policy.py` -- the analysis artifact workspace. Pure, no database.

The property this file exists to pin: an artifact is **narrower** than
the conversation turn it references, never wider. Tenant *and* owner must
match, and refreshing needs the same permission `POST /execute` needs.
"""

from __future__ import annotations

import pytest

from artifacts.policy import ArtifactAction, authorize_artifact_action

ALL_ACTIONS = list(ArtifactAction)
RESOURCE_ACTIONS = [
    ArtifactAction.VIEW,
    ArtifactAction.REFRESH,
    ArtifactAction.DELETE,
]


def decide(**overrides):
    kwargs = {
        "actor_roles": ["user"],
        "actor_user_id": "u1",
        "actor_tenant_id": "tenant-a",
        "resource_tenant_id": "tenant-a",
        "resource_owner_user_id": "u1",
        "action": ArtifactAction.VIEW,
    }
    kwargs.update(overrides)
    return authorize_artifact_action(**kwargs)


class TestOwnership:
    @pytest.mark.parametrize("action", RESOURCE_ACTIONS)
    def test_another_users_artifact_is_refused_for_every_action(self, action):
        # The check that makes this feature safe. An artifact points at a
        # conversation turn chat history scopes to one user, so a
        # tenant-wide artifact would be a readable back door into a
        # colleague's private question.
        decision = decide(resource_owner_user_id="u2", action=action)
        assert decision.allowed is False
        assert decision.reason == "artifact belongs to a different user"

    @pytest.mark.parametrize("action", RESOURCE_ACTIONS)
    def test_another_tenants_artifact_is_refused_for_every_action(self, action):
        decision = decide(resource_tenant_id="tenant-b", action=action)
        assert decision.allowed is False
        assert decision.reason == "artifact belongs to a different tenant"

    def test_an_admin_of_the_same_tenant_still_cannot_read_another_users_artifact(self):
        # Deliberately stricter than every other tenant-scoped resource in
        # this codebase, because the underlying resource is stricter.
        decision = decide(actor_roles=["admin"], resource_owner_user_id="u2")
        assert decision.allowed is False

    def test_the_owner_is_allowed(self):
        assert decide().allowed is True


class TestDenyByDefault:
    def test_an_actor_with_no_tenant_is_refused_before_any_permission_check(self):
        decision = decide(actor_tenant_id=None, actor_roles=["admin"])
        assert decision.allowed is False
        assert decision.reason == "actor has no resolved tenant"

    def test_an_actor_with_no_user_id_is_refused(self):
        decision = decide(actor_user_id=None, actor_roles=["admin"])
        assert decision.allowed is False
        assert decision.reason == "actor has no resolved user id"

    def test_an_unrecognized_action_is_refused(self):
        decision = decide(action="export_everything")
        assert decision.allowed is False
        assert decision.reason == "unrecognized action"

    def test_an_unknown_role_grants_nothing(self):
        decision = decide(actor_roles=["wizard"])
        assert decision.allowed is False

    def test_no_roles_at_all_grants_nothing(self):
        assert decide(actor_roles=[]).allowed is False


class TestRefreshNeedsExecutePermission:
    def test_a_viewer_may_read_their_own_artifacts(self):
        # `viewer` holds ASK, so saving and reading analyses of their own
        # questions is theirs.
        for action in (ArtifactAction.LIST, ArtifactAction.CREATE, ArtifactAction.VIEW):
            assert decide(actor_roles=["viewer"], action=action).allowed is True

    def test_a_viewer_may_not_refresh_one(self):
        # This is the substance of "artifacts must not bypass
        # authorization": a role that may not run a query must not be able
        # to make a saved artifact run one on its behalf. `viewer` lacks
        # `execute_sql`, which is exactly what `POST /execute` requires.
        decision = decide(actor_roles=["viewer"], action=ArtifactAction.REFRESH)
        assert decision.allowed is False
        assert decision.reason == "actor lacks execute_sql"

    def test_a_user_may_refresh_one(self):
        assert decide(actor_roles=["user"], action=ArtifactAction.REFRESH).allowed is True

    def test_a_viewer_may_still_delete_their_own_artifact(self):
        # Deleting your own saved work is not an execution.
        assert decide(actor_roles=["viewer"], action=ArtifactAction.DELETE).allowed is True


class TestNoInventedPermission:
    def test_every_action_maps_to_an_existing_base_permission(self):
        # No new permission code was introduced for this feature -- the
        # base-role vocabulary already drew the distinction it needed.
        from agent.authz import Permission
        from artifacts.policy import _REQUIRED_PERMISSION

        assert set(_REQUIRED_PERMISSION) == set(ALL_ACTIONS)
        for permission in _REQUIRED_PERMISSION.values():
            assert permission in set(Permission)

    def test_no_artifact_permission_code_exists_in_the_rbac_seed(self):
        from identity.rbac import Permission as IdentityPermission

        assert not [p for p in IdentityPermission if "artifact" in p.value]
