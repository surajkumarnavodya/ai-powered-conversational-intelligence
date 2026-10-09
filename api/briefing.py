"""`/briefing` -- the executive AI briefing's REST surface (Prompt 51,
`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`).

One route: `POST /briefing/generate` assembles a daily, weekly or
monthly briefing for the caller's own tenant and returns both the typed
claims and the single markdown rendering.

**The route never queries a monitored database and never calls an LLM.**
The only database it reads is the identity database, for two
tenant-scoped repository reads it genuinely needs -- this tenant's open
KPI/schema-drift findings from the shared review queue, and its
unresolved recommendation records. Data-quality findings arrive in the
request body, because nothing in this codebase persists a data-quality
scan. That is the structural reason a briefing cannot bypass
`agent/sql_validator.py`: it has no way to produce a row.

**Authorization is per section, not all-or-nothing.** A briefing
aggregates records that answer to two different existing permissions,
and an aggregate must never reveal what the caller could not read
directly:

- KPI and schema-drift findings are governed review items, so they
  answer to `semantic.catalog_policy.CatalogAction.REVIEW` -- the same
  gate `api/kpi_monitoring.py` and `api/schema_drift.py` already apply
  to the same rows.
- Recommendation records answer to
  `recommendation.governance_policy.RecommendationAction.VIEW` -- the
  same gate `api/recommendation_governance.py` already applies.

A caller holding one but not the other gets a briefing with the other
source **omitted and named** in `coverage`/`limitations`, never silently
absent. A caller holding neither gets a 403: there would be nothing
governed left to brief on.

No new permission was added. A briefing is a read of records whose
access rules already exist, and inventing a `BRIEFING_READ` code would
have created a second, drifting answer to "who may see this finding".
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from identity.models import RecommendationRecord, SemanticFinding, User
from identity.repositories.recommendation_governance import list_records, owner_display_names
from identity.repositories.semantic_intelligence import list_findings
from identity.repositories.users import get_user_permissions
from recommendation.governance_policy import (
    RecommendationAction,
    authorize_recommendation_action,
)
from semantic.catalog_policy import CatalogAction, authorize_catalog_action
from sqlalchemy.orm import Session

from api.briefing_schemas import (
    BriefingItemOut,
    BriefingRequest,
    BriefingResponse,
    EvidenceLinkOut,
    SourceCoverageOut,
    StaleInputOut,
    WithheldClaimOut,
)
from api.identity_authz import require_local_user
from api.rate_limit import enforce_api_action_rate_limit
from briefing.claims import UNRESOLVED_RECOMMENDATION_STATUSES
from briefing.engine import build_briefing
from briefing.freshness import parse_instant
from briefing.inputs import (
    DataQualityFindingInput,
    KpiFindingInput,
    RecommendationInput,
    SchemaDriftFindingInput,
)
from briefing.models import BriefingPeriod, ExecutiveBriefing
from briefing.reports import render_markdown
from config.settings import get_settings
from kpi_monitoring.findings import is_kpi_kind
from schema_drift.findings import is_schema_drift_kind
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/briefing", tags=["briefing"])

DISABLED_DETAIL = "The executive briefing is not enabled on this server."

#: How many recommendation records are read before filtering to the
#: unresolved ones. A bound, not a page: a briefing shows a ranked,
#: capped summary (`Settings.briefing_max_items_per_section`), so paging
#: further would only add work a reader never sees.
_RECOMMENDATION_READ_LIMIT = 200


def _require_enabled() -> None:
    if not get_settings().enable_executive_briefing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DISABLED_DETAIL)


def _forbidden(detail: str = "Not permitted.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def _actor_tenant(user: User) -> str:
    tenant_id = resolve_actor_tenant_id(user)
    if tenant_id is None:
        raise _forbidden()
    return tenant_id


def _allowed_database(tenant_id: str, database_id: str) -> str:
    """A database this tenant may actually use.

    A cross-tenant or unknown name gets the same 404 a genuinely
    nonexistent one gets -- the anti-enumeration convention
    `api/kpi_monitoring.py` and `api/schema_drift.py` already apply.
    """
    allowed = {database.name for database in get_settings().databases_for_tenant(tenant_id)}
    if database_id not in allowed:
        raise _not_found()
    return database_id


def _may_read_findings(permissions: frozenset[str], tenant_id: str) -> bool:
    return authorize_catalog_action(
        actor_permissions=permissions,
        actor_tenant_id=tenant_id,
        entry_tenant_id=tenant_id,
        action=CatalogAction.REVIEW,
    ).allowed


def _may_read_recommendations(permissions: frozenset[str], tenant_id: str) -> bool:
    return authorize_recommendation_action(
        actor_permissions=permissions,
        actor_tenant_id=tenant_id,
        record_tenant_id=tenant_id,
        action=RecommendationAction.VIEW,
    ).allowed


def _tier(value: str) -> Literal["low", "medium", "high"]:
    """Narrows a stored tier string onto the typed set.

    `identity.models.SemanticFinding.risk_tier` is a plain `String(8)`
    written by `semantic.intelligence.risk.score`, so every real row
    already holds one of these three. An unrecognized value is read as
    `"low"` rather than trusted: a briefing must not rank an unknown
    tier as urgent.
    """
    if value == "high":
        return "high"
    if value == "medium":
        return "medium"
    return "low"


def _recommendation_kind(value: str) -> Literal["next_question", "action"]:
    """Narrows a stored recommendation kind onto the typed set.

    The column is an `Enum` constrained to exactly these two, so an
    unrecognized value cannot occur in practice. It is read as
    `"next_question"` -- the non-actionable kind -- so a corrupt row can
    never be promoted into the opportunities section.
    """
    return "action" if value == "action" else "next_question"


def _kpi_input(row: SemanticFinding) -> KpiFindingInput:
    content = row.content or {}
    return KpiFindingInput(
        finding_id=str(row.id),
        kind=row.kind,
        title=row.title or "",
        detail=str(content.get("detail") or "")[:4000],
        status=row.status,
        risk_score=row.risk_score,
        risk_tier=_tier(row.risk_tier),
        confidence=max(0.0, min(1.0, float(row.confidence or 0.0))),
        version=row.version,
        last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
        payload=dict(content.get("payload") or {}),
    )


def _drift_input(row: SemanticFinding) -> SchemaDriftFindingInput:
    content = row.content or {}
    return SchemaDriftFindingInput(
        finding_id=str(row.id),
        kind=row.kind,
        title=row.title or "",
        detail=str(content.get("detail") or "")[:4000],
        status=row.status,
        risk_score=row.risk_score,
        risk_tier=_tier(row.risk_tier),
        confidence=max(0.0, min(1.0, float(row.confidence or 0.0))),
        version=row.version,
        last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
        payload=dict(content.get("payload") or {}),
    )


def _recommendation_input(
    row: RecommendationRecord, owners: dict[uuid.UUID, str]
) -> RecommendationInput:
    evidence = row.evidence if isinstance(row.evidence, list) else []
    limitations = row.limitations if isinstance(row.limitations, list) else []
    return RecommendationInput(
        record_id=str(row.id),
        category=row.category,
        kind=_recommendation_kind(row.kind),
        status=row.status,
        claim_text=(row.claim_text or "(no claim recorded)")[:4000],
        rationale=(row.rationale or None),
        action=(row.action or None),
        affected_entity=(row.affected_entity or None),
        measurable_impact=(row.measurable_impact or None),
        confidence=(
            max(0.0, min(1.0, float(row.confidence))) if row.confidence is not None else None
        ),
        evidence_count=len(evidence),
        limitations=tuple(str(item)[:500] for item in limitations),
        engine_version=row.engine_version or "unknown",
        evidence_version=row.evidence_version or "unknown",
        generated_at=row.generated_at.isoformat() if row.generated_at else None,
        owner_display_name=owners.get(row.owner_user_id) if row.owner_user_id else None,
    )


def _response(briefing: ExecutiveBriefing) -> BriefingResponse:
    return BriefingResponse(
        database_id=briefing.database_id,
        period=briefing.period.value,
        period_label=briefing.period_label,
        as_of=briefing.as_of,
        items=tuple(
            BriefingItemOut(
                section=item.section.value,
                claim_class=item.claim_class.value,
                headline=item.headline,
                detail=item.detail,
                subject=item.subject,
                period_label=item.period_label,
                risk_score=item.risk_score,
                risk_tier=item.risk_tier,
                evidence=tuple(
                    EvidenceLinkOut(
                        source=link.source,
                        reference_kind=link.reference_kind,
                        reference_id=link.reference_id,
                        observed=dict(link.observed),
                        versions=dict(link.versions),
                        recorded_at=link.recorded_at,
                        truth_level=link.truth_level.value,
                    )
                    for link in item.evidence
                ),
                is_stale=item.is_stale,
                stale_reason=item.stale_reason,
                limitations=item.limitations,
                truth_level=item.truth_level.value,
            )
            for item in briefing.items
        ),
        coverage=tuple(
            SourceCoverageOut(
                source=entry.source,
                supplied=entry.supplied,
                input_count=entry.input_count,
                items_produced=entry.items_produced,
                note=entry.note,
            )
            for entry in briefing.coverage
        ),
        empty_sections=tuple(section.value for section in briefing.empty_sections),
        stale_inputs=tuple(
            StaleInputOut(
                source=stale.source,
                reference_id=stale.reference_id,
                recorded_at=stale.recorded_at,
                age_seconds=stale.age_seconds,
                budget_seconds=stale.budget_seconds,
                detail=stale.detail,
            )
            for stale in briefing.stale_inputs
        ),
        withheld_claims=tuple(
            WithheldClaimOut(
                section=problem.section.value,
                headline=problem.headline,
                code=problem.code,
                detail=problem.detail,
            )
            for problem in briefing.withheld_claims
        ),
        limitations=briefing.limitations,
        truncated_sections=tuple(section.value for section in briefing.truncated_sections),
        observed_fact_count=briefing.observed_fact_count,
        estimate_count=briefing.estimate_count,
        recommendation_count=briefing.recommendation_count,
        stale_item_count=briefing.stale_item_count,
        report_markdown=render_markdown(briefing),
        engine_version=briefing.engine_version,
    )


@router.post("/generate", response_model=BriefingResponse)
def generate(
    payload: BriefingRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> BriefingResponse:
    """Assembles one executive briefing for the caller's own tenant.

    Reads this tenant's open KPI/schema-drift findings and unresolved
    recommendation records -- each gated by the permission those records
    already answer to -- combines them with any data-quality findings in
    the request body, and returns the ranked, evidence-linked claims
    plus the markdown rendering.

    Never queries a monitored database and never calls an LLM. Persists
    nothing.
    """
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "briefing_generate", settings)

    tenant_id = _actor_tenant(user)
    database_id = _allowed_database(tenant_id, payload.database_id)

    if len(payload.data_quality_findings) > settings.briefing_max_data_quality_findings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "At most "
                f"{settings.briefing_max_data_quality_findings} data-quality findings "
                "may be supplied in one briefing."
            ),
        )

    permissions = get_user_permissions(session, user.id)
    may_read_findings = _may_read_findings(permissions, tenant_id)
    may_read_recommendations = _may_read_recommendations(permissions, tenant_id)
    if not may_read_findings and not may_read_recommendations:
        raise _forbidden(
            "A briefing needs permission to read governed review items or "
            "recommendation records."
        )

    omitted: list[tuple[str, str]] = []
    kpi_inputs: list[KpiFindingInput] = []
    drift_inputs: list[SchemaDriftFindingInput] = []

    if may_read_findings:
        rows = list_findings(session, tenant_id=tenant_id, database_id=database_id, status="open")
        kpi_inputs = [_kpi_input(row) for row in rows if is_kpi_kind(row.kind)]
        drift_inputs = [_drift_input(row) for row in rows if is_schema_drift_kind(row.kind)]
    else:
        reason = "omitted: this account may not read governed review items."
        omitted.extend([("kpi_findings", reason), ("schema_drift_findings", reason)])

    recommendation_inputs: list[RecommendationInput] = []
    if may_read_recommendations:
        records = [
            row
            for row in list_records(
                session,
                tenant_id,
                database_id=database_id,
                limit=_RECOMMENDATION_READ_LIMIT,
            )
            if row.status in UNRESOLVED_RECOMMENDATION_STATUSES
        ]
        owner_ids = {row.owner_user_id for row in records if row.owner_user_id}
        owners = owner_display_names(session, owner_ids) if owner_ids else {}
        recommendation_inputs = [_recommendation_input(row, owners) for row in records]
    else:
        omitted.append(
            (
                "recommendations",
                "omitted: this account may not read recommendation records.",
            )
        )

    as_of = parse_instant(payload.as_of) or datetime.now(UTC)

    briefing = build_briefing(
        database_id=database_id,
        period=BriefingPeriod(payload.period),
        as_of=as_of,
        period_label=payload.period_label,
        kpi_findings=kpi_inputs,
        schema_drift_findings=drift_inputs,
        recommendations=recommendation_inputs,
        data_quality_findings=[
            DataQualityFindingInput(
                dimension=item.dimension,
                severity=item.severity,
                table=item.table,
                column=item.column,
                claim_text=item.claim_text,
                formula=item.formula,
                observed=dict(item.observed),
                engine_version=item.engine_version,
                observed_at=item.observed_at,
            )
            for item in payload.data_quality_findings
        ],
        omitted_sources=tuple(omitted),
        settings=settings,
    )
    return _response(briefing)


__all__ = ["router"]
