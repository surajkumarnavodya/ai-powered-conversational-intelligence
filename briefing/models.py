"""Typed contracts for the executive AI briefing (Prompt 51,
`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`).

Pure vocabulary: enums and frozen value objects. No I/O, no SQLAlchemy,
no LLM call -- the same posture `kpi_monitoring/models.py`,
`data_quality/models.py` and `analytics/models.py` already establish,
and what makes `briefing.claims`/`briefing.freshness`/`briefing.engine`
testable as pure functions.

**Two orthogonal classifications, deliberately not merged.**

- `agent.provenance.DataTruthLevel` answers *how this came to be known*
  (observed from rows, inferred, or confirmed by a human). It is this
  codebase's existing, shared vocabulary and is reused unchanged.
- `ClaimClass` answers *what kind of statement a reader is looking at*
  (an observed fact, an estimate, or a recommended action) -- the
  mission's own three-way split. The same orthogonality
  `analytics.models.ScenarioValueKind` already established against the
  same truth-level enum.

They are related but not interchangeable, so `BriefingItem` carries both
and a model validator refuses the combinations that would misrepresent
one as the other.

**Every claim links to evidence, enforced by the type.**
`BriefingItem.evidence` has `min_length=1`: a briefing item with no
`EvidenceLink` is unrepresentable, not merely discouraged. Each link
carries the deterministic numbers the claim rests on plus the engine/
content versions that produced them, so "which version said this" is
answerable from the briefing alone.

**Nothing here asserts a cause.** A driver is a contribution to an
arithmetic total -- `kpi_monitoring.models`' own framing, carried
through verbatim.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.provenance import DataTruthLevel

#: Bumped when the briefing's own assembly rules change in a way that
#: could alter which claims appear or how they rank. Recorded on every
#: result, so a rendered briefing is self-describing about which rule
#: set produced it -- the same reasoning `analytics.engine
#: .ANALYTICS_ENGINE_VERSION`, `kpi_monitoring.models
#: .KPI_MONITORING_VERSION` and `data_quality.models
#: .DATA_QUALITY_ENGINE_VERSION` already use.
BRIEFING_ENGINE_VERSION = "1.0.0"


class BriefingPeriod(str, Enum):
    """Which cadence a briefing covers.

    The cadence is *not* a date filter this package applies -- period
    labels are opaque strings here, exactly as in
    `kpi_monitoring.models.BusinessCalendar`. It selects the staleness
    budget (a daily briefing built from week-old evidence is stale; a
    monthly one is not) and the heading the report renders.
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class BriefingSection(str, Enum):
    """The mission's own named sections, as a closed set.

    Attributes:
        KPI_MOVEMENT: Governed metrics that moved meaningfully.
        ANOMALIES: Movements a statistical method also flagged, or that
            crossed an operator-declared threshold -- a strictly
            narrower, better-evidenced subset of the above, kept
            separate because an executive reads "unusual" differently
            from "changed".
        DRIVERS: Dimension values that contributed most to a movement.
            Only ever present when the underlying attribution actually
            found supporting evidence.
        RISKS: Open items that threaten a governed number -- a fall, a
            breached floor, schema drift under a published metric, or a
            critical data-quality failure.
        OPPORTUNITIES: Favourable movement, and recommended actions in a
            growth category.
        UNRESOLVED_RECOMMENDATIONS: Recommendation records still awaiting
            a decision, or still open after acceptance.
        DATA_QUALITY: Observed data-quality failures.
    """

    KPI_MOVEMENT = "kpi_movement"
    ANOMALIES = "anomalies"
    DRIVERS = "drivers"
    RISKS = "risks"
    OPPORTUNITIES = "opportunities"
    UNRESOLVED_RECOMMENDATIONS = "unresolved_recommendations"
    DATA_QUALITY = "data_quality"


class ClaimClass(str, Enum):
    """What kind of statement one briefing item makes.

    Attributes:
        OBSERVED_FACT: Arithmetic over rows that were actually fetched
            through this application's governed path -- a value, a
            count, a null rate. Always `DataTruthLevel.DATABASE_FACT`.
        ESTIMATE: This platform's own inference *about* those observed
            numbers -- that a movement is meaningful, that a dimension
            contributed most, that something is a risk. Always
            `AI_INFERENCE`.
        RECOMMENDATION: A suggested action. Always `AI_INFERENCE`, and
            never presented as something already true.
    """

    OBSERVED_FACT = "observed_fact"
    ESTIMATE = "estimate"
    RECOMMENDATION = "recommendation"


