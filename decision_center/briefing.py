"""Assembles one briefing. The package's single entry point.

Pure: it takes already-fetched rows and returns an `ExecutiveBriefing`.
The API route performs the tenant-scoped identity reads and hands them
in, which is what lets the whole assembly be exercised end to end with
no database.

The order of operations matters and is fixed: adapt every row to a
normalised headline, rank within each section, build the health rollup
from the *full* (uncapped) sets, build chains from the highest-severity
signals, and only then apply personalization. Personalizing earlier
would let a role's display cap change a health count -- exactly the
"personalization changes governed facts" failure this prompt forbids.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from decision_center.chain import DEFAULT_MAX_CHAINS, build_chains
from decision_center.health import build_health
from decision_center.models import (
    DECISION_CENTER_VERSION,
    BriefingSection,
    ExecutiveBriefing,
    Headline,
    HeadlineKind,
    PersonalizationProfile,
    SectionId,
    SourceCapability,
)
from decision_center.personalization import DEFAULT_PROFILE_ID, PROFILES, personalize
from decision_center.sources import (
    finding_headline,
    governance_headline,
    onboarding_headline,
    recommendation_headline,
    workflow_headline,
)

#: Capabilities that persist nothing of their own, so they can only ever
#: reach this briefing through a record some other capability wrote.
#: Listed explicitly and reported on every briefing -- a reader must be
#: able to tell "forecasting had nothing to say" from "forecasting was
#: never consulted", and the honest answer here is the latter.
COMPUTE_ONLY_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("forecasting", "computes on demand and persists no record of its own"),
    ("scenario_engine", "computes on demand and persists no record of its own"),
    ("optimization_engine", "computes on demand and persists no record of its own"),
    ("predictive_analytics", "computes on demand and persists no record of its own"),
    ("data_quality", "computes on demand and persists no record of its own"),
    ("lineage", "computes on demand and persists no record of its own"),
    (
        "root_cause",
        "computes on demand; its findings reach this briefing only through a KPI "
        "finding's own recorded evidence",
    ),
)

_SECTION_TITLES: dict[SectionId, str] = {
    SectionId.BUSINESS_HEALTH: "Business health",
    SectionId.KPI_MOVEMENT: "KPI movement",
    SectionId.ANOMALIES: "Anomalies",
    SectionId.RISKS: "Risks",
    SectionId.RECOMMENDATIONS: "Recommendations",
    SectionId.PENDING_DECISIONS: "Pending decisions",
    SectionId.ACTIONS: "Actions",
    SectionId.OUTCOMES: "Outcomes",
}


@dataclass(frozen=True)
class BriefingInputs:
    """The already-fetched, already-tenant-scoped rows a briefing is
    built from.

    Every field defaults to empty, so a caller that cannot read one
    source (a disabled feature, a repository error) simply supplies
    nothing for it and records the reason in `unavailable`. That is what
    keeps one unavailable source from failing the whole briefing.
    """

    findings: Sequence[Any] = ()
    recommendations: Sequence[Any] = ()
    workflows: Sequence[Any] = ()
    governance_versions: Sequence[Any] = ()
    onboarding_jobs: Sequence[Any] = ()
    unavailable: tuple[str, ...] = field(default_factory=tuple)


def _section(
    section_id: SectionId,
    headlines: Sequence[Headline],
    empty_reason: str = "",
) -> BriefingSection:
    """Builds one section, ranked highest-severity first with a stable
    tie-break -- the same ordering the review queues use, so the briefing
    and the queue agree about what matters most."""
    ranked = tuple(sorted(headlines, key=lambda item: (-item.severity, item.headline_id)))
    return BriefingSection(
        section=section_id,
        title=_SECTION_TITLES[section_id],
        headlines=ranked,
        total_available=len(ranked),
        empty_reason=empty_reason if not ranked else "",
    )


def _adapt(rows: Sequence[Any], adapter: Any, failures: list[str], label: str) -> list[Headline]:
    """Adapts rows, skipping any single malformed one.

    One unparseable row must not cost an executive the whole briefing,
    but it must not vanish silently either -- each failure is counted
    and surfaced on `unavailable_sources`.
    """
    headlines: list[Headline] = []
    skipped = 0
    for row in rows:
        try:
            headlines.append(adapter(row))
        except Exception:  # noqa: BLE001 - one bad row must not fail the briefing
            skipped += 1
    if skipped:
        failures.append(f"{label}: {skipped} record(s) could not be read and were omitted")
    return headlines


def build_briefing(
    *,
    tenant_id: str,
    inputs: BriefingInputs,
    profile: PersonalizationProfile | None = None,
    database_id: str | None = None,
    max_chains: int = DEFAULT_MAX_CHAINS,
    now: datetime | None = None,
) -> ExecutiveBriefing:
    """Assembles the briefing.

    Args:
        tenant_id: The viewer's own server-resolved tenant. Recorded on
            the briefing; the caller is responsible for having scoped
            every row in `inputs` to it.
        inputs: The fetched rows.
        profile: The viewer's personalization profile. `None` uses the
            executive default.
        database_id: The database these rows were scoped to, if any.
        max_chains: How many signals get a decision chain built.
        now: Generation timestamp, for a deterministic test.
    """
    resolved_profile = profile or PROFILES[DEFAULT_PROFILE_ID]
    failures: list[str] = list(inputs.unavailable)

    findings = _adapt(inputs.findings, finding_headline, failures, "findings")
    recommendations = _adapt(
        inputs.recommendations, recommendation_headline, failures, "recommendations"
    )
    workflows = _adapt(inputs.workflows, workflow_headline, failures, "workflows")
    governance = _adapt(
        inputs.governance_versions, governance_headline, failures, "governance versions"
    )
    onboarding = _adapt(inputs.onboarding_jobs, onboarding_headline, failures, "onboarding jobs")

    movement = [item for item in findings if item.kind is HeadlineKind.KPI_MOVEMENT]
    anomalies = [item for item in findings if item.kind is HeadlineKind.ANOMALY]
    risks = [item for item in findings if item.kind is HeadlineKind.RISK]
    actions = [item for item in workflows if item.kind is HeadlineKind.ACTION]
    outcomes = [item for item in workflows if item.kind is HeadlineKind.OUTCOME]
    pending = [*governance, *onboarding]

    # The health rollup is computed from the complete sets, before any
    # per-role display cap is applied -- see this module's own docstring.
    health = build_health(
        signals=[*movement, *anomalies],
        risks=risks,
        recommendations=recommendations,
        actions=workflows,
        pending_decisions=pending,
    )

    # The declared foreign key: `workflows.recommendation_id`.
    workflows_by_recommendation: dict[str, Headline] = {}
    for row, headline in zip(inputs.workflows, workflows, strict=False):
        recommendation_id = getattr(row, "recommendation_id", None)
        if recommendation_id:
            workflows_by_recommendation[str(recommendation_id)] = headline

    chains = build_chains(
        signals=[*movement, *anomalies],
        recommendations=recommendations,
        workflows_by_recommendation=workflows_by_recommendation,
        max_chains=max_chains,
    )

    sections = (
        _section(SectionId.BUSINESS_HEALTH, (), "Business health is reported above as indicators."),
        _section(SectionId.KPI_MOVEMENT, movement, "No governed metric has moved meaningfully."),
        _section(SectionId.ANOMALIES, anomalies, "No anomalies are open."),
        _section(SectionId.RISKS, risks, "No risks are open."),
        _section(SectionId.RECOMMENDATIONS, recommendations, "No recommendations are on file."),
        _section(SectionId.PENDING_DECISIONS, pending, "Nothing is waiting on a decision."),
        _section(SectionId.ACTIONS, actions, "No actions are in flight."),
        _section(SectionId.OUTCOMES, outcomes, "No action has recorded an outcome yet."),
    )

    contributing = sorted(
        {
            headline.provenance.source
            for headline in [*findings, *recommendations, *workflows, *pending]
        },
        key=lambda source: source.value,
    )

    unavailable = [
        *failures,
        *(f"{name}: {reason}" for name, reason in COMPUTE_ONLY_CAPABILITIES),
    ]
    for source in SourceCapability:
        if source not in set(contributing):
            unavailable.append(f"{source.value}: no records for this tenant")

    return ExecutiveBriefing(
        tenant_id=tenant_id,
        database_id=database_id,
        profile_id=resolved_profile.profile_id,
        generated_at=(now or datetime.now(UTC)).isoformat(),
        health=health,
        sections=personalize(sections, resolved_profile),
        chains=chains,
        contributing_sources=tuple(contributing),
        unavailable_sources=tuple(unavailable),
        engine_version=DECISION_CENTER_VERSION,
    )


__all__ = ["COMPUTE_ONLY_CAPABILITIES", "BriefingInputs", "build_briefing"]
