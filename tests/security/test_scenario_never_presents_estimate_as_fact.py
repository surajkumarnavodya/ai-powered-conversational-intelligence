"""Regression test for a governance invariant that spans three modules
at once -- `analytics/models.py` (`ScenarioMetricComparison`),
`analytics/scenario.py`, and `agent/multiagent/agents.py`'s
`scenario_handler` -- so per `tests/security/README.md`'s own rule, it
belongs here rather than being split across module-specific test files.

Master-contract rule 10 ("never present inference, simulation or
prediction as observed fact") applied specifically to the What-If
Scenario Engine (Prompt 38, `38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`):
nothing this engine produces, at any layer, may label a hypothetical,
modeled value as if it were an observed fact. Two independent checks,
mirroring `test_causal_never_asserts_causation.py`'s own structure for a
different engine's analogous guarantee:

1. **Type-level, not just convention**: `ScenarioMetricComparison
   .baseline_kind`/`.scenario_kind` are declared fixed `Literal`s --
   pydantic itself refuses to construct one with the two kinds swapped
   (or either set to anything else), so this isn't a runtime check a
   future edit could accidentally skip.
2. **Text-level**: every claim this engine (and the one live handler
   built on top of it) actually renders, across representative
   scenarios, explicitly says "observed" for the baseline and "modeled
   estimate" for the scenario value -- scanned for a forbidden
   affirmative "this scenario value is a fact" phrasing.
"""

from __future__ import annotations

import re

import pytest
from analytics.models import ScenarioChangeType, ScenarioMetricComparison, ScenarioParameter
from analytics.scenario import run_scenario
from pydantic import ValidationError

from config.settings import Settings

_SETTINGS = Settings(scenario_unrealistic_change_percent_threshold=500.0)

_FORBIDDEN_PATTERNS = (
    re.compile(r"\bis a fact\b", re.IGNORECASE),
    re.compile(r"\bconfirmed value\b", re.IGNORECASE),
    re.compile(r"\bactual result\b", re.IGNORECASE),
    re.compile(r"\bobserved fact\b.*\bscenario\b", re.IGNORECASE),
)


def _assert_never_presents_estimate_as_fact(text: str) -> None:
    for pattern in _FORBIDDEN_PATTERNS:
        assert not pattern.search(text), f"found a fact-like claim about an estimate in: {text!r}"


class TestTypeLevelGuarantee:
    def test_baseline_kind_cannot_be_constructed_as_modeled_estimate(self):
        with pytest.raises(ValidationError):
            ScenarioMetricComparison(
                metric="Revenue",
                baseline_value=100.0,
                scenario_value=120.0,
                delta=20.0,
                baseline_kind="modeled_estimate",  # type: ignore[arg-type]
            )

    def test_scenario_kind_cannot_be_constructed_as_observed(self):
        with pytest.raises(ValidationError):
            ScenarioMetricComparison(
                metric="Revenue",
                baseline_value=100.0,
                scenario_value=120.0,
                delta=20.0,
                scenario_kind="observed",  # type: ignore[arg-type]
            )

    def test_the_default_construction_is_always_correct(self):
        comparison = ScenarioMetricComparison(
            metric="Revenue", baseline_value=100.0, scenario_value=120.0, delta=20.0
        )
        assert comparison.baseline_kind.value == "observed"
        assert comparison.scenario_kind.value == "modeled_estimate"


class TestTextLevelGuarantee:
    _COLUMNS = ["Region", "Revenue"]
    _ROWS = [("North", 100.0), ("South", 200.0), ("East", 50.0)]

    def test_a_real_scenario_run_never_presents_its_estimate_as_a_fact(self):
        param = ScenarioParameter(
            name="North +20%",
            affected_metric="Revenue",
            unit="percent",
            change_type=ScenarioChangeType.PERCENT_CHANGE,
            scenario_value=20.0,
            target_label="North",
        )
        result = run_scenario(
            "s1", "North +20%", self._COLUMNS, self._ROWS, ["Region"], [param], settings=_SETTINGS
        )
        assert result.status == "ok"
        for comparison in result.comparisons:
            _assert_never_presents_estimate_as_fact(str(comparison.baseline_value))
            _assert_never_presents_estimate_as_fact(str(comparison.scenario_value))
        for text in result.limitations:
            _assert_never_presents_estimate_as_fact(text)

    def test_the_handler_built_claim_text_always_labels_both_sides_correctly(self, monkeypatch):
        """Exercises the one live caller (`agent.multiagent.agents
        .scenario_handler`) end to end -- the actual text a user would
        see."""
        import agent.multiagent.supervisor as supervisor
        import agent.multiagent.tools as tools
        from agent.multiagent.policy import reset_breakers
        from agent.multiagent.supervisor import ScenarioRequest, run_supervised_analysis

        reset_breakers()

        def fake_sql(question, **kwargs):
            rows = [("North", 100.0), ("South", 200.0)]
            return {
                "status": "succeeded",
                "sql": "SELECT 1",
                "row_count": len(rows),
                "result_columns": ["region", "total"],
                "result_rows": rows,
                "analytical_result": None,
                "analytical_intent": None,
                "recommendations": [],
            }

        monkeypatch.setattr(supervisor, "get_settings", lambda: _SETTINGS)
        monkeypatch.setattr(tools, "run_agent", fake_sql)
        monkeypatch.setattr(supervisor, "_database_id", lambda s, t: "default")
        monkeypatch.setattr(
            tools, "plan_subquestions", lambda q, s, m, n: (["Revenue by region?"], "planner")
        )
        monkeypatch.setattr(
            tools, "retrieve_business_context", lambda *a, **k: type("R", (), {"items": []})()
        )

        try:
            result = run_supervised_analysis(
                "What if North grows 20%?",
                caller_roles=("analyst",),
                tenant_id="tenant-a",
                scenario_requests=[
                    ScenarioRequest(
                        data_task="Q1",
                        scenario_id="sc1",
                        name="North +20%",
                        parameters=(
                            ScenarioParameter(
                                name="North +20%",
                                affected_metric="total",
                                unit="percent",
                                change_type=ScenarioChangeType.PERCENT_CHANGE,
                                scenario_value=20.0,
                            ),
                        ),
                    )
                ],
            )
        finally:
            reset_breakers()

        scenario_claims = [c for c in result.claims if c.agent.value == "scenario"]
        assert scenario_claims
        for claim in scenario_claims:
            assert "(observed)" in claim.text
            assert "(modeled estimate)" in claim.text
            _assert_never_presents_estimate_as_fact(claim.text)
