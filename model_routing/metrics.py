"""Per-route quality, latency, failure and cost rollup.

The mission's "track quality, latency, failures and cost by route" --
where a *route* is one `(task, model)` pair, which is the granularity a
routing decision is actually made at. `observability/metrics.py`'s
`PerformanceMetrics` aggregates per LangGraph *stage* per *tenant* and
knows nothing about which model served a call, so this is a sibling
rollup rather than a new dimension bolted onto that one: adding
`(task, model)` keys there would change the shape
`GET /metrics/performance` has returned since Prompt 22 for every
existing consumer, for data only this feature produces.

It deliberately copies that module's disclosed posture rather than
inventing a better one, because the same constraints apply and pretending
otherwise would be the dishonest option:

- **Single-process and in-memory.** A multi-worker deployment has one
  independent rollup per worker, exactly as `observability.metrics` and
  `agent.rate_limit` already disclose. Real cross-process metrics need
  Prometheus/OpenTelemetry, which is not part of this architecture.
- **Resets on restart**, and bounded by sample count rather than time.

**What "quality" means here, precisely.** It is the rate at which this
application's own deterministic parsers and validators rejected a
model's output (`RouteFailureKind.QUALITY` -- an unparseable plan, a
malformed SQL block, an unreadable review verdict). That is a real,
observed signal about a route, and it is *not* an accuracy score: a
perfectly parseable answer can still be wrong, and nothing here claims
otherwise. Benchmarked accuracy lives in `eval/` and, for a governed
model version, in `governance_center`'s own operator-recorded
`evaluation_results` -- never synthesized from these counters.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from functools import cache
from typing import TypedDict

from model_routing.models import (
    ModelTier,
    RouteDecision,
    RouteFailureKind,
    RouteOutcome,
    RouteReason,
)

#: Latency samples kept per `(task, model)` route. Bounded for the same
#: memory reason `observability.metrics._DEFAULT_MAX_REQUESTS` is.
_DEFAULT_MAX_SAMPLES = 200

#: Upper bound on distinct routes tracked at once. A route key is built
#: from a `RouteTask` enum member and an allowlisted model id, both
#: server-controlled, so this is a cheap guard rather than a response to
#: a real attack -- the same reasoning `agent.rate_limit
#: .BoundedLimiterCache` gives for its own bound.
_DEFAULT_MAX_ROUTES = 200


class RouteSummary(TypedDict):
    """One `(task, model)` route's rolled-up behavior."""

    task: str
    model_id: str
    tier: str
    calls: int
    successes: int
    availability_failures: int
    quality_failures: int
    quality_failure_rate: float
    fallback_uses: int
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    max_latency_ms: float
    cost_units: int


class RoutingMetricsSnapshot(TypedDict):
    """The full rollup, as `GET /model-routing/metrics` returns it."""

    started_at: str
    routes: list[RouteSummary]
    decisions: int
    escalations: int
    de_escalations: int
    decisions_by_reason: dict[str, int]
    budget_denials: int
    breaker_denials: int
    total_cost_units: int


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile -- the same implementation choice (and
    reason) as `observability.metrics._percentile`: `statistics.quantiles`
    raises on a single sample, which a freshly started process hits
    immediately, and a snapshot must stay well-defined."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


class _Route:
    """One route's mutable counters. Every field is written under
    `RouteMetrics._lock`, so there is no invariant of its own to protect."""

    __slots__ = (
        "tier",
        "calls",
        "successes",
        "availability_failures",
        "quality_failures",
        "fallback_uses",
        "cost_units",
        "latencies_ms",
    )

    def __init__(self, tier: ModelTier, max_samples: int) -> None:
        self.tier = tier
        self.calls = 0
        self.successes = 0
        self.availability_failures = 0
        self.quality_failures = 0
        self.fallback_uses = 0
        self.cost_units = 0
        self.latencies_ms: deque[float] = deque(maxlen=max_samples)


