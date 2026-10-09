"""The typed, storage-free inputs `briefing.engine` consumes.

Every one of these is a plain projection of a row some other governed
subsystem already persisted (or, for data quality, already computed) --
never an ORM object. That keeps this whole package free of `identity`,
`sqlalchemy` and any database handle, which is the structural reason a
briefing cannot read a monitored database, cannot generate SQL and
cannot bypass `agent/sql_validator.py`: it has no way to produce a row.
The projection itself happens in `api/briefing.py`, exactly where
`api/kpi_monitoring.py` already projects its own request body into
`kpi_monitoring.engine`'s inputs.

**What deliberately does not appear here.** No raw or sampled row value
(`data_quality.models.OrphanDetail.sample_orphan_values` and friends are
not projected), no metric `approved_expression`, no SQL text, no
credential, no caller identity. A briefing states aggregates and names
the record they came from; anything finer is read by opening that record
through its own already-authorized route.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KpiFindingInput(BaseModel):
    """One persisted KPI-monitoring finding, projected.

    Mirrors `identity.models.SemanticFinding` as written by
    `kpi_monitoring.findings.change_to_finding` -- `payload` is that
    module's own `kpi_monitoring.models.finding_payload` output, read
    back verbatim rather than recomputed, so the briefing cites exactly
    the numbers a reviewer already saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=300)
    detail: str = Field(default="", max_length=4000)
    status: str = Field(default="open", max_length=16)
    risk_score: int = Field(default=0, ge=0, le=100)
    risk_tier: Literal["low", "medium", "high"] = "low"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    version: int = Field(default=1, ge=1)
    last_seen_at: str | None = None
    #: `kpi_monitoring.models.finding_payload`'s output.
    payload: dict = Field(default_factory=dict)


class SchemaDriftFindingInput(BaseModel):
    """One persisted schema-drift finding, projected.

    The same shared `semantic_findings` row shape as above, with
    `schema_drift.findings`' own payload (which carries the impacted
    published concepts `schema_drift.impact` resolved).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=300)
    detail: str = Field(default="", max_length=4000)
    status: str = Field(default="open", max_length=16)
    risk_score: int = Field(default=0, ge=0, le=100)
    risk_tier: Literal["low", "medium", "high"] = "low"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    version: int = Field(default=1, ge=1)
    last_seen_at: str | None = None
    payload: dict = Field(default_factory=dict)


class RecommendationInput(BaseModel):
    """One persisted `identity.models.RecommendationRecord`, projected.

    `engine_version`/`evidence_version` are that row's own two
    independent version snapshots, carried through unchanged -- they are
    precisely the "versions" half of this prompt's evidence requirement
    for a recommendation, and already content-addressable
    (`identity.repositories.recommendation_governance
    .compute_evidence_version`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(min_length=1, max_length=200)
    category: str | None = None
    kind: Literal["next_question", "action"]
    status: str = Field(min_length=1, max_length=32)
    claim_text: str = Field(min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=4000)
    action: str | None = Field(default=None, max_length=4000)
    affected_entity: str | None = Field(default=None, max_length=500)
    measurable_impact: str | None = Field(default=None, max_length=4000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_count: int = Field(default=0, ge=0)
    limitations: tuple[str, ...] = ()
    engine_version: str = Field(min_length=1, max_length=64)
    evidence_version: str = Field(min_length=1, max_length=64)
    generated_at: str | None = None
    owner_display_name: str | None = Field(default=None, max_length=200)


class DataQualityFindingInput(BaseModel):
    """One `data_quality.models.DataQualityFinding`, projected.

    Data-quality findings are the one source with no persisted history
    anywhere in this codebase (Prompt 41 deliberately added none), so
    they arrive in the request body from a caller that just ran
    `POST /data-quality/scan`. `claim_text`/`formula` are that engine's
    own already-deterministic output; the per-kind counts are its
    aggregates. **No sample value is projected** -- see this module's
    own docstring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str = Field(min_length=1, max_length=64)
    severity: Literal["info", "warning", "critical"]
    table: str = Field(min_length=1, max_length=300)
    column: str | None = Field(default=None, max_length=300)
    claim_text: str = Field(min_length=1, max_length=2000)
    formula: str = Field(default="", max_length=1000)
    #: Deterministic aggregates for this finding (e.g. `null_rate`,
    #: `duplicate_row_count`, `p_value`). Keyed by short name, exactly as
    #: they will appear in the briefing's own `EvidenceLink.observed`.
    observed: dict[str, float] = Field(default_factory=dict)
    engine_version: str = Field(min_length=1, max_length=64)
    #: When the scan that produced this finding observed its data. The
    #: caller's own `as_of`, since nothing persists it.
    observed_at: str | None = None


__all__ = [
    "DataQualityFindingInput",
    "KpiFindingInput",
    "RecommendationInput",
    "SchemaDriftFindingInput",
]
