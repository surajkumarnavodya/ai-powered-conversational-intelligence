"""`POST /schema-drift/scan`, `GET /schema-drift/findings`,
`GET /schema-drift/findings/{id}` -- the Schema Drift Impact Analysis
REST surface (Prompt 43, `43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md`).

**Deliberately does not duplicate decision/rollback routes.** A
schema-drift finding is persisted as an ordinary `identity.models
.SemanticFinding` row (see `schema_drift/__init__.py`'s own docstring for
the full reuse rationale) -- reviewing one (accept/dismiss) or restoring
an earlier version of one already works, unmodified, through the
existing `POST /semantic-intelligence/findings/{id}/decision` and
`POST /semantic-intelligence/findings/{id}/rollback` routes, since those
operate on any finding row by id regardless of which detector produced
it. Likewise, "metadata rollback" of a governed catalog entry a drift
finding threatens is the existing `POST /semantic-intelligence/entries
/{entry_id}/rollback` route (`identity.repositories.semantic_intelligence
.rollback_catalog_entry` -- creates a new DRAFT from an older published
version; the published row is never touched, and the draft still needs
the catalog's normal review-and-publish approval before it goes live).
This router's own surface is therefore narrow: `/scan` (genuinely new
behavior) and `/findings` (a kind-scoped, read-only convenience view over
the same shared table Prompt 35's own `/semantic-intelligence/findings`
already exposes unscoped).

Authorization reuses `semantic.catalog_policy.authorize_catalog_action`
(`CatalogAction.REVIEW`) -- the identical tenant-scoped ABAC check
`api/semantic_intelligence.py`'s own `/run`/`/findings` routes already
make, since triaging a drift finding is the same kind of domain-judgment
review work. A resource in another tenant returns 404, not 403 (the same
anti-enumeration posture every other catalog-adjacent route in this
codebase already establishes).

`/scan` live-introspects the caller's own tenant-scoped database (the
identical, already-live pattern `api/lineage.py`'s own `/graph` route
uses) and reads that tenant's own *published* catalog entries to build
the impact-assessment lineage graph -- declared foreign keys only
(`db.relationship_inference`'s own lower-confidence candidates are
deliberately not included here, keeping a scan's own cost bounded and its
impact claims limited to `DATABASE_FACT`/`CONFIRMED_BUSINESS_TRUTH`
edges, never a second inference layered under this one).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from identity.models import SemanticFinding, User
from identity.repositories.semantic_catalog import entry_to_snapshot, list_entries
from identity.repositories.semantic_intelligence import get_finding, list_findings, upsert_findings
from identity.repositories.users import get_user_permissions
from semantic.catalog_policy import CatalogAction, authorize_catalog_action
from sqlalchemy.orm import Session

from api.identity_authz import require_local_user
from api.rate_limit import enforce_api_action_rate_limit
from api.schema_drift_schemas import FindingOut, ScanOut, ScanRequest
from config.settings import get_settings
from db.connection import get_read_only_engine
from db.schema_introspection import introspect_schema
from lineage.graph import build_lineage_graph
from schema_drift.engine import scan_for_drift
from schema_drift.findings import is_schema_drift_kind
from schema_drift.reports import render_markdown_report
from schema_drift.snapshot_store import load_snapshot, save_snapshot
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schema-drift", tags=["schema-drift"])

DISABLED_DETAIL = "Schema drift detection is not enabled on this server."
UNKNOWN_DATABASE_DETAIL = "Unknown database."
_TOP_FINDINGS_IN_SCAN = 10


def _require_enabled() -> None:
    if not get_settings().enable_schema_drift_detection:
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


@router.post("/scan", response_model=ScanOut)
def scan(
    payload: ScanRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ScanOut:
    """Re-introspects `database_id` live, diffs it against the last
    captured snapshot (or reports every table as added on a genuine first
    scan), assesses impact against this tenant's published catalog
    through lineage, persists the resulting findings (Prompt 35's shared
    review queue), and stores the current snapshot for next time.

    Requires a local account -- the same requirement `api/lineage.py`'s
    own `/graph` route has, since this reads tenant-scoped catalog data.
    Rate-limited -- this route does real, live introspection work, the
    same reasoning `POST /schema/refresh` and `POST /lineage/graph`
    already rate-limit for.
    """
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "schema_drift_scan", settings)
    tenant_id = _actor_tenant(user)
    _authorize_review(user, session, tenant_id)
    database_id = _allowed_database(user, payload.database_id)

    configs = {c.name: c for c in settings.databases_for_tenant(tenant_id)}
    config = configs[database_id]

    engine = get_read_only_engine(config)
    current_tables = introspect_schema(engine, config.db_schema)
    previous_tables = load_snapshot(database_id, settings)
    is_first_scan = not previous_tables

    previous_graph = None
    if previous_tables:
        catalog_rows = list_entries(session, tenant_id, database_id=database_id, status="published")
        catalog_entries = [entry_to_snapshot(row) for row in catalog_rows]
        previous_graph = build_lineage_graph(
            database_id, previous_tables, catalog_entries, settings=settings
        )

    result = scan_for_drift(
        database_id,
        previous_tables,
        current_tables,
        previous_graph,
        rename_similarity_threshold=settings.schema_drift_rename_similarity_threshold,
        impact_max_depth=settings.schema_drift_impact_max_trace_depth,
    )

    kept = list(result.findings)[: settings.schema_drift_max_findings_per_scan]
    truncated = len(result.findings) - len(kept)
    summary = upsert_findings(
        session, tenant_id=tenant_id, database_id=database_id, findings=kept, now=datetime.now(UTC)
    )
    save_snapshot(database_id, list(current_tables), settings)

    report = render_markdown_report(database_id, result.findings, result.tables_compared)
    queue = [
        row
        for row in list_findings(session, tenant_id=tenant_id, database_id=database_id)
        if is_schema_drift_kind(row.kind)
    ]
    return ScanOut(
        database_id=database_id,
        tables_compared=result.tables_compared,
        is_first_scan=is_first_scan,
        findings_detected=len(result.findings),
        findings_truncated=max(truncated, 0),
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        report_markdown=report,
        top_findings=[_finding_out(r) for r in queue[:_TOP_FINDINGS_IN_SCAN]],
    )


@router.get("/findings", response_model=list[FindingOut])
def findings(
    database_id: str = Query(..., min_length=1, max_length=200),
    finding_status: str | None = Query(
        default=None, alias="status", pattern="^(open|accepted|dismissed)$"
    ),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[FindingOut]:
    """The schema-drift review queue for one database, riskiest first --
    the same shared `identity.models.SemanticFinding` table
    `GET /semantic-intelligence/findings` reads, filtered to only this
    feature's own `schema_drift_*`-kind rows."""
    _require_enabled()
    user, session = user_and_session
    tenant_id = _actor_tenant(user)
    _authorize_review(user, session, tenant_id)
    database_id = _allowed_database(user, database_id)
    rows = list_findings(
        session, tenant_id=tenant_id, database_id=database_id, status=finding_status
    )
    return [_finding_out(r) for r in rows if is_schema_drift_kind(r.kind)]


@router.get("/findings/{finding_id}", response_model=FindingOut)
def finding_detail(
    finding_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> FindingOut:
    """One schema-drift finding, including its `payload["impacted"]`
    downstream-dependent list -- fetched via the shared
    `identity.repositories.semantic_intelligence.get_finding`, scoped to
    only this feature's own kind (a non-schema-drift finding id is a 404
    here, same as a cross-tenant or nonexistent one -- this route's own
    namespace, not a security boundary: `GET /semantic-intelligence
    /findings/{id}` already serves any finding a tenant member may see)."""
    _require_enabled()
    user, session = user_and_session
    row = get_finding(session, tenant_id=_actor_tenant(user), finding_id=finding_id)
    if row is None or not is_schema_drift_kind(row.kind):
        raise _not_found()
    _authorize_review(user, session, row.tenant_id)
    return _finding_out(row)


__all__ = ["router"]
