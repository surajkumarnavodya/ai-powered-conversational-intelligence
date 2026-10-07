"""Unit tests for `analytics/scenario.py` -- Prompt 38
(`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`)'s governed What-If Scenario
Engine.

Covers, matching the mission's own explicit testing requirement
("boundary, unrealistic and missing-data cases"):

- typed parameters with units/bounds/affected-metric/model-version,
- running a scenario without mutating the baseline rows,
- a boundary value exactly at min/max (valid) vs. just outside (invalid),
- an unrealistic magnitude (soft warning by default, hard rejection when
  `Settings.scenario_reject_unrealistic` is on),
- missing data (empty baseline rows, an unknown column, a target label
  absent from the baseline),
- unauthorized assumptions (a restricted column, gated on
  `Permission.VIEW_RESTRICTED_COLUMNS`),
- sequential composition of multiple parameters touching the same
  metric,
- "no partial trust": one invalid parameter rejects the whole scenario,
- the forecast-scenario adjustment, and
- the structural correlation-vs-fact guarantee
  (`ScenarioMetricComparison.baseline_kind`/`.scenario_kind`).

All example numbers in this file were independently hand-computed and
cross-checked by directly exercising the module before being written
into the test file -- not "whatever the code happened to produce."
"""

from __future__ import annotations

import math

import pytest
from analytics.forecasting import generate_forecast
from analytics.models import ScenarioChangeType, ScenarioParameter, ScenarioValueKind
from analytics.scenario import (
    apply_scenario_to_forecast,
    recommendations_for_scenario,
    run_scenario,
    validate_scenario_parameters,
)

from config.settings import Settings

_SETTINGS = Settings(
    scenario_unrealistic_change_percent_threshold=500.0,
    scenario_reject_unrealistic=False,
    scenario_max_parameters=20,
)

_COLUMNS = ["Region", "Revenue"]
_ROWS = [("North", 100.0), ("South", 200.0), ("East", 50.0)]


def _param(**overrides: object) -> ScenarioParameter:
    defaults: dict = dict(
        name="Test parameter",
        affected_metric="Revenue",
        unit="percent",
        change_type=ScenarioChangeType.PERCENT_CHANGE,
        scenario_value=10.0,
    )
    defaults.update(overrides)
    return ScenarioParameter(**defaults)


class TestTypedParameterContract:
    def test_a_parameter_carries_unit_bounds_affected_metric_and_model_version(self):
        param = _param(
            name="North growth",
            affected_metric="Revenue",
            unit="percent",
            change_type=ScenarioChangeType.PERCENT_CHANGE,
            scenario_value=20.0,
            target_label="North",
            min_value=-50.0,
            max_value=200.0,
        )
        assert param.unit == "percent"
        assert param.min_value == -50.0 and param.max_value == 200.0
        assert param.affected_metric == "Revenue"
        assert param.model_version


class TestRunsWithoutMutatingBaseline:
    def test_the_original_rows_list_is_untouched_after_running(self):
        original = list(_ROWS)
        run_scenario(
            "s1",
            "North +20%",
            _COLUMNS,
            _ROWS,
            ["Region"],
            [_param(scenario_value=20.0, target_label="North")],
            settings=_SETTINGS,
        )
        assert original == _ROWS


class TestBoundaryCases:
    def test_scenario_value_exactly_at_max_bound_is_valid(self):
        param = _param(scenario_value=50.0, max_value=50.0, min_value=0.0)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is True
        assert validations[0].violations == ()

    def test_scenario_value_exactly_at_min_bound_is_valid(self):
        param = _param(scenario_value=0.0, max_value=50.0, min_value=0.0)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is True

    def test_scenario_value_just_above_max_bound_is_invalid(self):
        param = _param(scenario_value=50.0001, max_value=50.0, min_value=0.0)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is False
        assert any("above max_value" in v for v in validations[0].violations)

    def test_scenario_value_just_below_min_bound_is_invalid(self):
        param = _param(scenario_value=-0.0001, max_value=50.0, min_value=0.0)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is False
        assert any("below min_value" in v for v in validations[0].violations)

    def test_malformed_bounds_min_exceeds_max_is_invalid(self):
        param = _param(min_value=100.0, max_value=0.0)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is False
        assert any("min_value exceeds max_value" in v for v in validations[0].violations)

    def test_nan_scenario_value_is_invalid(self):
        param = _param(scenario_value=math.nan)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is False
        assert any("finite" in v for v in validations[0].violations)

    def test_infinite_scenario_value_is_invalid(self):
        param = _param(scenario_value=math.inf)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is False


