"""Adapters: one persisted record from an earlier capability -> one
normalised `Headline`.

Every function here is pure and duck-typed against the ORM rows the API
route already loaded -- this package imports neither SQLAlchemy nor
`identity.models`, the same boundary `kpi_monitoring` and
`governance_center.models` keep. A caller hands in rows; these functions
read attributes.

**Nothing is recomputed.** A finding's `risk_score` becomes the
headline's severity unchanged; a recommendation's `confidence` is
carried, not re-derived. Re-scoring here would mean the executive view
silently disagreeing with the review queue an operator actually works
from, which is worse than showing nothing.

**Truth levels are carried, never assigned.** A finding row already
records its own (`truth_level`, always `ai_inference` for a detector's
suggestion); a workflow's recorded status is an observed fact about this
platform's own state and is `DATABASE_FACT`. Where a record declares
none, the adapter picks the conservative option and says so in its own
comment -- promoting an unlabelled value to a fact is the error that
matters.
"""

from __future__ import annotations

from typing import Any, Literal

from agent.provenance import DataTruthLevel
from decision_center.models import (
    NOT_RECORDED,
    EvidenceItem,
    Headline,
    HeadlineKind,
    Provenance,
    SourceCapability,
    evidence_from_payload,
)

#: `semantic_findings.kind` prefixes, mapped to what they mean to an
#: executive. Prompt 50's KPI kinds are movement; Prompt 43's schema
#: drift is risk; everything else from Prompt 35 is a semantic risk.
_KPI_PREFIX = "kpi_"
_DRIFT_PREFIX = "schema_drift_"

#: A KPI kind that is specifically an unusual shape rather than a
#: directional move -- shown under anomalies rather than movement.
_ANOMALY_KINDS = frozenset({"kpi_trend_break"})


def _text(value: Any, default: str = NOT_RECORDED, limit: int = 4000) -> str:
    if value is None:
        return default
    rendered = str(value).strip()
    return rendered[:limit] if rendered else default


def _iso(value: Any) -> str:
    if value is None:
        return NOT_RECORDED
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else _text(value)


#: The only tiers `Headline.severity_tier` accepts. A typed lookup
#: rather than a chain of comparisons, so the narrowing is explicit to
#: both a reader and the type checker.
_KNOWN_TIERS: dict[str, Literal["low", "medium", "high"]] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}


def _tier(score: int) -> Literal["low", "medium", "high"]:
    """Maps a recorded risk score onto the same three tiers
    `semantic.intelligence.risk` uses, so the Decision Center and the
    review queue describe the same item the same way."""
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _tier_of(recorded: Any, score: int) -> Literal["low", "medium", "high"]:
    """The tier a record declared, if it is one this platform knows.

    A stored tier outside the three known values falls back to the one
    derived from the score rather than being passed through: `Headline`
    declares a `Literal`, so an unrecognised string would raise and cost
    the whole briefing over one malformed row.
    """
    known = _KNOWN_TIERS.get(_text(recorded, default=""))
    return known if known is not None else _tier(score)


def _clamp_confidence(value: Any) -> float | None:
    """Keeps a recorded confidence inside [0, 1], and refuses an exact
    1.0 on an inference.

    `Headline` rejects a 1.0-confidence inference outright, which is the
    right contract but would turn one malformed stored row into a failed
    briefing for the whole tenant. Nudging it below 1.0 preserves the
    guarantee (nothing inferred is shown as certain) without letting a
    bad row take the page down.
    """
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0.0:
        return 0.0
    return min(confidence, 0.999)