#: The one legal truth level per claim class. `CONFIRMED_BUSINESS_TRUTH`
#: appears nowhere: a briefing reports and ranks what other governed
#: subsystems already recorded, and confirming anything is always a
#: separate, explicit human action elsewhere in this codebase (publishing
#: a catalog entry, accepting a finding, resolving a recommendation).
_REQUIRED_TRUTH_LEVEL: dict[ClaimClass, DataTruthLevel] = {
    ClaimClass.OBSERVED_FACT: DataTruthLevel.DATABASE_FACT,
    ClaimClass.ESTIMATE: DataTruthLevel.AI_INFERENCE,
    ClaimClass.RECOMMENDATION: DataTruthLevel.AI_INFERENCE,
}


class EvidenceLink(BaseModel):
    """One deterministic record a claim rests on.

    This is the mission's "every claim must link to deterministic
    evidence and versions" requirement made concrete, and the reason
    nothing in a briefing is a free-floating sentence: `reference_kind`
    plus `reference_id` name a row a reader can go and open, `observed`
    carries the numbers the claim is allowed to state, and `versions`
    records which engine/content version produced them.

    `observed` deliberately holds only aggregates another governed
    subsystem already computed (a metric's period value, a null rate, a
    contribution percent). It never carries a raw row, a sampled value,
    or a metric's own `approved_expression` -- a governed definition is
    not briefing content, the same boundary
    `kpi_monitoring.models.finding_payload` already draws.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Which subsystem produced this evidence, as a module/table "
            "name (e.g. 'kpi_monitoring.findings', 'data_quality.engine') "
            "-- for audit and for a reader to know where to look, never "
            "parsed programmatically."
        ),
    )
    reference_kind: Literal[
        "kpi_finding",
        "schema_drift_finding",
        "recommendation_record",
        "data_quality_finding",
    ]
    reference_id: str = Field(min_length=1, max_length=200)
    observed: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "The deterministic numbers this claim may state, keyed by a "
            "short name. A claim stating a number absent from here (and "
            "from its own subject/period labels) is an unsupported claim "
            "-- see `briefing.claims.find_unsupported_claims`."
        ),
    )
    versions: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Engine and content versions behind this evidence (e.g. "
            "`engine_version`, `evidence_version`, `finding_version`). At "
            "least one is required -- see `_versions_present`."
        ),
    )
    recorded_at: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "When the underlying subsystem last observed this, as an ISO "
            "8601 string. Compared against the briefing's own `as_of` by "
            "`briefing.freshness`; `None` means the source reported no "
            "timestamp, which is itself disclosed rather than assumed "
            "fresh."
        ),
    )
    truth_level: DataTruthLevel = DataTruthLevel.DATABASE_FACT

    @model_validator(mode="after")
    def _versions_present(self) -> EvidenceLink:
        """A link with no version at all could not answer "which version
        said this", which is half of what this model exists for."""
        if not self.versions:
            raise ValueError(
                "An EvidenceLink must record at least one version "
                "(engine_version, evidence_version or finding_version)."
            )
        return self


class BriefingItem(BaseModel):
    """One claim in a briefing, with its evidence and its class.

    Ordered by `risk_score` within a section, so an executive reads the
    biggest thing first -- reusing the score
    `semantic.intelligence.risk.score` already assigned to a finding
    rather than inventing a second ranking.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: BriefingSection
    claim_class: ClaimClass
    headline: str = Field(min_length=1, max_length=300)
    detail: str = Field(default="", max_length=4000)
    subject: str = Field(
        default="",
        max_length=300,
        description=(
            "What this item is about -- a metric concept key, a table "
            "name, a dimension label. Shown, and also treated as allowed "
            "text when checking whether the headline states an "
            "unsupported number (a label like 'EMEA 2026' is not a "
            "fabricated statistic)."
        ),
    )
    period_label: str = Field(default="", max_length=120)
    grounding_context: tuple[str, ...] = Field(
        default=(),
        description=(
            "Labels copied verbatim from the evidence (a baseline "
            "description, a dimension value, a table name). A number "
            "appearing in one of these is not a statistic this briefing "
            "invented, so the unsupported-claim check treats them as "
            "allowed text -- the identical allowance "
            "`agent.insight.is_insight_grounded` already makes for a "
            "number the user themselves typed into the question or SQL."
        ),
    )
    risk_score: int = Field(default=0, ge=0, le=100)
    risk_tier: Literal["low", "medium", "high"] = "low"
    evidence: tuple[EvidenceLink, ...] = Field(min_length=1)
    is_stale: bool = False
    stale_reason: str = Field(default="", max_length=500)
    limitations: tuple[str, ...] = ()
    truth_level: DataTruthLevel

    @model_validator(mode="after")
    def _class_matches_truth_level(self) -> BriefingItem:
        """The mission's "clearly distinguish facts, estimates and
        recommendations" requirement as a type-level guarantee rather
        than a convention a later edit could quietly drop: pydantic
        itself refuses an estimate labelled as an observed fact, and an
        observed fact labelled as an inference."""
        required = _REQUIRED_TRUTH_LEVEL[self.claim_class]
        if self.truth_level is not required:
            raise ValueError(
                f"A claim of class {self.claim_class.value!r} must be {required.value!r}, "
                f"never {self.truth_level.value!r}."
            )
        return self

    @model_validator(mode="after")
    def _stale_items_say_why(self) -> BriefingItem:
        """An item flagged stale with no reason leaves a reader unable to
        judge how stale -- the same "always carry a limitation" rule
        `kpi_monitoring.models.ChangeEvidence` applies to insufficient
        evidence."""
        if self.is_stale and not self.stale_reason:
            raise ValueError("A stale item must state why it is considered stale.")
        return self

    @property
    def allowed_numbers(self) -> set[float]:
        """Every number this item's own evidence permits it to state."""
        return {
            round(float(value), 4) for link in self.evidence for value in link.observed.values()
        }


