"""Harness execution telemetry: counts, durations and identifiers only.

**Never content.** Not a question, not SQL, not a row, not a tool
result, not a document, not a credential, not a caller's roles. The
whole module deals in numbers and opaque identifiers, which is what
CLAUDE.md §5.6 and the governing prompt's §27 both require --
`governance/telemetry.py` already takes the same stance for policy
decisions, and this is its runtime sibling.

Roles are deliberately excluded even though they are not secret: they
are *authorization context*, and an execution record that carries them
invites a reader to treat telemetry as an access-control audit trail,
which `security/audit_log.py` already is and this is not.

**Bounded by construction.** Per-execution records live in a deque with
a hard cap, and per-node/per-provider aggregates are keyed by names
that come from code (node names, provider names), never from user
input -- so neither structure can be grown without bound by a caller.
An unbounded metrics dict fed by request data is a memory-exhaustion
vector, not just untidy.

Recording must never fail the request it describes. Every public
function here is total: no raise, no validation error, no I/O.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from agent.harness.envelope import ExecutionEnvelope

#: How many finished executions to keep for percentile computation.
#: Same bounded-window idea `observability/metrics.py` already uses.
DEFAULT_WINDOW = 500


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. 0.0 for an empty window."""
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


@dataclass
class _Counter:
    """One named thing's call count, failures and latency window."""

    calls: int = 0
    failures: int = 0
    timeouts: int = 0
    total_ms: float = 0.0
    durations: deque[float] = field(default_factory=lambda: deque(maxlen=DEFAULT_WINDOW))

    def record(self, duration_ms: float, *, failed: bool = False, timed_out: bool = False) -> None:
        self.calls += 1
        self.total_ms += duration_ms
        self.durations.append(duration_ms)
        if failed:
            self.failures += 1
        if timed_out:
            self.timeouts += 1

    def summary(self) -> dict[str, Any]:
        ordered = sorted(self.durations)
        return {
            "calls": self.calls,
            "failures": self.failures,
            "timeouts": self.timeouts,
            "avg_ms": round(self.total_ms / self.calls, 2) if self.calls else 0.0,
            "p50_ms": round(_percentile(ordered, 0.50), 2),
            "p95_ms": round(_percentile(ordered, 0.95), 2),
            "p99_ms": round(_percentile(ordered, 0.99), 2),
        }


