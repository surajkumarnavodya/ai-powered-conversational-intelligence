"""Unit tests for `governance_center/` (Prompt 44,
`44_AI_GOVERNANCE_CENTER_CONTRACT.md`): the pure lifecycle vocabulary
and the RBAC+ABAC policy decision engine -- no database, no HTTP.
"""

from __future__ import annotations

from governance_center.models import (
    ROLLBACK_ELIGIBLE_STAGES,
    VALID_STAGE_TRANSITIONS,
    GovernanceStage,
    is_terminal_stage,
    stage_to_truth_level,
)
from governance_center.policy import GovernanceAction, authorize_governance_action
from identity.rbac import Permission

from agent.provenance import DataTruthLevel


class TestStageTransitions:
    def test_draft_may_move_to_testing_or_rejected(self):
        assert VALID_STAGE_TRANSITIONS[GovernanceStage.DRAFT] == frozenset(
            {GovernanceStage.TESTING, GovernanceStage.REJECTED}
        )

    def test_deployed_may_only_roll_back_or_retire(self):
        assert VALID_STAGE_TRANSITIONS[GovernanceStage.DEPLOYED] == frozenset(
            {GovernanceStage.ROLLED_BACK, GovernanceStage.RETIRED}
        )

    def test_terminal_stages_have_no_ordinary_outgoing_transition(self):
        for stage in (
            GovernanceStage.ROLLED_BACK,
            GovernanceStage.REJECTED,
            GovernanceStage.RETIRED,
        ):
            assert is_terminal_stage(stage)
            assert VALID_STAGE_TRANSITIONS[stage] == frozenset()

    def test_draft_is_not_terminal(self):
        assert not is_terminal_stage(GovernanceStage.DRAFT)

    def test_rollback_eligible_stages_are_exactly_retired_and_rolled_back(self):
        assert (
            frozenset({GovernanceStage.RETIRED, GovernanceStage.ROLLED_BACK})
            == ROLLBACK_ELIGIBLE_STAGES
        )

    def test_deployed_to_deployed_is_not_a_table_entry(self):
        """Rollback's own `retired`/`rolled_back` -> `deployed` move is
        deliberately NOT in this table -- see `VALID_STAGE_TRANSITIONS`'s
        own docstring for why that is `identity.repositories.governance
        .rollback`'s own, separately-validated action."""
        assert GovernanceStage.DEPLOYED not in VALID_STAGE_TRANSITIONS[GovernanceStage.RETIRED]
        assert GovernanceStage.DEPLOYED not in VALID_STAGE_TRANSITIONS[GovernanceStage.ROLLED_BACK]


class TestTruthLevel:
    def test_draft_testing_evaluating_are_ai_inference(self):
        for stage in (GovernanceStage.DRAFT, GovernanceStage.TESTING, GovernanceStage.EVALUATING):
            assert stage_to_truth_level(stage) == DataTruthLevel.AI_INFERENCE

    def test_approved_and_deployed_are_confirmed_business_truth(self):
        for stage in (GovernanceStage.APPROVED, GovernanceStage.DEPLOYED):
            assert stage_to_truth_level(stage) == DataTruthLevel.CONFIRMED_BUSINESS_TRUTH

    def test_rejected_rolled_back_retired_are_ai_inference_not_confirmed(self):
        """A version that was rejected, rolled back, or retired must never
        stay tagged as confirmed business truth -- a retired/rolled-back
        version is no longer the live, confirmed answer, and a rejected
        one was never confirmed at all."""
        for stage in (
            GovernanceStage.REJECTED,
            GovernanceStage.ROLLED_BACK,
            GovernanceStage.RETIRED,
        ):
            assert stage_to_truth_level(stage) == DataTruthLevel.AI_INFERENCE


