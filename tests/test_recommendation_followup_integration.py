"""Integration tests for the follow-up chips as they actually reach the
UI: through `recommendation.engine.generate_recommendations` (which owns
the evidence/confidence/authorization gates) and through
`agent.nodes.generate_recommendations_node` (which builds the grounding
context out of live `AgentState`).

The unit-level relevance/safety rules live in
`tests/test_recommendation_followups.py`. What is tested here is the
wiring: that a chip is produced as a real `Recommendation` of the right
kind with a short `action` and a full question, that the engine's own
gates still apply to it, and that the node assembles its context from
state without any new query.
"""

from __future__ import annotations

from analytics.engine import compute_analytics_result
from recommendation.engine import RecommendationInputs, generate_recommendations
from recommendation.followups import (
    MAX_LABEL_CHARS,
    FollowUpConcept,
    FollowUpContext,
    FollowUpSchemaTable,
)
from recommendation.models import RecommendationCategory, RecommendationKind

from agent.nodes import _follow_up_context_for_state
from agent.provenance import DataTruthLevel
from config.settings import Settings

SALES_DDL = """CREATE TABLE FactSales (
    SalesKey INT NOT NULL,
    OrderDate DATETIME NULL,
    Region NVARCHAR(50) NULL,
    EmployeeName NVARCHAR(100) NULL,
    SalesAmount DECIMAL(18,2) NULL,
    PRIMARY KEY (SalesKey)
)"""

COLUMNS = ["ProductCategory", "SalesAmount"]
ROWS = [
    ("Bikes", 500.0),
    ("Helmets", 300.0),
    ("Tires", 150.0),
    ("Jerseys", 90.0),
    ("Gloves", 60.0),
    ("Socks", 20.0),
]


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "enable_recommendation_engine": True,
        "enable_follow_up_suggestions": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def follow_up_context(**overrides: object) -> FollowUpContext:
    base: dict[str, object] = {
        "question": "What is revenue by product category?",
        "result_columns": tuple(COLUMNS),
        "schema_tables": (FollowUpSchemaTable(table_name="FactSales", ddl=SALES_DDL),),
    }
    base.update(overrides)
    return FollowUpContext(**base)  # type: ignore[arg-type]


def run_engine(*, roles: tuple[str, ...] = ("analyst",), **setting_overrides: object) -> tuple:
    return generate_recommendations(
        RecommendationInputs(
            analytics_result=compute_analytics_result(COLUMNS, ROWS),
            follow_up_context=follow_up_context(),
            caller_roles=roles,
        ),
        settings(**setting_overrides),
    )


def chips(recommendations: tuple) -> list:
    return [r for r in recommendations if r.kind == RecommendationKind.NEXT_QUESTION]


class TestEngineProducesChips:
    def test_the_engine_now_produces_next_question_recommendations(self) -> None:
        # Before this feature, `recommendation.engine` produced none at
        # all, so the chip component rendered nothing in the live
        # pipeline no matter what the result looked like.
        assert chips(run_engine())

    def test_a_chip_carries_a_short_action_and_a_full_question(self) -> None:
        # The split is what lets the chip read "Top 5" while what gets
        # asked stays unambiguous.
        top = next(r for r in chips(run_engine()) if r.action == "Top 5")
        assert len(top.action) <= MAX_LABEL_CHARS
        assert top.claim.value == "Top 5 Product Category by Sales Amount"
        assert top.action != top.claim.value

    def test_a_chip_is_categorized_as_exploration(self) -> None:
        # Not reused from one of the nine finding categories, and not left
        # as None (which already means "pre-taxonomy").
        for chip in chips(run_engine()):
            assert chip.category == RecommendationCategory.EXPLORATION

    def test_a_chip_is_always_an_ai_inference_grounded_in_facts(self) -> None:
        for chip in chips(run_engine()):
            assert chip.claim.level == DataTruthLevel.AI_INFERENCE
            assert chip.evidence
            for item in chip.evidence:
                assert item.level != DataTruthLevel.AI_INFERENCE

    def test_a_chip_records_the_rule_that_produced_it(self) -> None:
        for chip in chips(run_engine()):
            assert chip.rule_or_model.startswith("recommendation.followups.")

    def test_chips_come_after_every_real_finding(self) -> None:
        # The engine returns in rule order, and a suggestion is the least
        # urgent thing it produces.
        recommendations = generate_recommendations(
            RecommendationInputs(
                analytics_result=compute_analytics_result(COLUMNS, ROWS),
                follow_up_context=follow_up_context(),
                restricted_column_hits=(("FactSales", "EmployeeName"),),
                caller_roles=("admin",),
            ),
            settings(),
        )
        kinds = [r.kind for r in recommendations]
        if RecommendationKind.ACTION in kinds:
            assert kinds.index(RecommendationKind.ACTION) < kinds.index(
                RecommendationKind.NEXT_QUESTION
            )

    def test_no_follow_up_context_produces_no_chips(self) -> None:
        # A report job or batch recompute has no conversation -- that is
        # zero chips, never an error.
        recommendations = generate_recommendations(
            RecommendationInputs(
                analytics_result=compute_analytics_result(COLUMNS, ROWS),
                caller_roles=("analyst",),
            ),
            settings(),
        )
        assert chips(recommendations) == []

    def test_the_follow_up_flag_is_independent_of_the_engine_flag(self) -> None:
        # An operator can switch the chips off without losing real
        # findings.
        recommendations = run_engine(enable_follow_up_suggestions=False)
        assert chips(recommendations) == []

    def test_the_engine_flag_off_produces_nothing_at_all(self) -> None:
        assert run_engine(enable_recommendation_engine=False) == ()

    def test_the_engine_confidence_floor_applies_to_chips(self) -> None:
        # A chip is not exempt from the floor every other candidate
        # clears: raise it above the strongest rule and nothing survives.
        assert chips(run_engine(recommendation_min_confidence=0.99)) == []

    def test_a_chip_never_contains_sql(self) -> None:
        for chip in chips(run_engine()):
            text = f"{chip.action} {chip.claim.value}".upper()
            for token in ("SELECT ", "FROM ", "WHERE ", "GROUP BY", ";"):
                assert token not in text


