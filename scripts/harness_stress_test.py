"""Measures agent-harness overhead and behaviour under concurrency.

**What this measures, and what it does not.** It drives the harness
control boundary -- `guard_node`, budgets, deadline, cancellation,
telemetry -- with real threads at increasing concurrency, and reports
real p50/p95/p99 and memory. It deliberately does **not** call Ollama
or a database, so these numbers are the harness's *own* cost, not
end-to-end agent latency.

That distinction matters for reading the output. The question this
answers is "does the control layer add meaningful overhead, and does it
stay correct under concurrency?" End-to-end latency needs real
infrastructure and is what `scripts/run_benchmark.py` and
`scripts/integration_test.py` are for -- both manual, neither run in CI
(CLAUDE.md §7).

A `print`-based CLI like `scripts/test_db_connection.py` and
`scripts/integration_test.py`, which is the one place this codebase
allows `print` (CLAUDE.md §6.2).

Usage:
    python scripts/harness_stress_test.py
    python scripts/harness_stress_test.py --levels 1,10,50 --steps 20
"""

from __future__ import annotations

import argparse
import gc
import statistics
import sys
import threading
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.harness import (  # noqa: E402
    ExecutionState,
    HarnessTerminated,
    create_envelope,
    guard_node,
)
from agent.harness.telemetry import get_harness_telemetry  # noqa: E402

DEFAULT_LEVELS = (1, 10, 50, 100, 500)


@dataclass
class LevelResult:
    """One concurrency level's measured outcome."""

    concurrency: int
    completed: int
    failed: int
    wall_seconds: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    peak_memory_mb: float
    throughput_per_second: float


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _one_execution(steps: int, node_work_seconds: float) -> float:
    """Runs one guarded execution end to end. Returns its duration in ms."""
    envelope = create_envelope(
        graph_name="stress",
        tenant_id="stress-tenant",
        budgets=None,
    )
    state = {"harness_envelope": envelope}

    def _node(_state: dict) -> dict:
        if node_work_seconds:
            time.sleep(node_work_seconds)
        return {}

    guarded = guard_node("stress_node", _node)
    started = time.perf_counter()
    try:
        for _ in range(steps):
            guarded(state)
        envelope.finish(ExecutionState.COMPLETED)
    except HarnessTerminated:
        envelope.finish(ExecutionState.FAILED)
    duration_ms = (time.perf_counter() - started) * 1000
    get_harness_telemetry().record_execution(envelope)
    return duration_ms


def run_level(concurrency: int, steps: int, node_work_seconds: float) -> LevelResult:
    """Runs `concurrency` executions simultaneously and measures them.

    Real threads released by one barrier-ish event, so the executions
    genuinely overlap rather than trickling. A staggered start would
    measure the harness under no contention at all and report a number
    that means nothing.
    """
    get_harness_telemetry().reset()
    gc.collect()
    tracemalloc.start()

    durations: list[float] = []
    failures = 0
    lock = threading.Lock()
    go = threading.Event()

    def _worker() -> None:
        nonlocal failures
        go.wait(timeout=30)
        try:
            duration = _one_execution(steps, node_work_seconds)
            with lock:
                durations.append(duration)
        except Exception:  # noqa: BLE001 - a stress run reports, it does not raise
            with lock:
                failures += 1

    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(concurrency)]
    for thread in threads:
        thread.start()

    wall_start = time.perf_counter()
    go.set()
    for thread in threads:
        thread.join(timeout=120)
    wall = time.perf_counter() - wall_start

    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return LevelResult(
        concurrency=concurrency,
        completed=len(durations),
        failed=failures,
        wall_seconds=wall,
        p50_ms=_percentile(durations, 0.50),
        p95_ms=_percentile(durations, 0.95),
        p99_ms=_percentile(durations, 0.99),
        peak_memory_mb=peak / (1024 * 1024),
        throughput_per_second=(len(durations) / wall) if wall > 0 else 0.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--levels",
        default=",".join(str(level) for level in DEFAULT_LEVELS),
        help="Comma-separated concurrency levels.",
    )
    parser.add_argument("--steps", type=int, default=18, help="Guarded node steps per execution.")
    parser.add_argument(
        "--node-work-ms",
        type=float,
        default=0.0,
        help="Simulated work per node, in milliseconds.",
    )
    args = parser.parse_args()

    levels = [int(part) for part in args.levels.split(",") if part.strip()]
    node_work = args.node_work_ms / 1000.0

    print("Agent harness stress test")
    print("=" * 78)
    print(f"steps/execution={args.steps}  node_work={args.node_work_ms}ms")
    print(
        "NOTE: measures harness overhead only -- no Ollama, no database. "
        "End-to-end latency is scripts/run_benchmark.py's job."
    )
    print("-" * 78)
    print(
        f"{'conc':>6} {'done':>6} {'fail':>5} {'p50 ms':>9} {'p95 ms':>9} "
        f"{'p99 ms':>9} {'peak MB':>9} {'exec/s':>9}"
    )
    print("-" * 78)

    results: list[LevelResult] = []
    for level in levels:
        result = run_level(level, args.steps, node_work)
        results.append(result)
        print(
            f"{result.concurrency:>6} {result.completed:>6} {result.failed:>5} "
            f"{result.p50_ms:>9.2f} {result.p95_ms:>9.2f} {result.p99_ms:>9.2f} "
            f"{result.peak_memory_mb:>9.2f} {result.throughput_per_second:>9.1f}"
        )

    print("-" * 78)
    total_failures = sum(r.failed for r in results)
    overhead_per_step = (
        statistics.mean(r.p50_ms for r in results) / args.steps if args.steps else 0.0
    )
    print(f"total failures across all levels: {total_failures}")
    print(f"mean p50 per guarded step: {overhead_per_step:.4f} ms")

    if total_failures:
        print("RESULT: FAIL -- executions failed under load.")
        return 1
    print("RESULT: PASS -- every execution completed at every level.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
