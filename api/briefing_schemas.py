"""Request/response schemas for `/briefing` (Prompt 51,
`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`).

Mirrors `briefing/models.py` field by field rather than returning the
internal models directly -- the same boundary every other router in this
codebase keeps (`api/kpi_monitoring_schemas.py`,
`api/data_quality_schemas.py`), so an internal model may gain a field
without that field silently becoming public API.

**The one input source that arrives in the request body** is
data-quality findings: nothing in this codebase persists a data-quality
scan (Prompt 41 deliberately added no history), so a caller that wants
them in a briefing passes the findings from its own just-completed
`POST /data-quality/scan`. Only aggregates are accepted --
`DataQualityFindingIn` has no field a sampled row value could occupy,
which is deliberate: `data_quality.models.OrphanDetail
.sample_orphan_values` and friends are real row content and have no
place in an executive summary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DataQualityFindingIn(BaseModel):
    """One already-computed data-quality finding, aggregates only."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    dimension: str = Field(min_length=1, max_length=64)
    severity: Literal["info", "warning", "critical"]
    table: str = Field(min_length=1, max_length=300)
    column: str | None = Field(default=None, max_length=300)
    claim_text: str = Field(min_length=1, max_length=2000)
    formula: str = Field(default="", max_length=1000)
    observed: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Deterministic aggregates behind this finding (e.g. "
            "`null_rate`, `duplicate_row_count`, `p_value`). A claim "
            "stating a number absent from here is withheld as "
            "unsupported, so supplying them is what gets the finding "
            "into the briefing."
        ),
    )
    engine_version: str = Field(min_length=1, max_length=64)
    observed_at: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "When the scan observed its data, ISO 8601. Omitted means the "
            "age cannot be checked, which the briefing discloses rather "
            "than assuming freshness."
        ),
    )


class BriefingRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    database_id: str = Field(min_length=1, max_length=200)
    period: Literal["daily", "weekly", "monthly"] = "daily"
    period_label: str = Field(default="", max_length=120)
    as_of: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The reference instant every staleness check is made against, "
            "ISO 8601. Omitted uses this server's current time. Supplying "
            "it is what makes a briefing for a past period judge "
            "freshness against that period rather than against now."
        ),
    )
    data_quality_findings: tuple[DataQualityFindingIn, ...] = ()


class EvidenceLinkOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    reference_kind: str
    reference_id: str
    observed: dict[str, float]
    versions: dict[str, str]
    recorded_at: str | None
    truth_level: str


class BriefingItemOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    section: str
    claim_class: str
    headline: str
    detail: str
    subject: str
    period_label: str
    risk_score: int
    risk_tier: str
    evidence: tuple[EvidenceLinkOut, ...]
    is_stale: bool
    stale_reason: str
    limitations: tuple[str, ...]
    truth_level: str


class SourceCoverageOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    supplied: bool
    input_count: int
    items_produced: int
    note: str


class StaleInputOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    reference_id: str
    recorded_at: str | None
    age_seconds: float | None
    budget_seconds: float
    detail: str


class WithheldClaimOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    section: str
    headline: str
    code: str
    detail: str


class BriefingResponse(BaseModel):
    """One assembled briefing.

    `observed_fact_count`/`estimate_count`/`recommendation_count` are
    returned alongside the items so a client can show the fact/estimate/
    recommendation split without classifying anything itself -- the
    mission's "clearly distinguish" requirement, made available at the
    API boundary rather than left to each consumer.
    """

    model_config = ConfigDict(frozen=True)

    database_id: str
    period: str
    period_label: str
    as_of: str
    items: tuple[BriefingItemOut, ...]
    coverage: tuple[SourceCoverageOut, ...]
    empty_sections: tuple[str, ...]
    stale_inputs: tuple[StaleInputOut, ...]
    withheld_claims: tuple[WithheldClaimOut, ...]
    limitations: tuple[str, ...]
    truncated_sections: tuple[str, ...]
    observed_fact_count: int
    estimate_count: int
    recommendation_count: int
    stale_item_count: int
    report_markdown: str
    engine_version: str


__all__ = [
    "BriefingItemOut",
    "BriefingRequest",
    "BriefingResponse",
    "DataQualityFindingIn",
    "EvidenceLinkOut",
    "SourceCoverageOut",
    "StaleInputOut",
    "WithheldClaimOut",
]
