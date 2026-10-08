"""`POST /lineage/graph`, `POST /lineage/trace`, `POST /lineage/stale-check`
-- the Data Lineage Platform's REST surface (Prompt 42,
`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`).

**`/graph` and `/stale-check` are the two routes in this feature that
touch a database -- and both require a local account**, since both read
tenant-scoped catalog data through `identity.repositories.semantic_catalog
.list_entries` (the identical requirement `api/data_quality.py`'s own
`/lineage` route already has). Both live-introspect the caller's own
tenant-scoped database (`db.schema_introspection.introspect_schema`,
metadata-only, the identical, already-live `GET /schema/tables` call this
app already makes) -- never a client-supplied `tenant_id`
(`security.tenancy.resolve_actor_tenant_id`), and a `database_id` outside
`Settings.databases_for_tenant(tenant_id)` is a 404, the identical
anti-enumeration convention `GET /schema/tables` already establishes for
the same reason.

**`/trace` never touches a database or calls an LLM.** It operates
purely over a graph the caller already has (from a prior `/graph` call),
exactly the "neither route ever queries a database" posture
`analytics.scenario`/`optimization`/`analytics.predictive` already
establish for their own pure routes.

All three gated by `Settings.enable_lineage_platform` (404 when off) and
rate limiting plus payload bounds; `/trace` additionally requires
`Permission.ASK`, while `/graph`/`/stale-check` require a local account
(see above).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from identity.models import User
from identity.repositories.semantic_catalog import entry_to_snapshot, list_entries
from identity.repositories.users import get_user_roles
from pydantic import ValidationError
from sqlalchemy.orm import Session

from agent.authz import Permission
from agent.provenance import DataTruthLevel
from api.authz import require_permission
from api.identity_authz import require_local_user
from api.lineage_schemas import (
    LineageEdgeOut,
    LineageGraphRequest,
    LineageGraphResponse,
    LineageNodeOut,
    LineageStaleCheckRequest,
    LineageStaleCheckResponse,
    LineageStalenessFindingOut,
    LineageTraceRequest,
    LineageTraceResponse,
    LineageTraceStepOut,
)
from api.rate_limit import enforce_api_action_rate_limit
from config.settings import get_settings
from db.connection import get_read_only_engine
from db.relationship_inference import infer_relationships
from db.schema_introspection import get_schema_fingerprint, introspect_schema
from lineage.graph import build_lineage_graph, trace_lineage
from lineage.models import LineageEdge, LineageEdgeType, LineageGraph, LineageNode, LineageNodeType
from lineage.staleness import detect_staleness
from security.oidc import AuthIdentity
from security.tenancy import DEFAULT_TENANT_ID, resolve_actor_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lineage", tags=["lineage"])

LINEAGE_DISABLED_DETAIL = "The Data Lineage Platform is not enabled on this server."
TOO_MANY_QUERIES_DETAIL = "Too many queries for one lineage graph request."
TOO_MANY_NODES_DETAIL = "Too many nodes in the supplied graph for a trace request."
UNKNOWN_DATABASE_DETAIL = "Unknown database."


def _require_enabled() -> None:
    if not get_settings().enable_lineage_platform:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=LINEAGE_DISABLED_DETAIL)


def _resolve_database_or_404(database_id: str, tenant_id: str) -> None:
    settings = get_settings()
    visible = {config.name for config in settings.databases_for_tenant(tenant_id)}
    if database_id not in visible:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=UNKNOWN_DATABASE_DETAIL)


def _graph_to_response(graph: LineageGraph) -> LineageGraphResponse:
    return LineageGraphResponse(
        database_id=graph.database_id,
        nodes=[
            LineageNodeOut(
                id=n.id,
                type=n.type.value,
                label=n.label,
                database_id=n.database_id,
                table=n.table,
                column=n.column,
                concept_key=n.concept_key,
                concept_version=n.concept_version,
                truth_level=n.truth_level.value,
                extra=n.extra,
            )
            for n in graph.nodes
        ],
        edges=[
            LineageEdgeOut(
                source_id=e.source_id,
                target_id=e.target_id,
                type=e.type.value,
                truth_level=e.truth_level.value,
                detail=e.detail,
                is_inferred=e.is_inferred,
            )
            for e in graph.edges
        ],
        schema_fingerprint=graph.schema_fingerprint,
        catalog_versions=dict(graph.catalog_versions),
        suppressed_restricted_nodes=list(graph.suppressed_restricted_nodes),
        limitations=list(graph.limitations),
        engine_version=graph.engine_version,
    )


def _response_to_graph(payload: LineageGraphResponse) -> LineageGraph:
    """Reconstructs the internal `LineageGraph` from a caller-echoed
    `/graph` response -- `/trace`'s own input shape.

    This reconstruction happens inside the route body, not at FastAPI's
    own request-parsing boundary, so a `pydantic.ValidationError` raised
    by `LineageNode`/`LineageEdge` here (e.g. an unrecognized `type`/
    `truth_level` string) would **not** be automatically turned into a
    422 by FastAPI -- it would propagate as an unhandled exception,
    caught only by `api/main.py`'s generic last-resort handler (a 500).
    Converted explicitly here into a real 422 instead: a malformed
    caller-echoed graph is a client error, never a server error.
    """
    try:
        return LineageGraph(
            database_id=payload.database_id,
            nodes=tuple(
                LineageNode(
                    id=n.id,
                    type=LineageNodeType(n.type),
                    label=n.label,
                    database_id=n.database_id,
                    table=n.table,
                    column=n.column,
                    concept_key=n.concept_key,
                    concept_version=n.concept_version,
                    truth_level=DataTruthLevel(n.truth_level),
                    extra=n.extra,
                )
                for n in payload.nodes
            ),
            edges=tuple(
                LineageEdge(
                    source_id=e.source_id,
                    target_id=e.target_id,
                    type=LineageEdgeType(e.type),
                    truth_level=DataTruthLevel(e.truth_level),
                    detail=e.detail,
                    is_inferred=e.is_inferred,
                )
                for e in payload.edges
            ),
            schema_fingerprint=payload.schema_fingerprint,
            catalog_versions=payload.catalog_versions,
            suppressed_restricted_nodes=tuple(payload.suppressed_restricted_nodes),
            limitations=tuple(payload.limitations),
            engine_version=payload.engine_version,
        )
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The supplied graph is malformed: {exc}",
        ) from None


@router.post("/graph", response_model=LineageGraphResponse)
def graph(
    payload: LineageGraphRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> LineageGraphResponse:
    """Builds the full lineage graph for one configured database: live
    schema (tables/columns/declared FKs, optionally structurally-inferred
    candidates) + this tenant's own published governed-catalog entries,
    plus (optionally) each caller-supplied query's own read/visualize/
    explain/recommend tail.

    Requires a local account -- the same requirement `api/data_quality
    .py`'s own `/lineage` route and `api/semantic_catalog.py`'s read
    routes already have, since this reads tenant-scoped catalog data
    through `identity.repositories.semantic_catalog.list_entries`."""
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "lineage_graph", settings)

    if len(payload.queries) > settings.lineage_max_queries_per_request:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_QUERIES_DETAIL
        )

    tenant_id = resolve_actor_tenant_id(user) or DEFAULT_TENANT_ID
    _resolve_database_or_404(payload.database_id, tenant_id)
    configs = {c.name: c for c in settings.databases_for_tenant(tenant_id)}
    config = configs[payload.database_id]

    engine = get_read_only_engine(config)
    tables = introspect_schema(engine, config.db_schema)
    inferred = infer_relationships(tables) if payload.include_inferred_relationships else []

    catalog_rows = list_entries(
        session, tenant_id, database_id=payload.database_id, status="published"
    )
    catalog_entries = [entry_to_snapshot(row) for row in catalog_rows]

    queries = [
        {
            "sql": q.sql,
            "fingerprint": q.fingerprint,
            "visualization": q.visualization.model_dump() if q.visualization else None,
            "insight": q.insight.model_dump() if q.insight else None,
            "recommendations": [r.model_dump() for r in q.recommendations],
        }
        for q in payload.queries
    ]

    result = build_lineage_graph(
        payload.database_id,
        tables,
        catalog_entries,
        inferred,
        queries,
        caller_roles=get_user_roles(session, user.id),
        settings=settings,
    )
    return _graph_to_response(result)


@router.post("/trace", response_model=LineageTraceResponse)
def trace(
    payload: LineageTraceRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> LineageTraceResponse:
    """Upstream ("what feeds this node") or downstream ("what depends on
    this node" -- impact analysis) walk over a graph the caller already
    has from a prior `/graph` call. Never touches a database or calls an
    LLM."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "lineage_trace", settings, identity=identity)

    if len(payload.graph.nodes) > settings.lineage_max_trace_graph_nodes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_NODES_DETAIL
        )
    if payload.direction not in ("upstream", "downstream"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="direction must be 'upstream' or 'downstream'.",
        )

    internal_graph = _response_to_graph(payload.graph)
    max_depth = min(
        payload.max_depth or settings.lineage_max_trace_depth, settings.lineage_max_trace_depth
    )
    result = trace_lineage(internal_graph, payload.start_node_id, payload.direction, max_depth)

    return LineageTraceResponse(
        start_node_id=result.start_node_id,
        direction=result.direction,
        max_depth=result.max_depth,
        steps=[
            LineageTraceStepOut(
                node=LineageNodeOut(
                    id=s.node.id,
                    type=s.node.type.value,
                    label=s.node.label,
                    database_id=s.node.database_id,
                    table=s.node.table,
                    column=s.node.column,
                    concept_key=s.node.concept_key,
                    concept_version=s.node.concept_version,
                    truth_level=s.node.truth_level.value,
                    extra=s.node.extra,
                ),
                via_edge=(
                    LineageEdgeOut(
                        source_id=s.via_edge.source_id,
                        target_id=s.via_edge.target_id,
                        type=s.via_edge.type.value,
                        truth_level=s.via_edge.truth_level.value,
                        detail=s.via_edge.detail,
                        is_inferred=s.via_edge.is_inferred,
                    )
                    if s.via_edge is not None
                    else None
                ),
                depth=s.depth,
            )
            for s in result.steps
        ],
        truncated=result.truncated,
    )


