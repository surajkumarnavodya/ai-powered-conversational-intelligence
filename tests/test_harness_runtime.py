"""The harness runtime contract: envelope, lifecycle, budgets, cancellation.

Covers Prompt 02's required cases -- successful execution, max-step
termination, timeout, cancellation, retry exhaustion, malformed state,
repeated node execution, tool/model/database/provider failure, invalid
state transition, and cross-request state contamination.

These test the harness in isolation from the SQL pipeline on purpose.
The harness's job is to bound execution; whether a question becomes
good SQL is a different suite's question, and mixing the two would
make a budget test fail for a prompt-quality reason.
"""

from __future__ import annotations

import threading
import time
from dataclasses import FrozenInstanceError

import pytest

from agent.harness import (
    ExecutionBudgets,
    ExecutionEnvelope,
    ExecutionIdentity,
    ExecutionState,
    FailureClass,
    HarnessTerminated,
    InvalidStateTransition,
    budgets_from_settings,
    can_transition,
    check_cancelled,
    create_envelope,
    derive_child_budgets,
    envelope_from_state,
    guard_node,
    is_provider_health_signal,
    is_retryable,
    resolve_timeout,
)


def _envelope(**budget_overrides) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        identity=ExecutionIdentity(
            execution_id="exec-1", request_id="req-1", graph_name="text_to_sql"
        ),
        budgets=ExecutionBudgets(**budget_overrides),
    )


class TestEnvelopeIsServerOwned:
    """The security property the whole design rests on."""

    def test_identity_is_frozen(self):
        env = _envelope()
        with pytest.raises(FrozenInstanceError):
            env.identity.tenant_id = "attacker-tenant"  # type: ignore[misc]

    def test_budgets_are_frozen(self):
        env = _envelope(max_steps=5)
        with pytest.raises(FrozenInstanceError):
            env.budgets.max_steps = 10_000  # type: ignore[misc]

    def test_a_node_cannot_widen_its_own_step_budget(self):
        """The failure this guards: a node that returns state raising its
        own ceiling. The budget is not in the state, so there is nothing
        for a node to return."""
        env = _envelope(max_steps=2)
        state = {"harness_envelope": env}

        node = guard_node("n", lambda s: {"max_steps": 9999, "harness_max_steps": 9999})
        node(state)
        node(state)

        with pytest.raises(HarnessTerminated) as excinfo:
            node(state)
        assert excinfo.value.failure.failure_class is FailureClass.BUDGET_EXHAUSTED


class TestSuccessfulExecution:
    def test_a_clean_run_counts_steps_and_stays_running(self):
        env = _envelope(max_steps=10)
        state = {"harness_envelope": env}
        node = guard_node("retrieve_schema", lambda s: {"ok": True})

        for _ in range(3):
            assert node(state) == {"ok": True}

        assert env.step_count == 3
        assert env.state is ExecutionState.RUNNING
        assert env.failure is None

    def test_completion_is_terminal_and_maps_to_succeeded(self):
        env = _envelope()
        env.transition_to(ExecutionState.RUNNING)
        env.finish(ExecutionState.COMPLETED)

        assert env.state.is_terminal
        assert env.state.to_agent_status() == "succeeded"
        assert env.ended_at is not None


class TestStepBudget:
    def test_max_step_termination_is_deterministic(self):
        env = _envelope(max_steps=4)
        state = {"harness_envelope": env}
        node = guard_node("n", lambda s: {})

        for _ in range(4):
            node(state)
        with pytest.raises(HarnessTerminated) as excinfo:
            node(state)

        assert excinfo.value.failure.failure_class is FailureClass.BUDGET_EXHAUSTED
        assert env.state is ExecutionState.FAILED
        assert env.termination_reason == "budget_exhausted"

    def test_repeated_execution_of_one_node_is_counted(self):
        """An agent loop shows up as one node visited many times. The
        step budget stops it; this records the signal that says why."""
        env = _envelope(max_steps=50)
        state = {"harness_envelope": env}
        node = guard_node("generate_sql", lambda s: {})

        for _ in range(7):
            node(state)

        assert env.visits("generate_sql") == 7
        assert env.visits("never_ran") == 0