class TestUnrealisticCases:
    def test_a_huge_implied_change_is_flagged_unrealistic_but_not_rejected_by_default(self):
        param = _param(scenario_value=100000.0)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is True
        assert validations[0].is_unrealistic is True
        assert validations[0].warnings
        assert validations[0].violations == ()

    def test_the_same_huge_change_is_rejected_when_the_operator_opts_in(self):
        strict_settings = Settings(**{**_SETTINGS.__dict__, "scenario_reject_unrealistic": True})
        param = _param(scenario_value=100000.0)
        validations = validate_scenario_parameters(
            [param], _COLUMNS, _ROWS, settings=strict_settings
        )
        assert validations[0].is_valid is False
        assert validations[0].is_unrealistic is True
        assert any("rejected as unrealistic" in v for v in validations[0].violations)

    def test_a_modest_change_is_never_flagged_unrealistic(self):
        param = _param(scenario_value=10.0)
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_unrealistic is False


class TestMissingDataCases:
    def test_empty_baseline_rows_is_invalid(self):
        param = _param()
        validations = validate_scenario_parameters([param], _COLUMNS, [], settings=_SETTINGS)
        assert validations[0].is_valid is False
        assert any("no baseline rows" in v for v in validations[0].violations)
        assert validations[0].baseline_value is None

    def test_an_unknown_column_is_invalid(self):
        param = _param(affected_metric="DoesNotExist")
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, settings=_SETTINGS)
        assert validations[0].is_valid is False
        assert any("unknown column" in v for v in validations[0].violations)

    def test_a_target_label_absent_from_the_baseline_is_invalid(self):
        param = _param(target_label="West")
        validations = validate_scenario_parameters(
            [param], _COLUMNS, _ROWS, ["Region"], settings=_SETTINGS
        )
        assert validations[0].is_valid is False
        assert any("not found in the baseline data" in v for v in validations[0].violations)

    def test_a_target_label_without_dimension_columns_is_invalid(self):
        param = _param(target_label="North")
        validations = validate_scenario_parameters([param], _COLUMNS, _ROWS, [], settings=_SETTINGS)
        assert validations[0].is_valid is False
        assert any("requires dimension_columns" in v for v in validations[0].violations)


class TestUnauthorizedCases:
    def test_a_restricted_column_is_rejected_for_a_viewer(self, monkeypatch):
        import analytics.scenario as scenario_module

        monkeypatch.setattr(
            scenario_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "Revenue"): "restricted"},
        )
        param = _param()
        validations = validate_scenario_parameters(
            [param], _COLUMNS, _ROWS, caller_roles=("viewer",), settings=_SETTINGS
        )
        assert validations[0].is_valid is False
        assert any("not authorized" in v for v in validations[0].violations)

    def test_a_restricted_column_is_allowed_for_an_analyst(self, monkeypatch):
        import analytics.scenario as scenario_module

        monkeypatch.setattr(
            scenario_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "Revenue"): "restricted"},
        )
        param = _param()
        validations = validate_scenario_parameters(
            [param], _COLUMNS, _ROWS, caller_roles=("analyst",), settings=_SETTINGS
        )
        assert validations[0].is_valid is True

    def test_an_unauthorized_parameter_rejects_the_whole_scenario_run(self, monkeypatch):
        import analytics.scenario as scenario_module

        monkeypatch.setattr(
            scenario_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "Revenue"): "restricted"},
        )
        result = run_scenario(
            "s1",
            "Unauthorized",
            _COLUMNS,
            _ROWS,
            ["Region"],
            [_param()],
            caller_roles=("viewer",),
            settings=_SETTINGS,
        )
        assert result.status == "rejected"
        assert result.comparisons == ()


class TestSequentialComposition:
    def test_two_parameters_targeting_the_same_metric_compose_sequentially(self):
        params = [
            _param(name="North +50%", scenario_value=50.0, target_label="North"),
            _param(
                name="Global x1.1",
                unit="ratio",
                change_type=ScenarioChangeType.MULTIPLIER,
                scenario_value=1.1,
            ),
        ]
        result = run_scenario(
            "s1", "Composed", _COLUMNS, _ROWS, ["Region"], params, settings=_SETTINGS
        )
        assert result.status == "ok"
        comparison = result.comparisons[0]
        # North: 100 * 1.5 = 150, then * 1.1 = 165; South: 200 * 1.1 = 220;
        # East: 50 * 1.1 = 55 -- total = 440.
        assert comparison.baseline_value == 350.0
        assert comparison.scenario_value == pytest.approx(440.0)
        assert any("applied sequentially" in note for note in result.limitations)

    def test_scenario_rows_are_recomputed_through_the_real_analytics_engine(self):
        params = [_param(scenario_value=300.0, target_label="North")]
        result = run_scenario(
            "s1", "Big North jump", _COLUMNS, _ROWS, ["Region"], params, settings=_SETTINGS
        )
        assert result.baseline_analytics is not None
        assert result.scenario_analytics is not None
        assert result.baseline_analytics.row_count == result.scenario_analytics.row_count == 3
        # North should now be the top-ranked contributor in the scenario
        # analytics, having started as the smallest of the three.
        baseline_ranking = next(
            f.ranking for f in result.baseline_analytics.findings if f.ranking is not None
        )
        scenario_ranking = next(
            f.ranking for f in result.scenario_analytics.findings if f.ranking is not None
        )
        assert baseline_ranking.entries[0].label == "South"
        assert scenario_ranking.entries[0].label == "North"


