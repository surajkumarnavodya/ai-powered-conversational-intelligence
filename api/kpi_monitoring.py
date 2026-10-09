"""`/kpi-monitoring` -- proactive KPI monitoring's REST surface
(Prompt 50, `50_PROACTIVE_AI_ANALYST_CONTRACT.md`).

Two routes:

- `POST /kpi-monitoring/scan` -- runs one monitoring pass over
  caller-supplied series, persists the findings into Prompt 35's shared
  review queue, and returns the ranked changes plus per-recipient alert
  bundles.
- `GET /kpi-monitoring/findings` -- this tenant's open KPI findings from
  that same queue.

**Neither route queries a monitored database and neither calls an LLM.**
The series and dimension breakdowns arrive in the request body, already
fetched by the caller through this application's governed path
(`POST /execute`). That is the structural reason monitoring cannot
bypass `agent/sql_validator.py`: it has no way to produce a row.

The one database this feature *does* read is the identity database, for
two tenant-scoped reads it genuinely needs: the caller's own published
catalog metrics (to decide which policies target an approved metric) and
the currently-open finding keys (for alert-level duplicate suppression).
Both are already-tenant-filtered repository calls.

Deciding a KPI finding (accept/dismiss/rollback) is deliberately **not**
re-implemented here: those are `/semantic-intelligence/findings/...`'s
existing, already-tested routes over the same rows. Adding a second set
would be a second lifecycle to keep in step -- the same "link, don't
duplicate" call `api/schema_drift.py` already made for its own findings.

Authorization mirrors `api/schema_drift.py` exactly: a local account,
`CatalogAction.REVIEW` on the caller's own tenant (monitoring reads and
writes governed catalog-adjacent findings), and a database the caller's
tenant may actually use -- a cross-tenant or unknown `database_id` is
the same 404 a genuinely nonexistent one gets, never a distinguishable
403.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from identity.models import SemanticFinding, User
from identity.repositories.semantic_catalog import list_entries
from identity.repositories.semantic_intelligence import list_findings, upsert_findings
from identity.repositories.users import get_user_permissions, get_user_roles
from semantic.catalog_policy import CatalogAction, authorize_catalog_action
from sqlalchemy.orm import Session

from api.identity_authz import require_local_user
from api.kpi_monitoring_schemas import (
    AlertBundleOut,
    AlertDecisionOut,
    ChangeOut,
    ContributorOut,
    EvidenceOut,
    FindingOut,
    PolicyViolationOut,
    ScanRequest,
    ScanResponse,
)
from api.rate_limit import enforce_api_action_rate_limit
from config.settings import get_settings
from kpi_monitoring.engine import MetricSeries, MonitorRun, monitor_kpis
from kpi_monitoring.findings import is_kpi_kind
from kpi_monitoring.models import (
    KPI_MONITORING_VERSION,
    AlertPreference,
    BusinessCalendar,
    ComparisonPeriod,
    MonitoringPolicy,
    QuietPeriod,
)
from kpi_monitoring.reports import render_markdown
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kpi-monitoring", tags=["kpi-monitoring"])

DISABLED_DETAIL = "Proactive KPI monitoring is not enabled on this server."

#: How many of the tenant's own open KPI findings a scan echoes back.
_TOP_FINDINGS_IN_SCAN = 20


def _require_enabled() -> None:
    if not get_settings().enable_kpi_monitoring:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DISABLED_DETAIL)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")


def _actor_tenant(user: User) -> str:
    tenant_id = resolve_actor_tenant_id(user)
    if tenant_id is None:
        raise _forbidden()
    return tenant_id


def _authorize_review(user: User, session: Session, tenant_id: str) -> None:
    """The same catalog-review gate `api/schema_drift.py` applies to the
    same shared review queue -- a KPI finding is a governed review item,
    so it answers to the governance role, not to `Permission.ASK`."""
    decision = authorize_catalog_action(
        actor_permissions=get_user_permissions(session, user.id),
        actor_tenant_id=tenant_id,
        entry_tenant_id=tenant_id,
        action=CatalogAction.REVIEW,
    )
    if not decision.allowed:
        if decision.reason == "cross_tenant":
            raise _not_found()
        raise _forbidden()


def _allowed_database(user: User, database_id: str) -> str:
    allowed = {d.name for d in get_settings().databases_for_tenant(_actor_tenant(user))}
    if database_id not in allowed:
        raise _not_found()
    return database_id


def _to_policies(payload: ScanRequest) -> tuple[MonitoringPolicy, ...]:
    return tuple(
        MonitoringPolicy(
            policy_id=item.policy_id,
            metric_concept_key=item.metric_concept_key,
            comparison=ComparisonPeriod(item.comparison),
            min_change_percent=item.min_change_percent,
            lower_threshold=item.lower_threshold,
            upper_threshold=item.upper_threshold,
            seasonal_lag=item.seasonal_lag,
            trailing_window=item.trailing_window,
            dimension_columns=tuple(item.dimension_columns),
            enabled=item.enabled,
        )
        for item in payload.policies
    )


def _to_series(payload: ScanRequest) -> dict[str, MetricSeries]:
    series: dict[str, MetricSeries] = {}
    for item in payload.series:
        series[item.policy_id] = MetricSeries(
            policy_id=item.policy_id,
            points=tuple((period, value) for period, value in item.points),
            current_breakdown=(
                (
                    list(item.current_breakdown.columns),
                    [tuple(r) for r in item.current_breakdown.rows],
                )
                if item.current_breakdown
                else None
            ),
            baseline_breakdown=(
                (
                    list(item.baseline_breakdown.columns),
                    [tuple(r) for r in item.baseline_breakdown.rows],
                )
                if item.baseline_breakdown
                else None
            ),
            value_column=item.value_column,
        )
    return series


def _evidence_out(run: MonitorRun, key: str) -> EvidenceOut:
    evidence = run.result.evidence_by_key[key]
    return EvidenceOut(
        has_sufficient_evidence=evidence.has_sufficient_evidence,
        baseline_total=evidence.baseline_total,
        current_total=evidence.current_total,
        magnitude=evidence.magnitude,
        magnitude_percent=evidence.magnitude_percent,
        contributors=tuple(
            ContributorOut(
                label=contributor.label,
                current_value=contributor.current_value,
                baseline_value=contributor.baseline_value,
                contribution=contributor.contribution,
                contribution_percent=contributor.contribution_percent,
            )
            for contributor in evidence.contributors
        ),
        confidence=evidence.confidence,
        limitations=evidence.limitations,
        dimension_columns=evidence.dimension_columns,
        truth_level=evidence.truth_level.value,
    )


def _changes_out(run: MonitorRun) -> tuple[ChangeOut, ...]:
    out: list[ChangeOut] = []
    for finding in run.findings:
        change = run.changes_by_key[finding.key]
        out.append(
            ChangeOut(
                finding_key=finding.key,
                policy_id=change.policy_id,
                metric_concept_key=change.metric_concept_key,
                metric_display_name=change.metric_display_name,
                kind=change.kind.value,
                period=change.period,
                current_value=change.current_value,
                baseline_value=change.baseline_value,
                baseline_label=change.baseline_label,
                comparison=change.comparison.value,
                change_absolute=change.change_absolute,
                change_percent=change.change_percent,
                threshold_crossed=change.threshold_crossed,
                detection_methods=change.detection_methods,
                direction=change.direction,
                risk_score=finding.risk_score,
                risk_tier=finding.risk_tier,
                rank_reasons=finding.reasons,
                title=finding.title,
                truth_level=change.truth_level.value,
                evidence=_evidence_out(run, finding.key),
            )
        )
    return tuple(out)


def _finding_out(row: SemanticFinding) -> FindingOut:
    content = row.content or {}
    return FindingOut(
        id=str(row.id),
        kind=row.kind,
        title=row.title,
        detail=content.get("detail", ""),
        status=row.status,
        risk_score=row.risk_score,
        risk_tier=row.risk_tier,
        confidence=row.confidence,
        truth_level=row.truth_level,
        version=row.version,
        reasons=list(content.get("reasons", [])),
        subjects=list(content.get("subjects", [])),
        evidence=list(content.get("evidence", [])),
        decision_note=row.decision_note,
    )


@router.post("/scan", response_model=ScanResponse)
def scan(
    payload: ScanRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ScanResponse:
    """Runs one monitoring pass over the supplied series.

    Validates each policy against this tenant's published catalog
    metrics, detects meaningful movement, investigates it, ranks what is
    left by impact, decides alerts, and (unless `persist=false`) writes
    the findings into the shared review queue.

    Never queries a monitored database and never calls an LLM.
    """
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "kpi_monitoring_scan", settings)
    tenant_id = _actor_tenant(user)
    _authorize_review(user, session, tenant_id)
    database_id = _allowed_database(user, payload.database_id)

    catalog_rows = list_entries(session, tenant_id, database_id=database_id, status="published")

    # Alert-level duplicate suppression needs to know what is already
    # open and undecided for this tenant -- read here rather than inside
    # the engine, which stays database-free.
    previously_open = frozenset(
        row.finding_key
        for row in list_findings(
            session, tenant_id=tenant_id, database_id=database_id, status="open"
        )
        if is_kpi_kind(row.kind)
    )

    run = monitor_kpis(
        database_id=database_id,
        policies=_to_policies(payload),
        catalog_entries=catalog_rows,
        series_by_policy=_to_series(payload),
        calendar=(
            BusinessCalendar(
                name=payload.calendar.name,
                non_business_periods=frozenset(payload.calendar.non_business_periods),
            )
            if payload.calendar
            else None
        ),
        quiet_periods=tuple(
            QuietPeriod(name=item.name, periods=frozenset(item.periods), reason=item.reason)
            for item in payload.quiet_periods
        ),
        preferences=tuple(
            AlertPreference(
                user_id=item.user_id,
                min_risk_tier=item.min_risk_tier,
                muted_metric_keys=frozenset(item.muted_metric_keys),
                max_alerts_per_run=item.max_alerts_per_run,
            )
            for item in payload.preferences
        ),
        previously_open_keys=previously_open,
        caller_roles=get_user_roles(session, user.id),
        tenant_id=tenant_id,
        settings=settings,
    )

    created = updated = unchanged = 0
    if payload.persist and run.findings:
        summary = upsert_findings(
            session,
            tenant_id=tenant_id,
            database_id=database_id,
            findings=list(run.findings),
            now=datetime.now(UTC),
        )
        created, updated, unchanged = summary.created, summary.updated, summary.unchanged

    return ScanResponse(
        database_id=database_id,
        policies_evaluated=run.result.policies_evaluated,
        findings_detected=len(run.findings),
        truncated=run.result.truncated,
        persisted=bool(payload.persist and run.findings),
        created=created,
        updated=updated,
        unchanged=unchanged,
        violations=tuple(
            PolicyViolationOut(
                policy_id=violation.policy_id, code=violation.code, detail=violation.detail
            )
            for violation in run.result.violations
        ),
        series_skipped=run.result.series_skipped,
        changes=_changes_out(run),
        alert_bundles=tuple(
            AlertBundleOut(
                user_id=bundle.user_id,
                alerted_count=bundle.alerted_count,
                suppressed_count=bundle.suppressed_count,
                decisions=tuple(
                    AlertDecisionOut(
                        finding_key=decision.finding_key,
                        metric_concept_key=decision.metric_concept_key,
                        period=decision.period,
                        risk_score=decision.risk_score,
                        risk_tier=decision.risk_tier,
                        alerted=decision.alerted,
                        suppressed_by=tuple(r.value for r in decision.suppressed_by),
                        suppression_detail=decision.suppression_detail,
                    )
                    for decision in bundle.decisions
                ),
            )
            for bundle in run.alert_bundles
        ),
        report_markdown=render_markdown(run),
        engine_version=KPI_MONITORING_VERSION,
    )


@router.get("/findings", response_model=list[FindingOut])
def findings(
    request: Request,
    database_id: str = Query(..., min_length=1, max_length=200),
    status_filter: str | None = Query(default=None, alias="status"),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[FindingOut]:
    """This tenant's KPI findings, most impactful first.

    A kind-scoped read of the same shared queue
    `/semantic-intelligence/findings` exposes unscoped -- deciding one
    stays that router's job.
    """
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "kpi_monitoring_findings", settings)
    tenant_id = _actor_tenant(user)
    _authorize_review(user, session, tenant_id)
    resolved = _allowed_database(user, database_id)

    rows = list_findings(session, tenant_id=tenant_id, database_id=resolved, status=status_filter)
    return [_finding_out(row) for row in rows if is_kpi_kind(row.kind)][:_TOP_FINDINGS_IN_SCAN]