class TestDeadline:
    def test_an_expired_deadline_terminates_with_timed_out(self):
        env = _envelope(max_duration_seconds=0.01)
        state = {"harness_envelope": env}
        node = guard_node("n", lambda s: {})
        time.sleep(0.05)

        with pytest.raises(HarnessTerminated) as excinfo:
            node(state)

        assert excinfo.value.failure.failure_class is FailureClass.DEADLINE_EXCEEDED
        assert env.state is ExecutionState.TIMED_OUT
        assert env.state.to_agent_status() == "failed"

    def test_a_child_timeout_never_extends_the_parent_deadline(self):
        """The rule that turns independent per-call timeouts into a real
        end-to-end bound."""
        env = _envelope(max_duration_seconds=2.0)

        assert env.timeout_for(300.0) <= 2.0
        assert env.timeout_for(0.5) == pytest.approx(0.5)

    def test_a_child_timeout_is_never_zero_or_negative(self):
        """0 means 'no timeout' to several libraries -- handing one over
        would invert the control completely."""
        env = _envelope(max_duration_seconds=0.001)
        time.sleep(0.02)

        assert env.timeout_for(300.0) > 0

    def test_no_deadline_means_the_requested_timeout_is_used(self):
        env = _envelope(max_duration_seconds=None)

        assert env.timeout_for(42.0) == 42.0
        assert env.remaining_seconds() == float("inf")

    def test_resolve_timeout_is_a_no_op_when_unguarded(self):
        assert resolve_timeout(None, 300.0) == 300.0


class TestCancellation:
    def test_cancellation_stops_the_next_node(self):
        env = _envelope(max_steps=100)
        state = {"harness_envelope": env}
        node = guard_node("n", lambda s: {})
        node(state)

        env.cancel("request_deadline_exceeded")

        with pytest.raises(HarnessTerminated) as excinfo:
            node(state)
        assert excinfo.value.failure.failure_class is FailureClass.CANCELLED
        assert env.state is ExecutionState.CANCELLED
        assert env.termination_reason == "request_deadline_exceeded"

    def test_cancellation_is_idempotent_and_keeps_the_first_reason(self):
        env = _envelope()
        env.cancel("first")
        env.cancel("second")

        assert env.is_cancelled
        assert env.termination_reason == "first"

    def test_cancellation_from_another_thread_is_seen(self):
        """A real thread, because the API cancels from the request
        thread while the graph runs on a pool worker -- a sequential
        test would pass even if the flag were thread-local."""
        # The step budget is deliberately far larger than the loop can
        # reach, so the only thing that can stop this worker is the
        # cancellation flag -- otherwise the test could pass by
        # exhausting steps and prove nothing about thread visibility.
        env = _envelope(max_steps=10_000_000, max_duration_seconds=None)
        state = {"harness_envelope": env}
        node = guard_node("n", lambda s: {})
        observed: list[str] = []
        ready = threading.Event()

        def _worker() -> None:
            ready.set()
            try:
                while True:
                    node(state)
                    time.sleep(0.001)
            except HarnessTerminated as exc:
                observed.append(exc.failure.failure_class.value)

        thread = threading.Thread(target=_worker)
        thread.start()
        assert ready.wait(timeout=5)
        time.sleep(0.05)
        env.cancel()
        thread.join(timeout=10)

        assert not thread.is_alive(), "the worker never observed the cancellation"
        assert observed == ["cancelled"]
        assert env.step_count < 10_000_000, "stopped by cancellation, not by the budget"

    def test_check_cancelled_is_a_mid_node_checkpoint(self):
        env = _envelope()
        check_cancelled(env, "before_llm")  # not cancelled: no-op
        env.cancel()

        with pytest.raises(HarnessTerminated):
            check_cancelled(env, "before_llm")

    def test_check_cancelled_is_a_no_op_when_unguarded(self):
        check_cancelled(None, "anywhere")

    def test_cancellation_wins_over_an_expired_deadline(self):
        """Both are true; the operator needs to see the cancellation."""
        env = _envelope(max_duration_seconds=0.01)
        env.cancel()
        time.sleep(0.05)
        node = guard_node("n", lambda s: {})

        with pytest.raises(HarnessTerminated) as excinfo:
            node({"harness_envelope": env})
        assert excinfo.value.failure.failure_class is FailureClass.CANCELLED


