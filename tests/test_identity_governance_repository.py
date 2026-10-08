"""Tests for `identity/repositories/governance.py` (Prompt 44,
`44_AI_GOVERNANCE_CENTER_CONTRACT.md`) -- a real in-memory SQLite
identity database (`Base.metadata.create_all`), never a mock of the
ORM, matching this codebase's own stated convention for every
`identity/` test module.
"""

from __future__ import annotations

import identity.repositories.governance as gov
import pytest
from governance_center.models import GovernanceStage
from identity.models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine) -> Session:
    return Session(engine)


def _draft(session, *, tenant_id="t1", asset_key="m", asset_type="model"):
    return gov.create_draft(
        session,
        tenant_id=tenant_id,
        asset_type=asset_type,
        asset_key=asset_key,
        display_name="Test asset",
        description="",
        config={"foo": "bar"},
        owner="platform",
        created_by_user_id=None,
    )


class TestCreateAndVersioning:
    def test_a_brand_new_asset_starts_at_version_1_draft(self, session):
        v = _draft(session)
        assert v.version == 1
        assert v.stage == "draft"

    def test_a_second_draft_for_the_same_asset_key_is_version_2(self, session):
        _draft(session)
        v2 = _draft(session)
        assert v2.version == 2

    def test_versions_are_scoped_per_tenant(self, session):
        v1 = _draft(session, tenant_id="t1")
        v2 = _draft(session, tenant_id="t2")
        assert v1.version == 1
        assert v2.version == 1

    def test_create_draft_logs_an_initial_stage_change_event(self, session):
        v = _draft(session)
        events = gov.list_events(session, v.id)
        assert len(events) == 1
        assert events[0].event_type == "stage_change"
        assert events[0].to_stage == "draft"


class TestUpdateDraft:
    def test_a_draft_may_be_edited_in_place(self, session):
        v = _draft(session)
        updated = gov.update_draft(session, v, display_name="New name")
        assert updated.display_name == "New name"
        assert updated.version == v.version  # same row, not a new version

    def test_a_non_draft_version_cannot_be_edited(self, session):
        v = _draft(session)
        gov.submit_for_testing(session, v, actor_user_id=None)
        with pytest.raises(gov.InvalidGovernanceStageTransitionError):
            gov.update_draft(session, v, display_name="New name")


class TestOrdinaryLifecycle:
    def test_the_full_happy_path_reaches_deployed(self, session):
        v = _draft(session)
        v = gov.submit_for_testing(session, v, actor_user_id=None)
        assert v.stage == "testing"
        v = gov.record_test_results(session, v, test_results={"passed": True}, actor_user_id=None)
        assert v.test_results == {"passed": True}
        v = gov.submit_for_evaluation(session, v, actor_user_id=None)
        assert v.stage == "evaluating"
        v = gov.record_evaluation_results(
            session, v, evaluation_results={"score": 0.9}, actor_user_id=None
        )
        v = gov.approve(session, v, actor_user_id=None, notes="good")
        assert v.stage == "approved"
        assert v.approval_notes == "good"
        deployed, retired = gov.deploy(session, v, actor_user_id=None)
        assert deployed.stage == "deployed"
        assert retired is None  # nothing was deployed before

    def test_record_test_results_rejects_a_non_testing_version(self, session):
        v = _draft(session)
        with pytest.raises(gov.InvalidGovernanceStageTransitionError):
            gov.record_test_results(session, v, test_results={}, actor_user_id=None)

    def test_approve_rejects_a_non_evaluating_version(self, session):
        v = _draft(session)
        with pytest.raises(gov.InvalidGovernanceStageTransitionError):
            gov.approve(session, v, actor_user_id=None)

    def test_deploy_rejects_a_non_approved_version(self, session):
        v = _draft(session)
        with pytest.raises(gov.InvalidGovernanceStageTransitionError):
            gov.deploy(session, v, actor_user_id=None)

    @pytest.mark.parametrize(
        "from_stage",
        ["draft", "testing", "evaluating", "approved"],
    )
    def test_reject_is_legal_from_every_non_terminal_pre_deploy_stage(self, session, from_stage):
        v = _draft(session)
        if from_stage in ("testing", "evaluating", "approved"):
            v = gov.submit_for_testing(session, v, actor_user_id=None)
        if from_stage in ("evaluating", "approved"):
            v = gov.submit_for_evaluation(session, v, actor_user_id=None)
        if from_stage == "approved":
            v = gov.approve(session, v, actor_user_id=None)
        rejected = gov.reject(session, v, actor_user_id=None, reason="not good enough")
        assert rejected.stage == "rejected"
        assert rejected.rejection_reason == "not good enough"

    def test_a_rejected_version_has_no_further_legal_transition(self, session):
        v = _draft(session)
        v = gov.reject(session, v, actor_user_id=None, reason="no")
        with pytest.raises(gov.InvalidGovernanceStageTransitionError):
            gov.submit_for_testing(session, v, actor_user_id=None)


