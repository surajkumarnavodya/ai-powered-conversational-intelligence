"""Unit tests for workflow/policy.py -- pure, no database, no HTTP.
Prompt 49 (`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`)."""

from __future__ import annotations

import pytest
from identity.rbac import Permission

from workflow.policy import WorkflowAction, authorize_workflow_action

_MANAGE = frozenset({Permission.WORKFLOW_MANAGE.value})
_REVIEW = frozenset({Permission.WORKFLOW_REVIEW.value})
_BOTH = frozenset({Permission.WORKFLOW_MANAGE.value, Permission.WORKFLOW_REVIEW.value})
_NEITHER = frozenset({"ai.use"})

#: Every resource-scoped action (everything except CREATE/LIST).
_RESOURCE_ACTIONS = [
    a for a in WorkflowAction if a not in (WorkflowAction.CREATE, WorkflowAction.LIST)
]


class TestCreateAndList:
    def test_create_requires_manage(self):
        decision = authorize_workflow_action(
            actor_permissions=_MANAGE,
            actor_tenant_id="tenant-a",
            workflow_tenant_id=None,
            action=WorkflowAction.CREATE,
        )
        assert decision.allowed is True

    def test_create_is_denied_without_manage(self):
        decision = authorize_workflow_action(
            actor_permissions=_REVIEW,
            actor_tenant_id="tenant-a",
            workflow_tenant_id=None,
            action=WorkflowAction.CREATE,
        )
        assert decision.allowed is False
        assert decision.reason == "missing_permission"

    def test_list_is_allowed_with_either_permission(self):
        for perms in (_MANAGE, _REVIEW, _BOTH):
            decision = authorize_workflow_action(
                actor_permissions=perms,
                actor_tenant_id="tenant-a",
                workflow_tenant_id=None,
                action=WorkflowAction.LIST,
            )
            assert decision.allowed is True

    def test_list_is_denied_without_either_permission(self):
        decision = authorize_workflow_action(
            actor_permissions=_NEITHER,
            actor_tenant_id="tenant-a",
            workflow_tenant_id=None,
            action=WorkflowAction.LIST,
        )
        assert decision.allowed is False

    def test_list_never_needs_a_workflow_tenant_id(self):
        """Mirrors governance_center.policy's own LIST-vs-VIEW fix --
        LIST must never be denied just because no specific resource was
        resolved yet."""
        decision = authorize_workflow_action(
            actor_permissions=_MANAGE,
            actor_tenant_id="tenant-a",
            workflow_tenant_id=None,
            action=WorkflowAction.LIST,
        )
        assert decision.allowed is True


class TestResourceScopedDenyByDefault:
    @pytest.mark.parametrize("action", _RESOURCE_ACTIONS)
    def test_a_nonexistent_workflow_is_denied(self, action):
        decision = authorize_workflow_action(
            actor_permissions=_BOTH,
            actor_tenant_id="tenant-a",
            workflow_tenant_id=None,
            action=action,
        )
        assert decision.allowed is False
        assert decision.reason == "workflow_not_found"

    @pytest.mark.parametrize("action", _RESOURCE_ACTIONS)
    def test_a_cross_tenant_workflow_is_denied(self, action):
        decision = authorize_workflow_action(
            actor_permissions=_BOTH,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-b",
            action=action,
        )
        assert decision.allowed is False
        assert decision.reason == "cross_tenant"

    @pytest.mark.parametrize("action", _RESOURCE_ACTIONS)
    def test_a_none_actor_tenant_is_denied(self, action):
        decision = authorize_workflow_action(
            actor_permissions=_BOTH,
            actor_tenant_id=None,
            workflow_tenant_id="tenant-a",
            action=action,
        )
        assert decision.allowed is False
        assert decision.reason == "cross_tenant"


class TestManageOnlyActions:
    @pytest.mark.parametrize(
        "action",
        [
            WorkflowAction.SUBMIT_FOR_APPROVAL,
            WorkflowAction.ASSIGN,
            WorkflowAction.START,
            WorkflowAction.COMPLETE,
            WorkflowAction.SET_EXTERNAL_REFERENCE,
            WorkflowAction.ADD_COMMENT,
            WorkflowAction.EXPIRE,
        ],
    )
    def test_manage_is_allowed(self, action):
        decision = authorize_workflow_action(
            actor_permissions=_MANAGE,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=action,
        )
        assert decision.allowed is True

    @pytest.mark.parametrize(
        "action",
        [
            WorkflowAction.SUBMIT_FOR_APPROVAL,
            WorkflowAction.ASSIGN,
            WorkflowAction.START,
            WorkflowAction.COMPLETE,
            WorkflowAction.SET_EXTERNAL_REFERENCE,
            WorkflowAction.ADD_COMMENT,
            WorkflowAction.EXPIRE,
        ],
    )
    def test_review_alone_is_denied(self, action):
        """A plain reviewer (no manage permission) cannot perform an
        operational action -- the mission's own separation of a
        'propose/operate' tier from a 'decide' tier."""
        decision = authorize_workflow_action(
            actor_permissions=_REVIEW,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=action,
        )
        assert decision.allowed is False
        assert decision.reason == "missing_permission"


class TestApproveReject:
    @pytest.mark.parametrize("action", [WorkflowAction.APPROVE, WorkflowAction.REJECT])
    def test_review_is_allowed(self, action):
        decision = authorize_workflow_action(
            actor_permissions=_REVIEW,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=action,
        )
        assert decision.allowed is True

    @pytest.mark.parametrize("action", [WorkflowAction.APPROVE, WorkflowAction.REJECT])
    def test_manage_alone_is_also_allowed(self, action):
        """Mirrors governance_center.policy's own `_VIEW_OR_REVIEW_ACTIONS`
        shape -- in this codebase's real seeded roles, 'admin' holds both
        permissions via the role hierarchy (see workflow/policy.py's own
        docstring), so this is deliberate, not a loophole."""
        decision = authorize_workflow_action(
            actor_permissions=_MANAGE,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=action,
        )
        assert decision.allowed is True

    @pytest.mark.parametrize("action", [WorkflowAction.APPROVE, WorkflowAction.REJECT])
    def test_neither_permission_is_denied(self, action):
        decision = authorize_workflow_action(
            actor_permissions=_NEITHER,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=action,
        )
        assert decision.allowed is False
        assert decision.reason == "missing_permission"


class TestView:
    def test_either_permission_may_view(self):
        for perms in (_MANAGE, _REVIEW):
            decision = authorize_workflow_action(
                actor_permissions=perms,
                actor_tenant_id="tenant-a",
                workflow_tenant_id="tenant-a",
                action=WorkflowAction.VIEW,
            )
            assert decision.allowed is True

    def test_neither_permission_cannot_view(self):
        decision = authorize_workflow_action(
            actor_permissions=_NEITHER,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=WorkflowAction.VIEW,
        )
        assert decision.allowed is False


def test_unrecognized_action_is_denied():
    decision = authorize_workflow_action(
        actor_permissions=_BOTH,
        actor_tenant_id="tenant-a",
        workflow_tenant_id="tenant-a",
        action="not_a_real_action",  # type: ignore[arg-type]
    )
    assert decision.allowed is False
    assert decision.reason == "unrecognized_action"