class TestCallBudgets:
    def test_model_calls_are_bounded_per_execution(self):
        env = _envelope(max_llm_calls=3)
        for _ in range(3):
            env.record_llm_call()

        with pytest.raises(HarnessTerminated) as excinfo:
            env.record_llm_call()
        assert excinfo.value.failure.failure_class is FailureClass.BUDGET_EXHAUSTED

    def test_tool_calls_are_bounded_per_execution(self):
        env = _envelope(max_tool_calls=2)
        env.record_tool_call()
        env.record_tool_call()

        with pytest.raises(HarnessTerminated):
            env.record_tool_call()

    def test_a_token_budget_terminates_when_exceeded(self):
        env = _envelope(max_llm_calls=10, max_tokens=100)
        env.record_llm_call(tokens=60)

        with pytest.raises(HarnessTerminated):
            env.record_llm_call(tokens=60)

    def test_no_token_budget_means_tokens_are_counted_but_not_enforced(self):
        env = _envelope(max_llm_calls=10, max_tokens=None)
        for _ in range(5):
            env.record_llm_call(tokens=1_000_000)

        assert env.token_estimate == 5_000_000
        assert env.state is not ExecutionState.FAILED

    def test_retry_budget_is_reported_not_raised(self):
        """The caller is a retry loop that wants to fall through to its
        own handling, not unwind."""
        env = _envelope(max_retries=2)
        assert not env.retry_budget_exhausted
        env.record_retry()
        env.record_retry()
        assert env.retry_budget_exhausted


class TestLifecycle:
    def test_an_invalid_transition_raises(self):
        env = _envelope()
        env.transition_to(ExecutionState.RUNNING)
        env.transition_to(ExecutionState.COMPLETED)

        with pytest.raises(InvalidStateTransition):
            env.transition_to(ExecutionState.RUNNING)

    def test_a_terminal_state_has_no_outgoing_edges(self):
        for terminal in (
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        ):
            for target in ExecutionState:
                assert not can_transition(terminal, target), f"{terminal} -> {target}"

    def test_any_running_state_may_be_cancelled_or_time_out(self):
        for source in ExecutionState:
            if source.is_terminal:
                continue
            assert can_transition(source, ExecutionState.CANCELLED), source
            assert can_transition(source, ExecutionState.TIMED_OUT), source

    def test_retrying_returns_to_running_not_straight_to_a_result(self):
        assert can_transition(ExecutionState.RETRYING, ExecutionState.RUNNING)
        assert not can_transition(ExecutionState.RETRYING, ExecutionState.COMPLETED)

    def test_finish_tolerates_an_already_terminal_envelope(self):
        """A deadline can fire as the last node succeeds. The first
        terminal state wins; a race must not become a 500."""
        env = _envelope()
        env.transition_to(ExecutionState.RUNNING)
        env.finish(ExecutionState.COMPLETED)
        env.finish(ExecutionState.TIMED_OUT)

        assert env.state is ExecutionState.COMPLETED


class TestFailureClassification:
    @pytest.mark.parametrize(
        "failure",
        [
            FailureClass.MODEL_TIMEOUT,
            FailureClass.MODEL_UNAVAILABLE,
            FailureClass.TOOL_TIMEOUT,
            FailureClass.TOOL_FAILED,
            FailureClass.DATABASE_TIMEOUT,
            FailureClass.DATABASE_UNAVAILABLE,
        ],
    )
    def test_transient_failures_are_retryable(self, failure):
        assert is_retryable(failure)

    @pytest.mark.parametrize(
        "failure",
        [
            FailureClass.AUTHORIZATION_DENIED,
            FailureClass.VALIDATION_REJECTED,
            FailureClass.SQL_DETERMINISTIC,
            FailureClass.BUDGET_EXHAUSTED,
            FailureClass.DEADLINE_EXCEEDED,
            FailureClass.CANCELLED,
            FailureClass.INVALID_STATE,
            FailureClass.UNKNOWN,
        ],
    )
    def test_these_are_never_retried(self, failure):
        assert not is_retryable(failure)

    def test_an_unrecognized_failure_defaults_to_not_retrying(self):
        """A new class retried three times is a retry storm; surfaced
        once is a bug report."""
        assert not is_retryable(FailureClass.UNKNOWN)

    def test_only_provider_health_signals_can_open_a_circuit(self):
        assert is_provider_health_signal(FailureClass.MODEL_UNAVAILABLE)
        assert is_provider_health_signal(FailureClass.MODEL_TIMEOUT)
        # A malformed answer says nothing about the provider's health --
        # counting it would let one awkward question disable a model.
        assert not is_provider_health_signal(FailureClass.MODEL_MALFORMED_OUTPUT)
        assert not is_provider_health_signal(FailureClass.AUTHORIZATION_DENIED)


