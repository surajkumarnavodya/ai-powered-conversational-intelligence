"""Repository tests for identity/repositories/workflow.py -- Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`). Pure SQLAlchemy
against a real in-memory SQLite identity DB, no HTTP, no mocks of the
ORM (the same real-engine pattern
`tests/test_recommendation_actions_repository.py` establishes).

The retry/race tests interleave two `Session`s deterministically rather
than running real OS threads -- the same documented reason Prompt 27's
`TestConcurrentPublish` and Prompt 31's own race test use this shape
(SQLite's `:memory:` database shares one connection via `StaticPool`,
which isn't safe for genuinely concurrent statement execution). The
property under test (the database's conditional `UPDATE` is the single
point of truth, so a retried/raced request never double-applies) is the
same either way.
"""

from __future__ import annotations

import uuid

import pytest
from identity.bootstrap import seed_rbac
from identity.models import Base, RecommendationRecord, Workflow
from identity.repositories.recommendation_governance import create_record, submit_feedback
from identity.repositories.tenants import create_tenant
from identity.repositories.users import assign_role, create_user
from identity.repositories.workflow import (
    DuplicateActiveWorkflowError,
    InvalidWorkflowOwnerError,
    InvalidWorkflowStatusTransitionError,
    RecommendationNotEligibleError,
    add_comment,
    approve_workflow,
    assign_workflow,
    complete_workflow,
    create_workflow,
    expire_workflow,
    get_workflow_by_id,
    list_workflow_events,
    list_workflows,
    owner_display_names,
    reject_workflow,
    set_due_date,
    set_external_reference,
    start_workflow,
    submit_for_approval,
)
from recommendation.governance import RecommendationStatus
from recommendation.models import Recommendation, RecommendationCategory, RecommendationKind
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.provenance import DataTruthLevel, ProvenancedClaim


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    setup = factory()
    seed_rbac(setup)
    create_tenant(setup, tenant_id="tenant-a", name="A")
    create_tenant(setup, tenant_id="tenant-b", name="B")
    setup.commit()
    setup.close()
    yield factory
    engine.dispose()


def _user(session, email: str, *, tenant_id: str, role: str | None = None, status: str = "active"):
    user = create_user(
        session, email=email, password="correcthorse battery staple 1", tenant_id=tenant_id
    )
    if status != "active":
        user.status = status
    if role is not None:
        assign_role(session, user_id=user.id, role_name=role, assigned_by_user_id=None)
    session.commit()
    return user.id


def _recommendation() -> Recommendation:
    return Recommendation(
        kind=RecommendationKind.ACTION,
        claim=ProvenancedClaim(
            value="Investigate the anomalous spike.",
            level=DataTruthLevel.AI_INFERENCE,
            source="recommendation.engine.AnomalyRule",
        ),
        category=RecommendationCategory.ANOMALY,
        evidence=(
            ProvenancedClaim(
                value="2024 flagged.", level=DataTruthLevel.DATABASE_FACT, source="analytics.engine"
            ),
        ),
        confidence=0.8,
        rule_or_model="recommendation.engine.AnomalyRule",
        affected_entity="2024",
        action="Investigate the root cause with the finance team.",
        limitations=("Statistical signal, not a confirmed cause.",),
    )


def _accepted_recommendation(
    session, *, tenant_id="tenant-a", database_id="hr", actor_user_id=None
) -> RecommendationRecord:
    """An `ACCEPTED` recommendation -- the only status eligible for
    `create_workflow`."""
    record = create_record(
        session,
        tenant_id=tenant_id,
        database_id=database_id,
        recommendation=_recommendation(),
        created_by_user_id=actor_user_id,
    )
    return submit_feedback(
        session,
        record,
        target_status=RecommendationStatus.ACCEPTED,
        actor_user_id=actor_user_id,
    )