def finding_headline(row: Any) -> Headline:
    """A `semantic_findings` row -- Prompts 35, 43 and 50 all write here.

    The finding's own `content` dict carries everything needed to drill
    in (Prompt 35's `_content_of`): detail, subjects, evidence, reasons
    and a capability-specific payload. A Prompt 50 KPI finding's payload
    additionally holds the baseline, the period, the contributors,
    the confidence and the limitations -- which is why KPI movement
    drills all the way down without this package recomputing anything.
    """
    raw_content = getattr(row, "content", None)
    content: dict[str, Any] = raw_content if isinstance(raw_content, dict) else {}
    raw_payload = content.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    kind = _text(getattr(row, "kind", ""), default="")

    if kind.startswith(_KPI_PREFIX):
        source = SourceCapability.KPI_MONITORING
        headline_kind = (
            HeadlineKind.ANOMALY if kind in _ANOMALY_KINDS else HeadlineKind.KPI_MOVEMENT
        )
    elif kind.startswith(_DRIFT_PREFIX):
        source = SourceCapability.SCHEMA_DRIFT
        headline_kind = HeadlineKind.RISK
    else:
        source = SourceCapability.SEMANTIC_INTELLIGENCE
        headline_kind = HeadlineKind.RISK

    metric_key = payload.get("metric_concept_key")
    severity = int(getattr(row, "risk_score", 0) or 0)

    evidence = list(evidence_from_payload(content.get("evidence")))
    for reason in list(content.get("reasons", []))[:4]:
        evidence.append(
            EvidenceItem(
                label="ranked because",
                value=_text(reason, default="", limit=2000),
                # A ranking reason is this platform's own judgment about
                # priority, not an observation about the business.
                truth_level=DataTruthLevel.AI_INFERENCE,
            )
        )

    return Headline(
        headline_id=f"finding:{getattr(row, 'id', '')}",
        kind=headline_kind,
        title=_text(getattr(row, "title", ""), default="Untitled finding", limit=400),
        detail=_text(content.get("detail"), default=""),
        severity=max(0, min(severity, 100)),
        severity_tier=_tier_of(getattr(row, "risk_tier", None), severity),
        status=_text(getattr(row, "status", ""), default=""),
        provenance=Provenance(
            source=source,
            source_record_id=str(getattr(row, "id", "")),
            period=_text(payload.get("period")),
            metric_concept_key=str(metric_key) if metric_key else None,
            confidence=_clamp_confidence(getattr(row, "confidence", None)),
            database_id=_text(getattr(row, "database_id", None), default="") or None,
            recorded_at=_iso(
                getattr(row, "last_seen_at", None) or getattr(row, "updated_at", None)
            ),
            engine_version=_text(payload.get("engine_version")),
            limitations=tuple(str(item)[:500] for item in list(payload.get("limitations", []))[:6]),
            # A finding names a metric or a database object, so a lineage
            # trace has a subject to start from.
            lineage_available=bool(metric_key or getattr(row, "database_id", None)),
            metric_definition_available=bool(metric_key),
        ),
        evidence=tuple(evidence[:8]),
        # Prompt 35 records this on the row itself; every detector in
        # that queue writes `ai_inference`.
        truth_level=DataTruthLevel.AI_INFERENCE,
        occurred_at=_iso(getattr(row, "created_at", None)),
    )


def recommendation_headline(row: Any) -> Headline:
    """A `recommendation_records` row (Prompts 17/18).

    Carries the engine's own evidence, limitations and confidence.
    Always `AI_INFERENCE`: a recommendation is a suggested action
    grounded in facts, never itself an observed fact -- Prompt 17's own
    contract.
    """
    evidence = list(evidence_from_payload(getattr(row, "evidence", None)))
    measurable = getattr(row, "measurable_impact", None)
    if measurable:
        evidence.append(
            EvidenceItem(
                label="measurable impact",
                value=_text(measurable, default="", limit=2000),
                truth_level=DataTruthLevel.AI_INFERENCE,
            )
        )

    scaled = int(round(min(max(float(getattr(row, "confidence", 0.0) or 0.0), 0.0), 1.0) * 100))
    return Headline(
        headline_id=f"recommendation:{getattr(row, 'id', '')}",
        kind=HeadlineKind.RECOMMENDATION,
        title=_text(getattr(row, "claim_text", ""), default="Recommendation", limit=400),
        detail=_text(getattr(row, "action", ""), default=""),
        # The engine records a confidence, not a 0-100 severity. Scaling
        # it keeps one comparable ordering across sections without
        # inventing a severity the engine never assigned -- and the real
        # confidence stays visible on the provenance.
        severity=scaled,
        severity_tier=_tier(scaled),
        status=_text(getattr(row, "status", ""), default=""),
        provenance=Provenance(
            source=SourceCapability.RECOMMENDATION_ENGINE,
            source_record_id=str(getattr(row, "id", "")),
            period=NOT_RECORDED,
            metric_concept_key=None,
            confidence=_clamp_confidence(getattr(row, "confidence", None)),
            database_id=_text(getattr(row, "database_id", None), default="") or None,
            recorded_at=_iso(
                getattr(row, "generated_at", None) or getattr(row, "created_at", None)
            ),
            engine_version=_text(getattr(row, "engine_version", None)),
            limitations=tuple(
                str(item)[:500] for item in list(getattr(row, "limitations", []) or [])[:6]
            ),
            lineage_available=bool(getattr(row, "database_id", None)),
            metric_definition_available=False,
        ),
        evidence=tuple(evidence[:8]),
        truth_level=DataTruthLevel.AI_INFERENCE,
        occurred_at=_iso(getattr(row, "generated_at", None)),
    )


