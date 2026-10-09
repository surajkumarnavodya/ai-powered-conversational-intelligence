"""Request/response models for `api/kpi_monitoring.py` (Prompt 50).

The request carries the data to monitor -- a period series per policy,
plus optional dimension breakdowns -- because this feature never queries
a database itself. That is the same "the caller supplies already-governed
rows" shape `api/scenarios.py`, `api/optimization.py` and
`api/data_quality.py` already use, and it is what keeps
`agent/sql_validator.py`'s deterministic controls the only way rows are
ever produced.

Frozen responses, `extra="forbid"` requests, bounded collections -- the
same conventions as the rest of `api/`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MonitoringPolicyIn(BaseModel):
    """One policy. `metric_concept_key` must name a published catalog
    metric in the caller's own tenant, or the policy is refused."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    policy_id: str = Field(min_length=1, max_length=120)
    metric_concept_key: str = Field(min_length=1, max_length=200)
    comparison: Literal["previous_period", "same_period_last_year", "trailing_average"] = (
        "previous_period"
    )
    min_change_percent: float = Field(default=10.0, ge=0.0)
    lower_threshold: float | None = None
    upper_threshold: float | None = None
    seasonal_lag: int = Field(default=12, gt=0, le=1000)
    trailing_window: int = Field(default=4, gt=1, le=1000)
    dimension_columns: tuple[str, ...] = ()
    enabled: bool = True


class BreakdownIn(BaseModel):
    """A `(columns, rows)` dimension breakdown for one period -- exactly
    the shape `POST /execute` returns, so a caller hands it straight
    through without reshaping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: tuple[str, ...]
    rows: tuple[tuple[float | str | None, ...], ...] = ()


class MetricSeriesIn(BaseModel):
    """One policy's data: its chronological series, and optionally the
    breakdowns needed to attribute a change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1, max_length=120)
    points: tuple[tuple[str, float], ...] = Field(
        description="`(period_label, value)`, oldest first.",
    )
    current_breakdown: BreakdownIn | None = None
    baseline_breakdown: BreakdownIn | None = None
    value_column: str | None = None


class BusinessCalendarIn(BaseModel):
    """Periods that are not business periods -- excluded from baselines
    and never alerted on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(default="default", min_length=1, max_length=120)
    non_business_periods: tuple[str, ...] = ()


class QuietPeriodIn(BaseModel):
    """A window where findings accrue silently."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    periods: tuple[str, ...] = ()
    reason: str = Field(default="", max_length=500)


class AlertPreferenceIn(BaseModel):
    """One recipient's preferences. `user_id` is an opaque label for
    addressing a bundle in the response -- it grants nothing and is never
    resolved to an account."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=64)
    min_risk_tier: Literal["low", "medium", "high"] = "low"
    muted_metric_keys: tuple[str, ...] = ()
    max_alerts_per_run: int = Field(default=20, gt=0, le=1000)


class ScanRequest(BaseModel):
    """One monitoring run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_id: str = Field(min_length=1, max_length=200)
    policies: tuple[MonitoringPolicyIn, ...] = ()
    series: tuple[MetricSeriesIn, ...] = ()
    calendar: BusinessCalendarIn | None = None
    quiet_periods: tuple[QuietPeriodIn, ...] = ()
    preferences: tuple[AlertPreferenceIn, ...] = ()
    persist: bool = Field(
        default=True,
        description=(
            "Whether to write the findings into the shared review queue. "
            "False runs the whole pipeline and returns everything without "
            "storing anything -- for previewing a policy change against "
            "real data before it starts raising alerts."
        ),
    )


class PolicyViolationOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str
    code: str
    detail: str


class ContributorOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    current_value: float
    baseline_value: float
    contribution: float
    contribution_percent: float


class EvidenceOut(BaseModel):
    """Baseline, affected dimensions, confidence and limitations.

    `contributors`/`confidence` are populated only when
    `has_sufficient_evidence` is true -- never a cause, always a
    contribution to an arithmetic total.
    """

    model_config = ConfigDict(frozen=True)

    has_sufficient_evidence: bool
    baseline_total: float | None = None
    current_total: float | None = None
    magnitude: float | None = None
    magnitude_percent: float | None = None
    contributors: tuple[ContributorOut, ...] = ()
    confidence: float | None = None
    limitations: tuple[str, ...] = ()
    dimension_columns: tuple[str, ...] = ()
    truth_level: str


class ChangeOut(BaseModel):
    """One detected movement.

    The numbers are observed arithmetic over the rows the caller
    supplied; `truth_level` is always `ai_inference` because the judgment
    that the movement is *meaningful* is this platform's inference, not
    an observation.
    """

    model_config = ConfigDict(frozen=True)

    finding_key: str
    policy_id: str
    metric_concept_key: str
    metric_display_name: str
    kind: str
    period: str
    current_value: float
    baseline_value: float | None = None
    baseline_label: str
    comparison: str
    change_absolute: float | None = None
    change_percent: float | None = None
    threshold_crossed: float | None = None
    detection_methods: tuple[str, ...] = ()
    direction: str
    risk_score: int
    risk_tier: str
    rank_reasons: tuple[str, ...] = ()
    title: str
    truth_level: str
    evidence: EvidenceOut


class AlertDecisionOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_key: str
    metric_concept_key: str
    period: str
    risk_score: int
    risk_tier: str
    alerted: bool
    suppressed_by: tuple[str, ...] = ()
    suppression_detail: str = ""


class AlertBundleOut(BaseModel):
    """What one recipient would be told.

    **No delivery channel exists in this platform** -- nothing is sent;
    this describes what would be. See `kpi_monitoring.models.AlertBundle`.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str
    alerted_count: int
    suppressed_count: int
    decisions: tuple[AlertDecisionOut, ...] = ()


class ScanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_id: str
    policies_evaluated: int
    findings_detected: int
    truncated: bool
    persisted: bool
    created: int = 0
    updated: int = 0
    unchanged: int = Field(
        default=0,
        description=(
            "Findings whose content was byte-identical to what is already "
            "stored -- the storage-level half of duplicate suppression. A "
            "high number across runs is the expected steady state, not a "
            "problem."
        ),
    )
    violations: tuple[PolicyViolationOut, ...] = ()
    series_skipped: tuple[str, ...] = ()
    changes: tuple[ChangeOut, ...] = ()
    alert_bundles: tuple[AlertBundleOut, ...] = ()
    report_markdown: str = ""
    engine_version: str


class FindingOut(BaseModel):
    """One stored KPI finding from the shared review queue."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    title: str
    detail: str
    status: str
    risk_score: int
    risk_tier: str
    confidence: float
    truth_level: str
    version: int
    reasons: list[str] = Field(default_factory=list)
    subjects: list[dict] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    decision_note: str | None = None


__all__ = [
    "AlertBundleOut",
    "AlertDecisionOut",
    "AlertPreferenceIn",
    "BreakdownIn",
    "BusinessCalendarIn",
    "ChangeOut",
    "ContributorOut",
    "EvidenceOut",
    "FindingOut",
    "MetricSeriesIn",
    "MonitoringPolicyIn",
    "PolicyViolationOut",
    "QuietPeriodIn",
    "ScanRequest",
    "ScanResponse",
]
