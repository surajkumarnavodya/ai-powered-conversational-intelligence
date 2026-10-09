"""`/decision-center` -- the Executive Decision Center's REST surface
(Prompt 52, `52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`).

One route, `GET /decision-center/briefing`, which assembles everything
Prompts 33-50 already record into one coherent view.

**It reads only governed records this tenant already holds**, through
repository functions that are themselves tenant-filtered -- findings,
recommendations, workflows, governance asset versions, onboarding jobs
and the semantic catalog. It never queries a monitored database, never
generates SQL and never calls an LLM, so `agent/sql_validator.py` and
every data control around it are untouched and unreachable from here.

**One unavailable source never fails the briefing.** Each read is
wrapped: a repository that raises is recorded in `unavailable_sources`
with its reason and the rest of the briefing is assembled. The
alternative -- a 500 because one capability is misconfigured -- would
make the executive view the least reliable screen in the product,
precisely when it is most needed.

**Authorization** is `Permission.ADMIN_DASHBOARD_READ` (admin, auditor,
manager), the same tier `api/tenant_admin.py`'s own read routes use.
This aggregates governed review items across every capability, so it
answers to the dashboard-read permission rather than to
`Permission.ASK`. Personalization is resolved from the viewer's *own*
server-side capabilities and never from anything the client sends --
there is no `profile` request parameter, by design.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from identity.models import User
from identity.rbac import Permission as IdentityPermission
from identity.repositories.governance import list_versions
from identity.repositories.onboarding import list_jobs_for_tenant
from identity.repositories.recommendation_governance import list_records
from identity.repositories.semantic_catalog import list_entries
from identity.repositories.semantic_intelligence import list_findings
from identity.repositories.users import get_user_permissions, get_user_roles
from identity.repositories.workflow import list_workflows
from sqlalchemy.orm import Session

from agent.authz import permissions_for
from api.decision_center_schemas import (
    BriefingResponse,
    ChainLinkOut,
    ChainOut,
    EvidenceItemOut,
    HeadlineOut,
    HealthIndicatorOut,
    MetricDefinitionOut,
    ProvenanceOut,
    SectionOut,
)
from api.identity_authz import require_local_user
from api.rate_limit import enforce_api_action_rate_limit
from config.settings import get_settings
from decision_center.briefing import BriefingInputs, build_briefing
from decision_center.models import ExecutiveBriefing, Headline
from decision_center.personalization import profile_for_capabilities
from security.navigation import resolve_capabilities
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/decision-center", tags=["decision-center"])

DISABLED_DETAIL = "The Executive Decision Center is not enabled on this server."

#: Governance stages that still need a human. `deployed`/`retired`/
#: `rejected`/`rolled_back` are settled and are not pending decisions.
_PENDING_GOVERNANCE_STAGES = ("draft", "testing", "evaluating", "approved")

#: Onboarding statuses that still need a human.
_PENDING_ONBOARDING_STATUSES = ("awaiting_review", "discovering", "created")


def _require_enabled() -> None:
    if not get_settings().enable_decision_center:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DISABLED_DETAIL)


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")


def _actor_tenant(user: User) -> str:
    tenant_id = resolve_actor_tenant_id(user)
    if tenant_id is None:
        raise _forbidden()
    return tenant_id


def _authorize(user: User, session: Session) -> None:
    if IdentityPermission.ADMIN_DASHBOARD_READ not in get_user_permissions(session, user.id):
        raise _forbidden()


def _allowed_database(user: User, database_id: str | None) -> str | None:
    """A database this tenant may not use is the same 404 a nonexistent
    one gets -- the anti-enumeration convention every tenant-scoped route
    in this codebase already follows. `None` means "every database this
    tenant may use", which is the executive default."""
    if database_id is None:
        return None
    allowed = {d.name for d in get_settings().databases_for_tenant(_actor_tenant(user))}
    if database_id not in allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return database_id


def _safe(label: str, failures: list[str], loader, default):  # noqa: ANN001, ANN202
    """Runs one repository read; records a failure instead of raising.

    The exception type is logged, never its message -- a repository error
    can carry driver text, and this response is rendered on a dashboard.
    """
    try:
        return loader()
    except Exception as exc:  # noqa: BLE001 - one source must not fail the briefing
        logger.warning(
            "[decision_center] %s could not be read for this briefing: %s",
            label,
            type(exc).__name__,
        )
        failures.append(f"{label}: could not be read for this briefing")
        return default


def _headline_out(headline: Headline) -> HeadlineOut:
    provenance = headline.provenance
    return HeadlineOut(
        headline_id=headline.headline_id,
        kind=headline.kind.value,
        title=headline.title,
        detail=headline.detail,
        severity=headline.severity,
        severity_tier=headline.severity_tier,
        status=headline.status,
        occurred_at=headline.occurred_at,
        truth_level=headline.truth_level.value,
        provenance=ProvenanceOut(
            source=provenance.source.value,
            source_record_id=provenance.source_record_id,
            period=provenance.period,
            metric_concept_key=provenance.metric_concept_key,
            confidence=provenance.confidence,
            database_id=provenance.database_id,
            recorded_at=provenance.recorded_at,
            engine_version=provenance.engine_version,
            limitations=provenance.limitations,
            lineage_available=provenance.lineage_available,
            metric_definition_available=provenance.metric_definition_available,
        ),
        evidence=tuple(
            EvidenceItemOut(label=item.label, value=item.value, truth_level=item.truth_level.value)
            for item in headline.evidence
        ),
    )