class TestMalformedAndUnguardedState:
    @pytest.mark.parametrize("state", [None, "not a dict", 42, [], object()])
    def test_a_malformed_state_is_simply_unguarded_not_a_crash(self, state):
        assert envelope_from_state(state) is None

    def test_a_state_without_an_envelope_runs_unguarded(self):
        node = guard_node("n", lambda s: {"ran": True})
        assert node({}) == {"ran": True}

    def test_a_non_envelope_value_under_the_key_is_ignored(self):
        """Defends the harness against its own state key being set to
        junk, rather than trusting it blindly."""
        assert envelope_from_state({"harness_envelope": "pwn"}) is None

    def test_the_node_exception_is_not_swallowed(self):
        """Hiding a node's real error behind a harness message would
        make every bug look like a harness bug."""

        def _boom(_state):
            raise ValueError("the node's own problem")

        with pytest.raises(ValueError, match="the node's own problem"):
            guard_node("n", _boom)({"harness_envelope": _envelope()})


class TestCrossRequestIsolation:
    def test_two_envelopes_do_not_share_counters(self):
        a, b = _envelope(max_steps=10), _envelope(max_steps=10)
        node = guard_node("n", lambda s: {})

        node({"harness_envelope": a})
        node({"harness_envelope": a})
        node({"harness_envelope": b})

        assert (a.step_count, b.step_count) == (2, 1)

    def test_cancelling_one_execution_does_not_cancel_another(self):
        a, b = _envelope(), _envelope()
        a.cancel()

        assert a.is_cancelled
        assert not b.is_cancelled

    def test_each_created_envelope_gets_a_distinct_execution_id(self):
        ids = {create_envelope(graph_name="g").identity.execution_id for _ in range(200)}
        assert len(ids) == 200

    def test_one_tenants_envelope_never_carries_anothers_identity(self):
        a = create_envelope(graph_name="g", tenant_id="tenant-a")
        b = create_envelope(graph_name="g", tenant_id="tenant-b")

        assert a.identity.tenant_id == "tenant-a"
        assert b.identity.tenant_id == "tenant-b"
        assert a.identity.execution_id != b.identity.execution_id


class TestChildBudgets:
    def test_a_child_may_lower_a_limit_but_never_raise_one(self):
        parent = _envelope(max_steps=20, max_duration_seconds=100.0)

        tightened = derive_child_budgets(parent, max_steps=5)
        widened = derive_child_budgets(parent, max_steps=9999)

        assert tightened.max_steps == 5
        assert widened.max_steps == 20

    def test_a_child_inherits_only_the_time_that_is_left(self):
        parent = _envelope(max_duration_seconds=5.0)
        time.sleep(0.05)

        child = derive_child_budgets(parent)

        assert child.max_duration_seconds is not None
        assert child.max_duration_seconds < 5.0


class TestTelemetrySnapshot:
    def test_a_snapshot_carries_identifiers_and_counts_only(self):
        env = create_envelope(
            graph_name="text_to_sql", tenant_id="t1", caller_roles=("analyst", "admin")
        )
        env.transition_to(ExecutionState.RUNNING)
        env.record_llm_call(tokens=120)

        snapshot = env.snapshot()
        flat = str(snapshot)

        assert snapshot["llm_call_count"] == 1
        assert snapshot["token_estimate"] == 120
        assert "analyst" not in flat, "roles are authorization context, not telemetry"
        assert "admin" not in flat

    def test_a_snapshot_is_json_safe(self):
        import json

        json.dumps(create_envelope(graph_name="g").snapshot())


class TestSettingsWiring:
    def test_budgets_come_from_settings(self):
        from config.settings import Settings

        budgets = budgets_from_settings(Settings())

        assert budgets.max_steps == Settings().harness_max_steps
        assert budgets.max_duration_seconds == float(Settings().request_timeout_seconds)

    def test_a_zero_token_setting_means_no_budget_not_a_zero_budget(self):
        from config.settings import Settings

        assert budgets_from_settings(Settings(harness_max_tokens=0)).max_tokens is None
        assert budgets_from_settings(Settings(harness_max_tokens=500)).max_tokens == 500
