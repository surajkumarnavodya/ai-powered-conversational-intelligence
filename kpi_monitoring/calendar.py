"""Business-calendar filtering of a period series.

The single most common source of false KPI alerts is a real, expected
seasonal drop: a holiday shutdown, a planned maintenance window, or a
period still being collected. Each of those produces a large, genuine
movement that no one wants to be paged about, and that also poisons any
baseline computed from it.

This module handles both halves of that, deliberately as two separate
operations rather than one:

1. `exclude_non_business_periods` removes those periods from the series
   **before** a baseline is computed, so a holiday never drags a
   trailing average down and manufactures a finding in the *next*
   period.
2. `kpi_monitoring.suppression` separately refuses to alert on a
   non-business period itself.

Doing only (2) would still leave the poisoned baseline; doing only (1)
would still alert on the holiday. They are not interchangeable.

**No date parsing anywhere.** A period label is an opaque string, the
same boundary `analytics.engine.detect_missing_periods` already draws:
whatever `"2026-01"`/`"2026-W03"`/`"FY26-Q1"` means is the caller's
business, and a calendar is an explicit set of labels an operator
supplies. Inferring a holiday from a date would mean inferring a
locale, a fiscal year and a trading convention this application has no
way to know.
"""

from __future__ import annotations

from collections.abc import Sequence

from kpi_monitoring.models import BusinessCalendar

#: A period series, oldest first -- the same `(period, value)` shape
#: `analytics.engine.GrowthStat.points` and
#: `analytics.anomaly.detect_anomalies` already use, so a caller can hand
#: either straight through.
Series = Sequence[tuple[str, float]]


def exclude_non_business_periods(
    points: Series, calendar: BusinessCalendar | None
) -> tuple[tuple[tuple[str, float], ...], tuple[str, ...]]:
    """Drops non-business periods from a series.

    Args:
        points: The chronological series, oldest first.
        calendar: The tenant's calendar, or `None` for "every period is a
            business period" (the default, and what every caller gets
            without configuring anything).

    Returns:
        `(kept_points, excluded_periods)`. The excluded labels are
        returned rather than discarded so the run can report *which*
        periods it ignored -- an operator seeing a suspiciously quiet
        metric needs to be able to tell "nothing moved" from "the whole
        series was excluded by a mis-entered calendar".

    Chronological order is preserved: this only ever removes elements,
    never reorders them, so every downstream lag (`seasonal_lag`,
    `trailing_window`) still counts positions in real time order.
    """
    if calendar is None or not calendar.non_business_periods:
        return tuple(points), ()

    kept: list[tuple[str, float]] = []
    excluded: list[str] = []
    for period, value in points:
        if calendar.is_business_period(period):
            kept.append((period, value))
        else:
            excluded.append(period)
    return tuple(kept), tuple(excluded)


def is_alertable_period(period: str, calendar: BusinessCalendar | None) -> bool:
    """Whether a movement *in* this period may be alerted on at all.

    Separate from `exclude_non_business_periods` above by design -- see
    this module's own docstring for why removing a period from the
    baseline and refusing to alert on it are two different needs.
    """
    return calendar is None or calendar.is_business_period(period)


__all__ = ["Series", "exclude_non_business_periods", "is_alertable_period"]
