"""Turns one metric's series into the movements worth a human's attention.

Two complementary detectors run over the same series, and which one
fired is recorded rather than blurred together:

1. **The policy's own comparison rule** (this module). A deterministic
   comparison of the latest business period against its configured
   baseline -- the previous period, the same period last year, or a
   trailing average -- gated by `min_change_percent` and by any
   configured hard thresholds. This is what an operator reasons about
   and tunes.
2. **`analytics.anomaly.detect_anomalies`** (Prompt 14, reused
   verbatim). Five statistical methods over the whole series. It catches
   a shape the comparison rule cannot see -- a point that is unremarkable
   against last month but far outside the series' own recent spread.

A point flagged by both is reported once, with both sources named in
`DetectedChange.detection_methods`. Reporting it twice would be the
first and most obvious source of alert noise.

**Only the most recent business period is ever reported.** Scanning the
whole history and raising a finding per historical anomaly would bury
the thing that actually just happened under months of archaeology, and
would re-raise the same old movements on every run. History is still
used -- it is the baseline -- but the alert is about now.

Nothing here fetches anything. The series arrives already fetched
through this application's governed path; see this package's own
`__init__` for why that boundary is structural rather than stylistic.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from analytics.anomaly import detect_anomalies

from config.settings import Settings, get_settings
from kpi_monitoring.calendar import Series, exclude_non_business_periods
from kpi_monitoring.models import (
    BusinessCalendar,
    ChangeKind,
    ComparisonPeriod,
    DetectedChange,
    MonitoringPolicy,
)
from kpi_monitoring.policy import ApprovedMetric

logger = logging.getLogger(__name__)


class _Baseline:
    """A resolved comparison value plus how to describe it."""

    __slots__ = ("value", "label", "reason")

    def __init__(self, value: float | None, label: str, reason: str = "") -> None:
        self.value = value
        self.label = label
        self.reason = reason


def _resolve_baseline(points: Sequence[tuple[str, float]], policy: MonitoringPolicy) -> _Baseline:
    """Computes the baseline the policy asked for, or says why it could not.

    Every branch returns a description of what the baseline *is*, so a
    finding never shows a bare comparison number a reader has to guess
    the provenance of.
    """
    # `points` is the business-period-filtered series; the last element is
    # the period under judgment, so baselines look at everything before it.
    history = points[:-1]

    if policy.comparison is ComparisonPeriod.PREVIOUS_PERIOD:
        if not history:
            return _Baseline(None, "no previous period", "only one period available")
        period, value = history[-1]
        return _Baseline(value, f"previous period {period}")

    if policy.comparison is ComparisonPeriod.SAME_PERIOD_LAST_YEAR:
        lag = policy.seasonal_lag
        if len(history) < lag:
            return _Baseline(
                None,
                f"no period {lag} back",
                f"needs {lag} prior business periods, has {len(history)}",
            )
        period, value = history[-lag]
        return _Baseline(value, f"same period last year ({period}, {lag} periods back)")

    window = policy.trailing_window
    if len(history) < window:
        return _Baseline(
            None,
            f"no {window}-period trailing average",
            f"needs {window} prior business periods, has {len(history)}",
        )
    recent = history[-window:]
    mean = sum(value for _period, value in recent) / window
    return _Baseline(
        mean,
        f"mean of the {window} periods before this one " f"({recent[0][0]}..{recent[-1][0]})",
    )


def _threshold_breach(value: float, policy: MonitoringPolicy) -> float | None:
    """Which configured hard threshold this value crossed, if any.

    Checked independently of the percentage gate: an operator who set an
    absolute floor meant it regardless of how small the move that crossed
    it was. A value sitting below a floor and above a ceiling is
    impossible by `MonitoringPolicy`'s own ordering validator.
    """
    if policy.lower_threshold is not None and value < policy.lower_threshold:
        return policy.lower_threshold
    if policy.upper_threshold is not None and value > policy.upper_threshold:
        return policy.upper_threshold
    return None


def _anomaly_methods_for_last_point(
    points: Sequence[tuple[str, float]], settings: Settings
) -> tuple[str, ...]:
    """Which `analytics.anomaly` methods flagged the final point.

    Reuses `detect_anomalies` exactly as-is over the whole series and
    then looks only at the last point, rather than reimplementing any of
    its five methods. A detector error is never allowed to fail a
    monitoring run: this is one of two complementary signals, and losing
    it degrades the result rather than invalidating it -- the same
    fail-open posture every accuracy aid in this codebase takes.
    """
    if not points:
        return ()
    try:
        result = detect_anomalies(list(points), settings)
    except Exception as exc:  # noqa: BLE001 - a detector fault must not fail the run
        logger.warning(
            "[kpi_monitor] anomaly detection failed for a series; continuing with the "
            "comparison rule only: %s",
            type(exc).__name__,
        )
        return ()
    last_period = points[-1][0]
    for anomaly in result.anomalies:
        if anomaly.period == last_period:
            return tuple(sorted({signal.method.value for signal in anomaly.signals}))
    return ()


def detect_change(
    policy: MonitoringPolicy,
    metric: ApprovedMetric,
    points: Series,
    *,
    calendar: BusinessCalendar | None = None,
    settings: Settings | None = None,
) -> tuple[DetectedChange | None, str | None]:
    """Evaluates one policy against one metric's series.

    Args:
        policy: The validated policy (see `kpi_monitoring.policy`).
        metric: The approved metric it targets, for display only.
        points: The chronological `(period, value)` series, oldest first.
        calendar: The tenant's business calendar, or `None`.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        `(change, skip_reason)`. Exactly one is ever non-None: a
        `DetectedChange` when the latest business period moved
        meaningfully, otherwise a human-readable reason the series
        produced nothing -- which is reported on the run rather than
        swallowed, so "nothing moved" is distinguishable from "nothing
        was checked".
    """
    settings = settings or get_settings()

    usable, excluded = exclude_non_business_periods(points, calendar)
    if not usable:
        return None, (
            f"{policy.policy_id}: no business periods in the series"
            + (f" ({len(excluded)} excluded by the calendar)" if excluded else "")
        )
    if len(usable) > settings.kpi_monitor_max_points_per_metric:
        usable = usable[-settings.kpi_monitor_max_points_per_metric :]

    period, current_value = usable[-1]
    baseline = _resolve_baseline(usable, policy)

    breach = _threshold_breach(current_value, policy)
    methods = _anomaly_methods_for_last_point(usable, settings)

    change_absolute: float | None = None
    change_percent: float | None = None
    if baseline.value is not None:
        change_absolute = current_value - baseline.value
        if baseline.value != 0:
            change_percent = (change_absolute / abs(baseline.value)) * 100.0

    # A hard threshold is its own, independent reason to report --
    # an operator who set a floor meant it regardless of the move size.
    if breach is not None:
        kind = ChangeKind.THRESHOLD_BREACH
    elif baseline.value is None:
        # No baseline: the comparison rule cannot judge this period at
        # all. Only a statistical flag can still justify reporting it,
        # and it is reported as a trend break rather than a direction,
        # since there is nothing to have moved against.
        if not methods:
            return None, f"{policy.policy_id}: {baseline.reason}"
        kind = ChangeKind.TREND_BREAK
    elif change_percent is None:
        # A zero baseline makes a percentage meaningless (division by
        # zero), so the percentage gate cannot apply. Report only if a
        # statistical method independently flagged it.
        if not methods:
            return None, (
                f"{policy.policy_id}: baseline for {period} is zero, so a percentage "
                f"change is undefined and no statistical method flagged the point"
            )
        kind = ChangeKind.TREND_BREAK
    else:
        threshold_percent = max(policy.min_change_percent, 0.0)
        if abs(change_percent) < threshold_percent:
            if not methods:
                return None, (
                    f"{policy.policy_id}: {period} moved {change_percent:+.1f}% against "
                    f"{baseline.label}, below the {threshold_percent:.1f}% threshold"
                )
            # Below the operator's threshold but statistically unusual --
            # reported as a trend break, deliberately not as a drop/spike:
            # calling it one would overstate a move the operator already
            # said is too small to matter on its own.
            kind = ChangeKind.TREND_BREAK
        else:
            kind = ChangeKind.DROP if change_absolute and change_absolute < 0 else ChangeKind.SPIKE

    return (
        DetectedChange(
            policy_id=policy.policy_id,
            metric_concept_key=policy.metric_concept_key,
            metric_display_name=metric.display_name,
            kind=kind,
            period=period,
            current_value=current_value,
            baseline_value=baseline.value,
            baseline_label=baseline.label,
            comparison=policy.comparison,
            change_absolute=change_absolute,
            change_percent=change_percent,
            threshold_crossed=breach,
            detection_methods=methods,
        ),
        None,
    )


__all__ = ["detect_change"]
