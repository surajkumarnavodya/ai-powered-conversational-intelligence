"""Runs every domain's cases and produces a `ComponentBenchmarkReport`.

No live-agent/DB/LLM dependency at all -- every case calls a real
production function directly and synchronously, which is what makes this
runnable both from `scripts/run_component_benchmark.py` (operator use) and
directly inside the normal pytest suite (`tests/test_eval_component_
benchmark.py`), unlike `eval.runner`/`eval.security_benchmark.runner`.
"""

from __future__ import annotations

from eval.component_benchmark import (
    cases_analytics,
    cases_anomaly_root_cause,
    cases_calculations,
    cases_forecasting,
    cases_groundedness,
    cases_intent,
    cases_planning,
    cases_recommendations,
    cases_semantic,
)
from eval.component_benchmark.schema import (
    ComponentBenchmarkReport,
    ComponentCaseResult,
    ComponentDomainReport,
)

#: One entry per domain, in report order -- the single place that knows
#: about every domain module; adding a new domain means adding one line
#: here; no other module needs to change. Prompt 46
#: (`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`) added
#: groundedness/calculations/anomaly_root_cause/forecasting/intent -- the
#: five named-mission domains this harness had no coverage for at all
#: before. "SQL correctness"/"security"/"tenant isolation" (the mission's
#: other three named domains) are deliberately NOT added here -- each
#: already has its own real, much larger evaluation harness that needs a
#: live database/LLM (`eval/benchmark/` + `eval/runner.py`,
#: `eval/security_benchmark/`) or is already a CI-enforced pytest
#: regression suite (`tests/security/test_cross_tenant_isolation.py`) --
#: adding a parallel, zero-I/O-only dataset for them here would either
#: duplicate that coverage or, worse, misrepresent a live-dependent
#: domain as fully covered by this zero-I/O harness alone. See
#: `eval_platform/release_gate.py` for how all of these are combined.
_DOMAIN_MODULES = {
    "semantic": cases_semantic,
    "planning": cases_planning,
    "analytics": cases_analytics,
    "recommendations": cases_recommendations,
    "groundedness": cases_groundedness,
    "calculations": cases_calculations,
    "anomaly_root_cause": cases_anomaly_root_cause,
    "forecasting": cases_forecasting,
    "intent": cases_intent,
}


def run_all() -> ComponentBenchmarkReport:
    """Runs every case in every domain and returns the full report.

    A case whose `run()` itself raises is recorded as a failure (not
    propagated) -- an unexpected exception from a production function is
    exactly the kind of regression this harness exists to catch, not a
    reason to crash the whole run before the remaining cases are tried.
    """
    domain_reports: list[ComponentDomainReport] = []
    for domain_name, module in _DOMAIN_MODULES.items():
        results: list[ComponentCaseResult] = []
        for case in module.CASES:
            try:
                passed, detail = case.run()
            except (
                Exception
            ) as exc:  # noqa: BLE001 - a case's own crash is a result, not a harness failure
                passed, detail = False, f"raised {type(exc).__name__}: {exc}"
            results.append(
                ComponentCaseResult(
                    name=case.name, description=case.description, passed=passed, detail=detail
                )
            )
        domain_reports.append(ComponentDomainReport(domain=domain_name, results=results))
    return ComponentBenchmarkReport(domains=domain_reports)
