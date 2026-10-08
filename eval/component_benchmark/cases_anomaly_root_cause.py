"""Anomaly/root-cause cases: `analytics.anomaly.detect_anomalies` and
`analytics.root_cause.investigate_root_cause` (Prompt 14).

Each case feeds a realistic chronological series or current/baseline
row pair and asserts the engine's own flagged-point/contributor output
stays correct -- "did a real spike still get flagged," "did a flat
series still stay quiet," "did a genuine single-dimension driver still
get identified with sufficient evidence," not a re-run of
`tests/test_analytics_anomaly.py`/`test_analytics_root_cause.py`'s own
much finer-grained unit coverage of every method's formula.
"""

from __future__ import annotations

from analytics.anomaly import detect_anomalies
from analytics.root_cause import investigate_root_cause
from config.settings import Settings

from eval.component_benchmark.schema import ComponentCase

#: A permissive settings instance so these cases exercise the real
#: detection logic without depending on this developer's own `.env`
#: thresholds (the identical `tests/` isolation convention this
#: codebase's own `conftest.py` already establishes for the same reason).
_SETTINGS = Settings()


def _case_a_clear_spike_is_flagged() -> tuple[bool, str]:
    # A steady ~100 baseline, then one point jumps to 500.
    points = [(f"2024-0{i}", 100.0 + i) for i in range(1, 7)] + [("2024-07", 500.0)]
    result = detect_anomalies(points, _SETTINGS)
    flagged_periods = {a.period for a in result.anomalies}
    passed = "2024-07" in flagged_periods
    return passed, f"flagged_periods={flagged_periods}"


def _case_a_flat_series_flags_nothing() -> tuple[bool, str]:
    points = [(f"2024-{i:02d}", 100.0) for i in range(1, 13)]
    result = detect_anomalies(points, _SETTINGS)
    passed = result.anomalies == ()
    return passed, f"anomaly_count={len(result.anomalies)} (expected 0)"


def _case_every_flagged_point_carries_its_own_formula() -> tuple[bool, str]:
    points = [(f"2024-0{i}", 100.0) for i in range(1, 7)] + [("2024-07", 1000.0)]
    result = detect_anomalies(points, _SETTINGS)
    missing_formula = [a for a in result.anomalies for s in a.signals if not s.method]
    passed = len(result.anomalies) > 0 and not missing_formula
    return passed, f"anomaly_count={len(result.anomalies)} missing_formula={len(missing_formula)}"


def _case_a_genuine_single_dimension_driver_is_identified() -> tuple[bool, str]:
    # "East" alone drops hard; every other region is flat.
    baseline_rows = [
        ("East", 500.0),
        ("West", 300.0),
        ("North", 200.0),
    ]
    current_rows = [
        ("East", 50.0),  # dropped by 450
        ("West", 300.0),
        ("North", 200.0),
    ]
    result = investigate_root_cause(
        current_rows, baseline_rows, ["region", "revenue"], ["region"], "revenue", _SETTINGS
    )
    passed = result.has_sufficient_evidence and any(
        c.label == "East" for c in result.contributors
    )
    return passed, (
        f"has_sufficient_evidence={result.has_sufficient_evidence} "
        f"contributors={[c.label for c in result.contributors]}"
    )


def _case_no_single_dimension_explains_many_small_evenly_spread_changes() -> tuple[bool, str]:
    # Twenty regions, each contributing an equal, small (~5%) share of
    # the total change -- no single one should clear the default 10%
    # contribution-percent floor on its own.
    regions = [f"region_{i}" for i in range(20)]
    baseline_rows = [(r, 500.0) for r in regions]
    current_rows = [(r, 505.0) for r in regions]  # every region moves by the same +5
    result = investigate_root_cause(
        current_rows, baseline_rows, ["region", "revenue"], ["region"], "revenue", _SETTINGS
    )
    passed = not result.has_sufficient_evidence and result.contributors == ()
    return passed, (
        f"has_sufficient_evidence={result.has_sufficient_evidence} "
        f"contributors={result.contributors} (expected none)"
    )


def _case_root_cause_never_asserts_causation() -> tuple[bool, str]:
    """Master rule 10, applied to this specific engine: the result's own
    text/shape must never claim a confirmed cause -- correlation only."""
    baseline_rows = [("East", 500.0), ("West", 300.0)]
    current_rows = [("East", 50.0), ("West", 300.0)]
    result = investigate_root_cause(
        current_rows, baseline_rows, ["region", "revenue"], ["region"], "revenue", _SETTINGS
    )
    passed = result.has_sufficient_evidence and result.confidence is not None
    return passed, (
        f"confidence={result.confidence} (a probability, not a certainty -- "
        "never asserts a confirmed cause)"
    )


CASES: list[ComponentCase] = [
    ComponentCase(
        "a_clear_spike_is_flagged",
        "A single point jumping far above a steady baseline is flagged as anomalous.",
        _case_a_clear_spike_is_flagged,
    ),
    ComponentCase(
        "a_flat_series_flags_nothing",
        "A perfectly flat series produces zero anomaly findings.",
        _case_a_flat_series_flags_nothing,
    ),
    ComponentCase(
        "every_flagged_point_carries_its_own_formula",
        "Every anomaly signal names the detection method that flagged it.",
        _case_every_flagged_point_carries_its_own_formula,
    ),
    ComponentCase(
        "a_genuine_single_dimension_driver_is_identified",
        "A real, isolated single-region drop is correctly identified as the contributor.",
        _case_a_genuine_single_dimension_driver_is_identified,
    ),
    ComponentCase(
        "no_single_dimension_explains_many_small_evenly_spread_changes",
        "Twenty regions each contributing an equal, small share of the total change yields no "
        "contributors (no single dimension clears the floor on its own).",
        _case_no_single_dimension_explains_many_small_evenly_spread_changes,
    ),
    ComponentCase(
        "root_cause_never_asserts_causation",
        "A root-cause result with sufficient evidence still reports a confidence (probability), "
        "never an asserted, confirmed cause.",
        _case_root_cause_never_asserts_causation,
    ),
]
