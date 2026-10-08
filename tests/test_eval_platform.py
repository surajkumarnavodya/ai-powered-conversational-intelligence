"""Unit tests for the pure `eval_platform/` modules -- Prompt 46
(`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`). No I/O, no FastAPI, no
identity database -- see `tests/test_api_eval_platform.py` for the
HTTP-level coverage and `tests/test_identity_eval_platform_repository.py`
for the persistence layer.
"""

from __future__ import annotations

from identity.rbac import Permission

from eval.component_benchmark.regression import (
    ComponentBenchmarkComparison,
    ComponentRegression,
)
from eval.regression import RegressionReport
from eval_platform.dataset_registry import fingerprint_baseline
from eval_platform.models import (
    COMPONENT_BENCHMARK_DOMAINS,
    DOMAINS_FOR_SOURCE,
    MANDATORY_RELEASE_GATE_DOMAINS,
    EvalDomain,
    EvalRunSource,
)
from eval_platform.policy import EvalAction, authorize_eval_action
from eval_platform.release_gate import ReleaseGateInputs, evaluate_release_gate


class TestModels:
    def test_every_component_benchmark_domain_is_covered_by_the_real_runner(self):
        """Guards against this platform's own domain list silently
        drifting from `eval.component_benchmark.runner`'s real one."""
        from eval.component_benchmark.runner import _DOMAIN_MODULES

        assert {d.value for d in COMPONENT_BENCHMARK_DOMAINS} == set(_DOMAIN_MODULES)

    def test_mandatory_domains_are_security_and_tenant_isolation_only(self):
        assert {
            EvalDomain.SECURITY,
            EvalDomain.TENANT_ISOLATION,
        } == MANDATORY_RELEASE_GATE_DOMAINS

    def test_domains_for_source_covers_every_source(self):
        for source in EvalRunSource:
            assert source in DOMAINS_FOR_SOURCE
            assert DOMAINS_FOR_SOURCE[source]


class TestDatasetRegistry:
    def test_same_content_different_key_order_produces_the_same_fingerprint(self):
        a = {"x/case_1": True, "y/case_2": False}
        b = {"y/case_2": False, "x/case_1": True}
        assert fingerprint_baseline(a) == fingerprint_baseline(b)

    def test_a_real_content_change_produces_a_different_fingerprint(self):
        a = {"x/case_1": True}
        b = {"x/case_1": False}
        assert fingerprint_baseline(a) != fingerprint_baseline(b)

    def test_fingerprint_is_a_short_hex_string(self):
        fp = fingerprint_baseline({"a": True})
        assert len(fp) == 16
        int(fp, 16)  # raises if not valid hex


