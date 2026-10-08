"""Request and response models for `api/data_quality.py` (Prompt 41,
`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

Request models forbid unknown fields and strip whitespace, the same
convention `api/predictive_schemas.py`/`api/optimization_schemas.py`'s own
request models already establish, so a client cannot slip in a
`caller_roles` override -- roles are resolved server-side from the
authenticated identity.

Response models mirror `data_quality.models`/`data_quality.lineage`'s own
typed shapes field-by-field with plain primitives (strings instead of
enums), the identical "mirror the internal engine model, don't re-export
it" convention `PredictiveTrainResponse`/`OptimizationValidateResponse`
already establish.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from data_quality.models import ConsistencyOperator

# --- Scan request -----------------------------------------------------------


class DateColumnRuleIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    column: str = Field(..., min_length=1)
    min_date: str | None = None
    max_date: str | None = None


class CategoricalColumnRuleIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    column: str = Field(..., min_length=1)
    allowed_values: list[str] = Field(..., min_length=1)


class ConsistencyRuleIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1)
    left_column: str = Field(..., min_length=1)
    operator: ConsistencyOperator
    right_column: str = Field(..., min_length=1)
    description: str = ""


class ReferentialCheckIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    child_column: str = Field(..., min_length=1)
    parent_table: str = Field(..., min_length=1)
    parent_key_values: list[str] = Field(default_factory=list)


class FreshnessRuleIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    timestamp_column: str = Field(..., min_length=1)
    as_of: str = Field(..., min_length=1)
    max_staleness_seconds: int | None = Field(default=None, gt=0)


class TableScanRequest(BaseModel):
    """One table's already-fetched data plus every declared check -- the
    caller's own already-fetched, already-governed result (e.g. from a
    prior `POST /execute`), never fetched by this route itself."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    table: str = Field(..., min_length=1)
    columns: list[str] = Field(..., min_length=1)
    rows: list[list[Any]] = Field(default_factory=list)
    null_rate_overrides: dict[str, float] = Field(default_factory=dict)
    date_rules: list[DateColumnRuleIn] = Field(default_factory=list)
    categorical_rules: list[CategoricalColumnRuleIn] = Field(default_factory=list)
    unique_key_sets: list[list[str]] = Field(default_factory=list)
    consistency_rules: list[ConsistencyRuleIn] = Field(default_factory=list)
    referential_checks: list[ReferentialCheckIn] = Field(default_factory=list)
    freshness_rule: FreshnessRuleIn | None = None
    reference_columns: list[str] = Field(default_factory=list)
    reference_rows: list[list[Any]] = Field(default_factory=list)
    drift_columns: list[str] = Field(default_factory=list)
    enable_column_statistics: bool = True


class DataQualityScanRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    tables: list[TableScanRequest] = Field(..., min_length=1)


# --- Scan response -----------------------------------------------------------


class ClaimOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str
    level: str
    source: str | None = None


class NullSpikeDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    null_count: int
    total_count: int
    null_rate: float
    threshold: float


class DuplicateDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_columns: list[str]
    sample_duplicate_keys: list[str]
    distinct_duplicate_key_count: int
    duplicate_row_count: int
    total_row_count: int


class OrphanDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    child_column: str
    parent_table: str
    sample_orphan_values: list[str]
    distinct_orphan_value_count: int
    orphan_row_count: int
    checked_row_count: int


class InvalidValueDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    reason: str
    sample_values: list[str]
    invalid_row_count: int
    checked_row_count: int


class ConsistencyViolationDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_name: str
    left_column: str
    operator: str
    right_column: str
    violation_row_count: int
    skipped_row_count: int
    checked_row_count: int


class FreshnessDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp_column: str
    latest_value: str | None
    as_of: str
    staleness_seconds: float | None
    max_staleness_seconds: int


class OutlierDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    value: float
    column: str
    method: str
    formula: str


class DistributionDriftDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature: str
    reference_count: int
    new_count: int
    ks_statistic: float
    p_value: float
    threshold: float


class DataQualityFindingOut(BaseModel):
    """Mirrors `data_quality.models.DataQualityFinding` field-by-field --
    exactly one of the per-kind detail fields below is set, matching
    `dimension` (the identical convention the internal model itself
    documents)."""

    model_config = ConfigDict(frozen=True)

    dimension: str
    severity: str
    table: str
    column: str | None
    claim: ClaimOut
    formula: str
    null_spike: NullSpikeDetailOut | None = None
    duplicate: DuplicateDetailOut | None = None
    orphan: OrphanDetailOut | None = None
    invalid_value: InvalidValueDetailOut | None = None
    consistency_violation: ConsistencyViolationDetailOut | None = None
    freshness: FreshnessDetailOut | None = None
    outlier: OutlierDetailOut | None = None
    distribution_drift: DistributionDriftDetailOut | None = None


class DimensionScoreOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: str
    score: float | None
    checks_run: int
    findings_count: int
    formula: str


class TableQualityScoreOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    row_count: int
    dimension_scores: list[DimensionScoreOut]
    findings: list[DataQualityFindingOut]
    overall_score: float | None
    insufficient_checks: list[str]


class DataQualityScanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tables: list[TableQualityScoreOut]
    overall_score: float | None
    findings: list[DataQualityFindingOut]
    insufficient_checks: list[str]
    truth_level: str
    engine_version: str


# --- Lineage request/response -------------------------------------------------


class TableSeverityCountsIn(BaseModel):
    """One table's own severity-count summary -- built from a prior
    `DataQualityScanResponse.tables[i]`'s own `findings`, never re-sent as
    raw rows (lineage never needs the rows themselves)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    table: str = Field(..., min_length=1)
    severity_counts: dict[str, int] = Field(default_factory=dict)


class DataQualityLineageRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    database_id: str = Field(..., min_length=1)
    tables: list[TableSeverityCountsIn] = Field(..., min_length=1)
    min_severity: str = Field(default="warning")


class CatalogConceptRefOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    concept_key: str
    name: str
    concept_type: str
    status: str
    owner: str | None
    database_id: str
    source_tables: list[str]


class TableLineageImpactOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    finding_count: int
    severity_counts: dict[str, int]
    affected_concepts: list[CatalogConceptRefOut]


class DataQualityLineageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    impacts: list[TableLineageImpactOut]
    unmapped_tables: list[str]
    limitations: list[str]
    engine_version: str
