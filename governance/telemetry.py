"""Policy decision telemetry: how often governance acted, and how.

The mission's §34.15. `security.audit_log` already records each
individual decision; this is the rollup that answers "is the engine
doing anything, and did a change to it alter behaviour" without reading
a log file.

**Counts only, never content.** A counter is keyed by decision, domain
and category -- all fixed, reviewed vocabulary from
`governance.decisions` and `governance.domains`. No question text, no
column name, no cell value, no caller identity ever enters this module,
so the snapshot is safe to expose to an operator and safe to log.

**Process-local and cumulative**, mirroring `observability.metrics
.PerformanceMetrics` exactly: one independent rollup per worker, reset
on restart, with the same disclosed limitation (a multi-worker
deployment has several, and real cross-process metrics would need a
shared store this application does not have). Cumulative rather than a
rolling window, for the same reason that module chose it: the useful
question is "how much has governance acted over this process's life",
which a bounded window of recent requests would understate for a
low-traffic deployment.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GovernanceSnapshot:
    """A point-in-time read of the counters.

    Every mapping is a plain copy, so a caller can hold or serialize it
    without racing the live counters.
    """

    total_requests_evaluated: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    domains: dict[str, int] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)
    result_actions: dict[str, int] = field(default_factory=dict)
    suppressed_groups: int = 0
    withheld_recommendations: dict[str, int] = field(default_factory=dict)
    policy_version: str = ""

    @property
    def blocked_requests(self) -> int:
        """Requests that ended in a refusal or an authorization demand."""
        return self.decisions.get("refuse", 0) + self.decisions.get("require_authorization", 0)


class GovernanceMetrics:
    """Thread-safe counters for governance decisions.

    One lock around every mutation, the same shape
    `observability.metrics.PerformanceMetrics` uses -- the request path
    touches this once per decision, so contention is not a concern and
    correctness under concurrency is.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests = 0
        self._decisions: Counter[str] = Counter()
        self._domains: Counter[str] = Counter()
        self._categories: Counter[str] = Counter()
        self._result_actions: Counter[str] = Counter()
        self._suppressed_groups = 0
        self._withheld_recommendations: Counter[str] = Counter()

    def record_request_decision(self, decision: str, domain: str, category: str = "") -> None:
        """Records one request-level verdict."""
        with self._lock:
            self._requests += 1
            self._decisions[decision] += 1
            self._domains[domain] += 1
            if category:
                self._categories[category] += 1

    def record_result_action(self, action: str) -> None:
        """Records one result-level protection action (masking, redaction).

        `action` is the stable action name `governance.result_policy`
        produces, with any per-request detail (a column list, a count)
        already stripped by the caller -- see `normalize_action`.
        """
        with self._lock:
            self._result_actions[action] += 1

    def record_suppressed_groups(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self._suppressed_groups += count

    def record_withheld_recommendation(self, action: str) -> None:
        with self._lock:
            self._withheld_recommendations[action] += 1

    def snapshot(self) -> GovernanceSnapshot:
        """A consistent copy of every counter."""
        from governance.domains import GOVERNANCE_POLICY_VERSION

        with self._lock:
            return GovernanceSnapshot(
                total_requests_evaluated=self._requests,
                decisions=dict(self._decisions),
                domains=dict(self._domains),
                categories=dict(self._categories),
                result_actions=dict(self._result_actions),
                suppressed_groups=self._suppressed_groups,
                withheld_recommendations=dict(self._withheld_recommendations),
                policy_version=GOVERNANCE_POLICY_VERSION,
            )

    def reset(self) -> None:
        """Clears every counter. For tests; never called in production."""
        with self._lock:
            self._requests = 0
            self._decisions.clear()
            self._domains.clear()
            self._categories.clear()
            self._result_actions.clear()
            self._suppressed_groups = 0
            self._withheld_recommendations.clear()


_METRICS = GovernanceMetrics()


def get_governance_metrics() -> GovernanceMetrics:
    """The process-wide singleton."""
    return _METRICS


def normalize_action(action: str) -> str:
    """Strips the per-request detail from a result-policy action string.

    `govern_result` produces actions like
    `"masked_restricted_columns:salary,bonus"` so an operator reading one
    log line knows what happened. A counter must not key on that -- it
    would grow one bucket per distinct column set, and a column name is
    not something this module carries. Everything after the first colon
    is dropped.
    """
    return action.split(":", 1)[0]


__all__ = [
    "GovernanceMetrics",
    "GovernanceSnapshot",
    "get_governance_metrics",
    "normalize_action",
]