class TestRestrictedColumnAuthorizationStillApplies:
    """The role-independent filter in `recommendation.followups` is the
    first gate; the engine's existing `_authorized` check is the second,
    and a chip is not exempt from it."""

    def test_a_chip_naming_a_restricted_column_is_suppressed_for_an_unauthorized_viewer(
        self, monkeypatch
    ) -> None:
        # Classify `Region` restricted so the engine's own authorization
        # gate has something to act on: `recommendation.followups`' own
        # name-pattern filter leaves `Region` alone (it is not
        # name-detectable as sensitive), which is precisely why this
        # second, role-aware gate matters.
        import recommendation.engine as engine_module

        monkeypatch.setattr(
            engine_module,
            "load_sensitive_columns",
            lambda path=None: {("factsales", "region"): "restricted"},
        )
        governed = follow_up_context(
            governing_dimensions=(FollowUpConcept(business_name="Sales Region", column="Region"),),
        )

        def actions_for(roles: tuple[str, ...]) -> list[str]:
            recommendations = generate_recommendations(
                RecommendationInputs(
                    analytics_result=compute_analytics_result(COLUMNS, ROWS),
                    follow_up_context=governed,
                    caller_roles=roles,
                ),
                settings(),
            )
            return [c.action for c in chips(recommendations)]

        # Asserted in both directions, so the test cannot pass merely
        # because the chip was never produced in the first place.
        assert "By Sales Region" in actions_for(("admin",))
        assert "By Sales Region" not in actions_for(("viewer",))


class TestNodeBuildsContextFromState:
    """`_follow_up_context_for_state` reads only facts already on state --
    no new query, no new retrieval."""

    def test_it_collects_every_grounding_input_from_state(self) -> None:
        state = {
            "question": "What is revenue by product category?",
            "result_columns": ["ProductCategory", "SalesAmount"],
            "schema_tables": [
                {"table_name": "FactSales", "ddl": SALES_DDL, "similarity_score": 0.9}
            ],
            "governing_metrics": [{"business_name": "Net Revenue", "approved_expression": "x"}],
            "governing_dimensions": [{"business_name": "Sales Region", "column": "Region"}],
            "conversation_history": [
                {"question": "Show me total sales", "sql": "SELECT 1"},
                {"question": "  ", "sql": ""},
            ],
        }
        context = _follow_up_context_for_state(state)  # type: ignore[arg-type]

        assert context.question == "What is revenue by product category?"
        assert context.result_columns == ("ProductCategory", "SalesAmount")
        assert context.schema_tables[0].table_name == "FactSales"
        assert context.governing_metrics == (FollowUpConcept(business_name="Net Revenue"),)
        assert context.governing_dimensions == (
            FollowUpConcept(business_name="Sales Region", column="Region"),
        )
        # The blank prior question is dropped rather than carried as an
        # empty suppression key that would match nothing useful.
        assert context.prior_questions == ("Show me total sales",)

    def test_an_empty_state_yields_an_empty_context_rather_than_raising(self) -> None:
        context = _follow_up_context_for_state({})  # type: ignore[arg-type]
        assert context.question == ""
        assert context.schema_tables == ()
        assert context.governing_dimensions == ()
        assert context.prior_questions == ()

    def test_a_metric_or_dimension_with_no_business_name_is_skipped(self) -> None:
        state = {
            "governing_metrics": [{"approved_expression": "SUM(x)"}],
            "governing_dimensions": [{"column": "Region"}],
        }
        context = _follow_up_context_for_state(state)  # type: ignore[arg-type]
        assert context.governing_metrics == ()
        assert context.governing_dimensions == ()

    def test_the_context_it_builds_actually_produces_chips(self) -> None:
        # End to end over the real node helper and the real engine.
        state = {
            "question": "What is revenue by product category?",
            "result_columns": ["ProductCategory", "SalesAmount"],
            "schema_tables": [
                {"table_name": "FactSales", "ddl": SALES_DDL, "similarity_score": 0.9}
            ],
            "governing_dimensions": [{"business_name": "Sales Region", "column": "Region"}],
        }
        recommendations = generate_recommendations(
            RecommendationInputs(
                analytics_result=compute_analytics_result(COLUMNS, ROWS),
                follow_up_context=_follow_up_context_for_state(state),  # type: ignore[arg-type]
                caller_roles=("analyst",),
            ),
            settings(),
        )
        actions = [c.action for c in chips(recommendations)]
        assert "Top 5" in actions
        assert "By Sales Region" in actions
