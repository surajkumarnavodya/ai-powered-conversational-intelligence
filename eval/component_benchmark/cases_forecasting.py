"""Forecasting cases: `analytics.forecasting.generate_forecast`
(Prompt 16).

Each case feeds a realistic chronological series and asserts the
engine's own forecast/rejection behavior stays correct -- "did a clear
trend still extrapolate the right direction," "did insufficient data
still reject rather than guess," "does every successful forecast still
disclose its own limitations," the literal "forecasts are explicitly
represented as estimates with limitations" acceptance criterion this
engine's own contract names, not a re-run of
`tests/test_analytics_forecasting.py`'s own much finer-grained unit
coverage of every model's fitting/backtest logic.
"""

from __future__ import annotations

from analytics.forecasting import generate_forecast
from config.settings import Settings

from eval.component_benchmark.schema import ComponentCase

_SETTINGS = Settings()


def _case_a_clear_upward_trend_forecasts_upward() -> tuple[bool, str]:
    points = [(f"2024-{i:02d}", 100.0 + 20.0 * i) for i in range(1, 9)]
    result = generate_forecast(points, horizon=3, settings=_SETTINGS)
    passed = (
        result.status == "ok"
        and len(result.points) == 3
        and result.points[-1].forecast > points[-1][1]
    )
    return passed, f"status={result.status} forecasts={[p.forecast for p in result.points]}"


def _case_insufficient_data_points_is_rejected_not_guessed() -> tuple[bool, str]:
    points = [("2024-01", 100.0), ("2024-02", 110.0)]  # fewer than the minimum
    result = generate_forecast(points, horizon=3, settings=_SETTINGS)
    passed = result.status == "rejected" and len(result.rejection_reasons) > 0
    return passed, f"status={result.status} reasons={result.rejection_reasons}"


def _case_every_successful_forecast_discloses_limitations() -> tuple[bool, str]:
    points = [(f"2024-{i:02d}", 100.0 + 5.0 * i) for i in range(1, 9)]
    result = generate_forecast(points, horizon=2, settings=_SETTINGS)
    passed = result.status == "ok" and len(result.limitations) > 0
    return passed, f"status={result.status} limitations_count={len(result.limitations)}"


def _case_the_requested_horizon_is_respected() -> tuple[bool, str]:
    points = [(f"2024-{i:02d}", 100.0 + i) for i in range(1, 13)]
    result = generate_forecast(points, horizon=5, settings=_SETTINGS)
    passed = result.status == "ok" and len(result.points) == 5
    return passed, f"status={result.status} points_count={len(result.points)} (expected 5)"


def _case_an_unrecognized_period_label_is_rejected() -> tuple[bool, str]:
    """A raw-ISO-date-labeled series is deliberately an untrusted temporal
    shape for forecasting purposes (see this engine's own disclosed
    scope boundary) -- never silently forecast through an unrecognized
    pattern."""
    points = [(f"not-a-period-{i}", 100.0 + i) for i in range(1, 9)]
    result = generate_forecast(points, horizon=2, settings=_SETTINGS)
    passed = result.status == "rejected"
    return passed, f"status={result.status} reasons={result.rejection_reasons}"


def _case_a_flat_series_forecasts_flat_not_a_fabricated_trend() -> tuple[bool, str]:
    points = [(f"2024-{i:02d}", 100.0) for i in range(1, 9)]
    result = generate_forecast(points, horizon=2, settings=_SETTINGS)
    passed = result.status == "ok" and all(abs(p.forecast - 100.0) < 1.0 for p in result.points)
    return passed, f"status={result.status} forecasts={[p.forecast for p in result.points]}"


CASES: list[ComponentCase] = [
    ComponentCase(
        "a_clear_upward_trend_forecasts_upward",
        "A steadily rising series produces a final forecast point above the last observed value.",
        _case_a_clear_upward_trend_forecasts_upward,
    ),
    ComponentCase(
        "insufficient_data_points_is_rejected_not_guessed",
        "Fewer than the minimum required historical points rejects with a reason, never guesses.",
        _case_insufficient_data_points_is_rejected_not_guessed,
    ),
    ComponentCase(
        "every_successful_forecast_discloses_limitations",
        "Every ok-status forecast carries a non-empty limitations tuple.",
        _case_every_successful_forecast_discloses_limitations,
    ),
    ComponentCase(
        "the_requested_horizon_is_respected",
        "Requesting a 5-period horizon returns exactly 5 forecast points.",
        _case_the_requested_horizon_is_respected,
    ),
    ComponentCase(
        "an_unrecognized_period_label_is_rejected",
        "A raw, unrecognized period-label shape is rejected, never silently forecast through.",
        _case_an_unrecognized_period_label_is_rejected,
    ),
    ComponentCase(
        "a_flat_series_forecasts_flat_not_a_fabricated_trend",
        "A perfectly flat historical series forecasts flat, not an invented trend.",
        _case_a_flat_series_forecasts_flat_not_a_fabricated_trend,
    ),
]
