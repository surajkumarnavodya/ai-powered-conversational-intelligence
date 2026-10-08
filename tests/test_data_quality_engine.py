"""Tests for `data_quality/engine.py` (Prompt 41,
`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

Every example number below was independently hand-computed and
cross-checked by directly exercising the module (via a scratch script,
not part of this diff) before being written here -- the same standard
`tests/test_predictive_analytics.py`'s own module docstring states.
"""

from __future__ import annotations

from agent.provenance import DataTruthLevel
from config.settings import Settings
from data_quality.engine import assess_data_quality, assess_table_quality
from data_quality.models import (
    CategoricalColumnRule,
    ConsistencyOperator,
    ConsistencyRule,
    DataQualityDimension,
    DataQualitySeverity,
    DateColumnRule,
    FreshnessRule,
    ReferentialCheck,
    TableQualityInput,
)

_SETTINGS = Settings()
_ANALYST = ("analyst",)
_VIEWER = ("viewer",)


def _customers_table(**overrides: object) -> TableQualityInput:
    rows = [
        (1, "a@x.com", "2024-01-01", "active"),
        (2, None, "2024-01-02", "active"),
        (3, "c@x.com", "2024-01-03", "bogus"),
        (4, None, "2024-01-04", "active"),
        (5, "e@x.com", "not-a-date", "active"),
        (6, "f@x.com", "2024-01-06", "active"),
        (7, None, "2024-01-07", "active"),
        (8, "h@x.com", "2024-01-08", "active"),
        (9, "i@x.com", "2024-01-09", "active"),
        (10, "i@x.com", "2024-01-10", "active"),
    ]
    base: dict = dict(
        table="customers",
        columns=["id", "email", "signup_date", "status"],
        rows=rows,
        date_rules=(
            DateColumnRule(column="signup_date", min_date="2020-01-01", max_date="2025-01-01"),
        ),
        categorical_rules=(
            CategoricalColumnRule(column="status", allowed_values=("active", "inactive")),
        ),
        unique_key_sets=(("email",),),
    )
    base.update(overrides)
    return TableQualityInput(**base)


class TestCompleteness:
    def test_a_null_rate_at_or_above_the_threshold_is_flagged(self):
        score = assess_table_quality(_customers_table(), _ANALYST, _SETTINGS)
        completeness = [
            f for f in score.findings if f.dimension == DataQualityDimension.COMPLETENESS
        ]
        assert len(completeness) == 1
        finding = completeness[0]
        assert finding.column == "email"
        assert finding.null_spike.null_count == 3
        assert finding.null_spike.total_count == 10
        assert finding.null_spike.null_rate == 0.3
        assert finding.severity == DataQualitySeverity.WARNING
        assert finding.claim.level == DataTruthLevel.DATABASE_FACT

    def test_a_per_column_override_can_tighten_or_loosen_the_threshold(self):
        loose = assess_table_quality(
            _customers_table(null_rate_overrides={"email": 0.5}), _ANALYST, _SETTINGS
        )
        assert not [f for f in loose.findings if f.dimension == DataQualityDimension.COMPLETENESS]

        tight = assess_table_quality(
            _customers_table(null_rate_overrides={"email": 0.1}), _ANALYST, _SETTINGS
        )
        assert [f for f in tight.findings if f.dimension == DataQualityDimension.COMPLETENESS]

    def test_never_assessed_when_disabled_or_no_rows(self):
        score = assess_table_quality(
            _customers_table(enable_column_statistics=False), _ANALYST, _SETTINGS
        )
        dim = next(
            d for d in score.dimension_scores if d.dimension == DataQualityDimension.COMPLETENESS
        )
        assert dim.score is None
        assert dim.checks_run == 0


