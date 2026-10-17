"""Live, in-process rollup of per-stage LangGraph node timings.

`agent.nodes._timed_node` has, since before this module existed, logged a
`[timing] stage=... attempt=... duration_ms=...` line for every node call
and appended a `StageTiming` entry to `AgentState["stage_timings"]`. That
data was previously only ever consumed two ways: read live off a log line
by a human, or aggregated *offline* from a captured `eval/results/run_*
.json` file by `docs/PERFORMANCE_BASELINE.md`'s manual analysis. Neither
answers "how is this deployment performing right now" without either
grepping logs by hand or running the benchmark harness.

This module closes that gap with the smallest possible addition: a
bounded, thread-safe, in-process rolling window that `agent.graph.run_agent`
feeds after every completed run (see that module's own call to
`record_agent_run`), and `GET /metrics/performance` (`api/main.py`) reads
back as a live snapshot. No new per-node instrumentation was added -- this
purely aggregates data `_timed_node` already produces.

**Deliberate limits, stated up front, matching `agent.rate_limit`'s own
disclosed posture for the same reason:**
- **Single-process, in-memory only.** A multi-worker deployment (several
  `uvicorn` processes) would have one independent rollup per process, not
  one merged view -- exactly the same limitation `agent/rate_limit.py`'s
  own module docstring already discloses for its sliding-window limiters,
  and for the same reason (no shared store like Redis is part of this
  application's current architecture). Real cross-process metrics would
  need Prometheus/OpenTelemetry or an equivalent, which is a genuinely
  separate, larger piece of infrastructure -- not attempted here.
- **Resets on process restart.** By design -- this is a live operational
  view, not a durable metrics store. `docs/PERFORMANCE_BASELINE.md`'s own
  captured-benchmark analysis remains the durable, point-in-time record;
  this module is for "what is happening right now," not history.
- **Bounded by request count, not time.** The rolling window keeps the
  most recent `max_requests` (default 500) completed runs' timings, not a
  fixed time window -- simpler and memory-bounded regardless of traffic
  rate.

Prompt 22 (scale/performance hardening) added three cumulative (not
rolling-window) counters -- `result_cache_hits`/`result_cache_misses`
(`db.result_cache`) and `database_concurrency_rejections`
(`agent.rate_limit.get_database_execution_limiter`) -- reusing this same
singleton/lock rather than standing up a second metrics object.

The AI usage rollup (catalogue row 45) is a fourth such cumulative
counter, added the same way: `record_llm_call` sums Ollama's own
per-call `prompt_eval_count`/`eval_count` per model name, fed from
`agent.llm_client._log_ollama_timing` -- the one point all of that
module's `client.chat` calls already funnel through, which is why no
call site changed. **Tokens only; this module reports no monetary
figure**, because the LLM runs locally and no per-token price exists for
this deployment to report (`ModelUsageSummary`'s own docstring states
why inventing one would be a provenance violation, not just a guess).

This module deliberately does **not** own which model a request was
*routed* to or how that route performed -- `model_routing`'s own
`get_route_metrics()` owns that, and its `total_cost_units` is an
abstract tier weight rather than money. One fact, one owner
(`CLAUDE.md` §4.8): routing answers "which model, and did the route
work", this answers "what the models actually consumed".
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import OrderedDict, deque
from functools import cache
from typing import TypedDict

from agent.state import StageTiming
from security.tenancy import DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

_DEFAULT_MAX_REQUESTS = 500

#: Upper bound on how many distinct tenants' windows are kept at once
#: (LRU-evicted past this). Bounded for the same defense-in-depth reason
#: `agent.rate_limit.BoundedLimiterCache` is -- a tenant id reaching this
#: module is server-resolved, never attacker-chosen, so this is a cheap
#: guard rather than a response to a real attack.
_DEFAULT_MAX_TENANTS = 200

#: Upper bound on how many distinct model names one tenant's LLM-usage
#: rollup keeps (LRU-evicted past this), bounded for the same
#: defense-in-depth reason `_DEFAULT_MAX_TENANTS` is. A model name reaching
#: `record_llm_call` is echoed by the Ollama server from a request this app
#: built, and a per-request override is already validated against
#: `Settings.ollama_allowed_models`, so the set is server-controlled rather
#: than attacker-chosen -- but a swapped-out `OLLAMA_MODEL` over a long
#: process lifetime makes a bound worth having anyway, and it costs nothing.
_DEFAULT_MAX_MODELS = 50


class StageSummary(TypedDict):
    """One stage's rolled-up timing distribution across the current window.

    Mirrors the exact column shape `docs/PERFORMANCE_BASELINE.md`'s manual
    per-stage table already established (n/mean/P50/P95/total), so a live
    snapshot from this module and that document's offline analysis read
    the same way.
    """

    stage: str
    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    total_ms: float


class RequestSummary(TypedDict):
    """Rolled-up total-request-duration distribution across the window."""

    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float


class ModelUsageSummary(TypedDict):
    """One model's cumulative token consumption in this process.

    **Tokens only -- deliberately no monetary figure.** The LLM runs
    locally via Ollama, so no per-token price exists for this deployment;
    deriving one would mean inventing a number and presenting an inference
    as an observed fact, which `CLAUDE.md` §4.4 forbids. A reader who wants
    cost multiplies these counts by whatever their own hosting costs them.

    `calls` counts LLM calls, not `/ask` requests -- one question makes
    several (planning, generation, review, insight), and a retry makes more.
    `prompt_tokens`/`completion_tokens` are Ollama's own
    `prompt_eval_count`/`eval_count`, summed; a call whose response omitted
    them (older Ollama server) contributes to nothing at all, not a zero.
    """

    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class MetricsSnapshot(TypedDict):
    """The full live rollup, as returned by `GET /metrics/performance`."""

    started_at: str
    window_requests: int
    max_window_requests: int
    requests: RequestSummary
    stages: list[StageSummary]
    status_counts: dict[str, int]
    # Prompt 22 (scale/performance hardening) -- cumulative, process-
    # lifetime counters (never reset by the rolling window above, unlike
    # every other field here) for the new result cache
    # (`db.result_cache`) and per-database execution concurrency limiter
    # (`agent.rate_limit.get_database_execution_limiter`). Cumulative
    # rather than windowed deliberately: a cache's whole value proposition
    # is its hit *rate* over the process's life, which a bounded rolling
    # window of only the most recent `max_requests` would understate for
    # a long-running, low-traffic deployment.
    result_cache_hits: int
    result_cache_misses: int
    database_concurrency_rejections: int
    # Prompt 20 -- which tenant this rollup covers, or `None` for the merged
    # process-wide view. Present so a snapshot is self-describing about its
    # own scope rather than leaving a reader to infer it from the caller.
    tenant_id: str | None
    # Catalogue row 45 (AI usage rollup) -- cumulative per-model token
    # consumption, for the same reason the cache counters above are
    # cumulative: a usage total's whole point is "how much has this
    # deployment consumed", which a bounded rolling window of the most
    # recent `max_requests` would understate. Sorted by total tokens,
    # descending, so the heaviest model reads first.
    llm_usage: list[ModelUsageSummary]


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile over an already-sorted list.

    Deliberately not `statistics.quantiles` -- that raises on fewer than
    two data points, which a freshly-started (or very low-traffic) process
    hits immediately, and `MetricsSnapshot` must stay well-defined (0.0,
    not an exception) even with a single sample or none at all.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


class _TenantWindow:
    """One tenant's own rolling window plus cumulative counters.

    A plain mutable holder rather than a dataclass: every field is written
    under `PerformanceMetrics._lock`, so there is no invariant for a
    dataclass's own machinery to protect, and the deques need per-instance
    `maxlen` wiring anyway.
    """

    __slots__ = (
        "request_durations_ms",
        "stage_durations_ms",
        "status_counts",
        "result_cache_hits",
        "result_cache_misses",
        "database_concurrency_rejections",
        "llm_usage",
    )

    def __init__(self, max_requests: int) -> None:
        self.request_durations_ms: deque[float] = deque(maxlen=max_requests)
        self.stage_durations_ms: dict[str, deque[float]] = {}
        self.status_counts: dict[str, int] = {}
        self.result_cache_hits = 0
        self.result_cache_misses = 0
        self.database_concurrency_rejections = 0
        # model name -> [calls, prompt_tokens, completion_tokens]. An
        # `OrderedDict` so the model cap below can evict least-recently-used,
        # the same structure `PerformanceMetrics._windows` uses for tenants.
        self.llm_usage: OrderedDict[str, list[int]] = OrderedDict()


class PerformanceMetrics:
    """Bounded, thread-safe rolling window of stage/request timings,
    **partitioned per tenant** (Prompt 20).

    `record_agent_run` is the sole write path for timings, called once per
    completed `agent.graph.run_agent` invocation; `snapshot` is the sole
    read path, called by `GET /metrics/performance`. Both are cheap (a lock
    held only long enough to append/copy bounded deques) so neither adds
    meaningful latency to the request path they instrument -- consistent
    with this codebase's existing `agent.rate_limit.SlidingWindowRateLimiter`,
    which makes the identical tradeoff for the identical reason.

    **Why tenant-partitioned.** Latency distributions, status-code mixes and
    cache hit rates are operational data about a tenant's own usage: a p95
    that moves, or a sudden run of `failed` statuses, tells a reader
    something real about another tenant's workload.
    `GET /metrics/performance` is admin-gated, but this codebase has no
    platform-admin-versus-tenant-admin distinction, so admin-gating alone
    would have let one tenant's admin read another's numbers. Every write
    therefore lands in its own tenant's window, and the route reads only the
    caller's own (`snapshot(tenant_id=...)`).

    `snapshot()` with no argument still returns the merged, process-wide
    rollup -- the exact pre-Prompt-20 meaning, kept for
    `scripts/profile_pipeline.py`-style direct/operator use and so that no
    existing non-HTTP caller changed behavior. It is deliberately **not**
    reachable through the HTTP route.

    The number of distinct tenant windows is bounded (`max_tenants`,
    LRU-evicted) for the same reason `agent.rate_limit.BoundedLimiterCache`
    is bounded: a tenant id reaching here is server-resolved rather than
    attacker-chosen, so unbounded growth is not a real attack, but the bound
    costs nothing and removes the need to reason about it.
    """

    def __init__(
        self,
        max_requests: int = _DEFAULT_MAX_REQUESTS,
        max_tenants: int = _DEFAULT_MAX_TENANTS,
        max_models: int = _DEFAULT_MAX_MODELS,
    ) -> None:
        self._lock = threading.Lock()
        self._max_requests = max_requests
        self._max_tenants = max_tenants
        self._max_models = max_models
        self._started_at = time.time()
        self._windows: OrderedDict[str, _TenantWindow] = OrderedDict()

    def _window_locked(self, tenant_id: str | None) -> _TenantWindow:
        """Returns (creating if needed) one tenant's window. The caller must
        already hold `self._lock`."""
        key = tenant_id or DEFAULT_TENANT_ID
        window = self._windows.get(key)
        if window is None:
            window = _TenantWindow(self._max_requests)
            self._windows[key] = window
            if len(self._windows) > self._max_tenants:
                evicted, _ = self._windows.popitem(last=False)
                logger.warning(
                    "[metrics] tenant window cache at capacity (%d) -- evicted LRU tenant %r",
                    self._max_tenants,
                    evicted,
                )
        self._windows.move_to_end(key)
        return window

    def record_result_cache_hit(self, tenant_id: str | None = None) -> None:
        """Called by `api/main.py`'s `POST /execute` on a result-cache hit."""
        with self._lock:
            self._window_locked(tenant_id).result_cache_hits += 1

    def record_result_cache_miss(self, tenant_id: str | None = None) -> None:
        """Called by `api/main.py`'s `POST /execute` on a result-cache miss
        (including when the cache is disabled or the SQL isn't cacheable --
        every non-hit execution is a miss)."""
        with self._lock:
            self._window_locked(tenant_id).result_cache_misses += 1

    def record_database_concurrency_rejection(self, tenant_id: str | None = None) -> None:
        """Called whenever `agent.rate_limit.get_database_execution_limiter`
        rejects an execution attempt (from either
        `agent.nodes.execute_sql_node` or `api/main.py`'s `POST /execute`)."""
        with self._lock:
            self._window_locked(tenant_id).database_concurrency_rejections += 1

    def record_llm_call(
        self,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        tenant_id: str | None = None,
    ) -> None:
        """Adds one LLM call's own reported token counts to `tenant_id`'s
        cumulative per-model usage rollup (catalogue row 45).

        Called by `agent.llm_client._log_ollama_timing` -- the single point
        every one of that module's `client.chat` calls already funnels
        through, which is why no call site needed changing and why a future
        call site is counted for free. See that function's docstring for the
        same argument applied to the circuit breaker.

        **Never raises**, matching `record_agent_run` below and
        `CLAUDE.md` §4.2: a usage counter is an observability aid, so a
        failure here degrades to "this call wasn't counted", never to a
        question that cannot be answered.

        A call is recorded only when the server actually reported token
        counts. `model=None` (a mocked or older response that carries no
        model field) or both counts `None` records nothing at all, rather
        than a zero-token call against an `"unknown"` model -- a zero would
        be indistinguishable from a real call that genuinely consumed
        nothing, and inventing the attribution would be worse than omitting
        it. One count present and the other missing is kept, with the
        missing side contributing 0.
        """
        if model is None or (prompt_tokens is None and completion_tokens is None):
            return
        try:
            # Coerced *before* the window is touched, so an unusable value
            # leaves no partially-applied entry behind. Incrementing in
            # place and failing halfway would record a phantom call with
            # zero tokens -- worse than not counting it, because a reader
            # cannot tell it apart from a real call.
            prompt = int(prompt_tokens or 0)
            completion = int(completion_tokens or 0)
        except (TypeError, ValueError):
            logger.debug("[metrics] unusable token counts for model %r -- not recorded", model)
            return
        if prompt < 0 or completion < 0:
            logger.debug("[metrics] negative token counts for model %r -- not recorded", model)
            return
        try:
            with self._lock:
                usage = self._window_locked(tenant_id).llm_usage
                entry = usage.get(model)
                if entry is None:
                    entry = [0, 0, 0]
                    usage[model] = entry
                    if len(usage) > self._max_models:
                        evicted, _ = usage.popitem(last=False)
                        logger.warning(
                            "[metrics] llm usage model cache at capacity (%d) -- "
                            "evicted LRU model %r",
                            self._max_models,
                            evicted,
                        )
                usage.move_to_end(model)
                entry[0] += 1
                entry[1] += prompt
                entry[2] += completion
        except Exception:  # pragma: no cover -- defensive, see docstring
            logger.debug("[metrics] failed to record LLM usage", exc_info=True)

    def record_agent_run(
        self,
        stage_timings: list[StageTiming],
        total_duration_ms: float,
        status: str | None,
        tenant_id: str | None = None,
    ) -> None:
        """Feeds one completed `run_agent()` call's timings into the window
        belonging to `tenant_id`.

        Never raises -- a malformed/empty `stage_timings` (e.g. a rejected
        question that never reached any node) degrades to "no stage data
        recorded for this request," not a broken request. Matches this
        codebase's "an accuracy/observability aid must never be a reason a
        question can't be answered" posture (the same principle
        `agent.llm_client._build_golden_examples_block`'s own docstring
        states for a different subsystem).

        `tenant_id` defaults to `None`, which lands in the default tenant's
        window -- the correct answer for every pre-Prompt-20 caller, since
        every such caller was in the single default tenant by definition.
        """
        with self._lock:
            window = self._window_locked(tenant_id)
            window.request_durations_ms.append(total_duration_ms)
            window.status_counts[status or "unknown"] = (
                window.status_counts.get(status or "unknown", 0) + 1
            )
            for timing in stage_timings:
                stage = timing.get("stage")
                duration = timing.get("duration_ms")
                if not stage or duration is None:
                    continue
                bucket = window.stage_durations_ms.setdefault(
                    stage, deque(maxlen=self._max_requests)
                )
                bucket.append(duration)

    def snapshot(self, tenant_id: str | None = None) -> MetricsSnapshot:
        """Returns the current rollup. Cheap enough to call on every poll --
        computes percentiles over at most `max_requests` samples per stage
        per tenant.

        `tenant_id=None` merges every tenant's window into one process-wide
        rollup (the pre-Prompt-20 meaning, for direct/operator use); passing
        a tenant returns only that tenant's slice, which is what
        `GET /metrics/performance` does. A tenant that has recorded nothing
        yet returns a well-formed, all-zero snapshot rather than an error --
        the same "well-defined with no samples" property `_percentile`
        already guarantees, and deliberately indistinguishable from a tenant
        that does not exist (a metrics endpoint must not become a tenant
        existence oracle).
        """
        with self._lock:
            if tenant_id is None:
                windows = list(self._windows.values())
            else:
                existing = self._windows.get(tenant_id)
                windows = [existing] if existing is not None else []

            request_durations: list[float] = []
            stage_buckets: dict[str, list[float]] = {}
            status_counts: dict[str, int] = {}
            result_cache_hits = 0
            result_cache_misses = 0
            database_concurrency_rejections = 0
            # model -> [calls, prompt_tokens, completion_tokens], merged
            # across however many windows this snapshot covers.
            usage_totals: dict[str, list[int]] = {}
            for window in windows:
                request_durations.extend(window.request_durations_ms)
                for stage_name, stage_durations in window.stage_durations_ms.items():
                    # Named distinctly from the `stage`/`durations` pair the
                    # summary loop below reuses: these are the per-window
                    # bounded deques, those are the merged lists.
                    stage_buckets.setdefault(stage_name, []).extend(stage_durations)
                for status_name, count in window.status_counts.items():
                    status_counts[status_name] = status_counts.get(status_name, 0) + count
                result_cache_hits += window.result_cache_hits
                result_cache_misses += window.result_cache_misses
                database_concurrency_rejections += window.database_concurrency_rejections
                for model_name, counts in window.llm_usage.items():
                    running = usage_totals.setdefault(model_name, [0, 0, 0])
                    running[0] += counts[0]
                    running[1] += counts[1]
                    running[2] += counts[2]

        window_requests = len(request_durations)
        request_durations.sort()
        stage_items = [(stage, sorted(durations)) for stage, durations in stage_buckets.items()]

        requests: RequestSummary = {
            "count": len(request_durations),
            "mean_ms": round(statistics.fmean(request_durations), 2) if request_durations else 0.0,
            "p50_ms": round(_percentile(request_durations, 0.50), 2),
            "p95_ms": round(_percentile(request_durations, 0.95), 2),
            "max_ms": round(request_durations[-1], 2) if request_durations else 0.0,
        }

        stages: list[StageSummary] = []
        for stage, durations in sorted(stage_items, key=lambda item: sum(item[1]), reverse=True):
            stages.append(
                {
                    "stage": stage,
                    "count": len(durations),
                    "mean_ms": round(statistics.fmean(durations), 2),
                    "p50_ms": round(_percentile(durations, 0.50), 2),
                    "p95_ms": round(_percentile(durations, 0.95), 2),
                    "max_ms": round(durations[-1], 2),
                    "total_ms": round(sum(durations), 2),
                }
            )

        llm_usage: list[ModelUsageSummary] = sorted(
            (
                {
                    "model": model_name,
                    "calls": counts[0],
                    "prompt_tokens": counts[1],
                    "completion_tokens": counts[2],
                    "total_tokens": counts[1] + counts[2],
                }
                for model_name, counts in usage_totals.items()
            ),
            key=lambda summary: (-summary["total_tokens"], summary["model"]),
        )

        return {
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._started_at)),
            "tenant_id": tenant_id,
            "llm_usage": llm_usage,
            "window_requests": window_requests,
            "max_window_requests": self._max_requests,
            "requests": requests,
            "stages": stages,
            "status_counts": status_counts,
            "result_cache_hits": result_cache_hits,
            "result_cache_misses": result_cache_misses,
            "database_concurrency_rejections": database_concurrency_rejections,
        }


@cache
def get_default_metrics() -> PerformanceMetrics:
    """The process-wide singleton `agent.graph.run_agent` writes to and
    `GET /metrics/performance` reads from -- same `functools.cache`
    singleton pattern as every other process-lifetime object in this
    codebase (`db.connection._cached_engine`, `agent.llm_client
    ._get_ollama_client`, `agent.graph.build_graph`, etc.), not a new
    pattern introduced for this module."""
    return PerformanceMetrics()
