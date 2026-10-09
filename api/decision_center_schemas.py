"""Response models for `api/decision_center.py` (Prompt 52).

Read-only and frozen. Every field a headline needs in order to be
drilled into is present on the headline itself -- evidence, period,
source, confidence, limitations, plus flags saying whether the metric
definition and a lineage trace are resolvable. A client never needs a
second round trip to know whether a drill-down exists.

No field here can carry a credential, a connection string, SQL, or a
metric's own `approved_expression`: the only free-form strings are
titles, details and evidence values that the producing capability
already wrote into a governed record, and `MetricDefinitionOut`
deliberately omits the expression (see its own docstring).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItemOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    value: str
    truth_level: str = Field(
        description=(
            "Carried from the producing record, never assigned here. An "
            "observed count stays database_fact; a model's attribution "
            "stays ai_inference."
        )
    )


class ProvenanceOut(BaseModel):
    """Where a headline came from and how far it can be trusted.

    Fields the producing capability never recorded read `"not recorded"`
    rather than being omitted, so a reader can tell "nobody wrote this
    down" from "there is nothing to see".
    """

    model_config = ConfigDict(frozen=True)

    source: str
    source_record_id: str
    period: str
    metric_concept_key: str | None = None
    confidence: float | None = None
    database_id: str | None = None
    recorded_at: str
    engine_version: str
    limitations: tuple[str, ...] = ()
    lineage_available: bool
    metric_definition_available: bool


class HeadlineOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    headline_id: str
    kind: str
    title: str
    detail: str
    severity: int
    severity_tier: str
    status: str
    occurred_at: str
    truth_level: str
    provenance: ProvenanceOut
    evidence: tuple[EvidenceItemOut, ...] = ()


class HealthIndicatorOut(BaseModel):
    """Always `database_fact` -- a count or a ratio of counts.

    `direction` says which way is better and nothing more. This platform
    deliberately does not render a single "healthy/amber/red" verdict: a
    threshold turning a count into a colour is a business judgment
    nobody has given it.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    value: float
    unit: str
    direction: str
    detail: str
    truth_level: str


class SectionOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    section: str
    title: str
    headlines: tuple[HeadlineOut, ...] = ()
    total_available: int = Field(
        description="Count before the per-role display cap, so truncation is visible."
    )
    empty_reason: str = ""


class ChainLinkOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: str
    present: bool
    headline_id: str | None = None
    summary: str = ""
    link_type: str | None = Field(
        default=None,
        description=(
            "`declared` for a real foreign key; `correlated` for two "
            "records that merely share a subject -- which is never "
            "evidence that one followed from the other. `null` where no "
            "cross-record link is involved."
        ),
    )
    absent_reason: str = ""


class ChainOut(BaseModel):
    """signal -> evidence -> explanation -> recommendation -> action -> outcome."""

    model_config = ConfigDict(frozen=True)

    chain_id: str
    is_complete: bool
    furthest_stage: str | None = None
    break_reason: str = ""
    links: tuple[ChainLinkOut, ...] = ()
    truth_level: str


class MetricDefinitionOut(BaseModel):
    """A governed metric's definition, for the metric-definition
    drill-down.

    **Deliberately omits `approved_expression`.** A metric's authoritative
    SQL is a governed definition, not briefing content; `/semantic-catalog`
    is where it is read, by someone with the catalog permission. Omitting
    the field here makes that boundary structural.
    """

    model_config = ConfigDict(frozen=True)

    concept_key: str
    business_name: str
    description: str
    status: str
    version: int
    owner: str | None = None
    dimensions: tuple[str, ...] = ()
    confidence: float | None = None


class BriefingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    database_id: str | None = None
    profile_id: str = Field(
        description=(
            "Resolved from the viewer's own server-side permissions. "
            "Personalization reorders sections and caps how many "
            "headlines each shows; it never changes a value, a truth "
            "level or which sections exist."
        )
    )
    generated_at: str
    health: tuple[HealthIndicatorOut, ...] = ()
    sections: tuple[SectionOut, ...] = ()
    chains: tuple[ChainOut, ...] = ()
    metric_definitions: dict[str, MetricDefinitionOut] = Field(
        default_factory=dict,
        description=(
            "concept_key -> definition, for every governed metric a "
            "headline names. Resolved once per briefing so a headline's "
            "metric-definition drill-down needs no second request."
        ),
    )
    contributing_sources: tuple[str, ...] = ()
    unavailable_sources: tuple[str, ...] = Field(
        default=(),
        description=(
            "Capabilities that did not contribute, with the reason -- "
            "including the compute-on-demand engines that persist no "
            "records of their own. Reported so a reader can tell "
            "'nothing to report' from 'this was never read'."
        ),
    )
    engine_version: str


__all__ = [
    "BriefingResponse",
    "ChainLinkOut",
    "ChainOut",
    "EvidenceItemOut",
    "HeadlineOut",
    "HealthIndicatorOut",
    "MetricDefinitionOut",
    "ProvenanceOut",
    "SectionOut",
]