class TestGovernancePolicy:
    def test_create_draft_requires_governance_manage(self):
        decision = authorize_governance_action(
            actor_permissions=frozenset({Permission.GOVERNANCE_MANAGE.value}),
            actor_tenant_id="tenant-a",
            asset_tenant_id=None,
            action=GovernanceAction.CREATE_DRAFT,
        )
        assert decision.allowed

    def test_create_draft_denied_without_governance_manage(self):
        decision = authorize_governance_action(
            actor_permissions=frozenset({Permission.GOVERNANCE_REVIEW.value}),
            actor_tenant_id="tenant-a",
            asset_tenant_id=None,
            action=GovernanceAction.CREATE_DRAFT,
        )
        assert not decision.allowed
        assert decision.reason == "missing_permission"

    def test_cross_tenant_is_denied_before_permission_is_even_checked(self):
        decision = authorize_governance_action(
            actor_permissions=frozenset({Permission.GOVERNANCE_MANAGE.value}),
            actor_tenant_id="tenant-a",
            asset_tenant_id="tenant-b",
            action=GovernanceAction.DEPLOY,
        )
        assert not decision.allowed
        assert decision.reason == "cross_tenant"

    def test_an_unresolvable_actor_tenant_is_denied(self):
        decision = authorize_governance_action(
            actor_permissions=frozenset({Permission.GOVERNANCE_MANAGE.value}),
            actor_tenant_id=None,
            asset_tenant_id="tenant-a",
            action=GovernanceAction.DEPLOY,
        )
        assert not decision.allowed
        assert decision.reason == "cross_tenant"

    def test_deploy_rollback_retire_require_governance_manage_not_review(self):
        for action in (GovernanceAction.DEPLOY, GovernanceAction.ROLLBACK, GovernanceAction.RETIRE):
            decision = authorize_governance_action(
                actor_permissions=frozenset({Permission.GOVERNANCE_REVIEW.value}),
                actor_tenant_id="tenant-a",
                asset_tenant_id="tenant-a",
                action=action,
            )
            assert not decision.allowed, f"{action} should require GOVERNANCE_MANAGE"

    def test_approve_and_reject_accept_either_manage_or_review(self):
        for action in (GovernanceAction.APPROVE, GovernanceAction.REJECT):
            decision = authorize_governance_action(
                actor_permissions=frozenset({Permission.GOVERNANCE_REVIEW.value}),
                actor_tenant_id="tenant-a",
                asset_tenant_id="tenant-a",
                action=action,
            )
            assert decision.allowed

    def test_view_is_shared_between_manage_and_review(self):
        for permission in (Permission.GOVERNANCE_MANAGE, Permission.GOVERNANCE_REVIEW):
            decision = authorize_governance_action(
                actor_permissions=frozenset({permission.value}),
                actor_tenant_id="tenant-a",
                asset_tenant_id="tenant-a",
                action=GovernanceAction.VIEW,
            )
            assert decision.allowed

    def test_list_requires_no_specific_asset_tenant_and_is_not_wrongly_denied(self):
        """A real bug found while writing the API test suite: `LIST` (and
        `GET /assets/current`) have no single resolved resource to
        tenant-match against -- treating that the same as `VIEW` would
        wrongly deny every list/current-lookup call with `asset_tenant_id
        =None` (the caller's own tenant filter, not a missing resource).
        """
        decision = authorize_governance_action(
            actor_permissions=frozenset({Permission.GOVERNANCE_MANAGE.value}),
            actor_tenant_id="tenant-a",
            asset_tenant_id=None,
            action=GovernanceAction.LIST,
        )
        assert decision.allowed

    def test_a_caller_with_no_governance_permission_at_all_is_denied_view(self):
        decision = authorize_governance_action(
            actor_permissions=frozenset({"ask"}),
            actor_tenant_id="tenant-a",
            asset_tenant_id="tenant-a",
            action=GovernanceAction.VIEW,
        )
        assert not decision.allowed
        assert decision.reason == "missing_permission"
