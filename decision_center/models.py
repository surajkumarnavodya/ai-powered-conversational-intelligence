"""Typed contracts for the Executive Decision Center (Prompt 52,
`52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`).

Pure vocabulary: enums and frozen value objects. No I/O, no SQLAlchemy,
no LLM call -- the posture every engine package in this series
(`analytics/`, `optimization/`, `kpi_monitoring/`, `model_routing/`)
already establishes.

**This package aggregates; it never computes a business fact.** Every
number it shows was produced and recorded by an earlier capability
(Prompts 33-50) and is carried through unchanged, with the truth level
that capability assigned to it. The Decision Center's own contribution
is ordering, grouping and linking -- which is itself an `AI_INFERENCE`
judgment about what deserves attention, and is labelled as one.

**Three honesty rules are encoded in the types here**, not left to
whoever renders them:

1. `Provenance` is mandatory on every headline. A headline with no
   recorded period, source or confidence says so explicitly
   (`"not recorded"`), rather than omitting the field and letting a
   reader assume.
2. `LinkType` distinguishes a **declared** link (a real foreign key,
   e.g. `workflows.recommendation_id`) from a **correlated** one (two
   records that merely share a subject). They are never merged into one
   "related" list -- the same discipline
   `db/relationship_inference.py` already applies to a candidate
   foreign key versus a declared one.
3. A `Headline` carries the truth level of the record it came from and
   cannot promote it. Personalization reorders and hides; it never
   rewrites a value (see `decision_center.personalization`).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.provenance import DataTruthLevel

#: Bumped when the aggregation/ranking rules change in a way that could
#: alter what a briefing shows for unchanged underlying records.
DECISION_CENTER_VERSION = "1.0.0"

#: Shown wherever a provenance field was never recorded by the producing
#: capability. A fixed sentinel rather than an empty string or `None`, so
#: "we do not know" is visible in the rendered output instead of looking
#: like an omission.
NOT_RECORDED = "not recorded"


class SectionId(str, Enum):
    """The briefing's sections -- the mission's own named list.

    `BUSINESS_HEALTH` is a rollup of the others and is always present;
    every other section is present but may be empty, because an empty
    section ("no open risks") is information and hiding it would make a
    quiet tenant indistinguishable from an unchecked one.
    """

    BUSINESS_HEALTH = "business_health"
    KPI_MOVEMENT = "kpi_movement"
    ANOMALIES = "anomalies"
    RISKS = "risks"
    RECOMMENDATIONS = "recommendations"
    PENDING_DECISIONS = "pending_decisions"
    ACTIONS = "actions"
    OUTCOMES = "outcomes"


class HeadlineKind(str, Enum):
    """What a headline is, independent of which section it appears in."""

    KPI_MOVEMENT = "kpi_movement"
    ANOMALY = "anomaly"
    RISK = "risk"
    RECOMMENDATION = "recommendation"
    PENDING_DECISION = "pending_decision"
    ACTION = "action"
    OUTCOME = "outcome"


class SourceCapability(str, Enum):
    """Which earlier capability produced a headline.

    Recorded on every headline so a reader can always answer "where did
    this come from", and so the briefing can state honestly which
    capabilities contributed to it and which had nothing to contribute.
    """

    KPI_MONITORING = "kpi_monitoring"
    SCHEMA_DRIFT = "schema_drift"
    SEMANTIC_INTELLIGENCE = "semantic_intelligence"
    RECOMMENDATION_ENGINE = "recommendation_engine"
    GOVERNANCE_CENTER = "governance_center"
    WORKFLOW_AUTOMATION = "workflow_automation"
    ONBOARDING = "onboarding"


class LinkType(str, Enum):
    """How two records in a chain are related.

    Attributes:
        DECLARED: A real foreign key recorded by the producing
            capability -- `workflows.recommendation_id`. Trustworthy as
            an assertion that these two records are about the same
            decision.
        CORRELATED: The two records merely share a subject (the same
            database, metric or entity). **Not** evidence that one
            followed from the other, and never rendered as though it
            were. The same distinction `db/relationship_inference.py`
            draws between a declared and an inferred foreign key.
    """

    DECLARED = "declared"
    CORRELATED = "correlated"


class EvidenceItem(BaseModel):
    """One piece of supporting detail behind a headline.

    `truth_level` is carried from the producing record, never assigned
    here -- an observed count stays `DATABASE_FACT`, a model's
    attribution stays `AI_INFERENCE`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=2000)
    truth_level: DataTruthLevel


