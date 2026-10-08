"""Typed contracts for the Data Quality Command Center -- Prompt 41
(`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

Every rule a caller may declare is a typed, closed-vocabulary value --
a column name, an operator, a bound -- never a free-text formula or
expression string an LLM could author, the identical "never let an LLM
invent a check" structural guarantee `optimization.models`/
`analytics.scenario.models` already establish for their own engines
(see each module's own docstring).

**Every finding this package's engine produces is
`DataTruthLevel.DATABASE_FACT`, not `AI_INFERENCE`.** This is a
deliberate departure from every other engine in this series
(`analytics.scenario`/`optimization`/`analytics.predictive`/
`analytics.causal`, all `AI_INFERENCE` ceilings): a null rate, a
duplicate key, an orphaned foreign-key value, an out-of-range date, an
unexpected category, and a stale timestamp are not predictions,
simulations, or inferences about something not yet observed -- they are
deterministic measurements of real, already-fetched rows, the identical
posture `analytics.engine.AnalyticsResult`/`AnalyticsFinding.claim.level`
already establish for their own, narrower statistics. A quality *score*
is likewise a disclosed, deterministic formula applied to those same
real measurements, not a judgment call presented as more certain than
it is. See `tests/security/test_data_quality_findings_stay_observed_fact.py`
for the regression proof.

**Distribution-stability lineage is table-level, not column-level.**
`identity.models.SemanticCatalogEntry.source_tables` is a real, persisted
field; `source_columns` does not exist on that table (confirmed by
inspection -- every reader of a `CatalogEntrySnapshot` that renders a
`source_columns` field hardcodes it to `()`, since the catalog schema was
never extended to carry it). `data_quality.lineage` therefore connects a
finding to an affected governed metric/dashboard by *table* name only --
a disclosed, honest scope boundary, not a silently approximate
column-level claim.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel, ProvenancedClaim

#: Bumped when this module's typed shapes or the engine's detection/
#: scoring formulas change in a way a consumer should be able to detect --
#: master-contract rule 11, the identical convention every other engine
#: module in this codebase already follows.
DATA_QUALITY_ENGINE_VERSION = "1.0.0"


class DataQualityDimension(str, Enum):
    """The seven dimensions this command center covers -- the mission's
    own named list, as a closed set rather than a free-form string, so a
    future dashboard/report can group by dimension without string
    matching."""

    COMPLETENESS = "completeness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    CONSISTENCY = "consistency"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    FRESHNESS = "freshness"
    DISTRIBUTION_STABILITY = "distribution_stability"


class DataQualitySeverity(str, Enum):
    """A closed, three-tier severity -- deterministically derived from
    each finding's own magnitude (never an LLM's or a human's subjective
    call), see each `*_candidates`/`_assess_*` function in
    `data_quality/engine.py` for the exact, disclosed threshold."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# --- Declared, typed rules (never a free-text formula) ----------------------


class DateColumnRule(BaseModel):
    """One column's expected date/datetime bounds -- VALIDITY. Values are
    parsed with `datetime.date.fromisoformat`/`datetime.datetime
    .fromisoformat` only (a disclosed, bounded scope matching
    `analytics.engine`'s own "no general date-string parsing" precedent)
    -- an unparseable value is itself an invalid-date finding, not a
    silent skip.
    """

    model_config = ConfigDict(frozen=True)

    column: str = Field(..., min_length=1)
    min_date: str | None = None
    max_date: str | None = None


class CategoricalColumnRule(BaseModel):
    """One column's expected, closed set of allowed values -- VALIDITY. A
    non-null value outside `allowed_values` is an "unexpected category"
    finding."""

    model_config = ConfigDict(frozen=True)

    column: str = Field(..., min_length=1)
    allowed_values: tuple[str, ...] = Field(..., min_length=1)


class ConsistencyOperator(str, Enum):
    """The closed comparison vocabulary a `ConsistencyRule` may use --
    plain, typed data, never a formula string an LLM could author (the
    identical structural guarantee `optimization.models.ConstraintOperator`
    already establishes for a different engine)."""

    LE = "<="
    LT = "<"
    GE = ">="
    GT = ">"
    EQ = "=="
    NE = "!="


