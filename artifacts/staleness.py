"""Is this artifact still standing on a current result? -- the analysis artifact workspace.

A pure function. It reads no clock, opens no connection, and touches no
ORM row: `as_of` and the live environment facts are supplied by the
caller (`api/artifacts.py`), the same boundary
`data_quality.models.FreshnessRule.as_of` and `briefing.staleness`
already draw, and for the same reason -- a comparison that reads the
clock itself cannot be tested for the interesting cases, and a server's
own clock is not the right `as_of` for every caller.

**Freshness is computed on every read and never stored.** A persisted
`is_stale` column is exactly how a stale artifact comes to look current:
nothing updates it when the schema changes, when the source turn is
deleted, or when the clock simply moves. Requirement 6 is satisfied by
there being no such column to go wrong.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from artifacts.models import (
    ArtifactFreshness,
    ArtifactSnapshot,
    FreshnessAssessment,
    RefreshOutcome,
    ResultSnapshotRef,
    StalenessReason,
)


def as_utc(value: datetime) -> datetime:
    """Reads a naive timestamp as UTC.

    Every timestamp this application writes is UTC (`datetime.now(UTC)`,
    or a `server_default` on a `DateTime(timezone=True)` column), but not
    every driver hands one back with its offset attached -- SQLite in
    particular returns a naive `datetime` from a `timezone=True` column,
    and a stored ISO string can lack an offset too. Subtracting a naive
    from an aware datetime raises, so without this one malformed-looking
    row would fail the whole workspace listing rather than being graded.

    Assuming UTC is correct here precisely because nothing in this
    codebase writes a local-time timestamp; it is not a general-purpose
    guess about arbitrary input.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def fingerprint_result(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Deterministic sha256 over a result's columns and rows.

    The same "hash a canonical serialization" approach
    `db.schema_introspection.get_schema_fingerprint` already uses, applied
    to a result set. `default=str` makes a `Decimal`/`datetime` cell
    hashable without changing what it means, which matters because a SQL
    Server `decimal` column arrives as `decimal.Decimal` (see CLAUDE.md's
    own note on that classification).

    This is what lets a refresh report "the numbers moved" rather than
    only "a refresh happened" -- and it is a hash, so the artifact never
    stores a second copy of the values it is comparing.
    """
    payload = json.dumps(
        {"columns": list(columns), "rows": [list(row) for row in rows]},
        default=str,
        sort_keys=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def standing_result(artifact: ArtifactSnapshot) -> ResultSnapshotRef:
    """Which captured result the artifact currently presents.

    The refreshed one only when the last refresh actually **succeeded** --
    a failed refresh leaves the artifact standing on its original capture,
    so measuring age from the failed attempt would make an artifact look
    freshly verified precisely when verification did not happen.
    """
    if (
        artifact.last_refresh_outcome is RefreshOutcome.SUCCESS
        and artifact.refreshed.captured_at is not None
    ):
        return artifact.refreshed
    return artifact.origin


def result_changed_on_refresh(artifact: ArtifactSnapshot) -> bool:
    """Whether the last successful refresh returned something different
    from the capture the artifact was created from.

    Two independent comparisons, because each catches what the other
    misses. The **fingerprint** covers the stored rows, which are capped
    at `Settings.chat_history_max_result_rows` -- so a change beyond that
    row would be invisible to it. The **row count** is the query's own
    full count, uncapped, so a result that grew or shrank is caught even
    when every stored row is identical. Neither alone is enough; a change
    past the cap *within* an unchanged total row count is the one case
    neither sees, and is disclosed rather than claimed to be covered.
    """
    origin, refreshed = artifact.origin, artifact.refreshed
    if (
        origin.fingerprint is not None
        and refreshed.fingerprint is not None
        and origin.fingerprint != refreshed.fingerprint
    ):
        return True
    return (
        origin.row_count is not None
        and refreshed.row_count is not None
        and origin.row_count != refreshed.row_count
    )


def assess_freshness(
    artifact: ArtifactSnapshot,
    *,
    as_of: datetime,
    aging_after_seconds: float,
    stale_after_seconds: float,
    source_turn_available: bool = True,
    live_schema_fingerprint: str | None = None,
    database_available: bool = True,
) -> FreshnessAssessment:
    """Grades one artifact, reporting every observation a reader needs.

    Args:
        artifact: The artifact, as the repository read it back.
        as_of: The instant to measure age against. Caller-supplied.
        aging_after_seconds: Past this, a result is dated but usable.
        stale_after_seconds: Past this, a result is stale.
        source_turn_available: Whether the referenced conversation turn is
            still readable by this caller. `False` covers both "deleted"
            and "not visible", which are deliberately indistinguishable
            here -- the anti-enumeration convention every tenant-scoped
            route in this codebase already follows.
        live_schema_fingerprint: The database's current schema
            fingerprint, or `None` when it could not be determined.
            Compared, never trusted to exist: an unknown fingerprint
            yields no conclusion rather than a false "changed".
        database_available: Whether the artifact's database is still
            configured for this caller's tenant.

    Returns:
        A `FreshnessAssessment`. `reasons` is **everything the reader must
        be told**, which is very nearly but not exactly the same as "why
        this is not current": `RESULT_CHANGED_ON_REFRESH` can appear on a
        `CURRENT` artifact, because it describes the *superseded original*
        capture rather than the result the artifact now stands on. That is
        the one case, it is deliberate, and it is pinned by a test --
        dropping it would hide the single most useful thing a refresh can
        discover (requirement 7).
    """
    reasons: list[StalenessReason] = []

    if not source_turn_available:
        # Terminal. Nothing else is worth reporting about an artifact
        # whose source turn is gone -- age and schema drift describe a
        # result that can no longer be opened at all.
        return FreshnessAssessment(
            state=ArtifactFreshness.SOURCE_MISSING,
            reasons=(StalenessReason.SOURCE_TURN_DELETED,),
        )

    if not database_available:
        reasons.append(StalenessReason.DATABASE_NOT_AVAILABLE)
    if artifact.last_refresh_outcome is RefreshOutcome.FAILED:
        reasons.append(StalenessReason.LAST_REFRESH_FAILED)

    schema_changed = (
        live_schema_fingerprint is not None
        and artifact.schema_fingerprint is not None
        and live_schema_fingerprint != artifact.schema_fingerprint
    )
    if schema_changed:
        reasons.append(StalenessReason.SCHEMA_CHANGED)

    if result_changed_on_refresh(artifact):
        reasons.append(StalenessReason.RESULT_CHANGED_ON_REFRESH)

    standing = standing_result(artifact)
    if standing.captured_at is None:
        # The question was answered but its SQL was never confirmed and
        # run, so there is no result to be fresh or stale. Reported as its
        # own state rather than folded into "stale": the source turn is
        # still perfectly readable, and a refresh may well produce a
        # result for the first time.
        reasons.append(StalenessReason.NO_RESULT_CAPTURED)
        return FreshnessAssessment(
            state=ArtifactFreshness.NO_RESULT_CAPTURED, reasons=tuple(reasons)
        )

    captured_at = as_utc(standing.captured_at)
    age_seconds = (as_utc(as_of) - captured_at).total_seconds()

    state = ArtifactFreshness.CURRENT
    if age_seconds > stale_after_seconds:
        reasons.append(StalenessReason.AGED_PAST_STALE_BUDGET)
        state = ArtifactFreshness.STALE
    elif age_seconds > aging_after_seconds:
        reasons.append(StalenessReason.AGED_PAST_BUDGET)
        state = ArtifactFreshness.AGING

    if schema_changed:
        # The shape the result was computed against has changed, so the
        # numbers cannot be assumed to still mean the same thing however
        # recently they were captured.
        state = ArtifactFreshness.STALE

    return FreshnessAssessment(
        state=state,
        reasons=tuple(reasons),
        result_age_seconds=age_seconds,
        result_as_of=captured_at,
    )