def workflow_headline(row: Any) -> Headline:
    """A `workflows` row (Prompt 49).

    `DATABASE_FACT`: a workflow's status, owner and due date are
    observed facts about this platform's own records -- unlike the
    recommendation that may have prompted it, which stays an inference.
    Conflating the two would let "we decided to do this" read as "this
    was the right thing to do".
    """
    status = _text(getattr(row, "status", ""), default="")
    outcome = _text(getattr(row, "outcome_summary", None), default="")

    evidence: list[EvidenceItem] = [
        EvidenceItem(label="status", value=status, truth_level=DataTruthLevel.DATABASE_FACT)
    ]
    for label, value in (
        ("due date", _iso(getattr(row, "due_date", None))),
        ("external reference", _text(getattr(row, "external_reference_id", None), default="")),
        ("outcome", outcome),
        ("measured impact", _text(getattr(row, "outcome_measured_impact", None), default="")),
    ):
        if value and value != NOT_RECORDED:
            evidence.append(
                EvidenceItem(
                    label=label, value=value[:2000], truth_level=DataTruthLevel.DATABASE_FACT
                )
            )

    return Headline(
        headline_id=f"workflow:{getattr(row, 'id', '')}",
        kind=HeadlineKind.OUTCOME if outcome else HeadlineKind.ACTION,
        title=_text(getattr(row, "title", ""), default="Workflow", limit=400),
        detail=_text(getattr(row, "description", ""), default=""),
        severity=0,
        severity_tier="low",
        status=status,
        provenance=Provenance(
            source=SourceCapability.WORKFLOW_AUTOMATION,
            source_record_id=str(getattr(row, "id", "")),
            recorded_at=_iso(getattr(row, "updated_at", None) or getattr(row, "created_at", None)),
            lineage_available=False,
            metric_definition_available=False,
        ),
        evidence=tuple(evidence[:8]),
        truth_level=DataTruthLevel.DATABASE_FACT,
        occurred_at=_iso(getattr(row, "created_at", None)),
    )


def governance_headline(row: Any) -> Headline:
    """A `governance_asset_versions` row awaiting a decision (Prompt 44).

    `DATABASE_FACT` for the same reason a workflow is: the stage of an
    asset version is an observed fact about this platform's own
    governance records.
    """
    stage = _text(getattr(row, "stage", ""), default="")
    return Headline(
        headline_id=f"governance:{getattr(row, 'id', '')}",
        kind=HeadlineKind.PENDING_DECISION,
        title=(
            f"{_text(getattr(row, 'display_name', ''), default='Asset', limit=200)} "
            f"awaiting {stage or 'a decision'}"
        )[:400],
        detail=_text(getattr(row, "description", ""), default=""),
        severity=0,
        severity_tier="low",
        status=stage,
        provenance=Provenance(
            source=SourceCapability.GOVERNANCE_CENTER,
            source_record_id=str(getattr(row, "id", "")),
            recorded_at=_iso(getattr(row, "updated_at", None) or getattr(row, "created_at", None)),
            lineage_available=False,
            metric_definition_available=False,
        ),
        evidence=(
            EvidenceItem(
                label="asset",
                value=_text(getattr(row, "asset_key", ""), default="")[:2000],
                truth_level=DataTruthLevel.DATABASE_FACT,
            ),
            EvidenceItem(label="stage", value=stage, truth_level=DataTruthLevel.DATABASE_FACT),
        ),
        truth_level=DataTruthLevel.DATABASE_FACT,
        occurred_at=_iso(getattr(row, "created_at", None)),
    )


def onboarding_headline(row: Any) -> Headline:
    """An `onboarding_jobs` row awaiting SME review (Prompt 08)."""
    status = _text(getattr(row, "status", ""), default="")
    return Headline(
        headline_id=f"onboarding:{getattr(row, 'id', '')}",
        kind=HeadlineKind.PENDING_DECISION,
        title=(
            f"Onboarding '{_text(getattr(row, 'label', ''), default='job', limit=200)}' "
            f"is {status or 'in progress'}"
        )[:400],
        detail="",
        severity=0,
        severity_tier="low",
        status=status,
        provenance=Provenance(
            source=SourceCapability.ONBOARDING,
            source_record_id=str(getattr(row, "id", "")),
            recorded_at=_iso(getattr(row, "updated_at", None) or getattr(row, "created_at", None)),
            lineage_available=False,
            metric_definition_available=False,
        ),
        evidence=(
            EvidenceItem(label="status", value=status, truth_level=DataTruthLevel.DATABASE_FACT),
        ),
        truth_level=DataTruthLevel.DATABASE_FACT,
        occurred_at=_iso(getattr(row, "created_at", None)),
    )


__all__ = [
    "finding_headline",
    "governance_headline",
    "onboarding_headline",
    "recommendation_headline",
    "workflow_headline",
]
