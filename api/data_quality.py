"""POST /data-quality/scan and POST /data-quality/lineage -- the Data
Quality Command Center's REST surface (Prompt 41,
`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

**`/scan` never queries a database or calls an LLM.** The dataset(s) are
taken directly in the request body -- the caller's own already-fetched,
already-governed result (e.g. from a prior `POST /execute`).
`data_quality.engine` is a pure function over whatever rows it's handed.
Gated by `Settings.enable_data_quality_engine` (404 when off),
`Permission.ASK` plus a restricted-column suppression inside
`data_quality.engine.assess_table_quality` itself (a finding referencing
a restricted column is withheld, not the whole scan -- see that module's
own docstring), rate limiting, and payload bounds
(`Settings.data_quality_max_tables`/`data_quality_max_rows_per_table`).

**`/lineage` is the one route in this feature that does touch a
database** -- it reads the tenant's own *published* semantic-catalog
entries (`identity.repositories.semantic_catalog.list_entries`) and
connects them, by table name, to the per-table severity counts the
caller already computed via `/scan` (see `data_quality/lineage.py`'s own
docstring for why this never needs the findings re-sent). Gated by
`Settings.enable_data_quality_engine`, `require_local_user` (a published
catalog entry carries tenant-scoped business context, so this route
needs a resolvable tenant, the identical requirement every other
semantic-catalog-reading route already has), and rate limiting.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from identity.models import User
from identity.repositories.semantic_catalog import list_entries
from sqlalchemy.orm import Session

from agent.authz import Permission
from api.authz import require_permission
from api.data_quality_schemas import (
    DataQualityLineageRequest,
    DataQualityLineageResponse,
    DataQualityScanRequest,
    DataQualityScanResponse,
)
from api.identity_authz import require_local_user
from api.rate_limit import enforce_api_action_rate_limit
from config.settings import get_settings
from data_quality.engine import assess_data_quality
from data_quality.lineage import CatalogConceptRef, TableFindingsSummary, build_lineage_report
from data_quality.models import (
    CategoricalColumnRule,
    ConsistencyRule,
    DataQualitySeverity,
    DateColumnRule,
    FreshnessRule,
    ReferentialCheck,
    TableQualityInput,
)
from security.oidc import AuthIdentity
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-quality", tags=["data-quality"])

DATA_QUALITY_DISABLED_DETAIL = "The Data Quality Command Center is not enabled on this server."
TOO_MANY_TABLES_DETAIL = "Too many tables for this endpoint."
TOO_MANY_ROWS_DETAIL = "Too many rows for one table on this endpoint."


def _require_enabled() -> None:
    if not get_settings().enable_data_quality_engine:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=DATA_QUALITY_DISABLED_DETAIL
        )


@router.post("/scan", response_model=DataQualityScanResponse)
def scan(
    payload: DataQualityScanRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> DataQualityScanResponse:
    """Assesses every submitted table across the seven data-quality
    dimensions. Never touches a database or calls an LLM."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "data_quality_scan", settings, identity=identity)

    if len(payload.tables) > settings.data_quality_max_tables:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_TABLES_DETAIL
        )
    for table_in in payload.tables:
        if len(table_in.rows) > settings.data_quality_max_rows_per_table:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_ROWS_DETAIL
            )

    tables = [
        TableQualityInput(
            table=t.table,
            columns=t.columns,
            rows=[tuple(row) for row in t.rows],
            null_rate_overrides=t.null_rate_overrides,
            date_rules=tuple(DateColumnRule(**r.model_dump()) for r in t.date_rules),
            categorical_rules=tuple(
                CategoricalColumnRule(**r.model_dump()) for r in t.categorical_rules
            ),
            unique_key_sets=tuple(tuple(k) for k in t.unique_key_sets),
            consistency_rules=tuple(ConsistencyRule(**r.model_dump()) for r in t.consistency_rules),
            referential_checks=tuple(
                ReferentialCheck(
                    child_column=r.child_column,
                    parent_table=r.parent_table,
                    parent_key_values=tuple(r.parent_key_values),
                )
                for r in t.referential_checks
            ),
            freshness_rule=(
                FreshnessRule(**t.freshness_rule.model_dump())
                if t.freshness_rule is not None
                else None
            ),
            reference_columns=t.reference_columns,
            reference_rows=[tuple(row) for row in t.reference_rows],
            drift_columns=tuple(t.drift_columns),
            enable_column_statistics=t.enable_column_statistics,
        )
        for t in payload.tables
    ]

    report = assess_data_quality(tables, tuple(identity.roles), settings)
    return DataQualityScanResponse(**report.model_dump(mode="json"))


@router.post("/lineage", response_model=DataQualityLineageResponse)
def lineage(
    payload: DataQualityLineageRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> DataQualityLineageResponse:
    """Connects each submitted table's own severity counts (from a prior
    `/scan` call) to this tenant's *published* semantic-catalog entries,
    by table name. See `data_quality/lineage.py`'s own docstring for the
    table-level-only scope and the "exposure, not a verdict" framing.
    """
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "data_quality_lineage", settings)

    try:
        min_severity = DataQualitySeverity(payload.min_severity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unrecognized min_severity {payload.min_severity!r}.",
        ) from None

    tenant_id = resolve_actor_tenant_id(user)
    entries: list = []
    if tenant_id is not None:
        entries = list_entries(
            session, tenant_id, database_id=payload.database_id, status="published"
        )

    catalog_refs = [
        CatalogConceptRef(
            concept_key=entry.concept_key,
            name=entry.business_name,
            concept_type=entry.concept_type,
            status=entry.status,
            owner=entry.owner,
            database_id=entry.database_id,
            source_tables=tuple(entry.source_tables or ()),
        )
        for entry in entries
    ]
    summaries = [
        TableFindingsSummary(table=t.table, severity_counts=t.severity_counts)
        for t in payload.tables
    ]

    report = build_lineage_report(summaries, catalog_refs, min_severity)
    return DataQualityLineageResponse(**report.model_dump(mode="json"))