def _approved(session, **kwargs):
    v = _draft(session, **kwargs)
    v = gov.submit_for_testing(session, v, actor_user_id=None)
    v = gov.submit_for_evaluation(session, v, actor_user_id=None)
    return gov.approve(session, v, actor_user_id=None)


class TestDeployAndSupersession:
    def test_deploying_a_second_version_retires_the_first(self, session):
        v1 = _approved(session)
        d1, _ = gov.deploy(session, v1, actor_user_id=None)
        v2 = _approved(session)
        d2, retired = gov.deploy(session, v2, actor_user_id=None)
        assert d2.stage == "deployed"
        assert retired is not None
        assert retired.id == d1.id
        session.refresh(d1)
        assert d1.stage == "retired"

    def test_only_one_version_of_an_asset_is_ever_deployed_at_once(self, session):
        v1 = _approved(session)
        gov.deploy(session, v1, actor_user_id=None)
        v2 = _approved(session)
        gov.deploy(session, v2, actor_user_id=None)
        current = gov.get_current_deployed(session, v1.tenant_id, v1.asset_type, v1.asset_key)
        assert current.id == v2.id

    def test_get_current_deployed_is_none_when_nothing_is_deployed(self, session):
        _draft(session)
        assert gov.get_current_deployed(session, "t1", "model", "m") is None

    def test_deploying_a_different_assets_version_never_touches_an_unrelated_asset(self, session):
        v1 = _approved(session, asset_key="model-a")
        gov.deploy(session, v1, actor_user_id=None)
        v2 = _approved(session, asset_key="model-b")
        d2, retired = gov.deploy(session, v2, actor_user_id=None)
        assert retired is None  # a different asset_key -- nothing to retire
        session.refresh(v1)
        assert v1.stage == "deployed"  # untouched


class TestRollback:
    def test_rollback_redeploys_an_earlier_version_and_rolls_back_the_current_one(self, session):
        v1 = _approved(session)
        d1, _ = gov.deploy(session, v1, actor_user_id=None)
        v2 = _approved(session)
        d2, retired1 = gov.deploy(session, v2, actor_user_id=None)

        rolled_back, redeployed = gov.rollback(
            session, d2, retired1, actor_user_id=None, reason="regression"
        )
        assert rolled_back.id == d2.id
        assert rolled_back.stage == "rolled_back"
        assert redeployed.id == d1.id
        assert redeployed.stage == "deployed"

        current = gov.get_current_deployed(session, v1.tenant_id, v1.asset_type, v1.asset_key)
        assert current.id == v1.id

    def test_rollback_target_must_be_retired_or_rolled_back(self, session):
        v1 = _approved(session)
        d1, _ = gov.deploy(session, v1, actor_user_id=None)
        v2 = _approved(session)  # never deployed -- still "approved"
        with pytest.raises(gov.RollbackTargetIneligibleError):
            gov.rollback(session, d1, v2, actor_user_id=None, reason="x")

    def test_rollback_target_must_be_the_same_asset(self, session):
        v1 = _approved(session, asset_key="model-a")
        d1, _ = gov.deploy(session, v1, actor_user_id=None)
        other_asset = _approved(session, asset_key="model-b")
        gov.deploy(session, other_asset, actor_user_id=None)
        v1b = _approved(session, asset_key="model-a")
        d1b, retired_a = gov.deploy(session, v1b, actor_user_id=None)
        with pytest.raises(gov.RollbackTargetIneligibleError):
            # other_asset is a different asset_key entirely -- never eligible
            gov.rollback(session, d1b, other_asset, actor_user_id=None, reason="x")

    def test_rollback_requires_the_current_version_to_actually_be_deployed(self, session):
        v1 = _approved(session)
        d1, _ = gov.deploy(session, v1, actor_user_id=None)
        v2 = _approved(session)
        d2, retired1 = gov.deploy(session, v2, actor_user_id=None)
        v3 = _approved(session)
        d3, retired2 = gov.deploy(session, v3, actor_user_id=None)
        # retired1 (v1) is "retired", not "deployed" -- rolling back FROM it
        # is nonsensical, even though retired2 (v2) is itself an eligible
        # rollback *target*.
        with pytest.raises(gov.InvalidGovernanceStageTransitionError):
            gov.rollback(session, retired1, retired2, actor_user_id=None, reason="x")

    def test_rollback_logs_a_rollback_event_on_both_versions(self, session):
        v1 = _approved(session)
        d1, _ = gov.deploy(session, v1, actor_user_id=None)
        v2 = _approved(session)
        d2, retired1 = gov.deploy(session, v2, actor_user_id=None)
        gov.rollback(session, d2, retired1, actor_user_id=None, reason="bad release")

        d2_events = gov.list_events(session, d2.id)
        assert any(e.event_type == "rollback" and e.to_stage == "rolled_back" for e in d2_events)
        v1_events = gov.list_events(session, v1.id)
        assert any(e.event_type == "rollback" and e.to_stage == "deployed" for e in v1_events)


