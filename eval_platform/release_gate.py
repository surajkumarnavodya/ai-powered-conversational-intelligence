"""The release gate: a pure function deciding PASS/BLOCK from already-
computed evaluation results -- Prompt 46
(`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`).

**Reuses, never recomputes.** This module takes whatever comparison/
summary each existing harness already produces and combines them into
one verdict -- it never re-runs a benchmark, re-grades a case, or
re-derives a pass/fail decision a harness already made:

- `eval.component_benchmark.regression.ComponentBenchmarkComparison`
  (from `compare_against_baseline`) for the 9 component-benchmark
  domains, unmodified.
- `eval.regression.RegressionReport` (from `detect_regression`) for the
  SQL benchmark, unmodified.
- Plain counts/booleans an operator reports for the security benchmark
  and the tenant-isolation suite (see `eval_platform.models
  .EvalRunSource`'s own docstring for why those two are never executed
  by this platform itself).

**Two non-negotiable floors, independent of any baseline comparison.**
`eval_platform.models.MANDATORY_RELEASE_GATE_DOMAINS` (security, tenant
isolation) block a release the moment either domain reports *any*
failure right now -- not only a regression relative to a prior baseline.
This mirrors the real-world shape of those two domains already
established elsewhere in this codebase: the prompt-injection benchmark's
own headline is "0 critical findings" (not "no new findings since last
time"), and the tenant-isolation suite is a pass/fail CI gate, not a
scored metric that tolerates drift. Every other domain's floor is
"no regression" (a case/metric that already failed before is not a
*new* problem this gate exists to catch).

**Never blocks on a domain nothing reported.** A `ReleaseGateInputs`
field left `None` produces a `warning`, not a `blocking_reason` -- a
release gate run against only the component benchmark (the one domain
this platform can execute itself, with no live infrastructure at all)
must still be able to return a real, actionable verdict for *that*
domain, rather than refusing to answer at all just because the SQL/
security/tenant-isolation harnesses weren't also run this time.
"""

from __future__ import annotations

from dataclasses import dataclass

from eval.component_benchmark.regression import ComponentBenchmarkComparison
from eval.regression import RegressionReport
from eval_platform.models import ReleaseGateVerdict


@dataclass(frozen=True)
class ReleaseGateInputs:
    """Every already-computed result this gate can factor in -- every
    field optional, since a given release-gate run may only have fresh
    results for some domains (see this module's own docstring)."""

    component_benchmark_comparison: ComponentBenchmarkComparison | None = None
    sql_benchmark_regression: RegressionReport | None = None
    security_benchmark_critical_findings: int | None = None
    tenant_isolation_passed: bool | None = None


@dataclass(frozen=True)
class ReleaseGateResult:
    verdict: ReleaseGateVerdict
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.verdict == ReleaseGateVerdict.PASS


def evaluate_release_gate(inputs: ReleaseGateInputs) -> ReleaseGateResult:
    """Combines `inputs` into one PASS/BLOCK verdict.

    Every blocking condition is checked independently and every reason
    found is reported (never just the first) -- so a caller fixing one
    problem and re-running can see at a glance whether anything else
    still blocks the release.
    """
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    comparison = inputs.component_benchmark_comparison
    if comparison is not None:
        if comparison.has_regressions:
            names = ", ".join(f"{r.domain}/{r.case_name}" for r in comparison.regressions)
            blocking_reasons.append(f"component benchmark regression(s): {names}")
    else:
        warnings.append("no component-benchmark comparison supplied")

    sql_regression = inputs.sql_benchmark_regression
    if sql_regression is not None:
        if sql_regression.has_regression:
            blocking_reasons.append(f"SQL benchmark regression: {sql_regression.summary()}")
    else:
        warnings.append("no SQL-benchmark regression result supplied")

    critical = inputs.security_benchmark_critical_findings
    if critical is not None:
        if critical > 0:
            blocking_reasons.append(
                f"{critical} critical security-benchmark finding(s) -- zero tolerated"
            )
    else:
        warnings.append("no security-benchmark result supplied")

    tenant_isolation_passed = inputs.tenant_isolation_passed
    if tenant_isolation_passed is not None:
        if not tenant_isolation_passed:
            blocking_reasons.append("tenant-isolation suite failed -- zero tolerated")
    else:
        warnings.append("no tenant-isolation suite result supplied")

    verdict = ReleaseGateVerdict.BLOCK if blocking_reasons else ReleaseGateVerdict.PASS
    return ReleaseGateResult(
        verdict=verdict,
        blocking_reasons=tuple(blocking_reasons),
        warnings=tuple(warnings),
    )
