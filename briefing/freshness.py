"""Staleness assessment for briefing evidence.

Pure, and deliberately **never reads a clock**: the reference instant
(`as_of`) is always supplied by the caller, exactly as
`data_quality.models.FreshnessRule.as_of` already requires for the same
reason -- a function that reads the wall clock cannot be tested
deterministically, and a briefing assembled for "last Monday" must be
judged against last Monday, not against now.

**A stale item is reported, never dropped.** Hiding a week-old finding
from a daily briefing would turn a freshness problem into an apparent
absence of problems, which is the failure mode this whole module exists
to prevent. A stale item stays in its section, carries `is_stale` and a
plain-English reason, sorts below fresh items of the same rank, and is
additionally listed in `ExecutiveBriefing.stale_inputs`.

**A missing timestamp is disclosed, not assumed fresh.** A source that
reported no `recorded_at` yields `AGE_UNKNOWN`, which the report states
explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from briefing.models import BriefingPeriod, StaleInput
from config.settings import Settings, get_settings


class Freshness(str, Enum):
    """The three outcomes of a staleness check.

    Attributes:
        FRESH: Observed within this cadence's budget.
        STALE: Observed longer ago than the budget allows.
        AGE_UNKNOWN: The source reported no timestamp, so age cannot be
            computed -- treated as its own outcome rather than silently
            folded into either of the other two.
    """

    FRESH = "fresh"
    STALE = "stale"
    AGE_UNKNOWN = "age_unknown"


_HOUR_SECONDS = 3600.0


def staleness_budget_seconds(period: BriefingPeriod, settings: Settings | None = None) -> float:
    """How old evidence may be before a briefing of this cadence calls it
    stale.

    Three separate settings rather than one, because the honest budget
    scales with the cadence: a daily briefing built from evidence
    gathered a week ago is misleading, while a monthly one built from
    evidence gathered a week ago is entirely normal.
    """
    settings = settings or get_settings()
    hours = {
        BriefingPeriod.DAILY: settings.briefing_stale_after_daily_hours,
        BriefingPeriod.WEEKLY: settings.briefing_stale_after_weekly_hours,
        BriefingPeriod.MONTHLY: settings.briefing_stale_after_monthly_hours,
    }[period]
    return float(hours) * _HOUR_SECONDS


def parse_instant(value: str | None) -> datetime | None:
    """Parses an ISO 8601 instant, or `None` if it is absent or
    unparseable.

    Unparseable is treated exactly like absent -- an unknown age, never
    an exception and never a guess. A naive timestamp is read as UTC,
    matching how every `datetime` this codebase persists is stored
    (`identity.models`' columns are all `DateTime(timezone=True)`, and
    SQLite returns them naive).
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def age_seconds(recorded_at: str | None, as_of: datetime) -> float | None:
    """How long before `as_of` this evidence was recorded.

    A future timestamp yields `0.0`, not a negative age: clock skew
    between this process and whichever process wrote the row is a real
    possibility, and "slightly in the future" means fresh, not
    impossibly fresh.
    """
    parsed = parse_instant(recorded_at)
    if parsed is None:
        return None
    return max(0.0, (as_of - parsed).total_seconds())


def assess(
    recorded_at: str | None,
    *,
    as_of: datetime,
    budget_seconds: float,
) -> tuple[Freshness, float | None]:
    """Classifies one piece of evidence's freshness.

    Returns:
        The `Freshness` outcome and the computed age in seconds (`None`
        when the age is unknown).
    """
    age = age_seconds(recorded_at, as_of)
    if age is None:
        return Freshness.AGE_UNKNOWN, None
    return (Freshness.STALE if age > budget_seconds else Freshness.FRESH), age


def describe(freshness: Freshness, age: float | None, budget_seconds: float) -> str:
    """The plain-English reason shown on a stale or unknown-age item."""
    budget_hours = budget_seconds / _HOUR_SECONDS
    if freshness is Freshness.AGE_UNKNOWN:
        return (
            "The source recorded no observation timestamp, so this evidence's age "
            "could not be checked against this briefing's "
            f"{budget_hours:.0f}-hour freshness budget."
        )
    if freshness is Freshness.STALE and age is not None:
        return (
            f"This evidence was last observed {age / _HOUR_SECONDS:.1f} hours before "
            f"the briefing's reference time, beyond the {budget_hours:.0f}-hour "
            "freshness budget for this cadence."
        )
    return ""


def stale_input(
    *,
    source: str,
    reference_id: str,
    recorded_at: str | None,
    age: float | None,
    budget_seconds: float,
    freshness: Freshness,
) -> StaleInput:
    """Builds the `StaleInput` entry for one stale/unknown-age record."""
    return StaleInput(
        source=source,
        reference_id=reference_id,
        recorded_at=recorded_at,
        age_seconds=age,
        budget_seconds=budget_seconds,
        detail=describe(freshness, age, budget_seconds),
    )


__all__ = [
    "Freshness",
    "age_seconds",
    "assess",
    "describe",
    "parse_instant",
    "stale_input",
    "staleness_budget_seconds",
]