class TestReleaseGate:
    def test_no_inputs_at_all_passes_with_only_warnings(self):
        result = evaluate_release_gate(ReleaseGateInputs())
        assert result.passed
        assert result.blocking_reasons == ()
        assert len(result.warnings) == 4

    def test_a_component_benchmark_regression_blocks(self):
        comparison = ComponentBenchmarkComparison(
            regressions=[
                ComponentRegression(
                    domain="sql",
                    case_name="case_1",
                    baseline_passed=True,
                    current_passed=False,
                    current_detail="now fails",
                )
            ]
        )
        result = evaluate_release_gate(ReleaseGateInputs(component_benchmark_comparison=comparison))
        assert not result.passed
        assert any("component benchmark regression" in r for r in result.blocking_reasons)

    def test_a_sql_benchmark_regression_blocks(self):
        regression = RegressionReport(newly_failing_cases=["case_7"])
        result = evaluate_release_gate(ReleaseGateInputs(sql_benchmark_regression=regression))
        assert not result.passed

    def test_zero_critical_findings_and_passing_tenant_isolation_is_clean(self):
        result = evaluate_release_gate(
            ReleaseGateInputs(security_benchmark_critical_findings=0, tenant_isolation_passed=True)
        )
        assert result.passed
        assert result.warnings == (
            "no component-benchmark comparison supplied",
            "no SQL-benchmark regression result supplied",
        )

    def test_any_critical_finding_blocks_even_with_everything_else_clean(self):
        comparison = ComponentBenchmarkComparison()
        result = evaluate_release_gate(
            ReleaseGateInputs(
                component_benchmark_comparison=comparison,
                sql_benchmark_regression=RegressionReport(),
                security_benchmark_critical_findings=3,
                tenant_isolation_passed=True,
            )
        )
        assert not result.passed
        assert len(result.blocking_reasons) == 1
        assert "3 critical" in result.blocking_reasons[0]

    def test_a_failed_tenant_isolation_suite_blocks_even_with_zero_findings(self):
        result = evaluate_release_gate(
            ReleaseGateInputs(security_benchmark_critical_findings=0, tenant_isolation_passed=False)
        )
        assert not result.passed

    def test_multiple_independent_blocking_reasons_are_all_reported(self):
        result = evaluate_release_gate(
            ReleaseGateInputs(security_benchmark_critical_findings=1, tenant_isolation_passed=False)
        )
        assert len(result.blocking_reasons) == 2

    def test_no_regression_and_newly_fixed_cases_still_passes(self):
        """A case that newly started passing is never itself a reason
        to block -- only a regression (pass -> fail) is."""
        comparison = ComponentBenchmarkComparison(
            newly_fixed=[
                ComponentRegression(
                    domain="sql",
                    case_name="case_2",
                    baseline_passed=False,
                    current_passed=True,
                    current_detail="now fixed",
                )
            ]
        )
        result = evaluate_release_gate(ReleaseGateInputs(component_benchmark_comparison=comparison))
        assert result.passed


class TestPolicy:
    def test_denies_when_actor_has_no_resolved_tenant(self):
        decision = authorize_eval_action(
            actor_permissions=frozenset({Permission.EVAL_MANAGE.value}),
            actor_tenant_id=None,
            resource_tenant_id=None,
            action=EvalAction.LIST,
        )
        assert not decision.allowed

    def test_cross_tenant_resource_is_denied_before_any_permission_check(self):
        decision = authorize_eval_action(
            actor_permissions=frozenset({Permission.EVAL_MANAGE.value}),
            actor_tenant_id="tenant-a",
            resource_tenant_id="tenant-b",
            action=EvalAction.GET_RUN,
        )
        assert not decision.allowed
        assert "different tenant" in decision.reason

    def test_eval_review_alone_cannot_create_a_run(self):
        decision = authorize_eval_action(
            actor_permissions=frozenset({Permission.EVAL_REVIEW.value}),
            actor_tenant_id="tenant-a",
            resource_tenant_id=None,
            action=EvalAction.CREATE_RUN,
        )
        assert not decision.allowed

    def test_eval_manage_alone_can_create_a_run(self):
        decision = authorize_eval_action(
            actor_permissions=frozenset({Permission.EVAL_MANAGE.value}),
            actor_tenant_id="tenant-a",
            resource_tenant_id=None,
            action=EvalAction.CREATE_RUN,
        )
        assert decision.allowed

    def test_eval_manage_alone_can_also_submit_a_case_review(self):
        """EVAL_MANAGE is a superset for review-tier actions too -- an
        admin can do everything an analyst reviewer can."""
        decision = authorize_eval_action(
            actor_permissions=frozenset({Permission.EVAL_MANAGE.value}),
            actor_tenant_id="tenant-a",
            resource_tenant_id=None,
            action=EvalAction.SUBMIT_CASE_REVIEW,
        )
        assert decision.allowed

    def test_an_unrecognized_action_is_denied(self):
        decision = authorize_eval_action(
            actor_permissions=frozenset(
                {Permission.EVAL_MANAGE.value, Permission.EVAL_REVIEW.value}
            ),
            actor_tenant_id="tenant-a",
            resource_tenant_id=None,
            action="not_a_real_action",  # type: ignore[arg-type]
        )
        assert not decision.allowed
        assert decision.reason == "unrecognized action"

    def test_no_permission_at_all_is_denied_for_list(self):
        decision = authorize_eval_action(
            actor_permissions=frozenset(),
            actor_tenant_id="tenant-a",
            resource_tenant_id=None,
            action=EvalAction.LIST,
        )
        assert not decision.allowed
