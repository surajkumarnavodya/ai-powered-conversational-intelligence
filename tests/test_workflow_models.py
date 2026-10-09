"""Unit tests for workflow/models.py -- pure, no database, no HTTP.
Prompt 49 (`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`)."""

from __future__ import annotations

from workflow.models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    VALID_STATUS_TRANSITIONS,
    WorkflowEventType,
    WorkflowStatus,
    is_active_status,
    is_terminal_status,
)


class TestTransitionTable:
    def test_every_status_has_an_entry(self):
        assert set(VALID_STATUS_TRANSITIONS.keys()) == set(WorkflowStatus)

    def test_the_mission_chain_is_legal(self):
        """proposed -> approval_required -> approved -> assigned ->
        in_progress -> completed -- the exact chain the mission names."""
        chain = [
            WorkflowStatus.PROPOSED,
            WorkflowStatus.APPROVAL_REQUIRED,
            WorkflowStatus.APPROVED,
            WorkflowStatus.ASSIGNED,
            WorkflowStatus.IN_PROGRESS,
            WorkflowStatus.COMPLETED,
        ]
        for current, nxt in zip(chain, chain[1:], strict=False):
            assert nxt in VALID_STATUS_TRANSITIONS[current], f"{current} -> {nxt} must be legal"

    def test_rejection_is_only_reachable_from_approval_required(self):
        for status, targets in VALID_STATUS_TRANSITIONS.items():
            if WorkflowStatus.REJECTED in targets:
                assert status == WorkflowStatus.APPROVAL_REQUIRED

    def test_expiry_is_reachable_from_every_non_terminal_status(self):
        for status in WorkflowStatus:
            if status in TERMINAL_STATUSES:
                continue
            assert WorkflowStatus.EXPIRED in VALID_STATUS_TRANSITIONS[status]

    def test_terminal_statuses_have_no_outgoing_transition(self):
        for status in (WorkflowStatus.COMPLETED, WorkflowStatus.REJECTED, WorkflowStatus.EXPIRED):
            assert VALID_STATUS_TRANSITIONS[status] == frozenset()

    def test_cannot_skip_approval(self):
        """proposed -> approved directly is illegal -- approval is
        mandatory, never bypassable."""
        assert WorkflowStatus.APPROVED not in VALID_STATUS_TRANSITIONS[WorkflowStatus.PROPOSED]

    def test_cannot_skip_assignment_into_in_progress(self):
        assert WorkflowStatus.IN_PROGRESS not in VALID_STATUS_TRANSITIONS[WorkflowStatus.APPROVED]

    def test_a_terminal_status_can_never_be_left(self):
        for status in TERMINAL_STATUSES:
            assert VALID_STATUS_TRANSITIONS[status] == frozenset()
            assert is_terminal_status(status) is True

    def test_a_non_terminal_status_is_not_terminal(self):
        for status in (
            WorkflowStatus.PROPOSED,
            WorkflowStatus.APPROVAL_REQUIRED,
            WorkflowStatus.APPROVED,
            WorkflowStatus.ASSIGNED,
            WorkflowStatus.IN_PROGRESS,
        ):
            assert is_terminal_status(status) is False


class TestActiveStatuses:
    def test_active_and_terminal_partition_every_status(self):
        assert frozenset(WorkflowStatus) == ACTIVE_STATUSES | TERMINAL_STATUSES
        assert frozenset() == ACTIVE_STATUSES & TERMINAL_STATUSES

    def test_is_active_status_matches_the_set(self):
        for status in WorkflowStatus:
            assert is_active_status(status) == (status in ACTIVE_STATUSES)

    def test_proposed_is_active(self):
        assert is_active_status(WorkflowStatus.PROPOSED) is True

    def test_completed_is_not_active(self):
        assert is_active_status(WorkflowStatus.COMPLETED) is False


class TestWorkflowEventType:
    def test_every_mission_capability_has_an_event_type(self):
        """owners, due dates, comments, external task references,
        outcome measurement -- plus status changes themselves."""
        values = {t.value for t in WorkflowEventType}
        assert values == {
            "status_change",
            "comment",
            "owner_assigned",
            "due_date_set",
            "external_reference_set",
        }