def _metric_definitions(
    briefing: ExecutiveBriefing, catalog_rows: list
) -> dict[str, MetricDefinitionOut]:
    """Resolves the definition of every governed metric a headline names.

    Resolved once per briefing rather than per headline, so the
    metric-definition drill-down costs no extra request. Only *published*
    entries are offered: a draft definition is not what the number was
    computed against, and showing one would misrepresent the metric.
    """
    wanted = {
        headline.provenance.metric_concept_key
        for section in briefing.sections
        for headline in section.headlines
        if headline.provenance.metric_concept_key
    }
    if not wanted:
        return {}

    definitions: dict[str, MetricDefinitionOut] = {}
    for row in catalog_rows:
        key = str(getattr(row, "concept_key", "") or "")
        if key not in wanted or key in definitions:
            continue
        definitions[key] = MetricDefinitionOut(
            concept_key=key,
            business_name=str(getattr(row, "business_name", "") or key),
            description=str(getattr(row, "description", "") or ""),
            status=str(getattr(row, "status", "") or ""),
            version=int(getattr(row, "version", 1) or 1),
            owner=getattr(row, "owner", None),
            dimensions=tuple(getattr(row, "dimensions", ()) or ()),
            confidence=getattr(row, "confidence", None),
        )
    return definitions


@router.get("/briefing", response_model=BriefingResponse)
def briefing(
    request: Request,
    database_id: str | None = Query(default=None, max_length=200),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> BriefingResponse:
    """Assembles this tenant's executive briefing.

    Reads only governed records this tenant already holds. Never queries
    a monitored database, generates SQL, or calls an LLM.
    """
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "decision_center_briefing", settings)
    _authorize(user, session)
    tenant_id = _actor_tenant(user)
    resolved_db = _allowed_database(user, database_id)

    failures: list[str] = []

    findings = _safe(
        "findings",
        failures,
        lambda: list_findings(session, tenant_id=tenant_id, database_id=resolved_db, status="open"),
        [],
    )
    recommendations = _safe(
        "recommendations",
        failures,
        lambda: list_records(session, tenant_id, database_id=resolved_db),
        [],
    )
    workflows = _safe("workflows", failures, lambda: list_workflows(session, tenant_id), [])
    governance_versions = _safe(
        "governance versions",
        failures,
        lambda: [
            version
            for stage in _PENDING_GOVERNANCE_STAGES
            for version in list_versions(session, tenant_id, stage=stage)
        ],
        [],
    )
    onboarding_jobs = _safe(
        "onboarding jobs",
        failures,
        lambda: [
            job
            for job in list_jobs_for_tenant(session, tenant_id)
            if str(getattr(job, "status", "")) in _PENDING_ONBOARDING_STATUSES
        ],
        [],
    )
    catalog_rows = _safe(
        "semantic catalog",
        failures,
        lambda: list_entries(session, tenant_id, database_id=resolved_db, status="published"),
        [],
    )

    # Personalization comes from the viewer's own server-resolved
    # capabilities -- never from a client-supplied parameter.
    capabilities = resolve_capabilities(
        agent_permissions=permissions_for(get_user_roles(session, user.id)),
        identity_permissions=get_user_permissions(session, user.id),
    )
    profile = profile_for_capabilities(tuple(capabilities))

    assembled = build_briefing(
        tenant_id=tenant_id,
        inputs=BriefingInputs(
            findings=findings,
            recommendations=recommendations,
            workflows=workflows,
            governance_versions=governance_versions,
            onboarding_jobs=onboarding_jobs,
            unavailable=tuple(failures),
        ),
        profile=profile,
        database_id=resolved_db,
        max_chains=settings.decision_center_max_chains,
    )

    return BriefingResponse(
        tenant_id=assembled.tenant_id,
        database_id=assembled.database_id,
        profile_id=assembled.profile_id,
        generated_at=assembled.generated_at,
        health=tuple(
            HealthIndicatorOut(
                key=indicator.key,
                label=indicator.label,
                value=indicator.value,
                unit=indicator.unit,
                direction=indicator.direction,
                detail=indicator.detail,
                truth_level=indicator.truth_level.value,
            )
            for indicator in assembled.health
        ),
        sections=tuple(
            SectionOut(
                section=section.section.value,
                title=section.title,
                headlines=tuple(_headline_out(headline) for headline in section.headlines),
                total_available=section.total_available,
                empty_reason=section.empty_reason,
            )
            for section in assembled.sections
        ),
        chains=tuple(
            ChainOut(
                chain_id=chain.chain_id,
                is_complete=chain.is_complete,
                furthest_stage=(
                    chain.furthest_stage.value if chain.furthest_stage is not None else None
                ),
                break_reason=chain.break_reason,
                links=tuple(
                    ChainLinkOut(
                        stage=link.stage.value,
                        present=link.present,
                        headline_id=link.headline_id,
                        summary=link.summary,
                        link_type=link.link_type.value if link.link_type else None,
                        absent_reason=link.absent_reason,
                    )
                    for link in chain.links
                ),
                truth_level=chain.truth_level.value,
            )
            for chain in assembled.chains
        ),
        metric_definitions=_metric_definitions(assembled, catalog_rows),
        contributing_sources=tuple(source.value for source in assembled.contributing_sources),
        unavailable_sources=assembled.unavailable_sources,
        engine_version=assembled.engine_version,
    )
