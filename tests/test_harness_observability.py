"""Harness telemetry and the harness evaluation suite.

Two concerns, deliberately in one file because they share a fixture:

1. **Telemetry is correct and carries no content.** The second half of
   that is the one that matters -- a metrics store that quietly
   accumulates questions or rows is a data-retention incident wearing
   an observability badge.
2. **The 12 evaluation scenarios Prompt 04 lists** -- normal, complex,
   multi-step, tool failure, model failure, database failure, timeout,
   cancellation, retry, recovery, long context, concurrent requests --
   each driven through the real control boundary rather than asserted
   against a mock.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from agent.harness import (
    ExecutionBudgets,
    ExecutionEnvelope,
    ExecutionIdentity,
    ExecutionState,
    FailureClass,
    HarnessTerminated,
    create_envelope,
    decide_retry,
    enforce_text_limit,
    guard_node,
    record_outcome,
    reset_breakers,
)
from agent.harness.telemetry import HarnessTelemetry, get_harness_telemetry

#: Content that must never appear in telemetry, whatever path it took.
_SECRETS = [
    "SELECT salary FROM employees",
    "how much does Ada earn?",
    "sk-live-abcdef",
    "187431.55",
    "analyst",
]


@pytest.fixture
def telemetry() -> HarnessTelemetry:
    return HarnessTelemetry(window=50)


@pytest.fixture(autouse=True)
def _clean_global_state():
    get_harness_telemetry().reset()
    reset_breakers()
    yield
    get_harness_telemetry().reset()
    reset_breakers()


def _envelope(**budgets) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        identity=ExecutionIdentity(
            execution_id="e1",
            request_id="r1",
            graph_name="text_to_sql",
            tenant_id="tenant-a",
            caller_roles=("analyst", "admin"),
        ),
        budgets=ExecutionBudgets(**budgets),
    )


class TestTelemetryCarriesNoContent:
    def test_an_execution_record_contains_no_question_sql_or_role(self, telemetry):
        env = _envelope()
        env.transition_to(ExecutionState.RUNNING)
        env.finish(ExecutionState.COMPLETED)

        telemetry.record_execution(env)
        flat = json.dumps(telemetry.snapshot()) + json.dumps(telemetry.recent_executions())

        for secret in _SECRETS:
            assert secret not in flat, f"{secret!r} leaked into telemetry"

    def test_roles_are_excluded_even_though_they_are_not_secret(self, telemetry):
        """They are authorization context. An execution record carrying
        them invites a reader to treat telemetry as an access-control
        audit trail, which security/audit_log.py already is."""
        env = _envelope()
        telemetry.record_execution(env)

        assert "analyst" not in json.dumps(telemetry.recent_executions())

    def test_the_whole_snapshot_is_json_serializable(self, telemetry):
        env = _envelope()
        env.transition_to(ExecutionState.RUNNING)
        env.finish(ExecutionState.COMPLETED)
        telemetry.record_execution(env)
        telemetry.record_node("generate_sql", 12.0)
        telemetry.record_tool("sql_tool", 5.0, failed=True)
        telemetry.record_provider("ollama", 900.0, timed_out=True)
        telemetry.record_security_event("authorization_denied")

        json.dumps(telemetry.snapshot())

    def test_a_security_event_records_the_kind_not_the_content(self, telemetry):
        telemetry.record_security_event("restricted_column_blocked")

        security = telemetry.snapshot()["security"]
        assert security == {"restricted_column_blocked": 1}


class TestTelemetryIsBounded:
    def test_the_execution_window_is_capped(self, telemetry):
        for _ in range(500):
            telemetry.record_execution(create_envelope(graph_name="g"))

        assert telemetry.snapshot()["executions_recorded"] == 50

    def test_node_latency_windows_are_capped(self, telemetry):
        for i in range(5_000):
            telemetry.record_node("n", float(i))

        node = telemetry.snapshot()["nodes"]["n"]
        assert node["calls"] == 5_000
        assert node["p99_ms"] > 0

    def test_aggregate_keys_come_from_code_not_user_input(self, telemetry):
        """Node and provider names are code constants. If they were
        request-derived, an attacker could grow this dict without
        bound -- memory exhaustion, not untidiness."""
        telemetry.record_node("generate_sql", 1.0)

        assert set(telemetry.snapshot()["nodes"]) == {"generate_sql"}


class TestReliabilityAndEfficiencyMetrics:
    def _finished(self, state: ExecutionState, **counts) -> ExecutionEnvelope:
        env = _envelope(max_llm_calls=100, max_tool_calls=100)
        env.transition_to(ExecutionState.RUNNING)
        for _ in range(counts.get("llm", 0)):
            env.record_llm_call(tokens=10)
        for _ in range(counts.get("tool", 0)):
            env.record_tool_call()
        for _ in range(counts.get("retry", 0)):
            env.record_retry()
        env.finish(state)
        return env

    def test_rates_are_computed_over_the_window(self, telemetry):
        telemetry.record_execution(self._finished(ExecutionState.COMPLETED))
        telemetry.record_execution(self._finished(ExecutionState.COMPLETED))
        telemetry.record_execution(self._finished(ExecutionState.FAILED))
        telemetry.record_execution(self._finished(ExecutionState.TIMED_OUT))

        reliability = telemetry.snapshot()["reliability"]
        assert reliability["success_rate"] == 0.5
        assert reliability["failure_rate"] == 0.25
        assert reliability["timeout_rate"] == 0.25

    def test_efficiency_reports_average_model_and_tool_calls(self, telemetry):
        telemetry.record_execution(self._finished(ExecutionState.COMPLETED, llm=4, tool=2))
        telemetry.record_execution(self._finished(ExecutionState.COMPLETED, llm=6, tool=4))

        efficiency = telemetry.snapshot()["efficiency"]
        assert efficiency["avg_model_calls"] == 5.0
        assert efficiency["avg_tool_calls"] == 3.0
        assert efficiency["avg_token_estimate"] == 50.0

    def test_retry_rate_counts_executions_that_retried_not_total_retries(self, telemetry):
        telemetry.record_execution(self._finished(ExecutionState.COMPLETED, retry=3))
        telemetry.record_execution(self._finished(ExecutionState.COMPLETED))

        assert telemetry.snapshot()["reliability"]["retry_rate"] == 0.5

    def test_an_empty_store_reports_zeroes_not_a_division_error(self, telemetry):
        snapshot = telemetry.snapshot()

        assert snapshot["executions_recorded"] == 0
        assert snapshot["reliability"]["success_rate"] == 0.0
        assert snapshot["performance"]["p99_ms"] == 0.0

    def test_harness_health_counts_each_termination_kind(self, telemetry):
        env = _envelope(max_steps=1)
        node = guard_node("n", lambda s: {})
        node({"harness_envelope": env})
        with pytest.raises(HarnessTerminated):
            node({"harness_envelope": env})
        telemetry.record_execution(env)

        assert telemetry.snapshot()["harness_health"]["budget_terminations"] == 1


class TestEndToEndTraceability:
    def test_one_execution_can_be_followed_by_its_execution_id(self, telemetry):
        env = create_envelope(
            graph_name="text_to_sql",
            request_id="req-42",
            correlation_id="corr-42",
            conversation_id="conv-42",
            tenant_id="tenant-a",
        )
        env.transition_to(ExecutionState.RUNNING)
        env.finish(ExecutionState.COMPLETED)
        telemetry.record_execution(env)

        record = telemetry.recent_executions()[-1]
        assert record["execution_id"] == env.identity.execution_id
        assert record["request_id"] == "req-42"
        assert record["correlation_id"] == "corr-42"
        assert record["conversation_id"] == "conv-42"

    def test_the_node_breakdown_names_where_time_went(self, telemetry):
        for name, ms in (("retrieve_schema", 30.0), ("generate_sql", 900.0)):
            telemetry.record_node(name, ms)

        nodes = telemetry.snapshot()["nodes"]
        assert nodes["generate_sql"]["avg_ms"] > nodes["retrieve_schema"]["avg_ms"]

    def test_a_failing_node_still_appears_in_the_breakdown(self):
        """A node missing from telemetry precisely when it is broken is
        the opposite of useful."""
        store = get_harness_telemetry()

        def _boom(_state):
            raise ValueError("node failed")

        with pytest.raises(ValueError):
            guard_node("broken_node", _boom)({"harness_envelope": _envelope()})

        node = store.snapshot()["nodes"]["broken_node"]
        assert node["calls"] == 1
        assert node["failures"] == 1


class TestHarnessEvaluationScenarios:
    """Prompt 04's 12 scenarios, each through the real control boundary."""

    def test_01_normal_query(self):
        env = _envelope()
        node = guard_node("n", lambda s: {"status": "succeeded"})

        assert node({"harness_envelope": env})["status"] == "succeeded"
        assert env.step_count == 1

    def test_02_complex_query_uses_more_budget_but_completes(self):
        env = _envelope(max_steps=30)
        state = {"harness_envelope": env}
        node = guard_node("n", lambda s: {})
        for _ in range(18):
            node(state)

        env.finish(ExecutionState.COMPLETED)
        assert env.step_count == 18
        assert env.state is ExecutionState.COMPLETED

    def test_03_multi_step_execution_counts_every_distinct_node(self):
        env = _envelope(max_steps=30)
        state = {"harness_envelope": env}
        for name in ("retrieve_schema", "generate_sql", "validate_sql", "execute_sql"):
            guard_node(name, lambda s: {})(state)

        assert env.step_count == 4
        assert env.visits("generate_sql") == 1

    def test_04_tool_failure_is_classified_and_bounded(self):
        decision = decide_retry(failure=FailureClass.TOOL_FAILED, attempt=1)
        assert decision.should_retry
        assert not decide_retry(failure=FailureClass.TOOL_FAILED, attempt=99).should_retry

    def test_05_model_failure_trips_the_breaker_after_a_threshold(self):
        from agent.harness import ProviderUnavailable, check_provider

        for _ in range(5):
            record_outcome("ollama", FailureClass.MODEL_UNAVAILABLE)
        with pytest.raises(ProviderUnavailable):
            check_provider("ollama")

    def test_06_database_failure_is_retryable_but_on_a_tighter_policy(self):
        from agent.harness import DATABASE_RETRY_POLICY

        assert decide_retry(
            failure=FailureClass.DATABASE_TIMEOUT, attempt=1, policy=DATABASE_RETRY_POLICY
        ).should_retry
        assert not decide_retry(
            failure=FailureClass.DATABASE_TIMEOUT, attempt=2, policy=DATABASE_RETRY_POLICY
        ).should_retry

    def test_07_timeout_terminates_and_is_recorded(self):
        store = get_harness_telemetry()
        env = _envelope(max_duration_seconds=0.01)
        time.sleep(0.05)
        with pytest.raises(HarnessTerminated):
            guard_node("n", lambda s: {})({"harness_envelope": env})
        store.record_execution(env)

        assert store.snapshot()["terminations"]["deadline_exceeded"] == 1

    def test_08_cancellation_terminates_and_is_recorded(self):
        store = get_harness_telemetry()
        env = _envelope()
        env.cancel()
        with pytest.raises(HarnessTerminated):
            guard_node("n", lambda s: {})({"harness_envelope": env})
        store.record_execution(env)

        assert store.snapshot()["reliability"]["cancellation_rate"] == 1.0

    def test_09_retry_is_counted_against_the_execution(self):
        env = _envelope(max_retries=3)
        env.record_retry()
        env.record_retry()

        assert env.retry_count == 2
        assert not env.retry_budget_exhausted

    def test_10_recovery_returns_to_running(self):
        env = _envelope()
        env.transition_to(ExecutionState.RUNNING)
        env.transition_to(ExecutionState.RECOVERING)
        env.transition_to(ExecutionState.RUNNING)
        env.finish(ExecutionState.COMPLETED)

        assert env.state is ExecutionState.COMPLETED

    def test_11_long_context_is_truncated_and_disclosed(self):
        sized = enforce_text_limit("w" * 2_000_000, max_chars=400_000)

        assert sized.truncated
        assert len(sized.value) < 400_500

    def test_12_concurrent_executions_do_not_interfere(self):
        """Real threads: each execution must see only its own budget."""
        store = get_harness_telemetry()
        results: list[int] = []
        lock = threading.Lock()

        def _run(index: int) -> None:
            env = create_envelope(graph_name="g", tenant_id=f"tenant-{index}")
            node = guard_node("n", lambda s: {})
            state = {"harness_envelope": env}
            for _ in range(index + 1):
                node(state)
            store.record_execution(env)
            with lock:
                results.append(env.step_count)

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert sorted(results) == list(range(1, 13))
        assert store.snapshot()["executions_recorded"] == 12


class TestTelemetryNeverBreaksExecution:
    def test_a_telemetry_failure_does_not_fail_the_node(self, monkeypatch):
        """A metrics bug must cost a data point, never the answer."""
        import agent.harness.control as control

        class _Broken:
            def record_node(self, *a, **k):
                raise RuntimeError("telemetry is down")

        monkeypatch.setattr(control, "get_harness_telemetry", lambda: _Broken())

        result = guard_node("n", lambda s: {"ok": True})({"harness_envelope": _envelope()})
        assert result == {"ok": True}
