"""The one orchestration entry point: governed records in, one briefing
out.

Runs the mission's own named flow, each step delegated to the module
that owns it, so this file stays a sequence rather than an
implementation:

    project inputs (caller)              -> briefing.inputs
    -> build candidates per source       -> briefing.claims
    -> assess staleness                  -> briefing.freshness
    -> withhold unsupported claims       -> briefing.claims
    -> rank, cap and record coverage     -> here
    -> render                            -> briefing.reports

Pure and database-free. Everything situational -- the tenant's KPI and
schema-drift findings, its unresolved recommendations, a just-computed
data-quality report, and the reference instant -- arrives as an
argument, supplied by the caller from the already-authorized, governed
path. That is the structural reason a briefing cannot read a monitored
database, cannot generate SQL and cannot bypass
`agent/sql_validator.py`: it has no way to produce a row.

**An empty briefing is never silently empty.** `coverage` records, per
source, whether anything was supplied at all and how many claims it
produced, and `empty_sections` names every section with nothing in it.
An executive must be able to tell "nothing moved" from "nothing was
checked" -- the same distinction `kpi_monitoring.models.MonitorResult
.series_skipped` preserves one level down.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from briefing.claims import (
    ClaimCandidate,
    data_quality_candidates,
    finalize,
    find_unsupported_claims,
    kpi_candidates,
    recommendation_candidates,
    schema_drift_candidates,
)
from briefing.freshness import Freshness, assess, stale_input, staleness_budget_seconds
from briefing.inputs import (
    DataQualityFindingInput,
    KpiFindingInput,
    RecommendationInput,
    SchemaDriftFindingInput,
)
from briefing.models import (
    BRIEFING_ENGINE_VERSION,
    BriefingItem,
    BriefingPeriod,
    BriefingSection,
    EvidenceLink,
    ExecutiveBriefing,
    SourceCoverage,
    StaleInput,
)
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

#: The risk-score floor a KPI/schema-drift finding must clear before it
#: is read as a risk or an opportunity, by tier name. Resolved from
#: `Settings.briefing_risk_min_tier` against
#: `semantic.intelligence.risk`'s own thresholds, so one tier boundary
#: serves the whole platform.
_TIER_FLOOR: dict[str, int] = {"low": 0, "medium": 40, "high": 70}


def _coverage(
    source: str,
    *,
    supplied: bool,
    input_count: int,
    items_produced: int,
    note: str = "",
) -> SourceCoverage:
    return SourceCoverage(
        source=source,  # type: ignore[arg-type]
        supplied=supplied,
        input_count=input_count,
        items_produced=items_produced,
        note=note,
    )


def _stale_entries(
    links: Sequence[EvidenceLink], *, as_of: datetime, budget_seconds: float
) -> list[StaleInput]:
    """One `StaleInput` per distinct stale or unknown-age evidence link.

    De-duplicated by `(source, reference_id)`: one finding that produced
    a movement, an anomaly and two driver claims is a single stale
    *input*, not four.
    """
    seen: set[tuple[str, str]] = set()
    entries: list[StaleInput] = []
    for link in links:
        identity = (link.source, link.reference_id)
        if identity in seen:
            continue
        freshness, age = assess(link.recorded_at, as_of=as_of, budget_seconds=budget_seconds)
        if freshness is Freshness.FRESH:
            continue
        seen.add(identity)
        entries.append(
            stale_input(
                source=link.source,
                reference_id=link.reference_id,
                recorded_at=link.recorded_at,
                age=age,
                budget_seconds=budget_seconds,
                freshness=freshness,
            )
        )
    return entries


def _rank(item: BriefingItem) -> tuple[int, int, str]:
    """Highest risk first; a stale item sorts below a fresh item of the
    same rank, because an executive should read the current thing first
    -- never dropped, only ordered (see `briefing.freshness`)."""
    return (-item.risk_score, 1 if item.is_stale else 0, item.headline)


def build_briefing(
    *,
    database_id: str,
    period: BriefingPeriod,
    as_of: datetime,
    period_label: str = "",
    kpi_findings: Sequence[KpiFindingInput] | None = None,
    schema_drift_findings: Sequence[SchemaDriftFindingInput] | None = None,
    recommendations: Sequence[RecommendationInput] | None = None,
    data_quality_findings: Sequence[DataQualityFindingInput] | None = None,
    omitted_sources: Sequence[tuple[str, str]] = (),
    settings: Settings | None = None,
) -> ExecutiveBriefing:
    """Assembles one executive briefing.

    Args:
        database_id: Which configured database this briefing covers.
        period: The cadence -- selects the staleness budget and the
            report heading, never a date filter (period labels are
            opaque strings throughout this package).
        as_of: The reference instant every staleness check is made
            against. Caller-supplied, never read from a clock here, for
            the same reason `data_quality.models.FreshnessRule.as_of`
            is: a briefing assembled for a past day must be judged
            against that day.
        period_label: Display label for the period (e.g. `"2026-06"`,
            `"week of 2026-06-01"`). Also treated as allowed text by the
            unsupported-claim check, since a number in a period label is
            not an invented statistic.
        kpi_findings: Persisted KPI-monitoring findings, projected.
        schema_drift_findings: Persisted schema-drift findings,
            projected.
        recommendations: Persisted recommendation records, projected.
        data_quality_findings: A just-computed data-quality report's
            findings, projected. The one source with no persisted
            history anywhere in this codebase.
        omitted_sources: `(source, reason)` pairs for sources the caller
            deliberately did not supply -- most importantly, one the
            caller is not authorized to read. Recorded in `coverage` and
            `limitations` so an executive is never shown a briefing that
            silently omits a whole category.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        An `ExecutiveBriefing`. Nothing is persisted; this function
        writes nothing anywhere.
    """
    settings = settings or get_settings()
    budget = staleness_budget_seconds(period, settings)
    risk_floor = _TIER_FLOOR.get(settings.briefing_risk_min_tier, _TIER_FLOOR["medium"])
    per_section_cap = settings.briefing_max_items_per_section

    kpi = list(kpi_findings or ())
    drift = list(schema_drift_findings or ())
    recs = list(recommendations or ())
    quality = list(data_quality_findings or ())

    by_source: dict[str, list[ClaimCandidate]] = {
        "kpi_findings": kpi_candidates(
            kpi, as_of=as_of, budget_seconds=budget, risk_min_score=risk_floor
        ),
        "schema_drift_findings": schema_drift_candidates(
            drift, as_of=as_of, budget_seconds=budget, risk_min_score=risk_floor
        ),
        "recommendations": recommendation_candidates(recs, as_of=as_of, budget_seconds=budget),
        "data_quality_findings": data_quality_candidates(
            quality, as_of=as_of, budget_seconds=budget
        ),
    }
    input_counts = {
        "kpi_findings": len(kpi),
        "schema_drift_findings": len(drift),
        "recommendations": len(recs),
        "data_quality_findings": len(quality),
    }

    candidates = [candidate for group in by_source.values() for candidate in group]

    # The gate: an unsupported candidate never becomes a claim. Matching
    # is by (section, headline) -- a builder never emits two candidates
    # with the same pair, and the alternative (mutating candidates to
    # carry an id) would add state for no gain.
    withheld = find_unsupported_claims(candidates)
    withheld_keys = {(problem.section, problem.headline) for problem in withheld}
    if withheld:
        logger.info(
            "[briefing] withheld %d unsupported claim(s) for database %s",
            len(withheld),
            database_id,
        )

    items: list[BriefingItem] = []
    produced_by_source: dict[str, int] = dict.fromkeys(by_source, 0)
    for source, group in by_source.items():
        for candidate in group:
            if (candidate.section, candidate.headline[:300]) in withheld_keys:
                continue
            items.append(finalize(candidate))
            produced_by_source[source] += 1

    items.sort(key=_rank)

    # Cap per section, highest-ranked kept -- the same "rank first, then
    # truncate" order `kpi_monitoring.engine` uses, so a capped briefing
    # keeps the most important claims rather than whichever source
    # happened to be processed first.
    kept: list[BriefingItem] = []
    truncated: list[BriefingSection] = []
    counts: dict[BriefingSection, int] = {}
    for item in items:
        counts[item.section] = counts.get(item.section, 0) + 1
        if counts[item.section] <= per_section_cap:
            kept.append(item)
        elif item.section not in truncated:
            truncated.append(item.section)

    stale_inputs = _stale_entries(
        [link for item in kept for link in item.evidence], as_of=as_of, budget_seconds=budget
    )

    present = {item.section for item in kept}
    empty_sections = tuple(section for section in BriefingSection if section not in present)

    omitted = dict(omitted_sources)
    coverage = tuple(
        _coverage(
            source,
            supplied=source not in omitted and input_counts[source] > 0,
            input_count=input_counts[source],
            items_produced=produced_by_source[source],
            note=(
                omitted[source]
                if source in omitted
                else (
                    "No records were supplied for this source, so nothing was checked."
                    if input_counts[source] == 0
                    else (
                        "Records were supplied and checked; none produced a claim."
                        if produced_by_source[source] == 0
                        else ""
                    )
                )
            ),
        )
        for source in by_source
    )

    limitations: list[str] = []
    for source, reason in omitted.items():
        limitations.append(f"{source}: {reason}")
    if not kept:
        limitations.append(
            "No claim in this briefing. Read `coverage` before concluding nothing "
            "happened: a source with `supplied=false` was never checked."
        )
    if withheld:
        limitations.append(
            f"{len(withheld)} claim(s) were withheld because their own evidence did not "
            "support them -- see `withheld_claims`."
        )
    if stale_inputs:
        limitations.append(
            f"{len(stale_inputs)} evidence record(s) are older than this cadence's "
            "freshness budget, or recorded no timestamp -- see `stale_inputs`."
        )
    if truncated:
        limitations.append(
            "Some sections were capped at "
            f"{per_section_cap} item(s); lower-ranked claims are not shown."
        )

    return ExecutiveBriefing(
        database_id=database_id,
        period=period,
        period_label=period_label,
        as_of=as_of.isoformat(),
        items=tuple(kept),
        coverage=coverage,
        empty_sections=empty_sections,
        stale_inputs=tuple(stale_inputs),
        withheld_claims=withheld,
        limitations=tuple(limitations),
        truncated_sections=tuple(truncated),
        engine_version=BRIEFING_ENGINE_VERSION,
    )


__all__ = ["build_briefing"]
