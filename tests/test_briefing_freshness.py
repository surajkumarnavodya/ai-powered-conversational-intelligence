"""Unit tests for `briefing/freshness.py` (Prompt 51).

Pure: no database, no clock. The reference instant is always supplied,
which is the whole point of the module -- see its own docstring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from briefing.freshness import (
    Freshness,
    age_seconds,
    assess,
    describe,
    parse_instant,
    stale_input,
    staleness_budget_seconds,
)
from briefing.models import BriefingPeriod
from config.settings import Settings

AS_OF = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_SETTINGS = Settings()


def _iso(hours_ago: float) -> str:
    return (AS_OF - timedelta(hours=hours_ago)).isoformat()


class TestBudgets:
    def test_each_cadence_has_its_own_budget_and_they_increase(self):
        """A daily briefing must call week-old evidence stale; a monthly
        one must not. One shared budget could not do both."""
        daily = staleness_budget_seconds(BriefingPeriod.DAILY, _SETTINGS)
        weekly = staleness_budget_seconds(BriefingPeriod.WEEKLY, _SETTINGS)
        monthly = staleness_budget_seconds(BriefingPeriod.MONTHLY, _SETTINGS)
        assert daily < weekly < monthly

    def test_budget_is_read_from_settings_not_hardcoded(self):
        tight = Settings(**{**_SETTINGS.__dict__, "briefing_stale_after_daily_hours": 2.0})
        assert staleness_budget_seconds(BriefingPeriod.DAILY, tight) == pytest.approx(7200.0)


class TestParseInstant:
    def test_absent_and_unparseable_are_both_unknown_never_an_exception(self):
        assert parse_instant(None) is None
        assert parse_instant("") is None
        assert parse_instant("last Tuesday") is None

    def test_a_naive_timestamp_is_read_as_utc(self):
        """SQLite hands back naive datetimes even for a
        `DateTime(timezone=True)` column, so a naive string is the normal
        case rather than an error."""
        parsed = parse_instant("2026-07-01T10:00:00")
        assert parsed is not None
        assert parsed.tzinfo is UTC


class TestAge:
    def test_age_is_measured_against_the_supplied_instant(self):
        assert age_seconds(_iso(2), AS_OF) == pytest.approx(7200.0)

    def test_a_future_timestamp_is_zero_age_not_negative(self):
        """Clock skew between this process and whichever wrote the row is
        real; "slightly ahead" means fresh, not impossibly fresh."""
        future = (AS_OF + timedelta(hours=1)).isoformat()
        assert age_seconds(future, AS_OF) == 0.0

    def test_an_absent_timestamp_has_no_age(self):
        assert age_seconds(None, AS_OF) is None


class TestAssess:
    def test_inside_the_budget_is_fresh(self):
        freshness, age = assess(_iso(5), as_of=AS_OF, budget_seconds=36 * 3600)
        assert freshness is Freshness.FRESH
        assert age == pytest.approx(5 * 3600.0)

    def test_outside_the_budget_is_stale(self):
        freshness, age = assess(_iso(50), as_of=AS_OF, budget_seconds=36 * 3600)
        assert freshness is Freshness.STALE
        assert age == pytest.approx(50 * 3600.0)

    def test_exactly_at_the_budget_is_still_fresh(self):
        """The boundary is inclusive, so a record observed exactly one
        budget ago is not reported as a problem."""
        freshness, _ = assess(_iso(36), as_of=AS_OF, budget_seconds=36 * 3600)
        assert freshness is Freshness.FRESH

    def test_a_missing_timestamp_is_its_own_outcome_never_assumed_fresh(self):
        freshness, age = assess(None, as_of=AS_OF, budget_seconds=36 * 3600)
        assert freshness is Freshness.AGE_UNKNOWN
        assert age is None

    def test_the_same_record_is_stale_daily_and_fresh_monthly(self):
        """The cadence-dependence this module exists for, end to end."""
        recorded = _iso(100)
        daily, _ = assess(
            recorded,
            as_of=AS_OF,
            budget_seconds=staleness_budget_seconds(BriefingPeriod.DAILY, _SETTINGS),
        )
        monthly, _ = assess(
            recorded,
            as_of=AS_OF,
            budget_seconds=staleness_budget_seconds(BriefingPeriod.MONTHLY, _SETTINGS),
        )
        assert daily is Freshness.STALE
        assert monthly is Freshness.FRESH


class TestDescribe:
    def test_a_stale_reason_names_both_the_age_and_the_budget(self):
        text = describe(Freshness.STALE, 50 * 3600.0, 36 * 3600.0)
        assert "50.0 hours" in text
        assert "36-hour" in text

    def test_an_unknown_age_says_no_timestamp_was_recorded(self):
        text = describe(Freshness.AGE_UNKNOWN, None, 36 * 3600.0)
        assert "no observation timestamp" in text

    def test_a_fresh_record_has_nothing_to_explain(self):
        assert describe(Freshness.FRESH, 1.0, 36 * 3600.0) == ""


class TestStaleInput:
    def test_it_carries_the_identity_the_age_and_the_reason(self):
        entry = stale_input(
            source="kpi_monitoring.findings",
            reference_id="f1",
            recorded_at=_iso(60),
            age=60 * 3600.0,
            budget_seconds=36 * 3600.0,
            freshness=Freshness.STALE,
        )
        assert entry.source == "kpi_monitoring.findings"
        assert entry.reference_id == "f1"
        assert entry.age_seconds == pytest.approx(60 * 3600.0)
        assert "freshness budget" in entry.detail