class HarnessTelemetry:
    """Process-wide harness metrics. Thread-safe, bounded, content-free.

    Process-local, like every other metric store here: a multi-worker
    deployment has one per worker (CLAUDE.md §13).
    """

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._lock = threading.Lock()
        self._window = window
        self._executions: deque[dict[str, Any]] = deque(maxlen=window)
        self._nodes: dict[str, _Counter] = {}
        self._tools: dict[str, _Counter] = {}
        self._providers: dict[str, _Counter] = {}
        self._terminations: dict[str, int] = {}
        self._states: dict[str, int] = {}
        self._security_events: dict[str, int] = {}
        self._duplicate_executions_prevented = 0

    # ------------------------------------------------------------ recording

    def record_execution(self, envelope: ExecutionEnvelope) -> None:
        """Files one finished execution. Takes the envelope's own snapshot.

        `ExecutionEnvelope.snapshot()` is already the content-free view,
        so this cannot accidentally widen what is retained: there is one
        definition of "safe to record", and it lives on the envelope.
        """
        snapshot = envelope.snapshot()
        with self._lock:
            self._executions.append(snapshot)
            reason = snapshot.get("termination_reason") or "completed"
            self._terminations[reason] = self._terminations.get(reason, 0) + 1
            state = snapshot.get("state") or "unknown"
            self._states[state] = self._states.get(state, 0) + 1

    def record_node(self, node: str, duration_ms: float, *, failed: bool = False) -> None:
        with self._lock:
            self._nodes.setdefault(node, _Counter()).record(duration_ms, failed=failed)

    def record_tool(
        self, tool: str, duration_ms: float, *, failed: bool = False, timed_out: bool = False
    ) -> None:
        with self._lock:
            self._tools.setdefault(tool, _Counter()).record(
                duration_ms, failed=failed, timed_out=timed_out
            )

    def record_provider(
        self, provider: str, duration_ms: float, *, failed: bool = False, timed_out: bool = False
    ) -> None:
        with self._lock:
            self._providers.setdefault(provider, _Counter()).record(
                duration_ms, failed=failed, timed_out=timed_out
            )

    def record_security_event(self, kind: str) -> None:
        """Counts a block/denial/violation by kind. The kind, never the content."""
        with self._lock:
            self._security_events[kind] = self._security_events.get(kind, 0) + 1

    def record_duplicate_prevented(self) -> None:
        with self._lock:
            self._duplicate_executions_prevented += 1

    # -------------------------------------------------------------- reading

    def snapshot(self) -> dict[str, Any]:
        """Everything this store knows. Safe to log, serve and persist."""
        with self._lock:
            executions = list(self._executions)
            terminations = dict(self._terminations)
            states = dict(self._states)
            nodes = {name: counter.summary() for name, counter in self._nodes.items()}
            tools = {name: counter.summary() for name, counter in self._tools.items()}
            providers = {name: counter.summary() for name, counter in self._providers.items()}
            security = dict(self._security_events)
            duplicates = self._duplicate_executions_prevented

        total = len(executions)
        durations = sorted(float(e.get("elapsed_seconds") or 0.0) * 1000 for e in executions)
        completed = sum(1 for e in executions if e.get("state") == "completed")
        timed_out = sum(1 for e in executions if e.get("state") == "timed_out")
        cancelled = sum(1 for e in executions if e.get("state") == "cancelled")
        failed = sum(1 for e in executions if e.get("state") == "failed")
        retried = sum(1 for e in executions if (e.get("retry_count") or 0) > 0)
        budget_stops = sum(
            1 for e in executions if e.get("termination_reason") == "budget_exhausted"
        )

        def _rate(count: int) -> float:
            return round(count / total, 4) if total else 0.0

        def _mean(key: str) -> float:
            if not executions:
                return 0.0
            return round(sum(float(e.get(key) or 0) for e in executions) / total, 2)

        return {
            "executions_recorded": total,
            "reliability": {
                "success_rate": _rate(completed),
                "failure_rate": _rate(failed),
                "timeout_rate": _rate(timed_out),
                "cancellation_rate": _rate(cancelled),
                "retry_rate": _rate(retried),
            },
            "performance": {
                "p50_ms": round(_percentile(durations, 0.50), 2),
                "p95_ms": round(_percentile(durations, 0.95), 2),
                "p99_ms": round(_percentile(durations, 0.99), 2),
            },
            "efficiency": {
                "avg_model_calls": _mean("llm_call_count"),
                "avg_tool_calls": _mean("tool_call_count"),
                "avg_steps": _mean("step_count"),
                "avg_token_estimate": _mean("token_estimate"),
                "avg_cost_estimate": _mean("cost_estimate"),
            },
            "harness_health": {
                "budget_terminations": budget_stops,
                "max_step_terminations": terminations.get("budget_exhausted", 0),
                "deadline_terminations": terminations.get("deadline_exceeded", 0),
                "cancellations": terminations.get("cancelled", 0),
                "duplicate_executions_prevented": duplicates,
            },
            "terminations": terminations,
            "states": states,
            "nodes": nodes,
            "tools": tools,
            "providers": providers,
            "security": security,
        }

    def recent_executions(self, limit: int = 20) -> list[dict[str, Any]]:
        """The most recent execution records, newest last. Identifiers only."""
        with self._lock:
            return list(self._executions)[-limit:]

    def reset(self) -> None:
        """Clears everything. For tests and an operator-triggered reset."""
        with self._lock:
            self._executions.clear()
            self._nodes.clear()
            self._tools.clear()
            self._providers.clear()
            self._terminations.clear()
            self._states.clear()
            self._security_events.clear()
            self._duplicate_executions_prevented = 0


_default = HarnessTelemetry()


def get_harness_telemetry() -> HarnessTelemetry:
    """The process-wide store. One instance, like `get_default_metrics()`."""
    return _default


__all__ = [
    "DEFAULT_WINDOW",
    "HarnessTelemetry",
    "get_harness_telemetry",
]
