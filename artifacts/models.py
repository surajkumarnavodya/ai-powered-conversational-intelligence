"""Typed, ORM-independent vocabulary for analysis artifacts -- the analysis artifact workspace.

Mirrors `bi_integration/models.py`'s own split: the enums and the frozen
snapshot shape live here with no SQLAlchemy import at all, so the pure
staleness/authorization logic can be tested without a database and the
repository layer has exactly one place to convert a row into a view.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel


class ArtifactType(str, Enum):
    """What kind of work product this artifact is.

    Deliberately descriptive, never behavioural: the type decides how the
    workspace labels and icons a row, and nothing else. In particular it
    never decides whether a refresh is allowed -- that depends on whether
    real, re-runnable SQL was recorded, which `ArtifactSnapshot
    .is_refreshable` answers from the data rather than from the label a
    caller chose.
    """

    ANALYSIS = "analysis"
    CHART = "chart"
    RESULT_TABLE = "result_table"
    SQL = "sql"
    INSIGHT_SUMMARY = "insight_summary"
    REPORT = "report"


class ArtifactFreshness(str, Enum):
    """How much an artifact's stored result can still be relied on.

    **Never a stored column.** It is computed on every read from the
    artifact's own recorded timestamps/fingerprints and the live
    environment (`artifacts.staleness.assess_freshness`) -- a persisted
    boolean is precisely how a stale artifact comes to look current, since
    nothing updates it when the thing it describes changes underneath.
    """

    #: Inside the freshness budget, with nothing else disagreeing.
    CURRENT = "current"
    #: Past the "aging" budget but not yet past the stale one -- usable,
    #: and dated in the UI rather than silently presented as current.
    AGING = "aging"
    #: Past the stale budget, or something observable has changed
    #: underneath it (the schema, or the numbers on the last refresh).
    STALE = "stale"
    #: The turn this artifact references is gone (its conversation or its
    #: answer was deleted), so there is nothing left to be fresh about.
    SOURCE_MISSING = "source_missing"
    #: No result was ever captured for this artifact -- the question was
    #: answered but its SQL was never confirmed and run, so there is no
    #: result to be fresh or stale. Distinguished from `SOURCE_MISSING`
    #: because the source turn is still perfectly readable.
    NO_RESULT_CAPTURED = "no_result_captured"


class StalenessReason(str, Enum):
    """Why an artifact is not `CURRENT`.

    Reasons accumulate rather than short-circuiting -- "why is this
    dated" usually has more than one answer, and the first one found is
    not necessarily the one a reader needs (the same reason
    `kpi_monitoring`'s own suppression reasons accumulate).
    """

    AGED_PAST_BUDGET = "aged_past_budget"
    AGED_PAST_STALE_BUDGET = "aged_past_stale_budget"
    SOURCE_TURN_DELETED = "source_turn_deleted"
    NO_RESULT_CAPTURED = "no_result_captured"
    SCHEMA_CHANGED = "schema_changed"
    RESULT_CHANGED_ON_REFRESH = "result_changed_on_refresh"
    LAST_REFRESH_FAILED = "last_refresh_failed"
    DATABASE_NOT_AVAILABLE = "database_not_available"


class RefreshOutcome(str, Enum):
    """Outcome of the most recent refresh attempt.

    The same three-value shape `bi_integration.models.RefreshOutcome`
    uses, kept as its own enum because an artifact and a BI connector are
    refreshed for different reasons and neither should constrain the
    other's vocabulary.
    """

    NEVER_REFRESHED = "never_refreshed"
    SUCCESS = "success"
    FAILED = "failed"


class ResultSnapshotRef(BaseModel):
    """What is known about an artifact's result *without* copying it.

    `row_count`/`captured_at`/`fingerprint` are the three facts staleness
    actually needs, and all three are small. The rows themselves stay
    where they already are -- in the source turn's own
    `ai_outputs.metadata.result_snapshot` -- so saving a 50-row result
    adds three scalars to the database, not a second copy of the result
    (requirement 3).
    """

    model_config = ConfigDict(frozen=True)

    row_count: int | None = None
    captured_at: datetime | None = None
    #: sha256 over the snapshot's columns and rows. Lets a refresh say
    #: "the numbers moved" rather than only "a refresh happened".
    fingerprint: str | None = None


class ArtifactLineage(BaseModel):
    """The source/lineage metadata an artifact retains, all of it copied
    from the `AskResponse` the turn already returned -- never re-derived,
    and never anything the caller was not already shown.

    `truth_level` is `AI_INFERENCE` for the artifact's own narrative
    (`insight`) and is carried here only so the workspace can label it
    with the same vocabulary every other surface uses; the artifact
    itself asserts nothing new.
    """

    model_config = ConfigDict(frozen=True)

    database: str | None = None
    model: str | None = None
    sources_used: tuple[str, ...] = ()
    tables_referenced: tuple[str, ...] = ()
    governed_metrics: tuple[str, ...] = ()
    insight: str | None = None
    #: How many figures in `insight` the server matched against the result
    #: before storing it (`AskResponse.insight_numbers_checked`). `None`
    #: means no count was reported, which the UI states as such rather
    #: than implying a check happened -- see `frontend/src/lib/evidence.ts`.
    insight_numbers_checked: int | None = None
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE


class ArtifactSnapshot(BaseModel):
    """One artifact as every caller outside the repository sees it.

    Carries the identifiers the brief names -- source question id
    (`prompt_id`), answer/result id (`ai_output_id`), generated timestamp
    (`created_at`) -- plus the lineage and the result *reference*.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    owner_user_id: str
    conversation_id: str
    #: The `identity.models.Prompt` row that asked the question. This is
    #: also the `entryId` a reloaded conversation gives that turn on the
    #: frontend (`serverMessagesToQueryHistory` keys on the prompt row's
    #: id), which is what makes "navigate back to the source question"
    #: reuse the existing `turn-${entryId}` anchor instead of needing a
    #: second scroll mechanism.
    prompt_id: str | None = None
    ai_output_id: str | None = None

    artifact_type: ArtifactType
    title: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    database_id: str | None = None
    #: The SQL recorded at creation. Returned to a caller only when
    #: `api/artifacts.py` has re-checked that this caller may see it --
    #: this field being populated on the snapshot means "recorded", never
    #: "cleared for disclosure".
    source_sql: str | None = None
    #: The schema fingerprint of `database_id` when this artifact was
    #: created, from the schema index's own manifest. Compared against the
    #: live one on read.
    schema_fingerprint: str | None = None

    origin: ResultSnapshotRef = Field(default_factory=ResultSnapshotRef)
    lineage: ArtifactLineage = Field(default_factory=ArtifactLineage)

    last_refresh_at: datetime | None = None
    last_refresh_outcome: RefreshOutcome = RefreshOutcome.NEVER_REFRESHED
    last_refresh_error: str | None = None
    refreshed: ResultSnapshotRef = Field(default_factory=ResultSnapshotRef)

    @property
    def is_refreshable(self) -> bool:
        """Whether a refresh is even meaningful, judged from the data and
        not from `artifact_type`: a refresh re-runs recorded SQL against a
        recorded database, so an artifact missing either cannot be
        refreshed however it happens to be labelled."""
        return bool(self.source_sql) and bool(self.database_id)


class FreshnessAssessment(BaseModel):
    """`artifacts.staleness.assess_freshness`'s result."""

    model_config = ConfigDict(frozen=True)

    state: ArtifactFreshness
    reasons: tuple[StalenessReason, ...] = ()
    #: Age of the result the artifact currently stands on (its refresh if
    #: it has one, else its origin capture), in seconds. `None` when no
    #: result was ever captured.
    result_age_seconds: float | None = None
    #: The timestamp `result_age_seconds` was measured from, so a reader
    #: can be shown the date rather than only a verdict.
    result_as_of: datetime | None = None

    @property
    def is_current(self) -> bool:
        return self.state is ArtifactFreshness.CURRENT
