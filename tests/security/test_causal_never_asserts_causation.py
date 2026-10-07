"""Regression test for a governance invariant that spans two modules at
once -- `analytics/causal.py` (Prompt 37,
`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`) and `agent/multiagent/agents
.py`'s `root_cause_handler` -- so per `tests/security/README.md`'s own
rule, it belongs here rather than being split across two module-specific
test files.

Master-contract rule 10 ("never present inference, simulation or
prediction as observed fact") applied specifically to causal language:
nothing this codebase's root-cause engine produces, at any layer, may
assert a *confirmed* cause. Two independent checks:

1. **Type-level, not just convention**: `AdvancedRootCauseResult
   .causation_confirmed` is declared `Literal[False]` -- pydantic itself
   refuses to construct one with `True`, so this isn't a runtime check a
   future edit could accidentally skip.
2. **Text-level**: every claim/rationale/disclaimer string this engine
   (and the one live handler built on top of it) actually renders, across
   a battery of representative scenarios (a clear known cause, a
   justified interaction, a distractor, a broadly-distributed no-cause
   case), is scanned for an affirmative causal-assertion phrase -- and
   wherever the word "cause"/"causation" appears at all, it must appear
   alongside an explicit negation ("not"/"never"), never standing alone
   as "X caused Y."
"""

from __future__ import annotations

import re

import pytest
from analytics.causal import investigate_root_cause_advanced
from analytics.models import AdvancedRootCauseResult, HierarchicalRootCauseNode
from analytics.root_cause import investigate_root_cause
from pydantic import ValidationError

from config.settings import Settings

_SETTINGS = Settings(
    root_cause_min_contribution_percent=10.0,
    root_cause_min_sample_size=3,
    root_cause_significance_alpha=0.05,
    root_cause_interaction_min_percent=5.0,
)

#: An affirmative, unhedged assertion of causation -- never allowed to
#: appear in any generated text, regardless of case.
_FORBIDDEN_AFFIRMATIVE_PATTERNS = (
    re.compile(r"\bcaused\b", re.IGNORECASE),
    re.compile(r"\bcauses\b", re.IGNORECASE),
    re.compile(r"\bis the cause\b", re.IGNORECASE),
    re.compile(r"\bproves\b", re.IGNORECASE),
    re.compile(r"\bdefinitively\b", re.IGNORECASE),
)

_NEGATION_MARKERS = ("not", "never", "no genuine", "no confirmed")


def _assert_text_never_asserts_causation(text: str) -> None:
    for pattern in _FORBIDDEN_AFFIRMATIVE_PATTERNS:
        assert not pattern.search(text), f"found an unhedged causal assertion in: {text!r}"
    if "cause" in text.lower() or "causation" in text.lower():
        assert any(
            marker in text.lower() for marker in _NEGATION_MARKERS
        ), f"the word 'cause'/'causation' appears without an explicit negation in: {text!r}"


class TestTypeLevelGuarantee:
    def test_causation_confirmed_cannot_be_constructed_as_true(self):
        base_result = investigate_root_cause(
            [("West", 200.0)], [("West", 100.0)], ["Region", "Revenue"], ["Region"], "Revenue"
        )
        root = HierarchicalRootCauseNode(
            depth=0, dimension_columns=("Region",), base_result=base_result
        )
        with pytest.raises(ValidationError):
            AdvancedRootCauseResult(
                root=root,
                has_sufficient_evidence=True,
                causation_confirmed=True,  # type: ignore[arg-type]
            )

    def test_the_default_result_is_always_false(self):
        base_result = investigate_root_cause(
            [("West", 200.0)], [("West", 100.0)], ["Region", "Revenue"], ["Region"], "Revenue"
        )
        root = HierarchicalRootCauseNode(
            depth=0, dimension_columns=("Region",), base_result=base_result
        )
        result = AdvancedRootCauseResult(root=root, has_sufficient_evidence=True)
        assert result.causation_confirmed is False


