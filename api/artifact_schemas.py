"""Request/response Pydantic models for `api/artifacts.py` -- see
`docs/conversational-intelligence-ui.md`.

Two response shapes on purpose: `ArtifactOut` is the workspace list row
(everything needed to label, date and prioritise an artifact, with no
result rows at all), and `ArtifactDetailOut` is one opened artifact
(adding the source question, the lineage, the SQL when this caller may
see it, and the result snapshots themselves). A list of 200 artifacts
must not ship 200 result sets.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api.schemas import ChartRecommendationOut

ArtifactTypeLiteral = Literal[
    "analysis", "chart", "result_table", "sql", "insight_summary", "report"
]


class CreateArtifactRequest(BaseModel):
    """Saves an already-answered turn as an artifact.

    `message_id` is the `AiOutput` row id the turn's own `/ask` response
    already returned (`AskResponse.message_id`) -- the same id
    `ExecuteRequest.message_id` carries, so the client needs no new
    bookkeeping. Nothing about the result is sent: the server reads it
    from the turn it already persisted, which is what makes it impossible
    for a client to save an artifact describing a result that never
    happened.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conversation_id: str = Field(min_length=1, max_length=64)
    message_id: str = Field(min_length=1, max_length=64)
    artifact_type: ArtifactTypeLiteral = "analysis"
    #: Defaults server-side to the source question, trimmed -- the same
    #: "derive a readable name from the opening message" convention
    #: `identity.repositories.history._derive_title` already uses.
    title: str | None = Field(default=None, max_length=300)


class RefreshArtifactRequest(BaseModel):
    """No fields -- a refresh re-runs the artifact's *recorded* SQL, never
    SQL the caller supplies. Accepting SQL here would make "refresh" a
    second, less-governed `POST /execute`. Present as a model (with the
    module-level singleton default in `api/artifacts.py`) only so the
    route may gain a bounded option later without a breaking change."""

    model_config = ConfigDict(extra="forbid")


class ArtifactResultOut(BaseModel):
    """One captured result an artifact presents.

    For the origin capture these rows are read straight out of the source
    turn's own `ai_outputs.metadata.result_snapshot` -- the artifact owns
    no copy of them. For a refresh they are the artifact's own, because
    they exist nowhere else.
    """

    model_config = ConfigDict(frozen=True)

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    #: Rows the query actually returned, which may exceed `len(rows)`:
    #: a persisted snapshot is capped by
    #: `Settings.chat_history_max_result_rows`.
    row_count: int | None = None
    returned_rows: int = 0
    truncated: bool = False
    column_types: dict[str, str] | None = None
    chart_recommendation: ChartRecommendationOut | None = None
    captured_at: datetime | None = None


class ArtifactFreshnessOut(BaseModel):
    """`artifacts.staleness.assess_freshness`'s verdict, as sent.

    Computed on every read, never stored -- see `artifacts/staleness.py`.
    `reasons` is everything the reader must be told, which is why it can
    be non-empty on a `current` artifact (a refresh that found different
    numbers supersedes the original capture without making the new one
    stale).
    """

    model_config = ConfigDict(frozen=True)

    state: Literal["current", "aging", "stale", "source_missing", "no_result_captured"]
    reasons: list[str] = Field(default_factory=list)
    result_age_seconds: float | None = None
    result_as_of: datetime | None = None


class ArtifactOut(BaseModel):
    """One workspace list row. Carries no result rows."""

    model_config = ConfigDict(frozen=True)

    id: str
    artifact_type: ArtifactTypeLiteral
    title: str
    created_at: datetime
    updated_at: datetime

    #: The source question's own row id. Also the `entryId` a reloaded
    #: conversation gives that turn on the frontend, which is how "open
    #: the source question" reuses the existing `turn-${entryId}` anchor.
    conversation_id: str
    prompt_id: str | None = None
    message_id: str | None = None

    database: str | None = None
    model: str | None = None
    sources_used: list[str] = Field(default_factory=list)

    #: Row count of the result the artifact currently stands on (its
    #: refresh if the last one succeeded, else its origin capture).
    row_count: int | None = None
    freshness: ArtifactFreshnessOut
    is_refreshable: bool = False
    #: Why not, when `is_refreshable` is false -- a short, fixed sentence,
    #: never a validator rule name or a raw error.
    refresh_blocked_reason: str | None = None
    last_refresh_at: datetime | None = None
    last_refresh_outcome: Literal["never_refreshed", "success", "failed"] = "never_refreshed"
    last_refresh_error: str | None = None


class ArtifactDetailOut(ArtifactOut):
    """One opened artifact."""

    model_config = ConfigDict(frozen=True)

    question: str | None = None
    insight: str | None = None
    insight_numbers_checked: int | None = None
    tables_referenced: list[str] = Field(default_factory=list)
    governed_metrics: list[str] = Field(default_factory=list)
    truth_level: str = "ai_inference"

    #: `None` when the artifact recorded no SQL *or* when this caller may
    #: not be shown it; `sql_withheld_reason` distinguishes the two.
    sql: str | None = None
    sql_withheld_reason: str | None = None

    #: The capture the artifact was created from. `None` once its source
    #: turn is no longer readable -- the rows live in that turn, so they
    #: go when it does, and `freshness.state` says `source_missing`.
    origin_result: ArtifactResultOut | None = None
    #: The most recent successful refresh, when there has been one.
    refreshed_result: ArtifactResultOut | None = None
    #: True when the two differ. The single most useful thing a refresh
    #: can discover, and the literal answer to "is this based on an older
    #: result" (requirement 7).
    result_changed_since_creation: bool = False


class ArtifactListOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ArtifactOut] = Field(default_factory=list)
    total: int = 0
    #: `Settings.artifact_max_per_user`, so the workspace can say how much
    #: room is left rather than only failing a save at the cap.
    limit_per_user: int = 0
