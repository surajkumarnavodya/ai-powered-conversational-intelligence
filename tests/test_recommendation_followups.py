"""Tests for `recommendation.followups` -- the grounded one-click
follow-up suggestions ("Ask next" chips).

Every case calls the real production function. `analytics.engine
.compute_analytics_result` is called for real too (it is pure and
deterministic), so the result shapes and findings these suggestions are
grounded in are the genuine ones, not fabricated stand-ins -- the whole
point of the feature is that a suggestion is only offered when the real
analytics output supports it.
"""

from __future__ import annotations

import pytest
from analytics.engine import compute_analytics_result
from analytics.models import AnalyticsResult, ResultShape
from recommendation.followups import (
    MAX_LABEL_CHARS,
    FollowUpConcept,
    FollowUpContext,
    FollowUpSchemaTable,
    build_follow_up_suggestions,
)

from agent.provenance import DataTruthLevel
from config.settings import Settings

# A realistic fact-table DDL in `db.schema_introspection.render_ddl`'s own
# shape, carrying: a surrogate key, a real temporal column, two plain
# dimension columns, three columns the sensitivity classifier must refuse
# to suggest, and a measure.
SALES_DDL = """CREATE TABLE FactSales (
    SalesKey INT NOT NULL,
    OrderDate DATETIME NULL,
    Region NVARCHAR(50) NULL,
    Industry NVARCHAR(50) NULL,
    EmployeeName NVARCHAR(100) NULL,
    Email NVARCHAR(100) NULL,
    DiagnosisCode NVARCHAR(10) NULL,
    SalesAmount DECIMAL(18,2) NULL,
    PRIMARY KEY (SalesKey)
)"""

CATEGORICAL_COLUMNS = ["ProductCategory", "SalesAmount"]
CATEGORICAL_ROWS = [
    ("Bikes", 500.0),
    ("Helmets", 300.0),
    ("Tires", 150.0),
    ("Jerseys", 90.0),
    ("Gloves", 60.0),
    ("Socks", 20.0),
]


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"enable_follow_up_suggestions": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def sales_table() -> FollowUpSchemaTable:
    return FollowUpSchemaTable(table_name="FactSales", ddl=SALES_DDL)


def context(**overrides: object) -> FollowUpContext:
    base: dict[str, object] = {
        "question": "What is revenue by product category?",
        "result_columns": tuple(CATEGORICAL_COLUMNS),
        "schema_tables": (sales_table(),),
    }
    base.update(overrides)
    return FollowUpContext(**base)  # type: ignore[arg-type]


def categorical_result() -> AnalyticsResult:
    return compute_analytics_result(CATEGORICAL_COLUMNS, CATEGORICAL_ROWS)


def labels(suggestions: object) -> list[str]:
    return [s.label for s in suggestions]  # type: ignore[attr-defined]


def questions(suggestions: object) -> list[str]:
    return [s.question for s in suggestions]  # type: ignore[attr-defined]