class RouteMetrics:
    """Process-wide, thread-safe rollup of routing decisions and outcomes."""

    def __init__(
        self, max_samples: int = _DEFAULT_MAX_SAMPLES, max_routes: int = _DEFAULT_MAX_ROUTES
    ) -> None:
        self._lock = threading.Lock()
        self._max_samples = max_samples
        self._max_routes = max_routes
        self._routes: dict[tuple[str, str], _Route] = {}
        self._decisions = 0
        self._escalations = 0
        self._de_escalations = 0
        self._by_reason: dict[str, int] = {}
        self._budget_denials = 0
        self._breaker_denials = 0
        self._total_cost_units = 0
        self._started_at = time.time()

    def record_decision(self, decision: RouteDecision) -> None:
        """Counts one decision, including the ones that changed nothing --
        a rollup where `DEFAULT_NO_CHANGE` is invisible would make it
        impossible to tell "routing is off" from "routing never found a
        reason to act"."""
        with self._lock:
            self._decisions += 1
            self._by_reason[decision.reason.value] = (
                self._by_reason.get(decision.reason.value, 0) + 1
            )
            if decision.reason is RouteReason.UPGRADED_FOR_COMPLEXITY:
                self._escalations += 1
            elif decision.reason is RouteReason.DOWNGRADED_FOR_SIMPLICITY:
                self._de_escalations += 1
            elif decision.reason is RouteReason.BUDGET_EXHAUSTED:
                self._budget_denials += 1
            elif decision.reason is RouteReason.CIRCUIT_OPEN:
                self._breaker_denials += 1

    def record_outcome(self, outcome: RouteOutcome) -> None:
        """Records one routed call's measured result."""
        key = (outcome.task.value, outcome.model_id)
        with self._lock:
            route = self._routes.get(key)
            if route is None:
                if len(self._routes) >= self._max_routes:
                    return
                route = _Route(outcome.tier, self._max_samples)
                self._routes[key] = route
            route.calls += 1
            route.latencies_ms.append(outcome.latency_ms)
            route.cost_units += outcome.cost_units
            self._total_cost_units += outcome.cost_units
            if outcome.used_fallback:
                route.fallback_uses += 1
            if outcome.succeeded:
                route.successes += 1
            elif outcome.failure_kind is RouteFailureKind.AVAILABILITY:
                route.availability_failures += 1
            elif outcome.failure_kind is RouteFailureKind.QUALITY:
                route.quality_failures += 1

    def p95_latency_by_model(self) -> dict[str, float]:
        """Measured p95 latency per model id, across every task that model
        has served -- the input `model_routing.policy` uses to drop a
        candidate this deployment has actually found slow. Aggregated per
        *model* rather than per route because the question being asked is
        "is this model slow here," and a per-task split would make a
        rarely-used task's two samples outvote a well-measured one."""
        with self._lock:
            per_model: dict[str, list[float]] = {}
            for (_task, model_id), route in self._routes.items():
                per_model.setdefault(model_id, []).extend(route.latencies_ms)
        return {
            model_id: _percentile(sorted(samples), 0.95)
            for model_id, samples in per_model.items()
            if samples
        }

    def snapshot(self) -> RoutingMetricsSnapshot:
        """A consistent point-in-time view. Contains no question text, no
        SQL, no answer -- only task names, model ids and aggregates."""
        with self._lock:
            routes: list[RouteSummary] = []
            for (task, model_id), route in sorted(self._routes.items()):
                samples = sorted(route.latencies_ms)
                judged = route.successes + route.quality_failures
                routes.append(
                    RouteSummary(
                        task=task,
                        model_id=model_id,
                        tier=route.tier.value,
                        calls=route.calls,
                        successes=route.successes,
                        availability_failures=route.availability_failures,
                        quality_failures=route.quality_failures,
                        quality_failure_rate=(route.quality_failures / judged if judged else 0.0),
                        fallback_uses=route.fallback_uses,
                        mean_latency_ms=(sum(samples) / len(samples) if samples else 0.0),
                        p50_latency_ms=_percentile(samples, 0.5),
                        p95_latency_ms=_percentile(samples, 0.95),
                        max_latency_ms=(samples[-1] if samples else 0.0),
                        cost_units=route.cost_units,
                    )
                )
            return RoutingMetricsSnapshot(
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._started_at)),
                routes=routes,
                decisions=self._decisions,
                escalations=self._escalations,
                de_escalations=self._de_escalations,
                decisions_by_reason=dict(sorted(self._by_reason.items())),
                budget_denials=self._budget_denials,
                breaker_denials=self._breaker_denials,
                total_cost_units=self._total_cost_units,
            )

    def reset(self) -> None:
        """Clears everything. Tests use this for isolation -- the same
        reason `agent.multiagent.policy.reset_breakers` exists."""
        with self._lock:
            self._routes.clear()
            self._decisions = 0
            self._escalations = 0
            self._de_escalations = 0
            self._by_reason.clear()
            self._budget_denials = 0
            self._breaker_denials = 0
            self._total_cost_units = 0
            self._started_at = time.time()


@cache
def get_route_metrics() -> RouteMetrics:
    """The process-wide singleton, built on first use -- the same
    `functools.cache` pattern `observability.metrics.get_default_metrics`
    and `config.settings.get_settings` already use."""
    return RouteMetrics()


__all__ = [
    "RouteMetrics",
    "RouteSummary",
    "RoutingMetricsSnapshot",
    "get_route_metrics",
]