class ConsistencyRule(BaseModel):
    """One cross-column consistency expectation -- CONSISTENCY -- e.g.
    `start_date <= end_date`. Both sides are numeric or ISO-date/datetime
    strings (compared the identical way `DateColumnRule` parses dates); a
    row where either side is null, or not comparable as either numbers or
    dates, is skipped and counted separately in
    `ConsistencyViolationDetail.skipped_row_count`, never silently
    treated as a pass.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    left_column: str = Field(..., min_length=1)
    operator: ConsistencyOperator
    right_column: str = Field(..., min_length=1)
    description: str = ""


class ReferentialCheck(BaseModel):
    """One child-to-parent referential-integrity expectation --
    REFERENTIAL_INTEGRITY. `parent_key_values` is the parent table's own
    already-fetched key column values (this engine never queries a
    database itself -- see `data_quality/engine.py`'s own module
    docstring), so a child value not present in that set (and not null)
    is an orphan.
    """

    model_config = ConfigDict(frozen=True)

    child_column: str = Field(..., min_length=1)
    parent_table: str = Field(..., min_length=1)
    parent_key_values: tuple[str, ...] = Field(default_factory=tuple)


class FreshnessRule(BaseModel):
    """One table's freshness expectation -- FRESHNESS. `timestamp_column`
    values are parsed with `datetime.datetime.fromisoformat`;
    `as_of` is the caller's own reference "now" (never read from the
    server's wall clock internally, which is what keeps this engine a
    pure function of its inputs -- the identical "no internal
    timestamp" posture `analytics.models.ForecastModelMetadata`'s own
    docstring already establishes for a different engine). `None`
    `max_staleness_seconds` falls back to `Settings
    .data_quality_default_max_staleness_seconds`.
    """

    model_config = ConfigDict(frozen=True)

    timestamp_column: str = Field(..., min_length=1)
    as_of: str = Field(..., min_length=1)
    max_staleness_seconds: int | None = Field(default=None, gt=0)


# --- Findings -----------------------------------------------------------


class NullSpikeDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    null_count: int
    total_count: int
    null_rate: float
    threshold: float


class DuplicateDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_columns: tuple[str, ...]
    sample_duplicate_keys: tuple[str, ...]
    distinct_duplicate_key_count: int
    duplicate_row_count: int
    total_row_count: int


class OrphanDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    child_column: str
    parent_table: str
    sample_orphan_values: tuple[str, ...]
    distinct_orphan_value_count: int
    orphan_row_count: int
    checked_row_count: int


class InvalidValueDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    reason: Literal["invalid_date", "out_of_range_date", "unexpected_category"]
    sample_values: tuple[str, ...]
    invalid_row_count: int
    checked_row_count: int


class ConsistencyViolationDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_name: str
    left_column: str
    operator: str
    right_column: str
    violation_row_count: int
    skipped_row_count: int
    checked_row_count: int


class FreshnessDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp_column: str
    latest_value: str | None
    as_of: str
    staleness_seconds: float | None
    max_staleness_seconds: int


class OutlierDetail(BaseModel):
    """Re-tags an already-computed `analytics.models.OutlierFinding` (see
    `data_quality/engine.py`'s own reuse of `analytics.engine
    .compute_analytics_result` -- this module never reimplements z-score/
    IQR detection)."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: float
    column: str
    method: Literal["zscore", "iqr"]
    formula: str


class DistributionDriftDetail(BaseModel):
    """Re-tags an already-computed `analytics.models.FeatureDriftFinding`
    (see `data_quality/engine.py`'s own reuse of `analytics.predictive
    .detect_feature_drift`)."""

    model_config = ConfigDict(frozen=True)

    feature: str
    reference_count: int
    new_count: int
    ks_statistic: float
    p_value: float
    threshold: float


class DataQualityFinding(BaseModel):
    """One finding about one table's real, already-fetched rows -- always
    `claim.level == DataTruthLevel.DATABASE_FACT` (see this module's own
    docstring). Exactly one of the per-kind detail fields below is set,
    matching `dimension` -- the identical "one homogeneous tuple, `kind`
    alone tells you which field to read" convention
    `analytics.models.AnalyticsFinding` already establishes.
    """

    model_config = ConfigDict(frozen=True)

    dimension: DataQualityDimension
    severity: DataQualitySeverity
    table: str
    column: str | None
    #: A short, deterministic, human-readable sentence, always
    #: `level == DataTruthLevel.DATABASE_FACT` -- never an LLM's own
    #: claim, same posture as `analytics.models.AnalyticsFinding.claim`.
    claim: ProvenancedClaim
    formula: str
    null_spike: NullSpikeDetail | None = None
    duplicate: DuplicateDetail | None = None
    orphan: OrphanDetail | None = None
    invalid_value: InvalidValueDetail | None = None
    consistency_violation: ConsistencyViolationDetail | None = None
    freshness: FreshnessDetail | None = None
    outlier: OutlierDetail | None = None
    distribution_drift: DistributionDriftDetail | None = None


class DimensionScore(BaseModel):
    """One dimension's own 0-100 score for one table -- `None` when the
    dimension was never assessed (no check of that kind was configured
    for this table), kept distinct from a real `100` ("assessed, and
    found clean") so a caller never mistakes "we didn't look" for "we
    looked and it's fine" -- the literal "explainable evidence" half of
    the mission's own scoring requirement.
    """

    model_config = ConfigDict(frozen=True)

    dimension: DataQualityDimension
    score: float | None
    checks_run: int
    findings_count: int
    formula: str


class TableQualityScore(BaseModel):
    """One table's full data-quality assessment -- findings plus a
    per-dimension, explainable score breakdown.

    `overall_score` is the unweighted mean of every `DimensionScore
    .score` that is not `None` (a dimension never assessed does not drag
    the average toward either extreme) -- `None` when *no* dimension was
    assessed at all (no rule of any kind was configured for this table).
    """

    model_config = ConfigDict(frozen=True)

    table: str
    row_count: int
    dimension_scores: tuple[DimensionScore, ...]
    findings: tuple[DataQualityFinding, ...]
    overall_score: float | None
    insufficient_checks: tuple[str, ...] = ()
    formula: str = (
        "overall_score = mean(dimension_score for dimension_score in dimension_scores "
        "if dimension_score is not None)"
    )


class DataQualityReport(BaseModel):
    """`data_quality.engine.assess_data_quality`'s full result -- Prompt 41
    (`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

    Attributes:
        tables: One `TableQualityScore` per table the caller submitted,
            in submission order.
        overall_score: The unweighted mean of every table's own
            `TableQualityScore.overall_score` that is not `None` --
            `None` when no table had any dimension assessed.
        findings: Every finding across every table, flattened, for a
            caller that wants the full list without iterating `tables`.
        insufficient_checks: Every disclosed skip at the report level
            (e.g. a restricted column's checks suppressed for an
            unauthorized caller -- see this package's own module
            docstring) -- always inspectable, never a silent omission.
        truth_level: Always `DataTruthLevel.DATABASE_FACT` -- see this
            module's own docstring for why that is the honest
            classification here, unlike every other engine in this
            series.
        engine_version: `DATA_QUALITY_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    tables: tuple[TableQualityScore, ...]
    overall_score: float | None
    findings: tuple[DataQualityFinding, ...]
    insufficient_checks: tuple[str, ...] = ()
    truth_level: Literal[DataTruthLevel.DATABASE_FACT] = DataTruthLevel.DATABASE_FACT
    engine_version: str = DATA_QUALITY_ENGINE_VERSION


# --- Input shapes ---------------------------------------------------------


class TableQualityInput(BaseModel):
    """One table's already-fetched data plus every declared check to run
    against it -- the mission's own "configurable thresholds" requirement,
    made concrete: every threshold below is either an explicit override
    here or falls back to a `Settings` default, and every rule is a typed
    value, never a formula string (see this module's own docstring).

    `columns`/`rows` are read only, never retained or forwarded whole
    anywhere -- the identical "aggregate-only" posture every other
    engine in this codebase's own `*QualityInput`/`*Request` shape
    already establishes.
    """

    model_config = ConfigDict(frozen=True)

    table: str = Field(..., min_length=1)
    columns: list[str] = Field(..., min_length=1)
    rows: list[tuple] = Field(default_factory=list)
    null_rate_overrides: dict[str, float] = Field(default_factory=dict)
    date_rules: tuple[DateColumnRule, ...] = ()
    categorical_rules: tuple[CategoricalColumnRule, ...] = ()
    unique_key_sets: tuple[tuple[str, ...], ...] = ()
    consistency_rules: tuple[ConsistencyRule, ...] = ()
    referential_checks: tuple[ReferentialCheck, ...] = ()
    freshness_rule: FreshnessRule | None = None
    #: A reference (e.g. prior-period) dataset for the same columns, for
    #: DISTRIBUTION_STABILITY -- `None`/empty means that dimension is not
    #: assessed for this table.
    reference_columns: list[str] = Field(default_factory=list)
    reference_rows: list[tuple] = Field(default_factory=list)
    drift_columns: tuple[str, ...] = ()
    #: Whether this table's own per-column outlier/null statistics should
    #: be computed via `analytics.engine.compute_analytics_result` --
    #: on by default; a caller with an unusually large table and no need
    #: for the VALIDITY-outlier/COMPLETENESS reuse can opt out to save
    #: the (still bounded, still pure) extra computation.
    enable_column_statistics: bool = True


__all__ = [
    "DATA_QUALITY_ENGINE_VERSION",
    "DataQualityDimension",
    "DataQualitySeverity",
    "DateColumnRule",
    "CategoricalColumnRule",
    "ConsistencyOperator",
    "ConsistencyRule",
    "ReferentialCheck",
    "FreshnessRule",
    "NullSpikeDetail",
    "DuplicateDetail",
    "OrphanDetail",
    "InvalidValueDetail",
    "ConsistencyViolationDetail",
    "FreshnessDetail",
    "OutlierDetail",
    "DistributionDriftDetail",
    "DataQualityFinding",
    "DimensionScore",
    "TableQualityScore",
    "DataQualityReport",
    "TableQualityInput",
]