class TestRelevance:
    """A suggestion is offered only when this result's own shape, the real
    schema, or the approved catalog actually supports it."""

    def test_a_categorical_result_offers_a_top_n_and_a_breakdown(self) -> None:
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        assert "Top 5" in labels(suggestions)
        assert any(label.startswith("By ") for label in labels(suggestions))

    def test_top_n_is_grounded_in_the_real_ranking_finding(self) -> None:
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        top = next(s for s in suggestions if s.label == "Top 5")
        # The evidence is the analytics engine's own DATABASE_FACT ranking
        # claim, reused verbatim -- not a restatement this module invented.
        assert [item.level for item in top.evidence] == [DataTruthLevel.DATABASE_FACT]
        assert "ranks first" in top.evidence[0].value
        assert top.question == "Top 5 Product Category by Sales Amount"

    def test_top_n_is_not_offered_when_the_ranking_is_already_that_short(self) -> None:
        # Three rows are already their own top 5: narrowing drops nothing,
        # so suggesting it would be busywork, not analysis.
        result = compute_analytics_result(
            CATEGORICAL_COLUMNS, [("Bikes", 500.0), ("Helmets", 300.0), ("Tires", 150.0)]
        )
        suggestions = build_follow_up_suggestions(context(), result, settings())
        assert not any(label.startswith("Top ") for label in labels(suggestions))

    def test_a_time_series_offers_a_period_comparison(self) -> None:
        result = compute_analytics_result(
            ["OrderMonth", "SalesAmount"],
            [("2025-01", 100.0), ("2025-02", 120.0), ("2025-03", 90.0)],
        )
        suggestions = build_follow_up_suggestions(
            context(result_columns=("OrderMonth", "SalesAmount")), result, settings()
        )
        assert "Compare periods" in labels(suggestions)

    def test_a_time_series_does_not_offer_show_trend(self) -> None:
        # The trend is what they are already looking at.
        result = compute_analytics_result(
            ["OrderMonth", "SalesAmount"],
            [("2025-01", 100.0), ("2025-02", 120.0), ("2025-03", 90.0)],
        )
        suggestions = build_follow_up_suggestions(
            context(result_columns=("OrderMonth", "SalesAmount")), result, settings()
        )
        assert "Show trend" not in labels(suggestions)

    def test_a_non_time_series_offers_show_trend_when_a_real_date_column_exists(self) -> None:
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        trend = next(s for s in suggestions if s.label == "Show trend")
        assert "OrderDate" in trend.evidence[0].value
        assert trend.columns == ("OrderDate",)

    def test_show_trend_is_withheld_when_no_temporal_column_exists(self) -> None:
        # Requirement: never suggest an action the available data cannot
        # answer. Without a date column there is nothing to trend over.
        no_dates = FollowUpSchemaTable(
            table_name="FactSales",
            ddl=(
                "CREATE TABLE FactSales (\n"
                "    SalesKey INT NOT NULL,\n"
                "    Region NVARCHAR(50) NULL,\n"
                "    SalesAmount DECIMAL(18,2) NULL\n"
                ")"
            ),
        )
        suggestions = build_follow_up_suggestions(
            context(schema_tables=(no_dates,)), categorical_result(), settings()
        )
        assert "Show trend" not in labels(suggestions)

    def test_a_breakdown_names_no_column_outside_the_retrieved_schema(self) -> None:
        real_columns = {"SalesKey", "OrderDate", "Region", "Industry", "SalesAmount"}
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        for suggestion in suggestions:
            for column in suggestion.columns:
                assert column in real_columns | set(CATEGORICAL_COLUMNS)

    def test_a_surrogate_key_is_never_offered_as_a_dimension(self) -> None:
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        assert not any("SalesKey" in s.columns for s in suggestions)

    def test_a_dimension_already_in_the_result_is_not_re_offered(self) -> None:
        # "Break down by Region" when Region is already a result column is
        # a suggestion to re-ask what was just answered.
        suggestions = build_follow_up_suggestions(
            context(result_columns=("Region", "SalesAmount")), categorical_result(), settings()
        )
        assert not any("Region" in s.columns for s in suggestions)


