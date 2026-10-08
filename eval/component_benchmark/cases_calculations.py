"""Calculation cases: the deterministic analytics engine's own numeric
formulas (`analytics.engine.compute_analytics_result`) -- exact value
assertions against hand-computed expected numbers.

Deliberately a different focus from `cases_analytics.py` (which checks
*shape classification* and finding *kinds*): these cases assert the
*actual numeric values* a growth/variance/percentile/ranking-share
finding reports are exactly right, independent of classification --
"did the arithmetic stay correct," the literal thing a user-visible
narrative or dashboard figure would be wrong about if a formula ever
silently regressed.
"""

from __future__ import annotations

import statistics

from analytics.engine import compute_analytics_result
from analytics.models import AnalyticsFindingKind

from eval.component_benchmark.schema import ComponentCase


def _case_growth_rate_matches_hand_computed_percent() -> tuple[bool, str]:
    # 100 -> 150 is a +50% period-over-period change.
    result = compute_analytics_result(["year", "revenue"], [("2022", 100.0), ("2023", 150.0)])
    growth = next(f for f in result.findings if f.kind == AnalyticsFindingKind.GROWTH)
    points = growth.growth.points if growth.growth else []
    last_pct = points[-1].change_percent_from_previous if points else None
    passed = last_pct is not None and abs(last_pct - 50.0) < 1e-6
    return passed, f"change_percent_from_previous={last_pct} (expected 50.0)"


def _case_ranking_share_of_total_matches_hand_computed_percent() -> tuple[bool, str]:
    # East=600, West=400 -> East is exactly 60% of the 1000 total.
    result = compute_analytics_result(["region", "revenue"], [("East", 600.0), ("West", 400.0)])
    ranking = next(f for f in result.findings if f.kind == AnalyticsFindingKind.RANKING)
    top_entry = ranking.ranking.entries[0] if ranking.ranking else None
    share = top_entry.share_percent if top_entry else None
    passed = share is not None and abs(share - 60.0) < 1e-6
    return passed, f"share_percent={share} (expected 60.0)"


def _case_variance_and_stddev_match_statistics_module() -> tuple[bool, str]:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    expected_variance = statistics.pvariance(values)
    expected_stddev = statistics.pstdev(values)
    result = compute_analytics_result(["value"], [(v,) for v in values])
    variance_finding = next(
        (f for f in result.findings if f.kind == AnalyticsFindingKind.VARIANCE), None
    )
    variance_stat = variance_finding.variance if variance_finding is not None else None
    passed = (
        variance_stat is not None
        and abs(variance_stat.variance - expected_variance) < 1e-3
        and abs(variance_stat.stddev - expected_stddev) < 1e-3
    )
    actual_variance = variance_stat.variance if variance_stat is not None else None
    actual_stddev = variance_stat.stddev if variance_stat is not None else None
    detail = (
        f"variance={actual_variance} (expected {expected_variance}), "
        f"stddev={actual_stddev} (expected {expected_stddev})"
    )
    return passed, detail


def _case_median_matches_statistics_module() -> tuple[bool, str]:
    values = [5.0, 1.0, 9.0, 3.0, 7.0]
    expected_median = statistics.median(values)
    result = compute_analytics_result(["value"], [(v,) for v in values])
    median_finding = next(
        (f for f in result.findings if f.kind == AnalyticsFindingKind.MEDIAN), None
    )
    median_stat = median_finding.median if median_finding is not None else None
    passed = median_stat is not None and abs(median_stat.median - expected_median) < 1e-6
    actual_median = median_stat.median if median_stat is not None else None
    detail = f"median={actual_median} (expected {expected_median})"
    return passed, detail


def _case_percentile_matches_statistics_quantiles() -> tuple[bool, str]:
    values = list(range(1, 21))  # 1..20
    expected_p50 = statistics.quantiles([float(v) for v in values], n=100)[49]
    result = compute_analytics_result(["value"], [(float(v),) for v in values])
    percentile_finding = next(
        (f for f in result.findings if f.kind == AnalyticsFindingKind.PERCENTILE), None
    )
    p50 = None
    if percentile_finding is not None and percentile_finding.percentile is not None:
        p50 = percentile_finding.percentile.p50
    passed = p50 is not None and abs(p50 - expected_p50) < 1e-3
    return passed, f"p50={p50} (expected {expected_p50})"


def _case_a_declining_series_reports_a_negative_percent_change() -> tuple[bool, str]:
    # 200 -> 150 is a -25% change.
    result = compute_analytics_result(["year", "revenue"], [("2022", 200.0), ("2023", 150.0)])
    growth = next(f for f in result.findings if f.kind == AnalyticsFindingKind.GROWTH)
    points = growth.growth.points if growth.growth else []
    last_pct = points[-1].change_percent_from_previous if points else None
    passed = last_pct is not None and abs(last_pct - (-25.0)) < 1e-6
    return passed, f"change_percent_from_previous={last_pct} (expected -25.0)"


CASES: list[ComponentCase] = [
    ComponentCase(
        "growth_rate_matches_hand_computed_percent",
        "A 100 -> 150 period-over-period change reports exactly +50.0%.",
        _case_growth_rate_matches_hand_computed_percent,
    ),
    ComponentCase(
        "ranking_share_of_total_matches_hand_computed_percent",
        "A 600/400 two-category breakdown reports the top entry's share as exactly 60.0%.",
        _case_ranking_share_of_total_matches_hand_computed_percent,
    ),
    ComponentCase(
        "variance_and_stddev_match_statistics_module",
        "Variance/stddev over [10,20,30,40,50] exactly match Python's own statistics.pvariance/pstdev.",
        _case_variance_and_stddev_match_statistics_module,
    ),
    ComponentCase(
        "median_matches_statistics_module",
        "Median over an odd-length unsorted list exactly matches statistics.median.",
        _case_median_matches_statistics_module,
    ),
    ComponentCase(
        "percentile_matches_statistics_quantiles",
        "The p50 percentile over 1..20 exactly matches statistics.quantiles(n=100)[49].",
        _case_percentile_matches_statistics_quantiles,
    ),
    ComponentCase(
        "a_declining_series_reports_a_negative_percent_change",
        "A 200 -> 150 period-over-period change reports exactly -25.0%, not an absolute value.",
        _case_a_declining_series_reports_a_negative_percent_change,
    ),
]
