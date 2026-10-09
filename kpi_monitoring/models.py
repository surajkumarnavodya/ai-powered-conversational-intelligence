"""Typed contracts for proactive KPI monitoring (Prompt 50,
`50_PROACTIVE_AI_ANALYST_CONTRACT.md`).

Pure vocabulary: enums, frozen value objects and the small tables the
rest of the package reads. No I/O, no SQLAlchemy, no LLM call -- the
same posture `analytics/models.py`, `optimization/models.py` and
`model_routing/models.py` already establish, and what makes
`kpi_monitoring.detect`/`evidence`/`suppression` testable as pure
functions.

**Three different kinds of claim live in this module, and they are kept
apart deliberately** (master rule 10 -- never present inference as
observed fact):

- An **observed** number (`DetectedChange.current_value`,
  `.baseline_value`, `.change_percent`) is arithmetic over rows the
  caller already fetched through the governed path. `DATABASE_FACT`.
- A **judgment** that such a change is *meaningful* (`DetectedChange`
  existing at all, its `kind`, its ranking) is this platform's own
  inference. `AI_INFERENCE`.
- An **attribution** of that change to a dimension
  (`ChangeEvidence.contributors`) is a separate inference again, and one
  that is only ever populated when `analytics.root_cause` found real
  supporting evidence -- otherwise `has_sufficient_evidence` is `False`,
  the contributors list is empty, and `limitations` says why.

Nothing here ever asserts a *cause*. `analytics.causal`'s own
`CORRELATION_ONLY` framing applies verbatim: a contributor is a
contribution to an arithmetic total, not a demonstrated reason.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.provenance import DataTruthLevel

#: Bumped when the monitoring rules themselves change in a way that could
#: alter whether a given series produces a finding. Recorded on every
#: result, so a stored finding is self-describing about which rule set
#: produced it -- the same reasoning `analytics.engine
#: .ANALYTICS_ENGINE_VERSION` and `model_routing.ROUTING_POLICY_VERSION`
#: already use.
KPI_MONITORING_VERSION = "1.0.0"


class ComparisonPeriod(str, Enum):
    """What a period's value is judged against.

    Attributes:
        PREVIOUS_PERIOD: The immediately preceding period in the series.
            The default, and the only one that needs no extra history.
        SAME_PERIOD_LAST_YEAR: The point `seasonal_lag` positions back --
            the right comparison for a business with real seasonality,
            where "down against last month" is noise and "down against
            the same month last year" is not.
        TRAILING_AVERAGE: The mean of the previous `trailing_window`
            periods -- steadier than a single prior point, so a one-off
            blip in the comparison period does not manufacture a finding
            in the current one.
    """

    PREVIOUS_PERIOD = "previous_period"
    SAME_PERIOD_LAST_YEAR = "same_period_last_year"
    TRAILING_AVERAGE = "trailing_average"


class ChangeKind(str, Enum):
    """What kind of movement was detected.

    The values double as `semantic.intelligence.detect.Finding.kind`
    strings (prefixed `kpi_`), which is why each is kept well under the
    32-character limit of `identity.models.SemanticFinding.kind` -- see
    `kpi_monitoring.findings` for that constraint's own note.
    """

    DROP = "kpi_drop"
    SPIKE = "kpi_spike"
    THRESHOLD_BREACH = "kpi_threshold_breach"
    TREND_BREAK = "kpi_trend_break"


class SuppressionReason(str, Enum):
    """Why an otherwise-real finding was not raised as an alert.

    A suppressed finding is still detected, still persisted and still
    visible in the review queue -- suppression governs *alerting*, never
    evidence retention. Dropping the finding instead would destroy the
    record of a real movement, which is exactly what an operator looks
    for after the fact.
    """

    QUIET_PERIOD = "quiet_period"
    NON_BUSINESS_PERIOD = "non_business_period"
    BELOW_USER_SEVERITY = "below_user_severity"
    METRIC_MUTED_BY_USER = "metric_muted_by_user"
    DUPLICATE_OF_OPEN_FINDING = "duplicate_of_open_finding"


class BusinessCalendar(BaseModel):
    """Which periods count as business periods for one tenant.

    Deliberately a plain, caller-supplied set of period labels rather
    than a date-arithmetic engine: this package never parses a date. A
    period label here is whatever the caller's own series uses
    (`"2026-01"`, `"2026-W03"`, `"2026-01-05"`), compared as an opaque
    string, which is the same "the label's own semantics are the
    caller's" boundary `analytics.engine.detect_missing_periods` already
    draws for itself.

    A non-business period is excluded from both the baseline and the
    comparison, so a holiday shutdown does not manufacture a "revenue
    collapsed" finding every year -- the single most common source of
    false KPI alerts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(default="default", min_length=1, max_length=120)
    non_business_periods: frozenset[str] = Field(
        default_factory=frozenset,
        description=(
            "Period labels that are not business periods (holidays, "
            "planned shutdowns, a partial period still being collected). "
            "Excluded from baselines and never alerted on."
        ),
    )

    def is_business_period(self, period: str) -> bool:
        return period not in self.non_business_periods