class SourceCoverage(BaseModel):
    """Whether one input source was supplied at all, and what it yielded.

    The single most important field in an empty briefing: an executive
    must be able to tell "nothing happened" from "nothing was checked".
    The same distinction `kpi_monitoring.models.MonitorResult
    .series_skipped` exists to preserve, applied one level up.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Literal[
        "kpi_findings",
        "schema_drift_findings",
        "recommendations",
        "data_quality_findings",
    ]
    supplied: bool
    input_count: int = Field(default=0, ge=0)
    items_produced: int = Field(default=0, ge=0)
    note: str = Field(default="", max_length=500)


class StaleInput(BaseModel):
    """One piece of evidence older than this cadence's staleness budget."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    reference_id: str
    recorded_at: str | None
    age_seconds: float | None
    budget_seconds: float
    detail: str = Field(max_length=500)


class UnsupportedClaim(BaseModel):
    """One claim that failed the evidence check and was withheld.

    A withheld claim is reported, never silently dropped: an operator
    needs to know an item was suppressed and why, which is the whole
    point of detecting unsupported claims rather than trusting the
    builders.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: BriefingSection
    headline: str
    code: Literal[
        "no_evidence",
        "ungrounded_number",
        "missing_version",
        "truth_level_mismatch",
    ]
    detail: str = Field(max_length=500)


class ExecutiveBriefing(BaseModel):
    """One assembled briefing.

    `items` holds only claims that passed the evidence check;
    `withheld_claims` reports the ones that did not. `empty_sections`
    and `coverage` together make an uneventful briefing honest rather
    than merely short.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_id: str
    period: BriefingPeriod
    period_label: str = Field(default="", max_length=120)
    as_of: str = Field(description="The caller-supplied reference instant, ISO 8601.")
    items: tuple[BriefingItem, ...] = ()
    coverage: tuple[SourceCoverage, ...] = ()
    empty_sections: tuple[BriefingSection, ...] = ()
    stale_inputs: tuple[StaleInput, ...] = ()
    withheld_claims: tuple[UnsupportedClaim, ...] = ()
    limitations: tuple[str, ...] = ()
    truncated_sections: tuple[BriefingSection, ...] = ()
    engine_version: str = BRIEFING_ENGINE_VERSION

    def items_for(self, section: BriefingSection) -> tuple[BriefingItem, ...]:
        return tuple(item for item in self.items if item.section is section)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def observed_fact_count(self) -> int:
        return sum(1 for item in self.items if item.claim_class is ClaimClass.OBSERVED_FACT)

    @property
    def estimate_count(self) -> int:
        return sum(1 for item in self.items if item.claim_class is ClaimClass.ESTIMATE)

    @property
    def recommendation_count(self) -> int:
        return sum(1 for item in self.items if item.claim_class is ClaimClass.RECOMMENDATION)

    @property
    def stale_item_count(self) -> int:
        return sum(1 for item in self.items if item.is_stale)


__all__ = [
    "BRIEFING_ENGINE_VERSION",
    "BriefingItem",
    "BriefingPeriod",
    "BriefingSection",
    "ClaimClass",
    "EvidenceLink",
    "ExecutiveBriefing",
    "SourceCoverage",
    "StaleInput",
    "UnsupportedClaim",
]