class Provenance(BaseModel):
    """Where a headline came from and how far it can be trusted.

    The mission requires every headline to drill into evidence, metric
    definition, period, source, confidence and lineage. This model is
    that requirement as a type: a `Headline` cannot be constructed
    without one, so a headline with no provenance is unrepresentable
    rather than merely discouraged.

    Fields that the producing capability never recorded are
    `NOT_RECORDED`, which is shown to the reader. The alternative --
    leaving them blank -- reads as "nothing to see" rather than "nobody
    wrote this down".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceCapability
    source_record_id: str = Field(min_length=1)
    period: str = Field(default=NOT_RECORDED)
    metric_concept_key: str | None = Field(
        default=None,
        description=(
            "The governed metric this headline is about, when it is about "
            "one. Non-null is what makes the metric-definition drill-down "
            "resolvable against the semantic catalog."
        ),
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="As recorded by the producing capability; None when it recorded none.",
    )
    database_id: str | None = None
    recorded_at: str = Field(default=NOT_RECORDED)
    engine_version: str = Field(default=NOT_RECORDED)
    limitations: tuple[str, ...] = ()
    lineage_available: bool = Field(
        default=False,
        description=(
            "Whether a lineage trace can be resolved for this headline's "
            "subject (it names a metric or a database object). False means "
            "the drill-down is genuinely unavailable, not merely unlinked."
        ),
    )
    metric_definition_available: bool = Field(
        default=False,
        description=(
            "Whether the governed metric definition is resolvable in this "
            "tenant's catalog. False when the headline is not about a "
            "metric, or the metric is no longer published."
        ),
    )


class ChainStage(str, Enum):
    """The acceptance criterion's own named chain."""

    SIGNAL = "signal"
    EVIDENCE = "evidence"
    EXPLANATION = "explanation"
    RECOMMENDATION = "recommendation"
    ACTION = "action"
    OUTCOME = "outcome"


class ChainLink(BaseModel):
    """One stage of a decision chain, and how it was reached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: ChainStage
    present: bool
    headline_id: str | None = None
    summary: str = ""
    link_type: LinkType | None = Field(
        default=None,
        description=(
            "How this stage was linked to the previous one. `None` for the "
            "first stage, and for a stage derived from within the same "
            "record (evidence and explanation both live inside their own "
            "signal, so no cross-record link is involved)."
        ),
    )
    absent_reason: str = ""

    @model_validator(mode="after")
    def _absent_needs_a_reason(self) -> ChainLink:
        """A missing stage must say why. A chain that simply stops is
        indistinguishable from one that was never looked at, which is
        precisely the ambiguity this whole feature exists to remove."""
        if not self.present and not self.absent_reason:
            raise ValueError(f"stage {self.stage.value!r} is absent and must state why")
        if self.present and self.headline_id is None and self.stage is not ChainStage.EVIDENCE:
            raise ValueError(f"stage {self.stage.value!r} is present and needs a headline id")
        return self


class DecisionChain(BaseModel):
    """signal -> evidence -> explanation -> recommendation -> action -> outcome.

    The acceptance criterion made explicit. A chain is complete only when
    every stage is present; `break_reason` names the first stage that is
    not, so an incomplete chain is self-explaining rather than looking
    like a rendering bug.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chain_id: str = Field(min_length=1)
    links: tuple[ChainLink, ...]
    truth_level: Literal[DataTruthLevel.AI_INFERENCE] = DataTruthLevel.AI_INFERENCE

    @property
    def is_complete(self) -> bool:
        return all(link.present for link in self.links)

    @property
    def break_reason(self) -> str:
        for link in self.links:
            if not link.present:
                return f"{link.stage.value}: {link.absent_reason}"
        return ""

    @property
    def furthest_stage(self) -> ChainStage | None:
        reached = [link.stage for link in self.links if link.present]
        return reached[-1] if reached else None