class QuietPeriod(BaseModel):
    """A window during which findings are recorded but not alerted.

    Expressed in the same opaque period labels as everything else here
    (an explicit list, not a time range), so this package stays free of
    date parsing and a caller keeps full control of what its own labels
    mean. `reason` is operator-supplied text shown back on the suppressed
    finding, so "why didn't I get paged" is answerable without reading
    configuration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    periods: frozenset[str] = Field(default_factory=frozenset)
    reason: str = Field(default="", max_length=500)

    def covers(self, period: str) -> bool:
        return period in self.periods


class AlertPreference(BaseModel):
    """One recipient's own alerting preferences.

    Applied at *alert* time, never at detection time: the finding exists
    for the tenant regardless, and two users with different preferences
    see different alert bundles over the identical finding set. That
    split is what keeps one user's "mute this" from destroying another
    user's evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=64)
    min_risk_tier: Literal["low", "medium", "high"] = "low"
    muted_metric_keys: frozenset[str] = Field(default_factory=frozenset)
    max_alerts_per_run: int = Field(
        default=20,
        gt=0,
        description=(
            "A hard per-recipient ceiling on one run's alerts, applied "
            "after ranking -- the last line of defence against a noisy "
            "run, and the reason a misconfigured threshold produces a "
            "short, ranked digest rather than a hundred pages."
        ),
    )


class MonitoringPolicy(BaseModel):
    """What to watch on one approved metric, and when it matters.

    `metric_concept_key` must name a **published** `METRIC` entry in the
    tenant's own semantic catalog -- see
    `kpi_monitoring.policy.validate_policy`. Monitoring an arbitrary
    expression is deliberately impossible: a KPI alert asserts that a
    governed business number moved, so the number has to be a governed
    business number first.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1, max_length=120)
    metric_concept_key: str = Field(min_length=1, max_length=200)
    comparison: ComparisonPeriod = ComparisonPeriod.PREVIOUS_PERIOD
    #: Minimum absolute percentage move before a change is even a
    #: candidate. The primary noise control, and the one an operator
    #: tunes first.
    min_change_percent: float = Field(default=10.0, ge=0.0)
    #: Optional hard business thresholds. Crossing one is reported as
    #: `THRESHOLD_BREACH` -- scored above an ordinary move, because an
    #: operator explicitly said this level matters.
    lower_threshold: float | None = None
    upper_threshold: float | None = None
    #: How many positions back `SAME_PERIOD_LAST_YEAR` looks.
    seasonal_lag: int = Field(default=12, gt=0)
    #: How many prior periods `TRAILING_AVERAGE` averages over.
    trailing_window: int = Field(default=4, gt=1)
    #: Which dimension column(s) to attribute a change to. Empty means
    #: no attribution is attempted and every finding honestly reports
    #: that no breakdown was configured.
    dimension_columns: tuple[str, ...] = ()
    enabled: bool = True

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> MonitoringPolicy:
        """A lower threshold at or above the upper one can never be
        satisfied by any value, so the policy would silently never fire
        -- a configuration error worth catching at construction rather
        than discovering as an absence of alerts."""
        if (
            self.lower_threshold is not None
            and self.upper_threshold is not None
            and self.lower_threshold >= self.upper_threshold
        ):
            raise ValueError(
                f"lower_threshold ({self.lower_threshold}) must be strictly below "
                f"upper_threshold ({self.upper_threshold}); otherwise no value could "
                f"ever satisfy both."
            )
        return self


class PolicyViolation(BaseModel):
    """Why a policy was refused. Never echoes a metric's own expression
    or any row value -- only the policy id, the concept key and a fixed
    reason code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str
    code: Literal[
        "metric_not_found",
        "metric_not_published",
        "metric_not_a_metric",
        "restricted_dimension",
        "too_many_policies",
    ]
    detail: str