class TestRetire:
    def test_an_approved_version_may_be_manually_retired(self, session):
        v = _approved(session)
        retired = gov.retire(session, v, actor_user_id=None, reason="superseded by policy change")
        assert retired.stage == "retired"

    def test_a_deployed_version_may_be_manually_retired(self, session):
        v = _approved(session)
        d, _ = gov.deploy(session, v, actor_user_id=None)
        retired = gov.retire(session, d, actor_user_id=None)
        assert retired.stage == "retired"


class TestUsageTracking:
    def test_usage_samples_accumulate_into_a_deterministic_summary(self, session):
        v = _approved(session)
        d, _ = gov.deploy(session, v, actor_user_id=None)
        gov.record_usage(
            session,
            d,
            actor_user_id=None,
            payload={"request_count": 100, "total_cost": 2.0, "p95_latency_ms": 150.0},
        )
        gov.record_usage(
            session,
            d,
            actor_user_id=None,
            payload={"request_count": 50, "total_cost": 1.0, "p95_latency_ms": 100.0},
        )
        summary = gov.usage_summary(session, d.id)
        assert summary["sample_count"] == 2
        assert summary["total_requests"] == 150.0
        assert summary["total_cost"] == 3.0
        assert summary["mean_p95_latency_ms"] == 125.0

    def test_usage_summary_with_no_samples_is_well_defined(self, session):
        v = _draft(session)
        summary = gov.usage_summary(session, v.id)
        assert summary == {
            "sample_count": 0,
            "total_requests": 0,
            "total_cost": 0,
            "mean_p95_latency_ms": None,
        }


class TestVersionToSnapshot:
    def test_snapshot_is_orm_independent_and_carries_the_right_truth_level(self, session):
        v = _approved(session)
        snapshot = gov.version_to_snapshot(v)
        assert snapshot.stage == GovernanceStage.APPROVED
        assert snapshot.truth_level.value == "confirmed_business_truth"


class TestConcurrentTransitionRace:
    """Prompt 44's own explicit testing requirement ("test ... rollback")
    extended to the identical concurrency guarantee
    `identity/repositories/semantic_catalog.py::_apply_transition`'s own
    docstring documents finding and fixing for a real race (Prompt 27) --
    applied here to `deploy`, proven with two independent `Session`s
    (not real threads, for the identical, documented reason
    `tests/test_api_semantic_catalog.py::TestConcurrentPublish`'s own
    docstring gives: a shared in-memory SQLite connection under
    `StaticPool` is not a safe stand-in for genuine thread-level
    concurrency)."""

    def test_a_stale_concurrent_deploy_attempt_is_rejected_by_the_atomic_guard(self, engine):
        session1 = Session(engine)
        session2 = Session(engine)
        try:
            v = _approved(session1)
            version_id = v.id

            # Session 2 loads its own, independent copy of the same row --
            # exactly what a second concurrent request's own DB session
            # would have.
            v_in_session2 = gov.get_version_by_id(session2, version_id)

            # Session 1 deploys and commits first.
            gov.deploy(session1, v, actor_user_id=None)

            # Session 2's own in-memory object still says "approved" (it
            # was never refreshed) -- the in-memory pre-check alone would
            # wrongly allow this; only the atomic `WHERE stage =
            # expected_current` guard catches it.
            with pytest.raises(gov.InvalidGovernanceStageTransitionError):
                gov.deploy(session2, v_in_session2, actor_user_id=None)
        finally:
            session1.close()
            session2.close()
