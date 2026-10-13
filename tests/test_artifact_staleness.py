"""Tests for `artifacts/staleness.py` -- the analysis artifact workspace.

Pure: no database, no clock, no settings. `as_of` is supplied by every
case, which is exactly why "this artifact is three days old" is testable
at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from artifacts.models import (
    ArtifactFreshness,
    ArtifactSnapshot,
    ArtifactType,
    RefreshOutcome,
    ResultSnapshotRef,
    StalenessReason,
)
from artifacts.staleness import (
    assess_freshness,
    fingerprint_result,
    result_changed_on_refresh,
    standing_result,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
AGING_AFTER = 6 * 3600.0
STALE_AFTER = 72 * 3600.0


def artifact(**overrides) -> ArtifactSnapshot:
    base = {
        "id": "a1",
        "tenant_id": "tenant-a",
        "owner_user_id": "u1",
        "conversation_id": "c1",
        "prompt_id": "p1",
        "ai_output_id": "o1",
        "artifact_type": ArtifactType.ANALYSIS,
        "title": "Revenue by region",
        "created_at": NOW - timedelta(hours=1),
        "updated_at": NOW - timedelta(hours=1),
        "database_id": "default",
        "source_sql": "SELECT region, revenue FROM sales",
        "schema_fingerprint": "schema-v1",
        "origin": ResultSnapshotRef(
            row_count=3, captured_at=NOW - timedelta(hours=1), fingerprint="result-v1"
        ),
    }
    base.update(overrides)
    return ArtifactSnapshot(**base)


def assess(art: ArtifactSnapshot, **kwargs):
    return assess_freshness(
        art,
        as_of=kwargs.pop("as_of", NOW),
        aging_after_seconds=AGING_AFTER,
        stale_after_seconds=STALE_AFTER,
        **kwargs,
    )


class TestAgeBudgets:
    def test_a_result_captured_minutes_ago_is_current(self):
        assessment = assess(artifact())
        assert assessment.state is ArtifactFreshness.CURRENT
        assert assessment.reasons == ()
        assert assessment.is_current

    def test_a_result_past_the_aging_budget_is_dated_not_stale(self):
        # The distinction the two budgets exist for: "this is from
        # yesterday morning" is a different message from "this may no
        # longer be true".
        art = artifact(
            origin=ResultSnapshotRef(
                row_count=3, captured_at=NOW - timedelta(hours=10), fingerprint="result-v1"
            )
        )
        assessment = assess(art)
        assert assessment.state is ArtifactFreshness.AGING
        assert StalenessReason.AGED_PAST_BUDGET in assessment.reasons

    def test_a_result_past_the_stale_budget_is_stale(self):
        art = artifact(
            origin=ResultSnapshotRef(
                row_count=3, captured_at=NOW - timedelta(days=5), fingerprint="result-v1"
            )
        )
        assessment = assess(art)
        assert assessment.state is ArtifactFreshness.STALE
        assert StalenessReason.AGED_PAST_STALE_BUDGET in assessment.reasons

    def test_it_reports_the_date_it_measured_from_not_only_a_verdict(self):
        captured = NOW - timedelta(hours=10)
        assessment = assess(artifact(origin=ResultSnapshotRef(captured_at=captured)))
        assert assessment.result_as_of == captured
        assert assessment.result_age_seconds == pytest.approx(10 * 3600)


class TestStandingResult:
    def test_a_successful_refresh_becomes_the_result_the_artifact_stands_on(self):
        art = artifact(
            origin=ResultSnapshotRef(captured_at=NOW - timedelta(days=5), fingerprint="v1"),
            last_refresh_outcome=RefreshOutcome.SUCCESS,
            last_refresh_at=NOW - timedelta(minutes=5),
            refreshed=ResultSnapshotRef(captured_at=NOW - timedelta(minutes=5), fingerprint="v1"),
        )
        assert standing_result(art).fingerprint == "v1"
        # Age is measured from the refresh, so a five-day-old artifact that
        # was just re-run reads as current again.
        assert assess(art).state is ArtifactFreshness.CURRENT

    def test_a_failed_refresh_does_not_become_the_standing_result(self):
        # Otherwise a failed verification would make an artifact look
        # freshly verified -- precisely when verification did not happen.
        art = artifact(
            origin=ResultSnapshotRef(captured_at=NOW - timedelta(days=5), fingerprint="v1"),
            last_refresh_outcome=RefreshOutcome.FAILED,
            last_refresh_at=NOW - timedelta(minutes=5),
            last_refresh_error="Execution failed: timeout",
            refreshed=ResultSnapshotRef(captured_at=NOW - timedelta(minutes=5)),
        )
        assessment = assess(art)
        assert assessment.state is ArtifactFreshness.STALE
        assert StalenessReason.LAST_REFRESH_FAILED in assessment.reasons
        assert assessment.result_as_of == NOW - timedelta(days=5)

    def test_a_failed_refresh_alone_does_not_make_a_fresh_artifact_stale(self):
        art = artifact(
            last_refresh_outcome=RefreshOutcome.FAILED,
            last_refresh_error="Execution failed: timeout",
        )
        assessment = assess(art)
        assert assessment.state is ArtifactFreshness.CURRENT
        assert StalenessReason.LAST_REFRESH_FAILED in assessment.reasons


class TestSourceAndCapture:
    def test_a_deleted_source_turn_is_terminal(self):
        assessment = assess(artifact(), source_turn_available=False)
        assert assessment.state is ArtifactFreshness.SOURCE_MISSING
        assert assessment.reasons == (StalenessReason.SOURCE_TURN_DELETED,)
        # Nothing else is reported: age and schema drift describe a result
        # that can no longer be opened at all.
        assert assessment.result_age_seconds is None

    def test_a_turn_whose_sql_was_never_run_has_no_result_to_age(self):
        art = artifact(origin=ResultSnapshotRef())
        assessment = assess(art)
        assert assessment.state is ArtifactFreshness.NO_RESULT_CAPTURED
        assert StalenessReason.NO_RESULT_CAPTURED in assessment.reasons
        assert assessment.state is not ArtifactFreshness.SOURCE_MISSING

    def test_other_observations_still_accumulate_with_no_captured_result(self):
        assessment = assess(artifact(origin=ResultSnapshotRef()), database_available=False)
        assert StalenessReason.DATABASE_NOT_AVAILABLE in assessment.reasons
        assert StalenessReason.NO_RESULT_CAPTURED in assessment.reasons


class TestSchemaDrift:
    def test_a_changed_schema_makes_even_a_minutes_old_result_stale(self):
        # The shape the numbers were computed against moved, so they
        # cannot be assumed to still mean the same thing however recent
        # they are.
        assessment = assess(artifact(), live_schema_fingerprint="schema-v2")
        assert assessment.state is ArtifactFreshness.STALE
        assert StalenessReason.SCHEMA_CHANGED in assessment.reasons

    def test_an_unchanged_schema_changes_nothing(self):
        assessment = assess(artifact(), live_schema_fingerprint="schema-v1")
        assert assessment.state is ArtifactFreshness.CURRENT
        assert StalenessReason.SCHEMA_CHANGED not in assessment.reasons

    def test_an_unknown_live_fingerprint_draws_no_conclusion(self):
        # A database that has never been indexed has no fingerprint to
        # compare. "Unknown" must never read as "changed".
        assessment = assess(artifact(), live_schema_fingerprint=None)
        assert StalenessReason.SCHEMA_CHANGED not in assessment.reasons
        assert assessment.state is ArtifactFreshness.CURRENT

    def test_an_artifact_that_recorded_no_fingerprint_draws_no_conclusion(self):
        assessment = assess(artifact(schema_fingerprint=None), live_schema_fingerprint="schema-v2")
        assert StalenessReason.SCHEMA_CHANGED not in assessment.reasons


class TestResultChangedOnRefresh:
    def test_different_rows_are_reported(self):
        art = artifact(
            last_refresh_outcome=RefreshOutcome.SUCCESS,
            refreshed=ResultSnapshotRef(
                row_count=3, captured_at=NOW - timedelta(minutes=1), fingerprint="result-v2"
            ),
        )
        assert result_changed_on_refresh(art) is True

    def test_a_changed_row_count_is_reported_even_when_stored_rows_match(self):
        # The stored rows are capped, so a result that grew past the cap
        # can hash identically. The uncapped row count catches it.
        art = artifact(
            last_refresh_outcome=RefreshOutcome.SUCCESS,
            refreshed=ResultSnapshotRef(
                row_count=9, captured_at=NOW - timedelta(minutes=1), fingerprint="result-v1"
            ),
        )
        assert result_changed_on_refresh(art) is True

    def test_an_identical_refresh_reports_no_change(self):
        art = artifact(
            last_refresh_outcome=RefreshOutcome.SUCCESS,
            refreshed=ResultSnapshotRef(
                row_count=3, captured_at=NOW - timedelta(minutes=1), fingerprint="result-v1"
            ),
        )
        assert result_changed_on_refresh(art) is False

    def test_the_change_is_reported_on_a_current_artifact(self):
        # The one case where a reason appears on a CURRENT artifact: the
        # refreshed result is genuinely current, and the *original* capture
        # it superseded is the thing that moved. Dropping this would hide
        # the single most useful thing a refresh can discover.
        art = artifact(
            last_refresh_outcome=RefreshOutcome.SUCCESS,
            refreshed=ResultSnapshotRef(
                row_count=4, captured_at=NOW - timedelta(minutes=1), fingerprint="result-v2"
            ),
        )
        assessment = assess(art)
        assert assessment.state is ArtifactFreshness.CURRENT
        assert StalenessReason.RESULT_CHANGED_ON_REFRESH in assessment.reasons

    def test_never_refreshed_reports_no_change(self):
        assert result_changed_on_refresh(artifact()) is False


class TestFingerprint:
    def test_identical_results_hash_identically(self):
        assert fingerprint_result(["a"], [[1], [2]]) == fingerprint_result(["a"], [[1], [2]])

    def test_a_changed_value_changes_the_hash(self):
        assert fingerprint_result(["a"], [[1]]) != fingerprint_result(["a"], [[2]])

    def test_a_renamed_column_changes_the_hash(self):
        assert fingerprint_result(["a"], [[1]]) != fingerprint_result(["b"], [[1]])

    def test_row_order_is_significant(self):
        # Two orderings are two different answers to the same question --
        # a ranking that flipped is exactly the change worth reporting.
        assert fingerprint_result(["a"], [[1], [2]]) != fingerprint_result(["a"], [[2], [1]])

    def test_a_decimal_or_datetime_cell_is_hashable(self):
        from decimal import Decimal

        value = fingerprint_result(["amount", "when"], [[Decimal("1.50"), NOW]])
        assert len(value) == 64

    def test_the_hash_does_not_contain_the_values(self):
        value = fingerprint_result(["salary"], [["123456"]])
        assert "123456" not in value


class TestNoStoredVerdict:
    def test_no_freshness_verdict_is_stored_on_the_persisted_row(self):
        # Requirement 6 is satisfied structurally: there is no column to
        # go stale. Pinned as an exact column set rather than a substring
        # scan, so adding a stored verdict -- under any name -- has to
        # touch this test and explain itself.
        from identity.models import AnalysisArtifact

        assert set(AnalysisArtifact.__table__.columns.keys()) == {
            "id",
            "tenant_id",
            "owner_user_id",
            "conversation_id",
            "prompt_id",
            "ai_output_id",
            "artifact_type",
            "title",
            "database_id",
            "source_sql",
            "schema_fingerprint",
            "origin_row_count",
            "origin_captured_at",
            "origin_fingerprint",
            "last_refresh_at",
            "last_refresh_outcome",
            "last_refresh_error",
            "last_refresh_row_count",
            "last_refresh_fingerprint",
            "created_at",
            "updated_at",
            "deleted_at",
            "metadata",
        }