class DetectedChange(BaseModel):
    """One meaningful movement on one metric in one period.

    The numeric fields are observed arithmetic over caller-supplied rows
    (`DATABASE_FACT`); the existence of this object -- the judgment that
    the movement is worth a human's attention -- is `AI_INFERENCE`, which
    is why `truth_level` below is a fixed `Literal` pydantic itself
    refuses to promote.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str
    metric_concept_key: str
    metric_display_name: str = ""
    kind: ChangeKind
    period: str
    current_value: float
    baseline_value: float | None
    baseline_label: str = Field(
        description=(
            "Human-readable description of what the baseline *is* "
            "(e.g. 'previous period 2026-02', 'mean of the 4 periods "
            "before 2026-03') -- so a reader never has to guess what the "
            "comparison was."
        )
    )
    comparison: ComparisonPeriod
    change_absolute: float | None = None
    change_percent: float | None = None
    threshold_crossed: float | None = None
    detection_methods: tuple[str, ...] = Field(
        default=(),
        description=(
            "Which `analytics.anomaly` methods flagged this point, if "
            "any. Empty for a change found purely by this package's own "
            "comparison-period rule -- the two are complementary, and "
            "which one fired is recorded rather than blurred."
        ),
    )
    truth_level: Literal[DataTruthLevel.AI_INFERENCE] = DataTruthLevel.AI_INFERENCE

    @property
    def direction(self) -> Literal["up", "down", "flat"]:
        if self.change_absolute is None or self.change_absolute == 0:
            return "flat"
        return "up" if self.change_absolute > 0 else "down"


class DimensionContribution(BaseModel):
    """One dimension value's share of a change -- a contribution to an
    arithmetic total, never a demonstrated cause."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    current_value: float
    baseline_value: float
    contribution: float
    contribution_percent: float


class ChangeEvidence(BaseModel):
    """What was found when a detected change was investigated.

    Mirrors `analytics.models.RootCauseResult`'s own
    evidence-or-nothing contract exactly: `contributors` and `confidence`
    are populated **only** when `has_sufficient_evidence` is `True`.
    When it is `False`, `limitations` always explains why, and the
    finding that carries this evidence says plainly that no attribution
    could be made -- it is never silently presented as a bare change with
    an implied cause.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    has_sufficient_evidence: bool
    baseline_total: float | None = None
    current_total: float | None = None
    magnitude: float | None = None
    magnitude_percent: float | None = None
    contributors: tuple[DimensionContribution, ...] = ()
    confidence: float | None = None
    limitations: tuple[str, ...] = ()
    dimension_columns: tuple[str, ...] = ()
    truth_level: Literal[DataTruthLevel.AI_INFERENCE] = DataTruthLevel.AI_INFERENCE

    @model_validator(mode="after")
    def _no_attribution_without_evidence(self) -> ChangeEvidence:
        """The acceptance criterion as a type-level guarantee, not a
        convention a later edit could quietly drop: no contributor and no
        confidence may exist unless the evidence check actually passed."""
        if not self.has_sufficient_evidence and (self.contributors or self.confidence is not None):
            raise ValueError(
                "contributors/confidence may not be set when has_sufficient_evidence is False"
            )
        if not self.has_sufficient_evidence and not self.limitations:
            raise ValueError("insufficient evidence must always carry at least one limitation")
        return self


class AlertDecision(BaseModel):
    """Whether one finding reaches one recipient, and why not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_key: str
    metric_concept_key: str
    period: str
    risk_score: int
    risk_tier: str
    alerted: bool
    suppressed_by: tuple[SuppressionReason, ...] = ()
    suppression_detail: str = ""