class TestValidity:
    def test_an_unparseable_date_is_flagged_invalid_date(self):
        score = assess_table_quality(_customers_table(), _ANALYST, _SETTINGS)
        invalid = [
            f
            for f in score.findings
            if f.dimension == DataQualityDimension.VALIDITY
            and f.invalid_value is not None
            and f.invalid_value.reason == "invalid_date"
        ]
        assert len(invalid) == 1
        assert invalid[0].column == "signup_date"
        assert invalid[0].invalid_value.invalid_row_count == 1
        assert "not-a-date" in invalid[0].invalid_value.sample_values

    def test_an_out_of_range_date_is_flagged_separately_from_an_invalid_one(self):
        rows = [(1, "2019-01-01"), (2, "2021-01-01")]
        table = TableQualityInput(
            table="t",
            columns=["id", "d"],
            rows=rows,
            date_rules=(DateColumnRule(column="d", min_date="2020-01-01", max_date="2025-01-01"),),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        out_of_range = [
            f
            for f in score.findings
            if f.invalid_value is not None and f.invalid_value.reason == "out_of_range_date"
        ]
        assert len(out_of_range) == 1
        assert out_of_range[0].invalid_value.invalid_row_count == 1

    def test_a_value_outside_the_allowed_set_is_flagged_unexpected_category(self):
        score = assess_table_quality(_customers_table(), _ANALYST, _SETTINGS)
        unexpected = [
            f
            for f in score.findings
            if f.invalid_value is not None and f.invalid_value.reason == "unexpected_category"
        ]
        assert len(unexpected) == 1
        assert unexpected[0].column == "status"
        assert unexpected[0].invalid_value.sample_values == ("bogus",)


class TestUniqueness:
    def test_a_duplicate_composite_key_is_flagged(self):
        score = assess_table_quality(_customers_table(), _ANALYST, _SETTINGS)
        dup = [f for f in score.findings if f.dimension == DataQualityDimension.UNIQUENESS]
        assert len(dup) == 1
        assert dup[0].duplicate.key_columns == ("email",)
        assert dup[0].duplicate.distinct_duplicate_key_count == 1
        assert dup[0].duplicate.duplicate_row_count == 2
        # 7 of 10 rows have a non-null email; the 3 null-email rows are
        # excluded from both the numerator and the denominator.
        assert dup[0].duplicate.total_row_count == 7

    def test_a_row_with_a_null_key_component_is_never_counted(self):
        rows = [(1, None), (2, None), (3, "a")]
        table = TableQualityInput(
            table="t",
            columns=["id", "k"],
            rows=rows,
            unique_key_sets=(("k",),),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        assert not [f for f in score.findings if f.dimension == DataQualityDimension.UNIQUENESS]

    def test_no_duplicates_produces_no_finding(self):
        rows = [(1, "a"), (2, "b"), (3, "c")]
        table = TableQualityInput(
            table="t",
            columns=["id", "k"],
            rows=rows,
            unique_key_sets=(("k",),),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        assert not [f for f in score.findings if f.dimension == DataQualityDimension.UNIQUENESS]


class TestConsistency:
    def test_a_violated_cross_column_rule_is_flagged(self):
        rows = [
            (1, "2024-01-01", "2024-01-05"),
            (2, "2024-02-10", "2024-02-05"),
            (3, "2024-03-01", "2024-03-10"),
        ]
        table = TableQualityInput(
            table="orders",
            columns=["id", "start_date", "end_date"],
            rows=rows,
            consistency_rules=(
                ConsistencyRule(
                    name="start_before_end",
                    left_column="start_date",
                    operator=ConsistencyOperator.LE,
                    right_column="end_date",
                ),
            ),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        violations = [f for f in score.findings if f.dimension == DataQualityDimension.CONSISTENCY]
        assert len(violations) == 1
        assert violations[0].consistency_violation.violation_row_count == 1
        assert violations[0].consistency_violation.checked_row_count == 3
        assert violations[0].consistency_violation.skipped_row_count == 0

    def test_numeric_consistency_rules_are_also_supported(self):
        rows = [(1, 10, 20), (2, 15, 5)]
        table = TableQualityInput(
            table="t",
            columns=["id", "a", "b"],
            rows=rows,
            consistency_rules=(
                ConsistencyRule(
                    name="a_lt_b",
                    left_column="a",
                    operator=ConsistencyOperator.LT,
                    right_column="b",
                ),
            ),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        violations = [f for f in score.findings if f.dimension == DataQualityDimension.CONSISTENCY]
        assert violations[0].consistency_violation.violation_row_count == 1

    def test_a_row_with_a_null_side_is_skipped_not_a_violation(self):
        rows = [(1, None, 5), (2, 1, 2)]
        table = TableQualityInput(
            table="t",
            columns=["id", "a", "b"],
            rows=rows,
            consistency_rules=(
                ConsistencyRule(
                    name="a_le_b",
                    left_column="a",
                    operator=ConsistencyOperator.LE,
                    right_column="b",
                ),
            ),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        assert not [f for f in score.findings if f.dimension == DataQualityDimension.CONSISTENCY]


class TestReferentialIntegrity:
    def test_a_child_value_absent_from_the_parent_set_is_an_orphan(self):
        rows = [(1, "P1"), (2, "P2"), (3, "P9")]
        table = TableQualityInput(
            table="orders",
            columns=["id", "product_id"],
            rows=rows,
            referential_checks=(
                ReferentialCheck(
                    child_column="product_id",
                    parent_table="products",
                    parent_key_values=("P1", "P2"),
                ),
            ),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        orphans = [
            f for f in score.findings if f.dimension == DataQualityDimension.REFERENTIAL_INTEGRITY
        ]
        assert len(orphans) == 1
        assert orphans[0].orphan.orphan_row_count == 1
        assert orphans[0].orphan.distinct_orphan_value_count == 1
        assert orphans[0].orphan.sample_orphan_values == ("P9",)

    def test_a_null_child_value_is_never_counted_as_an_orphan(self):
        rows = [(1, None), (2, "P1")]
        table = TableQualityInput(
            table="orders",
            columns=["id", "product_id"],
            rows=rows,
            referential_checks=(
                ReferentialCheck(
                    child_column="product_id", parent_table="products", parent_key_values=("P1",)
                ),
            ),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        assert not [
            f for f in score.findings if f.dimension == DataQualityDimension.REFERENTIAL_INTEGRITY
        ]


class TestFreshness:
    def test_a_stale_latest_timestamp_is_flagged(self):
        rows = [(1, "2024-01-01"), (2, "2024-03-01")]
        table = TableQualityInput(
            table="orders",
            columns=["id", "start_date"],
            rows=rows,
            freshness_rule=FreshnessRule(
                timestamp_column="start_date",
                as_of="2024-06-01T00:00:00",
                max_staleness_seconds=86400,
            ),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        stale = [f for f in score.findings if f.dimension == DataQualityDimension.FRESHNESS]
        assert len(stale) == 1
        assert stale[0].severity == DataQualitySeverity.CRITICAL
        assert stale[0].freshness.staleness_seconds == 7_948_800.0

    def test_fresh_data_produces_no_finding(self):
        rows = [(1, "2024-05-31T12:00:00")]
        table = TableQualityInput(
            table="orders",
            columns=["id", "start_date"],
            rows=rows,
            freshness_rule=FreshnessRule(
                timestamp_column="start_date",
                as_of="2024-06-01T00:00:00",
                max_staleness_seconds=86400,
            ),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        assert not [f for f in score.findings if f.dimension == DataQualityDimension.FRESHNESS]

    def test_falls_back_to_the_settings_default_when_unset(self):
        rows = [(1, "2024-01-01")]
        table = TableQualityInput(
            table="orders",
            columns=["id", "start_date"],
            rows=rows,
            freshness_rule=FreshnessRule(timestamp_column="start_date", as_of="2024-01-02"),
            enable_column_statistics=False,
        )
        score = assess_table_quality(
            table, _ANALYST, Settings(data_quality_default_max_staleness_seconds=3600)
        )
        stale = [f for f in score.findings if f.dimension == DataQualityDimension.FRESHNESS]
        assert stale[0].freshness.max_staleness_seconds == 3600


class TestDistributionStability:
    def test_a_shifted_distribution_is_flagged(self):
        reference_rows = [(i,) for i in range(1, 21)]
        new_rows = [(i,) for i in range(1, 4)]
        table = TableQualityInput(
            table="t",
            columns=["id"],
            rows=new_rows,
            reference_columns=["id"],
            reference_rows=reference_rows,
            drift_columns=("id",),
            enable_column_statistics=False,
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        drift = [
            f for f in score.findings if f.dimension == DataQualityDimension.DISTRIBUTION_STABILITY
        ]
        assert len(drift) == 1
        assert drift[0].distribution_drift.feature == "id"

    def test_never_assessed_without_a_reference_dataset(self):
        table = TableQualityInput(
            table="t", columns=["id"], rows=[(1,), (2,)], enable_column_statistics=False
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        dim = next(
            d
            for d in score.dimension_scores
            if d.dimension == DataQualityDimension.DISTRIBUTION_STABILITY
        )
        assert dim.score is None


class TestScoring:
    def test_hand_verified_overall_score_for_the_customers_table(self):
        score = assess_table_quality(_customers_table(), _ANALYST, _SETTINGS)
        assert score.overall_score == 77.1
        by_dim = {d.dimension: d.score for d in score.dimension_scores}
        assert by_dim[DataQualityDimension.COMPLETENESS] == 70.0
        assert by_dim[DataQualityDimension.VALIDITY] == 90.0
        assert by_dim[DataQualityDimension.UNIQUENESS] == 71.4
        assert by_dim[DataQualityDimension.CONSISTENCY] is None
        assert by_dim[DataQualityDimension.REFERENTIAL_INTEGRITY] is None

    def test_a_clean_table_scores_100_on_every_assessed_dimension(self):
        table = TableQualityInput(
            table="t",
            columns=["id", "k"],
            rows=[(1, "a"), (2, "b"), (3, "c")],
            unique_key_sets=(("k",),),
        )
        score = assess_table_quality(table, _ANALYST, _SETTINGS)
        assert score.overall_score == 100.0
        assert score.findings == ()

    def test_assess_data_quality_averages_across_tables(self):
        clean = TableQualityInput(
            table="clean", columns=["id"], rows=[(1,), (2,)], enable_column_statistics=False
        )
        dirty = TableQualityInput(
            table="dirty",
            columns=["id", "k"],
            rows=[(1, "a"), (2, "a")],
            unique_key_sets=(("k",),),
            enable_column_statistics=False,
        )
        report = assess_data_quality([clean, dirty], _ANALYST, _SETTINGS)
        assert report.tables[1].overall_score == 0.0  # 2 of 2 rows duplicate on "k"
        assert report.overall_score == 0.0  # clean has no assessed dimension -> excluded
        assert report.truth_level == DataTruthLevel.DATABASE_FACT


class TestAuthorization:
    def test_a_restricted_column_finding_is_suppressed_for_a_viewer(self, monkeypatch):
        import data_quality.engine as engine_module

        monkeypatch.setattr(
            engine_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "email"): "restricted"},
        )
        table = _customers_table()
        viewer_score = assess_table_quality(table, _VIEWER, _SETTINGS)
        assert not any(f.column == "email" for f in viewer_score.findings)
        assert any("email" in note for note in viewer_score.insufficient_checks)

        analyst_score = assess_table_quality(table, _ANALYST, _SETTINGS)
        assert any(f.column == "email" for f in analyst_score.findings)

    def test_suppression_never_rejects_the_whole_scan(self, monkeypatch):
        import data_quality.engine as engine_module

        monkeypatch.setattr(
            engine_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "email"): "restricted"},
        )
        table = _customers_table()
        viewer_score = assess_table_quality(table, _VIEWER, _SETTINGS)
        # The status column's finding is unaffected by email's suppression.
        assert any(f.column == "status" for f in viewer_score.findings)


class TestNoLlmInventedRule:
    def test_consistency_rule_has_no_formula_field_only_typed_columns_and_an_operator(self):
        fields = set(ConsistencyRule.model_fields)
        assert fields == {"name", "left_column", "operator", "right_column", "description"}

    def test_consistency_operator_is_a_closed_enum(self):
        assert {op.value for op in ConsistencyOperator} == {"<=", "<", ">=", ">", "==", "!="}
