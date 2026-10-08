"""Regression test for a governance invariant that spans three modules
at once -- `data_quality/models.py`, `data_quality/engine.py`/`lineage.py`,
and `agent/multiagent/agents.py`'s `data_quality_handler` -- so per
`tests/security/README.md`'s own rule, it belongs here rather than being
split across module-specific test files.

Master-contract rule 10 ("never present inference, simulation or
prediction as observed fact"), applied specifically to the Data Quality
Command Center (Prompt 41, `41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`)
-- the *inverse* direction every other engine's own test in this
directory proves. Every other engine (`analytics.scenario`/
`optimization`/`analytics.predictive`/`analytics.causal`) must never
present its own inference/simulation/prediction AS an observed fact.
This engine's findings genuinely ARE observed facts (a null rate, a
duplicate key, an orphaned value are deterministic measurements of real,
already-fetched rows) -- so the invariant here is the honest
classification itself, proven three ways:

1. **Structural**: `data_quality.models.ConsistencyRule` has no formula/
   expression field -- only typed column names and a closed
   `ConsistencyOperator` -- an LLM can never invent what gets checked.
2. **Behavioral**: every `DataQualityFinding.claim.level` and the overall
   `DataQualityReport.truth_level` are `DataTruthLevel.DATABASE_FACT`,
   never `AI_INFERENCE`/`CONFIRMED_BUSINESS_TRUTH`, across every
   dimension.
3. **Text-level, through the one live multi-agent handler**: every claim
   `data_quality_handler` actually renders carries `truth_level ==
   DATABASE_FACT` -- unlike `predictive_handler`'s own claims, which must
   say "modeled estimate"/"not an observed fact," these must NOT say
   either, since saying so about a genuine observed fact would itself be
   dishonest in the other direction.
4. **Lineage never overclaims**: `data_quality.lineage` never asserts a
   governed metric IS broken/wrong -- only that it reads a table with an
   open finding (exposure, not a verdict).
"""

from __future__ import annotations

from agent.provenance import DataTruthLevel
from config.settings import Settings
from data_quality.engine import assess_data_quality, assess_table_quality
from data_quality.lineage import CatalogConceptRef, TableFindingsSummary, build_lineage_report
from data_quality.models import (
    ConsistencyOperator,
    ConsistencyRule,
    TableQualityInput,
)

_SETTINGS = Settings()
_ANALYST = ("analyst",)


def _table_with_every_dimension() -> TableQualityInput:
    return TableQualityInput(
        table="t",
        columns=["id", "k", "a", "b"],
        rows=[(1, "x", 1, 2), (2, "x", 5, 1)],
        unique_key_sets=(("k",),),
        consistency_rules=(
            ConsistencyRule(
                name="a_le_b", left_column="a", operator=ConsistencyOperator.LE, right_column="b"
            ),
        ),
    )


class TestStructuralNoFormulaField:
    def test_consistency_rule_has_no_free_text_formula_field(self):
        assert "formula" not in ConsistencyRule.model_fields
        assert "expression" not in ConsistencyRule.model_fields

    def test_consistency_operator_is_a_closed_reviewed_enum(self):
        assert {op.value for op in ConsistencyOperator} == {"<=", "<", ">=", ">", "==", "!="}


class TestTruthLevelAlwaysDatabaseFact:
    def test_every_finding_across_every_dimension_is_database_fact(self):
        score = assess_table_quality(_table_with_every_dimension(), _ANALYST, _SETTINGS)
        assert score.findings  # sanity: this scenario does produce findings
        assert all(f.claim.level == DataTruthLevel.DATABASE_FACT for f in score.findings)

    def test_the_overall_report_truth_level_is_database_fact(self):
        report = assess_data_quality([_table_with_every_dimension()], _ANALYST, _SETTINGS)
        assert report.truth_level == DataTruthLevel.DATABASE_FACT

    def test_an_empty_clean_table_is_still_database_fact(self):
        clean = TableQualityInput(table="clean", columns=["id"], rows=[(1,), (2,)])
        report = assess_data_quality([clean], _ANALYST, _SETTINGS)
        assert report.truth_level == DataTruthLevel.DATABASE_FACT


class TestHandlerClaimTextNeverMisclassifiesAnObservedFact:
    """Exercises the one live caller (`agent.multiagent.agents
    .data_quality_handler`) end to end -- the actual claims a user would
    see."""

    def test_claims_carry_database_fact_not_ai_inference(self, monkeypatch):
        import agent.multiagent.supervisor as supervisor
        import agent.multiagent.tools as tools
        from agent.multiagent.policy import reset_breakers
        from agent.multiagent.supervisor import DataQualityRequest, run_supervised_analysis

        reset_breakers()
        settings = _SETTINGS

        def fake_sql(question, **kwargs):
            rows = [
                (1, "a@x.com"),
                (2, None),
                (3, None),
            ]
            return {
                "status": "succeeded",
                "sql": "SELECT 1",
                "row_count": len(rows),
                "result_columns": ["id", "email"],
                "result_rows": rows,
                "analytical_result": None,
                "analytical_intent": None,
                "recommendations": [],
            }

        monkeypatch.setattr(supervisor, "get_settings", lambda: settings)
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
                "How complete is our customer data?",
                caller_roles=("analyst",),
                tenant_id="tenant-a",
                data_quality_requests=[DataQualityRequest(data_task="Q1", table_name="customers")],
            )
        finally:
            reset_breakers()

        dq_claims = [c for c in result.claims if c.agent.value == "data_quality"]
        assert dq_claims
        for claim in dq_claims:
            assert claim.truth_level == DataTruthLevel.DATABASE_FACT
            # Unlike every AI_INFERENCE engine's own claims, these must NOT
            # carry the "modeled estimate"/"not an observed fact" disclosure
            # -- that disclosure would itself misclassify a genuine
            # observed fact.
            assert "modeled estimate" not in claim.text
            assert "not an observed fact" not in claim.text


class TestLineageNeverOverclaims:
    def test_lineage_reports_exposure_never_a_verdict_about_correctness(self):
        summary = TableFindingsSummary(table="customers", severity_counts={"critical": 3})
        concept = CatalogConceptRef(
            concept_key="customer_ltv",
            name="Customer LTV",
            concept_type="metric",
            status="published",
            owner="analyst",
            database_id="default",
            source_tables=("customers",),
        )
        report = build_lineage_report([summary], [concept])
        for note in report.limitations:
            assert "is broken" not in note.lower()
            assert "is wrong" not in note.lower()
            assert "is incorrect" not in note.lower()
        assert any("not a verdict" in note.lower() for note in report.limitations)

    def test_only_a_published_concept_can_ever_be_linked(self):
        summary = TableFindingsSummary(table="customers", severity_counts={"critical": 1})
        draft_concept = CatalogConceptRef(
            concept_key="customer_ltv",
            name="Customer LTV",
            concept_type="metric",
            status="draft",
            owner="analyst",
            database_id="default",
            source_tables=("customers",),
        )
        report = build_lineage_report([summary], [draft_concept])
        assert report.impacts[0].affected_concepts == ()
