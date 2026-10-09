"""Turning governed records into briefing claims, and refusing the ones
that are not supported.

Two halves, deliberately separated:

1. **Builders** (`kpi_candidates`, `schema_drift_candidates`,
   `recommendation_candidates`, `data_quality_candidates`) read one
   already-persisted (or already-computed) governed record and emit
   `ClaimCandidate`s. A candidate is a plain, *unvalidated* dataclass --
   it is not yet a claim.
2. **The gate** (`find_unsupported_claims`, `finalize`) is the only
   place a `BriefingItem` is ever constructed. A candidate with no
   evidence, with a version-less evidence link, with a claim class that
   disagrees with its truth level, or stating a number its own evidence
   does not contain is **withheld** and reported as an
   `UnsupportedClaim`.

That split is why "every claim links to deterministic evidence" is a
pipeline-level invariant here and not merely a convention -- the same
shape `recommendation.engine._finalize_candidate` already establishes
for its own "no recommendation without evidence" rule, layered on top of
`BriefingItem`'s own type-level guarantees (evidence `min_length=1`, and
the claim-class/truth-level validator) so neither layer alone has to
carry it.

**Number grounding reuses `agent.insight.extract_numbers` verbatim**
rather than re-implementing number extraction. The tolerance convention
is the same one `agent.insight.is_insight_grounded` uses, and so is the
allowance for numbers that merely appear in a label the user or the
source supplied (there, the question/SQL text; here,
`BriefingItem.subject`/`period_label`/`grounding_context`).

**Trusted source text may be quoted; caller-supplied text must be
verified.** A KPI finding, a schema-drift finding and a recommendation
record are all rows this server's own engines wrote into the identity
database through authorized paths, so their own wording is put into
`grounding_context` and quoted as-is. A data-quality finding is the one
input that arrives in the *request body* (nothing in this codebase
persists a data-quality scan), which makes it untrusted input like any
other: its text is **not** self-grounding, so every number it states
must also appear in the `observed` aggregates the same request supplies.
A caller that is internally inconsistent gets that claim withheld rather
than republished -- which is precisely what
`tests/test_briefing_engine.py::TestUnsupportedClaimsAreWithheld`
pins.

**Nothing in this module asserts a cause.** A driver claim states a
contribution to an arithmetic total and says so in its own text, exactly
as `kpi_monitoring.findings._detail` already does.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from semantic.intelligence.risk import HIGH_THRESHOLD, MEDIUM_THRESHOLD

from agent.insight import extract_numbers
from agent.provenance import DataTruthLevel
from briefing.freshness import Freshness, assess, describe
from briefing.inputs import (
    DataQualityFindingInput,
    KpiFindingInput,
    RecommendationInput,
    SchemaDriftFindingInput,
)
from briefing.models import (
    BRIEFING_ENGINE_VERSION,
    BriefingItem,
    BriefingSection,
    ClaimClass,
    EvidenceLink,
    UnsupportedClaim,
)
from kpi_monitoring.findings import is_kpi_kind
from kpi_monitoring.models import KPI_MONITORING_VERSION
from schema_drift.findings import is_schema_drift_kind

logger = logging.getLogger(__name__)

#: Recommendation statuses that still need someone's attention.
#: `accepted`/`partially_useful` are included on purpose: a recommendation
#: judged useful but never acted on is exactly the thing an executive
#: briefing exists to resurface. `resolved`/`rejected`/`incorrect`/
#: `expired` are all closed judgments and appear nowhere.
UNRESOLVED_RECOMMENDATION_STATUSES: frozenset[str] = frozenset(
    {"generated", "reviewed", "accepted", "partially_useful"}
)

#: Recommendation categories whose actions read as upside rather than
#: remediation. `recommendation.models.RecommendationCategory`'s own
#: value strings, not a new vocabulary.
OPPORTUNITY_CATEGORIES: frozenset[str] = frozenset({"revenue", "customer", "product"})

#: A recommendation is never ranked above this. It is a suggestion: a
#: suggestion outranking an observed collapse in a governed metric would
#: misorder exactly the attention this briefing exists to direct.
_MAX_RECOMMENDATION_SCORE = 60

#: `data_quality.models.DataQualitySeverity` -> a risk score on the same
#: 0-100 scale `semantic.intelligence.risk.score` uses, so one tier
#: definition covers every source. Deterministic, because the severity
#: itself already is (`data_quality/engine.py` derives it from each
#: finding's own magnitude, never from a judgment call).
_DATA_QUALITY_SCORE: dict[str, int] = {"critical": 70, "warning": 45, "info": 15}

#: How many contributors one movement may contribute as driver claims.
#: `kpi_monitoring.findings._detail` already shows three; matching it
#: keeps the briefing and the review queue telling the same story.
_MAX_DRIVERS_PER_CHANGE = 3


@dataclass(frozen=True)
class ClaimCandidate:
    """A proposed claim, before the evidence gate has passed it.

    Unvalidated by design: a builder may legitimately produce a
    candidate that turns out to be unsupported (a payload missing the
    number its own title states, a source that recorded no version), and
    the gate's whole job is to catch that. Constructing a `BriefingItem`
    directly here would make the failure unrepresentable and therefore
    undetectable.
    """

    section: BriefingSection
    claim_class: ClaimClass
    headline: str
    detail: str
    truth_level: DataTruthLevel
    evidence: tuple[EvidenceLink, ...]
    subject: str = ""
    period_label: str = ""
    grounding_context: tuple[str, ...] = ()
    risk_score: int = 0
    risk_tier: Literal["low", "medium", "high"] = "low"
    is_stale: bool = False
    stale_reason: str = ""
    limitations: tuple[str, ...] = field(default_factory=tuple)


def tier_for(score: int) -> Literal["low", "medium", "high"]:
    """The same tier boundaries `semantic.intelligence.risk.score` uses,
    imported rather than restated, so a tier means one thing across the
    whole platform."""
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


# --- the evidence gate ----------------------------------------------------


def _isclose(value: float, allowed: float, *, is_percent: bool) -> bool:
    """`agent.insight._isclose`'s convention: an absolute floor plus a 1%
    relative band, with a wider floor for percentages (which are
    routinely rendered to one decimal place)."""
    abs_tol = 1.0 if is_percent else 0.5
    return abs(value - allowed) <= max(abs_tol, abs(allowed) * 0.01)


def _allowed_numbers(candidate: ClaimCandidate) -> set[float]:
    """Every number a candidate may state.

    Each evidence number is allowed in both its signed and absolute
    form: a movement's own `change_percent` is stored signed
    (`-36.0`) while any readable sentence says "fell 36.0%", and
    rejecting that would reject correct text.
    """
    numbers: set[float] = set()
    for link in candidate.evidence:
        for value in link.observed.values():
            numbers.add(round(float(value), 4))
            numbers.add(round(abs(float(value)), 4))
    return numbers


def _ungrounded_number(candidate: ClaimCandidate) -> float | None:
    """The first number in a candidate's own text that its evidence does
    not support, or `None` when every number is supported."""
    label_text = " ".join([candidate.subject, candidate.period_label, *candidate.grounding_context])
    allowed = _allowed_numbers(candidate)
    for value, _ in extract_numbers(label_text):
        allowed.add(round(value, 4))
        allowed.add(round(abs(value), 4))

    for value, is_percent in extract_numbers(f"{candidate.headline} {candidate.detail}"):
        if not any(
            _isclose(value, candidate_value, is_percent=is_percent) for candidate_value in allowed
        ):
            return value
    return None


def find_unsupported_claims(candidates: Sequence[ClaimCandidate]) -> tuple[UnsupportedClaim, ...]:
    """Every candidate that may not become a claim, with the reason.

    Four independent checks, in the order a reader would want them:

    - `no_evidence` -- nothing to link to at all.
    - `missing_version` -- an evidence link that cannot answer "which
      version said this".
    - `truth_level_mismatch` -- an estimate dressed as an observed fact,
      or the reverse.
    - `ungrounded_number` -- a number in the claim's own text that its
      evidence does not contain.

    Reported, never raised: an unsupported candidate is a bug in a
    builder or a gap in a source's payload, and the briefing's job is to
    surface it rather than fail the whole request over it.
    """
    problems: list[UnsupportedClaim] = []
    for candidate in candidates:
        if not candidate.evidence:
            problems.append(
                UnsupportedClaim(
                    section=candidate.section,
                    headline=candidate.headline[:300],
                    code="no_evidence",
                    detail="The claim linked to no deterministic evidence.",
                )
            )
            continue

        versionless = [link for link in candidate.evidence if not link.versions]
        if versionless:
            problems.append(
                UnsupportedClaim(
                    section=candidate.section,
                    headline=candidate.headline[:300],
                    code="missing_version",
                    detail=(
                        f"{len(versionless)} evidence link(s) recorded no engine or "
                        "content version, so the claim could not be traced to a "
                        "specific version of whatever produced it."
                    ),
                )
            )
            continue

        expected = (
            DataTruthLevel.DATABASE_FACT
            if candidate.claim_class is ClaimClass.OBSERVED_FACT
            else DataTruthLevel.AI_INFERENCE
        )
        if candidate.truth_level is not expected:
            problems.append(
                UnsupportedClaim(
                    section=candidate.section,
                    headline=candidate.headline[:300],
                    code="truth_level_mismatch",
                    detail=(
                        f"A claim of class {candidate.claim_class.value!r} must be "
                        f"{expected.value!r}, not {candidate.truth_level.value!r}."
                    ),
                )
            )
            continue

        offending = _ungrounded_number(candidate)
        if offending is not None:
            problems.append(
                UnsupportedClaim(
                    section=candidate.section,
                    headline=candidate.headline[:300],
                    code="ungrounded_number",
                    detail=(
                        f"The claim states {offending:g}, which none of its evidence "
                        "links records as an observed value."
                    ),
                )
            )
    return tuple(problems)


def finalize(candidate: ClaimCandidate) -> BriefingItem:
    """Promotes one already-checked candidate into a real claim.

    The **only** `BriefingItem` constructor call in this package. Callers
    must run `find_unsupported_claims` first; `briefing.engine` does.
    """
    return BriefingItem(
        section=candidate.section,
        claim_class=candidate.claim_class,
        headline=candidate.headline[:300],
        detail=candidate.detail[:4000],
        subject=candidate.subject[:300],
        period_label=candidate.period_label[:120],
        grounding_context=candidate.grounding_context,
        risk_score=max(0, min(100, candidate.risk_score)),
        risk_tier=candidate.risk_tier,
        evidence=candidate.evidence,
        is_stale=candidate.is_stale,
        stale_reason=candidate.stale_reason[:500],
        limitations=candidate.limitations,
        truth_level=candidate.truth_level,
    )


# --- shared helpers -------------------------------------------------------


def _number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _observed(pairs: dict[str, float | None]) -> dict[str, float]:
    """Drops the keys a source did not record, so an evidence link never
    carries a placeholder number nothing observed."""
    return {key: float(value) for key, value in pairs.items() if value is not None}


def _driver_score(parent_score: int, contribution_percent: float) -> int:
    """How a single driver claim ranks against its own parent movement.

    A driver's share of the change, capped by the movement it explains.
    Two properties this buys, both deliberate: a driver is ranked
    biggest-share-first within the section (the obvious reading order,
    which a flat inherited score would leave to an alphabetical
    tie-break), and a driver can never outrank the movement it is only
    one part of.
    """
    return max(1, min(parent_score, int(round(abs(contribution_percent)))))


def _staleness(
    recorded_at: str | None, *, as_of: datetime, budget_seconds: float
) -> tuple[bool, str, Freshness, float | None]:
    """One record's staleness, as the flag/reason pair a candidate needs
    plus the raw outcome the engine needs for `stale_inputs`."""
    freshness, age = assess(recorded_at, as_of=as_of, budget_seconds=budget_seconds)
    if freshness is Freshness.FRESH:
        return False, "", freshness, age
    return True, describe(freshness, age, budget_seconds), freshness, age


# --- KPI movement, anomalies, drivers, risks, opportunities ---------------


def _kpi_link(finding: KpiFindingInput, observed: dict[str, float]) -> EvidenceLink:
    payload = finding.payload
    engine_version = str(payload.get("engine_version") or KPI_MONITORING_VERSION)
    return EvidenceLink(
        source="kpi_monitoring.findings",
        reference_kind="kpi_finding",
        reference_id=finding.finding_id,
        observed=observed,
        versions={
            "kpi_engine_version": engine_version,
            "finding_version": str(finding.version),
            "briefing_engine_version": BRIEFING_ENGINE_VERSION,
        },
        recorded_at=finding.last_seen_at,
        truth_level=DataTruthLevel.DATABASE_FACT,
    )


def _kpi_subject(finding: KpiFindingInput) -> str:
    payload = finding.payload
    return str(payload.get("metric_concept_key") or finding.kind)


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def kpi_candidates(
    findings: Sequence[KpiFindingInput],
    *,
    as_of: datetime,
    budget_seconds: float,
    risk_min_score: int,
) -> list[ClaimCandidate]:
    """Movement, anomaly, driver, risk and opportunity candidates from
    this tenant's persisted KPI findings.

    One finding can legitimately produce several candidates in different
    sections, because they are different *kinds* of statement about the
    same evidence: the movement itself is observed arithmetic
    (`OBSERVED_FACT`); that it is anomalous, risky, favourable, or
    attributable to a dimension are each this platform's own inferences
    (`ESTIMATE`). Splitting them is what makes the fact/estimate
    distinction visible to a reader rather than buried in one paragraph.
    """
    candidates: list[ClaimCandidate] = []

    for finding in findings:
        if not is_kpi_kind(finding.kind):
            continue
        payload = finding.payload
        period = str(payload.get("period") or "")
        subject = _kpi_subject(finding)
        baseline_label = str(payload.get("baseline_label") or "")
        current = _number(payload, "current_value")
        baseline = _number(payload, "baseline_value")
        change_percent = _number(payload, "change_percent")
        change_absolute = _number(payload, "change_absolute")
        threshold = _number(payload, "threshold_crossed")
        direction = str(payload.get("direction") or "flat")
        methods = [str(method) for method in payload.get("detection_methods") or []]
        limitations = tuple(str(item) for item in payload.get("limitations") or [])

        if current is None:
            logger.info(
                "[briefing] KPI finding %s recorded no current value; skipped",
                finding.finding_id,
            )
            continue

        observed = _observed(
            {
                "current_value": current,
                "baseline_value": baseline,
                "change_absolute": change_absolute,
                "change_percent": change_percent,
                "threshold_crossed": threshold,
            }
        )
        link = _kpi_link(finding, observed)
        is_stale, stale_reason, _, _ = _staleness(
            finding.last_seen_at, as_of=as_of, budget_seconds=budget_seconds
        )

        movement_detail = f"{subject} was {_format(current)} in {period or 'this period'}"
        if baseline is not None:
            movement_detail += f", against {_format(baseline)} ({baseline_label})"
            if change_percent is not None:
                movement_detail += f" -- a change of {change_percent:+.1f}%"
        movement_detail += "."

        verb = "fell" if direction == "down" else "rose" if direction == "up" else "held flat"
        headline = (
            f"{subject} {verb} {abs(change_percent):.1f}% in {period}"
            if change_percent is not None
            else f"{subject} was {_format(current)} in {period}"
        )

        candidates.append(
            ClaimCandidate(
                section=BriefingSection.KPI_MOVEMENT,
                claim_class=ClaimClass.OBSERVED_FACT,
                headline=headline,
                detail=movement_detail,
                truth_level=DataTruthLevel.DATABASE_FACT,
                evidence=(link,),
                subject=subject,
                period_label=period,
                grounding_context=(baseline_label,),
                risk_score=finding.risk_score,
                risk_tier=finding.risk_tier,
                is_stale=is_stale,
                stale_reason=stale_reason,
                limitations=limitations,
            )
        )

        # Anomalies: a strictly narrower, better-evidenced subset -- a
        # statistical method flagged the point, or it crossed a level an
        # operator explicitly declared significant.
        if methods or threshold is not None:
            reason = (
                f"flagged by {', '.join(methods)}"
                if methods
                else f"crossed the configured threshold of {_format(threshold)}"
            )
            candidates.append(
                ClaimCandidate(
                    section=BriefingSection.ANOMALIES,
                    claim_class=ClaimClass.ESTIMATE,
                    headline=f"{subject} in {period} looks unusual ({reason})",
                    detail=(
                        f"{movement_detail} This platform judged the movement unusual: "
                        f"{reason}. That judgment is an inference about observed "
                        "numbers, not an observation itself."
                    ),
                    truth_level=DataTruthLevel.AI_INFERENCE,
                    evidence=(link,),
                    subject=subject,
                    period_label=period,
                    grounding_context=(baseline_label, reason),
                    risk_score=finding.risk_score,
                    risk_tier=finding.risk_tier,
                    is_stale=is_stale,
                    stale_reason=stale_reason,
                    limitations=limitations,
                )
            )

        # Drivers: only when the underlying attribution actually found
        # supporting evidence. `kpi_monitoring.models.ChangeEvidence`
        # guarantees contributors are absent otherwise, so there is
        # nothing to hedge about here -- their absence is simply silence.
        if payload.get("has_sufficient_evidence"):
            confidence = _number(payload, "confidence")
            dimensions = ", ".join(str(item) for item in payload.get("dimension_columns") or [])
            for contributor in (payload.get("contributors") or [])[:_MAX_DRIVERS_PER_CHANGE]:
                if not isinstance(contributor, dict):
                    continue
                label = str(contributor.get("label") or "")
                share = contributor.get("contribution_percent")
                contribution = contributor.get("contribution")
                if not isinstance(share, int | float) or isinstance(share, bool):
                    continue
                driver_observed = _observed(
                    {
                        "contribution_percent": float(share),
                        "contribution": (
                            float(contribution)
                            if isinstance(contribution, int | float)
                            and not isinstance(contribution, bool)
                            else None
                        ),
                        "change_absolute": change_absolute,
                        "confidence": confidence,
                    }
                )
                candidates.append(
                    ClaimCandidate(
                        section=BriefingSection.DRIVERS,
                        claim_class=ClaimClass.ESTIMATE,
                        headline=(
                            f"{label} accounts for {float(share):+.1f}% of the change in "
                            f"{subject}"
                        ),
                        detail=(
                            f"Of {subject}'s movement in {period}, {label} contributes "
                            f"{float(share):+.1f}%. This is a contribution to an "
                            "arithmetic total, not a demonstrated cause."
                        ),
                        truth_level=DataTruthLevel.AI_INFERENCE,
                        evidence=(_kpi_link(finding, driver_observed),),
                        subject=f"{subject} / {label}",
                        period_label=period,
                        grounding_context=(label, dimensions, baseline_label),
                        risk_score=_driver_score(finding.risk_score, float(share)),
                        risk_tier=tier_for(_driver_score(finding.risk_score, float(share))),
                        is_stale=is_stale,
                        stale_reason=stale_reason,
                        limitations=limitations,
                    )
                )

        if finding.risk_score < risk_min_score:
            continue

        # Risk / opportunity: the same evidence, read for direction. A
        # downward move or a crossed threshold is something to act on; an
        # upward move is upside worth naming. Both are inferences.
        if direction == "down" or threshold is not None:
            candidates.append(
                ClaimCandidate(
                    section=BriefingSection.RISKS,
                    claim_class=ClaimClass.ESTIMATE,
                    headline=f"Risk: {subject} is below its {baseline_label or 'baseline'}",
                    detail=(
                        f"{movement_detail} Treated as a risk because the movement is "
                        "downward or crossed an operator-declared level. Whether it "
                        "matters to the business is a judgment this briefing does not "
                        "make for you."
                    ),
                    truth_level=DataTruthLevel.AI_INFERENCE,
                    evidence=(link,),
                    subject=subject,
                    period_label=period,
                    grounding_context=(baseline_label,),
                    risk_score=finding.risk_score,
                    risk_tier=finding.risk_tier,
                    is_stale=is_stale,
                    stale_reason=stale_reason,
                    limitations=limitations,
                )
            )
        elif direction == "up":
            candidates.append(
                ClaimCandidate(
                    section=BriefingSection.OPPORTUNITIES,
                    claim_class=ClaimClass.ESTIMATE,
                    headline=f"Opportunity: {subject} is above its {baseline_label or 'baseline'}",
                    detail=(
                        f"{movement_detail} Named as upside because the movement is "
                        "upward and large enough to rank. A favourable number can also "
                        "be a data artifact, which is why this is an estimate."
                    ),
                    truth_level=DataTruthLevel.AI_INFERENCE,
                    evidence=(link,),
                    subject=subject,
                    period_label=period,
                    grounding_context=(baseline_label,),
                    risk_score=finding.risk_score,
                    risk_tier=finding.risk_tier,
                    is_stale=is_stale,
                    stale_reason=stale_reason,
                    limitations=limitations,
                )
            )

    return candidates


# --- schema drift as a risk to a governed number --------------------------


def schema_drift_candidates(
    findings: Sequence[SchemaDriftFindingInput],
    *,
    as_of: datetime,
    budget_seconds: float,
    risk_min_score: int,
) -> list[ClaimCandidate]:
    """Risk candidates from this tenant's persisted schema-drift
    findings.

    Only drift that either ranks at or above the risk floor or already
    has a resolved downstream impact reaches a briefing: an additive
    column nobody depends on yet is real, recorded, and reviewable in
    its own queue, but it is not executive news. The impacted-concept
    list is `schema_drift.impact`'s own already-computed output, read
    back rather than recomputed.
    """
    candidates: list[ClaimCandidate] = []
    for finding in findings:
        if not is_schema_drift_kind(finding.kind):
            continue
        payload = finding.payload
        impacted = [item for item in payload.get("impacted") or [] if isinstance(item, dict)]
        published = [
            item for item in impacted if item.get("truth_level") == "confirmed_business_truth"
        ]
        if finding.risk_score < risk_min_score and not published:
            continue

        table = str(payload.get("table") or "")
        column = payload.get("column")
        subject = table if not column else f"{table}.{column}"
        change_kind = str(payload.get("change_kind") or finding.kind)

        link = EvidenceLink(
            source="schema_drift.findings",
            reference_kind="schema_drift_finding",
            reference_id=finding.finding_id,
            observed=_observed(
                {
                    "impacted_concepts": float(len(impacted)),
                    "impacted_published_concepts": float(len(published)),
                    "risk_score": float(finding.risk_score),
                }
            ),
            versions={
                "finding_version": str(finding.version),
                "briefing_engine_version": BRIEFING_ENGINE_VERSION,
            },
            recorded_at=finding.last_seen_at,
            truth_level=DataTruthLevel.DATABASE_FACT,
        )
        is_stale, stale_reason, _, _ = _staleness(
            finding.last_seen_at, as_of=as_of, budget_seconds=budget_seconds
        )

        impact_sentence = (
            f"{len(published)} published business concept(s) read this object."
            if published
            else "No published business concept is currently known to read this object."
        )
        limitations: tuple[str, ...] = ()
        if payload.get("impact_truncated"):
            limitations = (
                "The downstream impact walk was truncated, so more dependents may exist.",
            )

        candidates.append(
            ClaimCandidate(
                section=BriefingSection.RISKS,
                claim_class=ClaimClass.ESTIMATE,
                headline=f"Risk: schema change under {subject} ({change_kind})",
                detail=(
                    f"{finding.title or change_kind}. {impact_sentence} A structural "
                    "change is an observed fact; that it threatens a reported number is "
                    "an inference, and resolving it stays a review decision in the "
                    "semantic-intelligence queue."
                ),
                truth_level=DataTruthLevel.AI_INFERENCE,
                evidence=(link,),
                subject=subject,
                grounding_context=(table, str(column or ""), change_kind, finding.title),
                risk_score=finding.risk_score,
                risk_tier=finding.risk_tier,
                is_stale=is_stale,
                stale_reason=stale_reason,
                limitations=limitations,
            )
        )
    return candidates


# --- unresolved recommendations and opportunities -------------------------


def _recommendation_score(record: RecommendationInput) -> int:
    """A transparent, capped rank for a recommendation.

    Deliberately *not* `semantic.intelligence.risk.score`: that function
    ranks review findings by kind, and a recommendation has no kind in
    that vocabulary. The rule here is the whole rule -- confidence
    scaled onto the same 0-100 scale, capped at
    `_MAX_RECOMMENDATION_SCORE` so a suggestion never outranks an
    observed governed-metric movement.
    """
    confidence = record.confidence if record.confidence is not None else 0.5
    return min(_MAX_RECOMMENDATION_SCORE, int(round(confidence * 100)))


def recommendation_candidates(
    records: Sequence[RecommendationInput],
    *,
    as_of: datetime,
    budget_seconds: float,
) -> list[ClaimCandidate]:
    """Unresolved-recommendation and opportunity candidates.

    A record with no evidence at all is still reported -- as an
    unresolved item whose evidence link cites the record itself (its own
    id and both version snapshots), with an explicit limitation saying
    the originating engine recorded no evidence. That is the honest
    reading: the *record* is a real, governed artifact worth
    resurfacing; what it asserts is not itself grounded, and the
    briefing says which.
    """
    candidates: list[ClaimCandidate] = []
    for record in records:
        if record.status not in UNRESOLVED_RECOMMENDATION_STATUSES:
            continue

        score = _recommendation_score(record)
        link = EvidenceLink(
            source="recommendation.engine",
            reference_kind="recommendation_record",
            reference_id=record.record_id,
            observed=_observed(
                {
                    "confidence": record.confidence,
                    "evidence_items": float(record.evidence_count),
                }
            ),
            versions={
                "recommendation_engine_version": record.engine_version,
                "evidence_version": record.evidence_version,
                "briefing_engine_version": BRIEFING_ENGINE_VERSION,
            },
            recorded_at=record.generated_at,
            truth_level=DataTruthLevel.AI_INFERENCE,
        )
        is_stale, stale_reason, _, _ = _staleness(
            record.generated_at, as_of=as_of, budget_seconds=budget_seconds
        )

        limitations = tuple(record.limitations)
        if record.evidence_count == 0:
            limitations = (
                *limitations,
                "The originating engine recorded no evidence for this recommendation.",
            )

        owner = record.owner_display_name or "nobody"
        detail_parts = [
            f"Status: {record.status}. Owner: {owner}.",
            record.rationale or "",
            f"Proposed action: {record.action}" if record.action else "",
            f"Stated impact: {record.measurable_impact}" if record.measurable_impact else "",
        ]
        subject = record.affected_entity or record.category or "unassigned"
        grounding = (
            record.claim_text,
            record.rationale or "",
            record.action or "",
            record.measurable_impact or "",
            subject,
            record.status,
            owner,
        )

        candidates.append(
            ClaimCandidate(
                section=BriefingSection.UNRESOLVED_RECOMMENDATIONS,
                claim_class=ClaimClass.RECOMMENDATION,
                headline=f"Unresolved: {record.claim_text}",
                detail=" ".join(part for part in detail_parts if part.strip()),
                truth_level=DataTruthLevel.AI_INFERENCE,
                evidence=(link,),
                subject=subject,
                grounding_context=grounding,
                risk_score=score,
                risk_tier=tier_for(score),
                is_stale=is_stale,
                stale_reason=stale_reason,
                limitations=limitations,
            )
        )

        if record.kind == "action" and (record.category or "") in OPPORTUNITY_CATEGORIES:
            candidates.append(
                ClaimCandidate(
                    section=BriefingSection.OPPORTUNITIES,
                    claim_class=ClaimClass.RECOMMENDATION,
                    headline=f"Suggested action: {record.action or record.claim_text}",
                    detail=(
                        f"A {record.category} recommendation still awaiting a decision. "
                        f"{record.rationale or ''} This is a suggestion, not an "
                        "observed outcome."
                    ),
                    truth_level=DataTruthLevel.AI_INFERENCE,
                    evidence=(link,),
                    subject=subject,
                    grounding_context=grounding,
                    risk_score=score,
                    risk_tier=tier_for(score),
                    is_stale=is_stale,
                    stale_reason=stale_reason,
                    limitations=limitations,
                )
            )
    return candidates


# --- data quality ---------------------------------------------------------


def data_quality_candidates(
    findings: Sequence[DataQualityFindingInput],
    *,
    as_of: datetime,
    budget_seconds: float,
) -> list[ClaimCandidate]:
    """Data-quality candidates: the observed failure, and -- for a
    critical one -- a risk reading of it.

    `data_quality.models.DataQualityReport.truth_level` is
    `DATABASE_FACT` for a real reason (a null rate is a measurement, not
    a prediction), so the data-quality claim itself is an
    `OBSERVED_FACT`. Calling a critical failure a *risk* is a separate,
    inferred statement and gets its own `ESTIMATE` item.
    """
    candidates: list[ClaimCandidate] = []
    for finding in findings:
        subject = finding.table if not finding.column else f"{finding.table}.{finding.column}"
        score = _DATA_QUALITY_SCORE.get(finding.severity, _DATA_QUALITY_SCORE["info"])
        link = EvidenceLink(
            source="data_quality.engine",
            reference_kind="data_quality_finding",
            reference_id=f"{finding.dimension}:{subject}",
            observed=dict(finding.observed),
            versions={
                "data_quality_engine_version": finding.engine_version,
                "briefing_engine_version": BRIEFING_ENGINE_VERSION,
            },
            recorded_at=finding.observed_at,
            truth_level=DataTruthLevel.DATABASE_FACT,
        )
        is_stale, stale_reason, _, _ = _staleness(
            finding.observed_at, as_of=as_of, budget_seconds=budget_seconds
        )
        grounding = (subject, finding.dimension, finding.severity, finding.formula)

        candidates.append(
            ClaimCandidate(
                section=BriefingSection.DATA_QUALITY,
                claim_class=ClaimClass.OBSERVED_FACT,
                headline=f"{finding.severity.capitalize()} {finding.dimension} issue on {subject}",
                detail=finding.claim_text,
                truth_level=DataTruthLevel.DATABASE_FACT,
                evidence=(link,),
                subject=subject,
                grounding_context=grounding,
                risk_score=score,
                risk_tier=tier_for(score),
                is_stale=is_stale,
                stale_reason=stale_reason,
            )
        )

        if finding.severity == "critical":
            candidates.append(
                ClaimCandidate(
                    section=BriefingSection.RISKS,
                    claim_class=ClaimClass.ESTIMATE,
                    headline=f"Risk: {finding.dimension} failure on {subject} may affect reporting",
                    detail=(
                        f"{finding.claim_text} Any governed number computed from this "
                        "object may be affected. Whether a specific report is actually "
                        "wrong is not established here -- exposure, not a verdict."
                    ),
                    truth_level=DataTruthLevel.AI_INFERENCE,
                    evidence=(link,),
                    subject=subject,
                    grounding_context=grounding,
                    risk_score=score,
                    risk_tier=tier_for(score),
                    is_stale=is_stale,
                    stale_reason=stale_reason,
                )
            )
    return candidates


__all__ = [
    "OPPORTUNITY_CATEGORIES",
    "UNRESOLVED_RECOMMENDATION_STATUSES",
    "ClaimCandidate",
    "data_quality_candidates",
    "finalize",
    "find_unsupported_claims",
    "kpi_candidates",
    "recommendation_candidates",
    "schema_drift_candidates",
    "tier_for",
]