class TestNoPartialTrust:
    def test_one_invalid_parameter_rejects_the_entire_scenario_not_just_itself(self):
        params = [_param(name="Valid"), _param(name="Invalid", affected_metric="NoSuchColumn")]
        result = run_scenario(
            "s1", "Mixed validity", _COLUMNS, _ROWS, ["Region"], params, settings=_SETTINGS
        )
        assert result.status == "rejected"
        assert result.comparisons == ()
        assert len(result.parameter_validations) == 2
        assert any("Invalid" in reason for reason in result.rejection_reasons)

    def test_too_many_parameters_is_rejected_before_any_validation_runs(self):
        settings = Settings(**{**_SETTINGS.__dict__, "scenario_max_parameters": 1})
        params = [_param(name="P1"), _param(name="P2")]
        result = run_scenario(
            "s1", "Too many", _COLUMNS, _ROWS, ["Region"], params, settings=settings
        )
        assert result.status == "rejected"
        assert result.parameter_validations == ()


class TestNoOpScenario:
    def test_zero_parameters_is_a_valid_trivial_scenario(self):
        result = run_scenario("s1", "No-op", _COLUMNS, _ROWS, ["Region"], [], settings=_SETTINGS)
        assert result.status == "ok"
        assert result.comparisons == ()
        assert result.scenario_analytics == result.baseline_analytics


class TestForecastScenario:
    def test_applying_a_percent_change_uniformly_to_every_forecast_point(self):
        forecast = generate_forecast(
            [("2020", 100.0), ("2021", 110.0), ("2022", 121.0), ("2023", 133.0)],
            horizon=2,
            settings=_SETTINGS,
        )
        assert forecast.status == "ok"
        param = _param(scenario_value=10.0)
        result = apply_scenario_to_forecast(forecast, param, settings=_SETTINGS)
        assert result.status == "ok"
        for point in result.points:
            assert point.scenario_forecast == pytest.approx(point.baseline_forecast * 1.1)

    def test_a_rejected_source_forecast_is_never_adjusted(self):
        forecast = generate_forecast([("2020", 100.0)], horizon=1, settings=_SETTINGS)
        assert forecast.status == "rejected"
        result = apply_scenario_to_forecast(forecast, _param(), settings=_SETTINGS)
        assert result.status == "rejected"
        assert result.points == ()

    def test_a_non_finite_parameter_is_rejected(self):
        forecast = generate_forecast(
            [("2020", 100.0), ("2021", 110.0), ("2022", 121.0), ("2023", 133.0)],
            horizon=1,
            settings=_SETTINGS,
        )
        result = apply_scenario_to_forecast(
            forecast, _param(scenario_value=math.nan), settings=_SETTINGS
        )
        assert result.status == "rejected"


class TestRecommendationsForScenario:
    def test_a_rejected_scenario_produces_one_action_recommendation(self):
        result = run_scenario(
            "s1",
            "Bad",
            _COLUMNS,
            _ROWS,
            ["Region"],
            [_param(affected_metric="Nope")],
            settings=_SETTINGS,
        )
        recs = recommendations_for_scenario(result)
        assert len(recs) == 1
        assert recs[0].claim.level.value == "ai_inference"

    def test_an_ok_scenario_produces_one_recommendation_per_comparison(self):
        result = run_scenario(
            "s1",
            "North +20%",
            _COLUMNS,
            _ROWS,
            ["Region"],
            [_param(scenario_value=20.0, target_label="North")],
            settings=_SETTINGS,
        )
        recs = recommendations_for_scenario(result)
        assert len(recs) == len(result.comparisons)
        assert "estimate, not an observed fact" in recs[0].claim.value


class TestCorrelationNeverFact:
    """The structural half of "clearly separate observed facts, ...
    and modeled estimates" -- a comparison's own kinds cannot be set to
    anything else."""

    def test_baseline_and_scenario_kinds_are_fixed(self):
        result = run_scenario(
            "s1",
            "North +20%",
            _COLUMNS,
            _ROWS,
            ["Region"],
            [_param(scenario_value=20.0, target_label="North")],
            settings=_SETTINGS,
        )
        comparison = result.comparisons[0]
        assert comparison.baseline_kind == ScenarioValueKind.OBSERVED
        assert comparison.scenario_kind == ScenarioValueKind.MODELED_ESTIMATE

    def test_the_overall_result_is_always_ai_inference(self):
        result = run_scenario("s1", "No-op", _COLUMNS, _ROWS, ["Region"], [], settings=_SETTINGS)
        assert result.truth_level.value == "ai_inference"
