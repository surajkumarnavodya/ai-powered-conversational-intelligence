"""Pure typed vocabulary for the AI Evaluation Benchmarking platform --
Prompt 46 (`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`).

**This module reuses, never duplicates, every evaluation harness this
codebase already built** -- `EvalDomain` is the union of what each
existing harness actually covers today:

- `SEMANTIC`/`INTENT`/`PLANNING`/`ANALYTICS`/`GROUNDEDNESS`/
  `CALCULATIONS`/`ANOMALY_ROOT_CAUSE`/`FORECASTING`/`RECOMMENDATIONS`
  (`COMPONENT_BENCHMARK_DOMAINS` below) are exactly the 9 domains
  `eval.component_benchmark.runner._DOMAIN_MODULES` already covers --
  zero-I/O, deterministic, executable synchronously inside this
  process (see `eval_platform.release_gate`'s own docstring for how a
  run of this kind is actually produced).
- `SQL` is the existing, mature `eval/benchmark/*.yaml` harness
  (`eval.runner`/`eval.regression`) -- needs a live database + LLM this
  sandboxed environment does not have.
- `SECURITY` is `eval/security_benchmark/` (the 500-case prompt-
  injection benchmark) -- likewise needs a live database + LLM.
- `TENANT_ISOLATION` is `tests/security/test_cross_tenant_isolation.py`/
  `test_cross_tenant_shared_infrastructure.py` -- a real, CI-enforced
  pytest regression suite, not a parallel dataset.

`SQL`/`SECURITY`/`TENANT_ISOLATION` runs are **operator-reported**, not
executed by this platform itself -- see `EvalRunSource`'s own docstring
for why, and `identity.models.EvaluationRun`'s own docstring for what
"operator-reported" means concretely (the same, already-established
posture `identity.models.GovernanceEvent`'s own `usage_recorded` rows
take for cost/latency this codebase has no live instrumentation for).
"""

from __future__ import annotations

from enum import Enum


class EvalDomain(str, Enum):
    """What an evaluation case/finding is actually about -- the union of
    every domain this mission names ("SQL correctness, semantic mapping,
    intent, planning, groundedness, calculations, anomaly/root cause,
    forecasting, recommendations, security and tenant isolation") plus
    `ANALYTICS`, the one pre-existing `eval/component_benchmark/` domain
    (shape-classification correctness) the mission doesn't name
    separately from `CALCULATIONS` (exact-value correctness) but which
    this platform still needs to report on, since it's a real, already-
    running domain nothing should silently drop from the picture.
    """

    SQL = "sql"
    SEMANTIC = "semantic"
    INTENT = "intent"
    PLANNING = "planning"
    ANALYTICS = "analytics"
    GROUNDEDNESS = "groundedness"
    CALCULATIONS = "calculations"
    ANOMALY_ROOT_CAUSE = "anomaly_root_cause"
    FORECASTING = "forecasting"
    RECOMMENDATIONS = "recommendations"
    SECURITY = "security"
    TENANT_ISOLATION = "tenant_isolation"


#: The 9 domains `eval.component_benchmark.runner.run_all()` actually
#: executes today -- every other `EvalDomain` needs live infrastructure
#: (a database, an LLM, or a full pytest/benchmark run) this platform
#: does not itself run, see `EvalRunSource` below.
COMPONENT_BENCHMARK_DOMAINS: frozenset[EvalDomain] = frozenset(
    {
        EvalDomain.SEMANTIC,
        EvalDomain.INTENT,
        EvalDomain.PLANNING,
        EvalDomain.ANALYTICS,
        EvalDomain.GROUNDEDNESS,
        EvalDomain.CALCULATIONS,
        EvalDomain.ANOMALY_ROOT_CAUSE,
        EvalDomain.FORECASTING,
        EvalDomain.RECOMMENDATIONS,
    }
)

#: The two domains this mission's own "release gates for critical
#: regressions" requirement treats as a non-negotiable floor, regardless
#: of what any baseline comparison says -- a security or tenant-isolation
#: regression blocks a release even if no case was ever in a committed
#: baseline to regress *from* (unlike `COMPONENT_BENCHMARK_DOMAINS`'
#: case-level regression check, which can only ever flag a *change*).
#: See `eval_platform.release_gate.evaluate_release_gate`'s own docstring.
MANDATORY_RELEASE_GATE_DOMAINS: frozenset[EvalDomain] = frozenset(
    {EvalDomain.SECURITY, EvalDomain.TENANT_ISOLATION}
)


class EvalRunSource(str, Enum):
    """Where one `identity.models.EvaluationRun`'s own result came from --
    the literal distinction between "this platform ran it" and "an
    operator is reporting a result produced elsewhere," since only one
    of the four harnesses this codebase has is runnable with zero I/O
    inside a normal request.

    `COMPONENT_BENCHMARK` runs are executed synchronously by
    `api/eval_platform.py`'s own create-run route, calling
    `eval.component_benchmark.runner.run_all()` directly (the real
    production function, never a mock) -- the one source this platform
    actually produces a result for itself. The other three are always
    operator-reported: a caller who already ran `eval.runner`/
    `eval.security_benchmark.runner`/the tenant-isolation pytest suite
    (by hand, in CI, or via a future scheduled job this prompt does not
    build) submits that run's own already-computed summary.
    """

    COMPONENT_BENCHMARK = "component_benchmark"
    SQL_BENCHMARK = "sql_benchmark"
    SECURITY_BENCHMARK = "security_benchmark"
    TENANT_ISOLATION_SUITE = "tenant_isolation_suite"


#: Which domain(s) each `EvalRunSource` reports on -- used to validate a
#: caller-submitted run's own `domain` field matches its `source`, and by
#: `eval_platform.release_gate` to know which domain a run's summary
#: counts/verdict apply to.
DOMAINS_FOR_SOURCE: dict[EvalRunSource, frozenset[EvalDomain]] = {
    EvalRunSource.COMPONENT_BENCHMARK: COMPONENT_BENCHMARK_DOMAINS,
    EvalRunSource.SQL_BENCHMARK: frozenset({EvalDomain.SQL}),
    EvalRunSource.SECURITY_BENCHMARK: frozenset({EvalDomain.SECURITY}),
    EvalRunSource.TENANT_ISOLATION_SUITE: frozenset({EvalDomain.TENANT_ISOLATION}),
}


class EvalRunStatus(str, Enum):
    """Every run this platform knows about is created already-complete --
    there is no background worker (the same disclosed, deliberate
    limitation `onboarding/jobs.py`'s own module docstring already names
    for an identical reason: no async execution machinery exists in this
    codebase). `FAILED_TO_RUN` exists for the one source that genuinely
    executes code in-process (`COMPONENT_BENCHMARK`) and that execution
    itself raised unexpectedly -- `eval.component_benchmark.runner
    .run_all()`'s own per-case try/except already makes this exceedingly
    unlikely (a case's own crash is recorded as a failing case, never
    propagated), but a genuinely unexpected error (e.g. an import error)
    is still possible and must be recorded honestly, not silently
    swallowed into a fabricated "completed" run with no real summary.
    """

    COMPLETED = "completed"
    FAILED_TO_RUN = "failed_to_run"


class ReleaseGateVerdict(str, Enum):
    """The outcome of `eval_platform.release_gate.evaluate_release_gate` --
    deliberately binary (never a numeric "score"), since the mission's
    own acceptance criterion is "critical regressions block it," a
    yes/no decision, not a graded one."""

    PASS = "pass"  # nosec B105 - a gate verdict ("pass"/"block"), not a
    # credential (BP-005).
    BLOCK = "block"