@router.post("/stale-check", response_model=LineageStaleCheckResponse)
def stale_check(
    payload: LineageStaleCheckRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> LineageStaleCheckResponse:
    """Re-fingerprints the live schema and re-fetches the current
    published-catalog versions for `database_id`, and compares both
    against the caller-supplied, previously-recorded values from a prior
    `/graph` response. Requires a local account (the same requirement
    `api/data_quality.py`'s own `/lineage` route already has) since this
    reads tenant-scoped catalog data."""
    _require_enabled()
    user, session = user_and_session
    settings = get_settings()
    enforce_api_action_rate_limit(request, "lineage_stale_check", settings)

    tenant_id = resolve_actor_tenant_id(user) or DEFAULT_TENANT_ID
    _resolve_database_or_404(payload.database_id, tenant_id)
    configs = {c.name: c for c in settings.databases_for_tenant(tenant_id)}
    config = configs[payload.database_id]

    engine = get_read_only_engine(config)
    tables = introspect_schema(engine, config.db_schema)
    current_schema_fingerprint = get_schema_fingerprint(tables)

    catalog_rows = list_entries(
        session, tenant_id, database_id=payload.database_id, status="published"
    )
    current_catalog_versions = {row.concept_key: row.version for row in catalog_rows}

    # `detect_staleness` reads `graph.catalog_versions`/`graph
    # .schema_fingerprint` -- build a minimal stand-in graph carrying just
    # the caller-supplied recorded values rather than requiring the whole
    # graph to be re-sent (mirroring `data_quality/lineage.py`'s own
    # "a lightweight summary, never the full object re-transmitted" rule).
    recorded_graph = LineageGraph(
        database_id=payload.database_id,
        nodes=(),
        edges=(),
        schema_fingerprint=payload.recorded_schema_fingerprint,
        catalog_versions=payload.recorded_catalog_versions,
    )
    report = detect_staleness(recorded_graph, current_schema_fingerprint, current_catalog_versions)

    return LineageStaleCheckResponse(
        is_stale=report.is_stale,
        findings=[
            LineageStalenessFindingOut(kind=f.kind, detail=f.detail, node_id=f.node_id)
            for f in report.findings
        ],
        engine_version=report.engine_version,
    )