class Headline(BaseModel):
    """One item an executive sees, with everything needed to drill in.

    `truth_level` is the producing record's own. A headline built from a
    `DATABASE_FACT` count stays a fact; one built from a model's
    attribution stays an inference. This class has no code path that
    changes it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    headline_id: str = Field(min_length=1)
    kind: HeadlineKind
    title: str = Field(min_length=1, max_length=400)
    detail: str = Field(default="", max_length=4000)
    severity: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "As recorded by the producing capability (a finding's own "
            "risk score, a workflow's urgency). Never recomputed here -- "
            "re-scoring would mean the Decision Center disagreeing with "
            "the queue an operator actually works from."
        ),
    )
    severity_tier: Literal["low", "medium", "high"] = "low"
    status: str = Field(default="", max_length=64)
    provenance: Provenance
    evidence: tuple[EvidenceItem, ...] = ()
    truth_level: DataTruthLevel
    occurred_at: str = Field(default=NOT_RECORDED)

    @model_validator(mode="after")
    def _inference_cannot_claim_certainty(self) -> Headline:
        """An `AI_INFERENCE` headline may not report a confidence of
        exactly 1.0. Nothing this platform infers is certain, and a
        rendered "100% confident" inference is the single most
        misleading thing this surface could show."""
        if (
            self.truth_level is DataTruthLevel.AI_INFERENCE
            and self.provenance.confidence is not None
            and self.provenance.confidence >= 1.0
        ):
            raise ValueError(
                "an AI_INFERENCE headline may not report a confidence of 1.0; "
                "nothing inferred is certain"
            )
        return self


class HealthIndicator(BaseModel):
    """One business-health number.

    Always a `DATABASE_FACT`: every indicator is a count or a ratio of
    counts over governed records this tenant actually holds. The
    *judgment* about whether that number is good is deliberately not
    made here -- `direction` says which way is better and nothing more,
    because "healthy" is a business threshold this platform has not been
    told.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    value: float
    unit: Literal["count", "percent"] = "count"
    direction: Literal["higher_is_better", "lower_is_better", "neutral"] = "neutral"
    detail: str = Field(default="", max_length=500)
    truth_level: Literal[DataTruthLevel.DATABASE_FACT] = DataTruthLevel.DATABASE_FACT


class BriefingSection(BaseModel):
    """One section of the briefing, with its headlines in rank order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: SectionId
    title: str
    headlines: tuple[Headline, ...] = ()
    total_available: int = Field(
        default=0,
        ge=0,
        description=(
            "How many headlines exist for this section before the display "
            "cap -- so a truncated section says so instead of quietly "
            "under-reporting."
        ),
    )
    empty_reason: str = Field(
        default="",
        max_length=400,
        description=(
            "Why an empty section is empty: nothing found, or the source "
            "was unavailable. Those are different, and conflating them "
            "would let an outage read as good news."
        ),
    )


class PersonalizationProfile(BaseModel):
    """How one role sees the briefing.

    **Presentation only.** A profile may reorder sections and set how
    many headlines each shows. It may not filter by content, change a
    number, change a truth level, or hide a section outright -- every
    section is always present, so no role is shown a briefing that
    silently omits an open risk. Enforced in
    `decision_center.personalization` and asserted in
    `tests/security/test_decision_center_personalization_never_changes_facts.py`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(min_length=1, max_length=64)
    section_order: tuple[SectionId, ...] = ()
    headlines_per_section: int = Field(default=5, gt=0, le=50)


class ExecutiveBriefing(BaseModel):
    """One assembled briefing for one tenant and one viewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    database_id: str | None = None
    profile_id: str
    generated_at: str
    health: tuple[HealthIndicator, ...] = ()
    sections: tuple[BriefingSection, ...] = ()
    chains: tuple[DecisionChain, ...] = ()
    contributing_sources: tuple[SourceCapability, ...] = ()
    unavailable_sources: tuple[str, ...] = Field(
        default=(),
        description=(
            "Capabilities that could not contribute to this briefing, with "
            "the reason. Reported rather than omitted: a reader must be "
            "able to tell 'nothing to report' from 'this was not read'."
        ),
    )
    engine_version: str = DECISION_CENTER_VERSION


def evidence_from_payload(payload: Any, limit: int = 8) -> tuple[EvidenceItem, ...]:
    """Flattens a producing capability's own recorded evidence payload.

    Each item keeps the truth level the payload declared for it. A
    payload that declares none is treated as `AI_INFERENCE` -- the
    conservative direction, since promoting an unlabelled value to a
    fact is the error that matters.
    """
    if not isinstance(payload, list):
        return ()
    items: list[EvidenceItem] = []
    for raw in payload[:limit]:
        if not isinstance(raw, dict):
            continue
        declared = str(raw.get("truth_level", "") or "").lower()
        level = (
            DataTruthLevel.DATABASE_FACT
            if declared == "database_fact"
            else (
                DataTruthLevel.CONFIRMED_BUSINESS_TRUTH
                if declared == "confirmed_business_truth"
                else DataTruthLevel.AI_INFERENCE
            )
        )
        for key, value in raw.items():
            if key == "truth_level":
                continue
            items.append(
                EvidenceItem(label=str(key)[:200], value=str(value)[:2000], truth_level=level)
            )
    return tuple(items[:limit])


__all__ = [
    "DECISION_CENTER_VERSION",
    "NOT_RECORDED",
    "BriefingSection",
    "ChainLink",
    "ChainStage",
    "DecisionChain",
    "EvidenceItem",
    "ExecutiveBriefing",
    "HeadlineKind",
    "Headline",
    "HealthIndicator",
    "LinkType",
    "PersonalizationProfile",
    "Provenance",
    "SectionId",
    "SourceCapability",
    "evidence_from_payload",
]