class AlertBundle(BaseModel):
    """What one recipient would be told about one run.

    **This platform has no delivery channel.** Nothing in this codebase
    sends an email, a webhook or a chat message, and this prompt did not
    add one -- a bundle is the typed, inspectable description of what
    *would* be sent, returned over the API and rendered by the CLI
    report. Building a real transport would mean handling provider
    credentials, which is a separate decision with its own secret
    handling; claiming delivery that does not happen would be worse than
    saying so.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    decisions: tuple[AlertDecision, ...] = ()

    @property
    def alerted_count(self) -> int:
        return sum(1 for decision in self.decisions if decision.alerted)

    @property
    def suppressed_count(self) -> int:
        return sum(1 for decision in self.decisions if not decision.alerted)


class MonitorResult(BaseModel):
    """One full monitoring run's outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_id: str
    policies_evaluated: int = 0
    series_skipped: tuple[str, ...] = Field(
        default=(),
        description=(
            "Per-policy reasons a series could not be evaluated at all "
            "(no data, too few points for the configured comparison, "
            "every period excluded by the business calendar). Reported, "
            "never silently dropped -- an operator must be able to tell "
            "'nothing moved' from 'nothing was checked'."
        ),
    )
    changes: tuple[DetectedChange, ...] = ()
    evidence_by_key: dict[str, ChangeEvidence] = Field(default_factory=dict)
    violations: tuple[PolicyViolation, ...] = ()
    truncated: bool = False
    engine_version: str = KPI_MONITORING_VERSION


def finding_payload(
    change: DetectedChange, evidence: ChangeEvidence, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The JSON payload carried on a persisted finding.

    Kept here, beside the models, so the stored shape and the typed shape
    cannot drift. Contains no row values beyond the aggregates already
    present on `change`/`evidence`, and never the metric's own
    `approved_expression` (a governed definition is not alert content).
    """
    payload: dict[str, Any] = {
        "metric_concept_key": change.metric_concept_key,
        "policy_id": change.policy_id,
        "period": change.period,
        "comparison": change.comparison.value,
        "baseline_label": change.baseline_label,
        "current_value": change.current_value,
        "baseline_value": change.baseline_value,
        "change_absolute": change.change_absolute,
        "change_percent": change.change_percent,
        "threshold_crossed": change.threshold_crossed,
        "detection_methods": list(change.detection_methods),
        "direction": change.direction,
        "has_sufficient_evidence": evidence.has_sufficient_evidence,
        "limitations": list(evidence.limitations),
        "dimension_columns": list(evidence.dimension_columns),
        "engine_version": KPI_MONITORING_VERSION,
    }
    if evidence.has_sufficient_evidence:
        payload["confidence"] = evidence.confidence
        payload["contributors"] = [
            {
                "label": contributor.label,
                "contribution": contributor.contribution,
                "contribution_percent": contributor.contribution_percent,
            }
            for contributor in evidence.contributors
        ]
    if extra:
        payload.update(extra)
    return payload


__all__ = [
    "KPI_MONITORING_VERSION",
    "AlertBundle",
    "AlertDecision",
    "AlertPreference",
    "BusinessCalendar",
    "ChangeEvidence",
    "ChangeKind",
    "ComparisonPeriod",
    "DetectedChange",
    "DimensionContribution",
    "MonitorResult",
    "MonitoringPolicy",
    "PolicyViolation",
    "QuietPeriod",
    "SuppressionReason",
    "finding_payload",
]