class TestTextLevelGuarantee:
    """Every string a representative battery of scenarios actually
    produces (claim text would be built from these same fields by
    `agent.multiagent.agents.root_cause_handler` -- see that module's own
    `_root_cause_advanced`) is scanned for an unhedged causal claim."""

    def _collect_all_strings(self, result: AdvancedRootCauseResult) -> list[str]:
        strings = [result.causation_disclaimer]

        def _walk(node: HierarchicalRootCauseNode) -> None:
            for evidence in node.contributor_evidence + node.distractors:
                strings.append(evidence.contributor.label)
            strings.extend(node.base_result.limitations)
            for child in node.children:
                _walk(child)

        _walk(result.root)
        for interaction in result.interactions:
            strings.append(interaction.rationale)
        strings.extend(result.limitations)
        return strings

    def test_a_clear_known_cause_scenario_never_asserts_causation(self):
        baseline = [
            ("North", 100.0),
            ("North", 99.0),
            ("North", 101.0),
            ("South", 6.0),
            ("South", 6.0),
        ]
        current = [
            ("North", 400.0),
            ("North", 402.0),
            ("North", 398.0),
            ("South", 6.0),
            ("South", 6.0),
        ]
        result = investigate_root_cause_advanced(
            current, baseline, ["Region", "Revenue"], [["Region"]], "Revenue", _SETTINGS
        )
        assert result.has_sufficient_evidence is True
        for text in self._collect_all_strings(result):
            _assert_text_never_asserts_causation(text)

    def test_a_justified_interaction_scenario_never_asserts_causation(self):
        pattern = [99.0, 100.0, 101.0]

        def cell(region, channel, shift):
            baseline_rows = [(region, channel, v) for v in pattern]
            current_rows = [(region, channel, v + shift) for v in pattern]
            return baseline_rows, current_rows

        b1, c1 = cell("North", "Online", 400.0)
        b2, c2 = cell("North", "Store", 0.0)
        b3, c3 = cell("South", "Online", 0.0)
        b4, c4 = cell("South", "Store", 0.0)
        baseline = b1 + b2 + b3 + b4
        current = c1 + c2 + c3 + c4
        result = investigate_root_cause_advanced(
            current,
            baseline,
            ["Region", "Channel", "Revenue"],
            [["Region"], ["Channel"]],
            "Revenue",
            _SETTINGS,
        )
        assert any(i.is_statistically_justified for i in result.interactions)
        for text in self._collect_all_strings(result):
            _assert_text_never_asserts_causation(text)

    def test_a_distractor_scenario_never_asserts_causation(self):
        baseline = [
            ("Solid", 100.0),
            ("Solid", 98.0),
            ("Solid", 102.0),
            ("Solid", 99.0),
            ("Solid", 101.0),
            ("Flaky", 10.0),
        ]
        current = [
            ("Solid", 100.0),
            ("Solid", 98.0),
            ("Solid", 102.0),
            ("Solid", 99.0),
            ("Solid", 101.0),
            ("Flaky", 250.0),
        ]
        result = investigate_root_cause_advanced(
            current, baseline, ["Region", "Revenue"], [["Region"]], "Revenue", _SETTINGS
        )
        assert result.has_sufficient_evidence is False
        for text in self._collect_all_strings(result):
            _assert_text_never_asserts_causation(text)

    def test_a_broadly_distributed_no_cause_scenario_never_asserts_causation(self):
        baseline = [(f"R{i}", 100.0) for i in range(12)]
        current = [(f"R{i}", 108.0) for i in range(12)]
        result = investigate_root_cause_advanced(
            current, baseline, ["Region", "Revenue"], [["Region"]], "Revenue", _SETTINGS
        )
        assert result.has_sufficient_evidence is False
        for text in self._collect_all_strings(result):
            _assert_text_never_asserts_causation(text)

    def test_the_handler_built_claim_text_also_never_asserts_causation(self, monkeypatch):
        """Exercises the one live caller (`agent.multiagent.agents
        .root_cause_handler`'s advanced path) end to end, over the same
        known-cause scenario -- the actual text a user would see."""
        import agent.multiagent.supervisor as supervisor
        import agent.multiagent.tools as tools
        from agent.multiagent.policy import reset_breakers
        from agent.multiagent.supervisor import RootCausePair, run_supervised_analysis

        reset_breakers()
        settings = _SETTINGS

        def fake_sql(question, **kwargs):
            baseline_rows = [
                ("North", 100.0),
                ("North", 99.0),
                ("North", 101.0),
                ("South", 6.0),
                ("South", 6.0),
            ]
            current_rows = [
                ("North", 400.0),
                ("North", 402.0),
                ("North", 398.0),
                ("South", 6.0),
                ("South", 6.0),
            ]
            rows = current_rows if question == "Revenue by region?" else baseline_rows
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

        monkeypatch.setattr(supervisor, "get_settings", lambda: settings)
        monkeypatch.setattr(tools, "run_agent", fake_sql)
        monkeypatch.setattr(supervisor, "_database_id", lambda s, t: "default")
        monkeypatch.setattr(
            tools,
            "plan_subquestions",
            lambda q, s, m, n: (["Revenue by region?", "Revenue by month?"], "planner"),
        )
        monkeypatch.setattr(
            tools, "retrieve_business_context", lambda *a, **k: type("R", (), {"items": []})()
        )

        try:
            result = run_supervised_analysis(
                "Why did North revenue jump?",
                caller_roles=("analyst",),
                tenant_id="tenant-a",
                root_cause_pairs=[
                    RootCausePair(
                        current_task="Q1",
                        baseline_task="Q2",
                        dimension_columns=("region",),
                        value_column="total",
                        dimension_hierarchy=(("region",),),
                    )
                ],
            )
        finally:
            reset_breakers()

        root_claims = [c for c in result.claims if c.agent.value == "root_cause"]
        assert root_claims
        for claim in root_claims:
            _assert_text_never_asserts_causation(claim.text)
        for item in result.open_items:
            _assert_text_never_asserts_causation(item)