class TestApprovedMetricsAndDimensions:
    """A published catalog definition takes precedence over a raw column
    name, and is evidenced as CONFIRMED_BUSINESS_TRUTH."""

    def test_a_governed_dimension_is_preferred_and_names_the_chip(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert "By Sales Region" in labels(suggestions)

    def test_a_governed_dimension_carries_confirmed_business_truth_evidence(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        governed = next(s for s in suggestions if s.label == "By Sales Region")
        assert DataTruthLevel.CONFIRMED_BUSINESS_TRUTH in [i.level for i in governed.evidence]
        # And still verified against real schema, not taken on the
        # catalog's word alone.
        assert any("declares column Region" in i.value for i in governed.evidence)

    def test_an_approved_metric_name_phrases_the_question(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(governing_metrics=(FollowUpConcept(business_name="Net Revenue"),)),
            categorical_result(),
            settings(),
        )
        assert any("Net Revenue" in question for question in questions(suggestions))

    def test_a_governed_dimension_whose_column_is_not_in_the_schema_is_dropped(self) -> None:
        # The catalog can name a dimension that this question's retrieved
        # tables do not expose. Suggesting it would be suggesting data
        # that is not reachable here.
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Channel", column="ChannelName"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert "By Sales Channel" not in labels(suggestions)

    def test_a_governed_dimension_with_no_column_recorded_is_dropped(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(governing_dimensions=(FollowUpConcept(business_name="Sales Region"),)),
            categorical_result(),
            settings(),
        )
        assert "By Sales Region" not in labels(suggestions)

    def test_an_unverified_schema_dimension_discloses_that_it_is_inferred(self) -> None:
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        inferred = next(s for s in suggestions if s.label == "By Industry")
        assert inferred.limitations
        assert "inferred" in inferred.limitations[0]

    def test_a_governed_dimension_outranks_the_same_column_by_raw_name(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert (
            next(s for s in suggestions if s.label == "By Sales Region").confidence
            > next(s for s in suggestions if s.label == "By Industry").confidence
        )


class TestEmptyAndUngroundedResults:
    """Nothing to drill into means no suggestions -- never a guess."""

    def test_an_empty_result_offers_nothing(self) -> None:
        result = compute_analytics_result(CATEGORICAL_COLUMNS, [])
        assert result.shape == ResultShape.EMPTY
        assert build_follow_up_suggestions(context(), result, settings()) == ()

    def test_a_zero_row_result_offers_nothing_even_with_a_non_empty_shape(self) -> None:
        # Defence in depth: the row count is checked independently of the
        # shape classification, so a shape that disagrees cannot slip a
        # suggestion through.
        result = AnalyticsResult(row_count=0, shape=ResultShape.CATEGORICAL_AGGREGATE)
        assert build_follow_up_suggestions(context(), result, settings()) == ()

    def test_no_analytics_result_offers_nothing(self) -> None:
        # Result shape is a required grounding input; with the analytics
        # engine off or failed, guessing the shape is exactly the
        # unsupported suggestion this module exists to avoid.
        assert build_follow_up_suggestions(context(), None, settings()) == ()

    def test_no_schema_still_yields_the_result_grounded_suggestions(self) -> None:
        # Less context means fewer suggestions, never an error.
        suggestions = build_follow_up_suggestions(
            context(schema_tables=()), categorical_result(), settings()
        )
        assert "Top 5" in labels(suggestions)
        assert not any(label.startswith("By ") for label in labels(suggestions))

    def test_an_entirely_empty_context_never_raises(self) -> None:
        assert (
            build_follow_up_suggestions(FollowUpContext(), categorical_result(), settings())
            is not None
        )

    def test_the_feature_flag_turns_it_off_completely(self) -> None:
        assert (
            build_follow_up_suggestions(
                context(), categorical_result(), settings(enable_follow_up_suggestions=False)
            )
            == ()
        )


class TestRestrictedFields:
    """A chip proactively steers someone toward a field, so the bar is
    higher than for a statistic they explicitly asked for: a sensitive
    column is never named, for any caller."""

    @pytest.mark.parametrize("column", ["EmployeeName", "Email", "DiagnosisCode"])
    def test_a_sensitive_column_is_never_suggested(self, column: str) -> None:
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        assert not any(column in s.columns for s in suggestions)
        assert not any(column in s.question for s in suggestions)

    def test_a_sensitive_column_is_withheld_regardless_of_the_governed_catalog(self) -> None:
        # Even an approved catalog dimension does not unlock a column the
        # classifier puts above the ceiling: approval is about business
        # meaning, not about disclosure.
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Staff Member", column="EmployeeName"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert "By Staff Member" not in labels(suggestions)

    def test_the_only_dimensions_offered_are_the_unclassified_ones(self) -> None:
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        offered = {column for s in suggestions for column in s.columns}
        assert offered & {"Region", "Industry"}
        assert not offered & {"EmployeeName", "Email", "DiagnosisCode"}


class TestNoRepetition:
    """Two chips that ask the same thing read as a bug."""

    def test_a_question_already_asked_in_this_turn_is_dropped(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(question="Top 5 Product Category by Sales Amount"),
            categorical_result(),
            settings(),
        )
        assert "Top 5" not in labels(suggestions)

    def test_a_question_asked_earlier_in_the_conversation_is_dropped(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(prior_questions=("top 5 product category by sales amount?",)),
            categorical_result(),
            settings(),
        )
        assert "Top 5" not in labels(suggestions)

    def test_the_same_column_is_never_offered_twice_under_two_names(self) -> None:
        # The governed rule and the schema rule can both reach `Region`.
        # Text deduplication alone would miss it, since "By Sales Region"
        # and "By Region" differ as strings while being one choice.
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        naming_region = [s for s in suggestions if "Region" in s.columns]
        assert len(naming_region) == 1
        assert naming_region[0].label == "By Sales Region"

    def test_every_label_and_question_in_one_batch_is_unique(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert len(set(labels(suggestions))) == len(suggestions)
        assert len(set(questions(suggestions))) == len(suggestions)

    def test_a_measure_column_shared_with_an_earlier_chip_is_not_a_repeat(self) -> None:
        # Dedup is on "every column already claimed", not "any": a
        # breakdown that introduces a new dimension is still useful even
        # though it reports the same measure.
        suggestions = build_follow_up_suggestions(context(), categorical_result(), settings())
        assert "Top 5" in labels(suggestions)
        assert any(label.startswith("By ") for label in labels(suggestions))


class TestShortLabelsAndDeterminism:
    def test_every_label_is_short_enough_to_scan(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert suggestions
        for suggestion in suggestions:
            assert len(suggestion.label) <= MAX_LABEL_CHARS

    def test_a_governed_name_too_long_for_a_chip_is_dropped_not_truncated(self) -> None:
        # A truncated chip reading "By Regional Sales Terr…" is worse than
        # no chip: the reader cannot tell what it asks.
        long_name = "Regional Sales Territory Grouping"
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(FollowUpConcept(business_name=long_name, column="Region"),),
            ),
            categorical_result(),
            settings(),
        )
        assert not any(long_name in label for label in labels(suggestions))
        assert not any("…" in label for label in labels(suggestions))

    def test_the_cap_is_respected(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(), categorical_result(), settings(max_follow_up_suggestions=2)
        )
        assert len(suggestions) == 2

    def test_a_zero_cap_yields_nothing(self) -> None:
        assert (
            build_follow_up_suggestions(
                context(), categorical_result(), settings(max_follow_up_suggestions=0)
            )
            == ()
        )

    def test_the_cap_drops_the_weakest_suggestion_first(self) -> None:
        capped = build_follow_up_suggestions(
            context(), categorical_result(), settings(max_follow_up_suggestions=1)
        )
        full = build_follow_up_suggestions(context(), categorical_result(), settings())
        assert labels(capped) == labels(full)[:1]
        assert capped[0].confidence == max(s.confidence for s in full)

    def test_the_same_input_always_yields_the_same_output(self) -> None:
        first = build_follow_up_suggestions(context(), categorical_result(), settings())
        second = build_follow_up_suggestions(context(), categorical_result(), settings())
        assert [(s.label, s.question, s.confidence) for s in first] == [
            (s.label, s.question, s.confidence) for s in second
        ]

    def test_top_n_is_configurable(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(), categorical_result(), settings(follow_up_top_n=3)
        )
        assert "Top 3" in labels(suggestions)


class TestNoSqlAndEvidenceFloor:
    def test_no_suggestion_contains_sql(self) -> None:
        # A chip carries natural language only. If it ever carried SQL,
        # the frontend would be shipping a query it never validated.
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert suggestions
        for suggestion in suggestions:
            text = f"{suggestion.label} {suggestion.question}".upper()
            for token in ("SELECT ", "FROM ", "WHERE ", "GROUP BY", "JOIN ", ";"):
                assert token not in text

    def test_every_suggestion_carries_grounding_evidence(self) -> None:
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        assert suggestions
        for suggestion in suggestions:
            assert suggestion.evidence

    def test_no_evidence_item_is_an_inference(self) -> None:
        # An inference cannot ground another inference -- the same rule
        # `Recommendation.evidence` enforces at the type level.
        suggestions = build_follow_up_suggestions(
            context(
                governing_dimensions=(
                    FollowUpConcept(business_name="Sales Region", column="Region"),
                ),
            ),
            categorical_result(),
            settings(),
        )
        for suggestion in suggestions:
            for item in suggestion.evidence:
                assert item.level != DataTruthLevel.AI_INFERENCE

    def test_the_question_text_is_never_used_as_evidence(self) -> None:
        # User input is not a fact. The question is only ever used to
        # suppress a repeat.
        question = "What is revenue by product category?"
        suggestions = build_follow_up_suggestions(
            context(question=question), categorical_result(), settings()
        )
        for suggestion in suggestions:
            for item in suggestion.evidence:
                assert question not in item.value