class TestCreateWorkflow:
    def test_creates_in_proposed_status(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        assert workflow.status == "proposed"
        assert workflow.tenant_id == "tenant-a"
        assert workflow.recommendation_id == recommendation.id

    def test_title_and_description_default_from_the_recommendation(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        assert workflow.title == recommendation.claim_text
        assert workflow.description == recommendation.action

    def test_caller_may_override_title_and_description(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(
            session,
            recommendation=recommendation,
            created_by_user_id=actor,
            title="Custom title",
            description="Custom description",
        )
        assert workflow.title == "Custom title"
        assert workflow.description == "Custom description"

    def test_records_an_initial_audit_event(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        events = list_workflow_events(session, workflow.id)
        assert len(events) == 1
        assert events[0].from_status is None
        assert events[0].to_status == "proposed"
        assert events[0].actor_user_id == actor

    def test_a_generated_recommendation_is_not_eligible(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = create_record(
            session,
            tenant_id="tenant-a",
            database_id="hr",
            recommendation=_recommendation(),
            created_by_user_id=actor,
        )
        with pytest.raises(RecommendationNotEligibleError):
            create_workflow(session, recommendation=recommendation, created_by_user_id=actor)

    def test_a_rejected_recommendation_is_not_eligible(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        record = create_record(
            session,
            tenant_id="tenant-a",
            database_id="hr",
            recommendation=_recommendation(),
            created_by_user_id=actor,
        )
        rejected = submit_feedback(
            session, record, target_status=RecommendationStatus.REJECTED, actor_user_id=actor
        )
        with pytest.raises(RecommendationNotEligibleError):
            create_workflow(session, recommendation=rejected, created_by_user_id=actor)

    def test_a_second_active_workflow_for_the_same_recommendation_is_refused(self, session_factory):
        """The mission's own 'test ... duplicate events' requirement:
        proposing a second workflow while one is already in flight is
        refused, not silently allowed to coexist."""
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        with pytest.raises(DuplicateActiveWorkflowError):
            create_workflow(session, recommendation=recommendation, created_by_user_id=actor)

    def test_a_fresh_workflow_is_allowed_after_the_first_one_is_rejected(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        first = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        first = submit_for_approval(session, first, actor_user_id=actor)
        reject_workflow(session, first, actor_user_id=reviewer)

        second = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        assert second.id != first.id
        assert second.status == "proposed"


class TestLifecycleTransitions:
    def _proposed(self, session, actor):
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        return create_workflow(session, recommendation=recommendation, created_by_user_id=actor)

    def test_full_happy_path(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        owner = _user(session, "o@x.example", tenant_id="tenant-a", role="admin")

        workflow = self._proposed(session, actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        assert workflow.status == "approval_required"

        workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        assert workflow.status == "approved"

        workflow = assign_workflow(session, workflow, owner_user_id=owner, actor_user_id=reviewer)
        assert workflow.status == "assigned"
        assert workflow.owner_user_id == owner

        workflow = start_workflow(session, workflow, actor_user_id=owner)
        assert workflow.status == "in_progress"

        workflow = complete_workflow(
            session,
            workflow,
            actor_user_id=owner,
            outcome_summary="Root cause found and fixed.",
            outcome_measured_impact={"metric": "revenue", "delta_percent": 4.2},
        )
        assert workflow.status == "completed"
        assert workflow.outcome_summary == "Root cause found and fixed."
        assert workflow.outcome_measured_impact == {"metric": "revenue", "delta_percent": 4.2}

        events = list_workflow_events(session, workflow.id)
        status_changes = [e for e in events if e.event_type == "status_change"]
        assert [e.to_status for e in status_changes] == [
            "proposed",
            "approval_required",
            "approved",
            "in_progress",
            "completed",
        ]

    def test_approval_is_mandatory_before_assignment(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        workflow = self._proposed(session, actor)
        # Still "proposed" -- assign must be refused.
        with pytest.raises(InvalidWorkflowStatusTransitionError):
            assign_workflow(session, workflow, owner_user_id=actor, actor_user_id=actor)

    def test_rejection_is_terminal(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        workflow = self._proposed(session, actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow = reject_workflow(session, workflow, actor_user_id=reviewer)
        assert workflow.status == "rejected"
        with pytest.raises(InvalidWorkflowStatusTransitionError):
            approve_workflow(session, workflow, actor_user_id=reviewer)

    @pytest.mark.parametrize(
        "starting_status",
        ["proposed", "approval_required", "approved", "assigned", "in_progress"],
    )
    def test_expiry_is_reachable_from_every_non_terminal_status(
        self, session_factory, starting_status
    ):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        owner = _user(session, "o@x.example", tenant_id="tenant-a", role="admin")
        workflow = self._proposed(session, actor)
        if starting_status in ("approval_required", "approved", "assigned", "in_progress"):
            workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        if starting_status in ("approved", "assigned", "in_progress"):
            workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        if starting_status in ("assigned", "in_progress"):
            workflow = assign_workflow(
                session, workflow, owner_user_id=owner, actor_user_id=reviewer
            )
        if starting_status == "in_progress":
            workflow = start_workflow(session, workflow, actor_user_id=owner)
        assert workflow.status == starting_status
        workflow = expire_workflow(session, workflow, actor_user_id=actor)
        assert workflow.status == "expired"


class TestRetrySafety:
    """The mission's own 'test ... retries' requirement: a client
    retrying an already-succeeded request must get a clean 409, never a
    duplicated status change or a second audit event."""

    def test_retrying_the_same_transition_is_refused_and_writes_no_second_event(
        self, session_factory
    ):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)

        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        assert workflow.status == "approval_required"

        # A retried client call for the identical transition -- the
        # workflow has already moved on, so this must be refused, not
        # silently re-applied. With this same in-memory object already
        # reflecting the first call's success, the refusal surfaces at
        # the transition-table check itself (there is no
        # `approval_required -> approval_required` edge) -- a genuine
        # cross-session race instead surfaces at the conditional
        # `UPDATE` layer (see `TestRetrySafety
        # ::test_a_stale_second_approve_is_rejected_by_the_conditional_update`
        # below), but either way the same exception type is raised and
        # no second event is ever written.
        with pytest.raises(InvalidWorkflowStatusTransitionError):
            submit_for_approval(session, workflow, actor_user_id=actor)

        events = list_workflow_events(session, workflow.id)
        approval_required_events = [e for e in events if e.to_status == "approval_required"]
        assert len(approval_required_events) == 1

    def test_a_stale_second_approve_is_rejected_by_the_conditional_update(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow_id = workflow.id
        session.close()

        # Two sessions both load the workflow while it is
        # "approval_required".
        first = session_factory()
        second = session_factory()
        first_workflow = first.get(Workflow, workflow_id)
        second_workflow = second.get(Workflow, workflow_id)
        assert first_workflow.status == second_workflow.status == "approval_required"

        approve_workflow(first, first_workflow, actor_user_id=reviewer)

        # The second caller's in-memory status is stale
        # ("approval_required"), so the Python-side transition check
        # passes -- only the database's conditional UPDATE can refuse it.
        with pytest.raises(InvalidWorkflowStatusTransitionError, match="no longer"):
            approve_workflow(second, second_workflow, actor_user_id=reviewer)
        second.close()

        check = session_factory()
        try:
            events = list_workflow_events(check, workflow_id)
            approved_events = [e for e in events if e.to_status == "approved"]
            assert len(approved_events) == 1, "exactly one caller's transition may be recorded"
            assert check.get(Workflow, workflow_id).status == "approved"
        finally:
            check.close()
        first.close()

    def test_a_refused_transition_writes_no_event(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        before = len(list_workflow_events(session, workflow.id))
        with pytest.raises(InvalidWorkflowStatusTransitionError):
            # Still "proposed" -- approve is illegal from here.
            approve_workflow(session, workflow, actor_user_id=actor)
        after = len(list_workflow_events(session, workflow.id))
        assert before == after


class TestCommentsNeverChangeStatus:
    def test_a_comment_never_changes_status(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        add_comment(session, workflow, actor_user_id=actor, comment="Looking into this.")
        session.refresh(workflow)
        assert workflow.status == "proposed"

    def test_comments_are_recorded_in_order(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        add_comment(session, workflow, actor_user_id=actor, comment="First.")
        add_comment(session, workflow, actor_user_id=actor, comment="Second.")
        comments = [
            e for e in list_workflow_events(session, workflow.id) if e.event_type == "comment"
        ]
        assert [c.reason for c in comments] == ["First.", "Second."]


class TestOwnerAssignment:
    def test_a_same_tenant_manager_can_be_assigned(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        owner = _user(session, "o@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        workflow = assign_workflow(session, workflow, owner_user_id=owner, actor_user_id=reviewer)
        assert workflow.owner_user_id == owner
        assert workflow.status == "assigned"

    def test_a_cross_tenant_owner_is_refused_with_the_same_error_as_unknown(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        other_tenant_user = _user(session, "b@y.example", tenant_id="tenant-b", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        with pytest.raises(InvalidWorkflowOwnerError):
            assign_workflow(
                session, workflow, owner_user_id=other_tenant_user, actor_user_id=reviewer
            )
        with pytest.raises(InvalidWorkflowOwnerError):
            assign_workflow(session, workflow, owner_user_id=uuid.uuid4(), actor_user_id=reviewer)

    def test_a_disabled_owner_is_refused(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        disabled = _user(
            session, "d@x.example", tenant_id="tenant-a", role="admin", status="suspended"
        )
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        with pytest.raises(InvalidWorkflowOwnerError):
            assign_workflow(session, workflow, owner_user_id=disabled, actor_user_id=reviewer)

    def test_a_non_workflow_capable_owner_is_refused(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        plain_user = _user(session, "p@x.example", tenant_id="tenant-a", role="user")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        with pytest.raises(InvalidWorkflowOwnerError):
            assign_workflow(session, workflow, owner_user_id=plain_user, actor_user_id=reviewer)

    def test_reassignment_from_in_progress_does_not_change_status(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        owner_1 = _user(session, "o1@x.example", tenant_id="tenant-a", role="admin")
        owner_2 = _user(session, "o2@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        workflow = assign_workflow(session, workflow, owner_user_id=owner_1, actor_user_id=reviewer)
        workflow = start_workflow(session, workflow, actor_user_id=owner_1)
        workflow = assign_workflow(session, workflow, owner_user_id=owner_2, actor_user_id=reviewer)
        assert workflow.status == "in_progress"
        assert workflow.owner_user_id == owner_2

    def test_records_previous_owner_in_the_event(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        owner_1 = _user(session, "o1@x.example", tenant_id="tenant-a", role="admin")
        owner_2 = _user(session, "o2@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = submit_for_approval(session, workflow, actor_user_id=actor)
        workflow = approve_workflow(session, workflow, actor_user_id=reviewer)
        workflow = assign_workflow(session, workflow, owner_user_id=owner_1, actor_user_id=reviewer)
        workflow = assign_workflow(session, workflow, owner_user_id=owner_2, actor_user_id=reviewer)
        events = [
            e
            for e in list_workflow_events(session, workflow.id)
            if e.event_type == "owner_assigned"
        ]
        assert events[-1].detail["previous_owner_user_id"] == str(owner_1)
        assert events[-1].detail["owner_user_id"] == str(owner_2)


class TestDueDateAndExternalReference:
    def test_set_due_date_never_changes_status(self, session_factory):
        from datetime import UTC, datetime

        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        due = datetime(2026, 12, 31, tzinfo=UTC)
        workflow = set_due_date(session, workflow, actor_user_id=actor, due_date=due)
        assert workflow.status == "proposed"
        # SQLite has no real timezone-aware datetime type -- the value
        # round-trips naive, same as every other `DateTime(timezone=True)`
        # column read back against this test suite's in-memory SQLite.
        assert workflow.due_date.replace(tzinfo=UTC) == due

    def test_set_external_reference_never_changes_status(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = set_external_reference(
            session,
            workflow,
            actor_user_id=actor,
            external_system="jira",
            external_reference_id="OPS-123",
            external_reference_url="https://example.atlassian.net/browse/OPS-123",
        )
        assert workflow.status == "proposed"
        assert workflow.external_system == "jira"
        assert workflow.external_reference_id == "OPS-123"

    def test_clearing_the_external_reference_removes_it(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        recommendation = _accepted_recommendation(session, actor_user_id=actor)
        workflow = create_workflow(session, recommendation=recommendation, created_by_user_id=actor)
        workflow = set_external_reference(
            session,
            workflow,
            actor_user_id=actor,
            external_system="jira",
            external_reference_id="OPS-123",
            external_reference_url="https://example.atlassian.net/browse/OPS-123",
        )
        workflow = set_external_reference(
            session,
            workflow,
            actor_user_id=actor,
            external_system=None,
            external_reference_id=None,
            external_reference_url=None,
        )
        assert workflow.external_system is None
        assert workflow.external_reference_id is None


class TestListAndLookup:
    def test_list_filters_by_status_and_owner(self, session_factory):
        session = session_factory()
        actor = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        reviewer = _user(session, "r@x.example", tenant_id="tenant-a", role="analyst")
        owner = _user(session, "o@x.example", tenant_id="tenant-a", role="admin")

        rec_1 = _accepted_recommendation(session, actor_user_id=actor)
        wf_1 = create_workflow(session, recommendation=rec_1, created_by_user_id=actor)

        rec_2 = _accepted_recommendation(session, actor_user_id=actor)
        wf_2 = create_workflow(session, recommendation=rec_2, created_by_user_id=actor)
        wf_2 = submit_for_approval(session, wf_2, actor_user_id=actor)
        wf_2 = approve_workflow(session, wf_2, actor_user_id=reviewer)
        wf_2 = assign_workflow(session, wf_2, owner_user_id=owner, actor_user_id=reviewer)

        proposed_only = list_workflows(session, "tenant-a", status="proposed")
        assert {w.id for w in proposed_only} == {wf_1.id}

        owned_by = list_workflows(session, "tenant-a", owner_user_id=owner)
        assert {w.id for w in owned_by} == {wf_2.id}

        unassigned = list_workflows(session, "tenant-a", unassigned=True)
        assert {w.id for w in unassigned} == {wf_1.id}

    def test_list_is_scoped_to_tenant(self, session_factory):
        session = session_factory()
        actor_a = _user(session, "a@x.example", tenant_id="tenant-a", role="admin")
        actor_b = _user(session, "b@y.example", tenant_id="tenant-b", role="admin")
        rec_a = _accepted_recommendation(session, tenant_id="tenant-a", actor_user_id=actor_a)
        rec_b = _accepted_recommendation(session, tenant_id="tenant-b", actor_user_id=actor_b)
        create_workflow(session, recommendation=rec_a, created_by_user_id=actor_a)
        create_workflow(session, recommendation=rec_b, created_by_user_id=actor_b)

        a_only = list_workflows(session, "tenant-a")
        assert len(a_only) == 1
        assert a_only[0].tenant_id == "tenant-a"

    def test_get_by_id_returns_none_for_unknown(self, session_factory):
        session = session_factory()
        assert get_workflow_by_id(session, uuid.uuid4()) is None

    def test_owner_display_names_empty_input(self, session_factory):
        session = session_factory()
        assert owner_display_names(session, set()) == {}
