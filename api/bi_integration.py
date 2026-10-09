"""The Enterprise BI Integrations REST surface -- Prompt 48
(`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`).

**Every route here does exactly one authorization dance**: resolve the
`BiConnector` row by id when one is needed (never trusting a query
filter alone to scope it), then call `bi_integration.policy
.authorize_bi_action` and act only on an `allowed=True` decision --
mirrors `api/eval_platform.py`/`api/governance.py`'s own identical
discipline, including the cross-tenant-denial-is-a-404 anti-enumeration
precedent.

**Behind `Settings.enable_bi_integration`** (404 when off, default
off) -- the identical `_require_enabled()` pattern
`api/optimization.py`/`api/scenarios.py`/`api/predictive.py` already
establish, called as the first line of every route body (not a FastAPI
`Depends`), since this is a plain settings check with no DB/session
dependency of its own.

**`POST .../refresh` reuses this app's own governed SQL execution path
verbatim** -- `agent.sql_validator.validate_sql` ->
`enforce_row_limit` -> `qualify_table_schema` -> `db.execution
.execute_readonly_sql`, the exact sequence `POST /execute` already
uses, followed by `governance.result_policy.govern_rows_for_caller`
(masking a restricted column's values for a caller lacking
`Permission.VIEW_RESTRICTED_COLUMNS`) **before** a push-style
connector's own rows ever leave this process, and before a pull-style
connector's own export cache is populated. This is never a parallel
execution path -- "preserve ... deterministic SQL/data controls" is
satisfied by reuse, not by a second implementation of the same checks.

**An operational refresh failure (a SQL error, a push HTTP error, an
SSRF-blocked webhook) is a 200 with `status: "error"` in the body, never
an HTTP error code** -- the identical "secure export/connectors,
refresh/status/error handling" shape `POST /execute`'s own
`ExecuteResponse(status="failed", ...)` already establishes for an
execution failure. An HTTP error code is reserved for a request-level
problem (authorization, validation, rate limit, not-found) -- the
connector's own row (`last_error`/`last_refresh_status`) is where an
operational failure is recorded and reported back.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from identity.models import BiConnector, User
from identity.repositories.bi_integration import connector_to_snapshot, get_connector_by_id
from identity.repositories.bi_integration import create_connector as _create_connector
from identity.repositories.bi_integration import delete_connector as _delete_connector
from identity.repositories.bi_integration import list_connectors as _list_connectors
from identity.repositories.bi_integration import record_refresh_result as _record_refresh_result
from identity.repositories.semantic_catalog import entry_to_snapshot, list_versions_for_concept_key
from identity.repositories.users import get_user_permissions, get_user_roles
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent.sql_validator import enforce_row_limit, qualify_table_schema, validate_sql
from api.bi_integration_schemas import (
    BiConnectorOut,
    CreateConnectorRequest,
    RefreshConnectorRequest,
)
from api.identity_authz import require_local_user
from api.rate_limit import enforce_api_action_rate_limit
from bi_integration.export_cache import CachedExport, get_export_cache
from bi_integration.metric_binding import validate_governed_metric_binding
from bi_integration.models import PULL_TARGETS, PUSH_TARGETS, BiExportFormat, BiTargetType
from bi_integration.outbound_safety import OutboundUrlBlockedError
from bi_integration.policy import BiAction, authorize_bi_action
from bi_integration.push import BiPushNotConfiguredError, push_looker_webhook, push_power_bi
from bi_integration.rendering import render_csv, render_json, render_xlsx
from config.settings import get_settings
from db.connection import get_read_only_engine, get_sqlglot_dialect
from db.execution import execute_readonly_sql
from governance.result_policy import govern_rows_for_caller
from security.audit_log import log_security_event
from security.redaction import redact_secrets
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bi", tags=["bi-integration"])

_BI_DISABLED_DETAIL = "Enterprise BI Integrations is not enabled on this server."

_DEFAULT_REFRESH_REQUEST = RefreshConnectorRequest()


def _require_enabled() -> None:
    if not get_settings().enable_bi_integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_BI_DISABLED_DETAIL)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BI connector not found.")


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")


def _require_connector(session: Session, connector_id: uuid.UUID) -> BiConnector:
    connector = get_connector_by_id(session, connector_id)
    if connector is None:
        raise _not_found()
    return connector


def _authorize(
    user: User, session: Session, action: BiAction, connector: BiConnector | None
) -> str:
    """Returns the actor's own resolved tenant id on success; raises on denial."""
    permissions = get_user_permissions(session, user.id)
    actor_tenant_id = resolve_actor_tenant_id(user)
    decision = authorize_bi_action(
        actor_permissions=permissions,
        actor_tenant_id=actor_tenant_id,
        resource_tenant_id=connector.tenant_id if connector is not None else None,
        action=action,
    )
    if not decision.allowed:
        if connector is not None and decision.reason == "resource belongs to a different tenant":
            raise _not_found()
        raise _forbidden()
    if actor_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not resolve a tenant for this account.",
        )
    return actor_tenant_id


def _out(connector: BiConnector) -> BiConnectorOut:
    snapshot = connector_to_snapshot(connector)
    return BiConnectorOut(
        id=snapshot.id,
        tenant_id=snapshot.tenant_id,
        target_type=snapshot.target_type.value,
        display_name=snapshot.display_name,
        sql_text=snapshot.sql_text,
        database_id=snapshot.database_id,
        governed_metric_concept_key=snapshot.governed_metric_concept_key,
        governed_metric_version=snapshot.governed_metric_version,
        status=snapshot.status.value,
        last_refresh_at=snapshot.last_refresh_at,
        last_refresh_status=(
            snapshot.last_refresh_status.value if snapshot.last_refresh_status else None
        ),
        last_error=snapshot.last_error,
        last_row_count=snapshot.last_row_count,
        owner_user_id=snapshot.owner_user_id,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


@router.post("/connectors", response_model=BiConnectorOut)
def create_connector(
    payload: CreateConnectorRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> BiConnectorOut:
    """Creates a connector -- the SQL is validated fresh right now
    (never trusted because a caller claims it already ran successfully
    elsewhere), and an optional governed-metric link is checked against
    the real, currently-published catalog entry (see
    `bi_integration.metric_binding.validate_governed_metric_binding`'s
    own docstring for exactly what is and isn't verified)."""
    user, session = user_and_session
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "bi_connector_create", settings, identity=None)
    tenant_id = _authorize(user, session, BiAction.CREATE, None)

    visible_databases = settings.databases_for_tenant(tenant_id)
    if not visible_databases:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No database configured."
        )
    database_name = payload.database or visible_databases[0].name
    db_config = next((db for db in visible_databases if db.name == database_name), None)
    if db_config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown database {database_name!r}."
        )

    dialect = get_sqlglot_dialect(db_config.db_type)
    validation = validate_sql(payload.sql_text, dialect=dialect)
    if not validation.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Rejected: {validation.error}",
        )
    if validation.normalized_sql is None:
        # Bandit B101: explicit raise, not `assert` (stripped under `python
        # -O`). `ValidationResult`'s own model_validator (agent/
        # sql_validator.py) already guarantees normalized_sql is set
        # whenever is_valid is True -- the same precondition
        # `api/main.py`'s own `/execute` route documents identically.
        raise RuntimeError("validate_sql returned is_valid=True with no normalized_sql")

    target_type = BiTargetType(payload.target_type)
    if target_type in PUSH_TARGETS:
        if target_type is BiTargetType.POWER_BI:
            missing = {"dataset_id", "table_name"} - payload.push_config.keys()
        else:  # LOOKER
            missing = {"webhook_url"} - payload.push_config.keys()
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"push_config is missing required key(s): {sorted(missing)}.",
            )

    governed_version: int | None = None
    if payload.governed_metric_concept_key is not None:
        versions = list_versions_for_concept_key(
            session,
            tenant_id=tenant_id,
            database_id=database_name,
            concept_type="metric",
            concept_key=payload.governed_metric_concept_key,
        )
        if payload.governed_metric_version is not None:
            entry = next(
                (v for v in versions if v.version == payload.governed_metric_version), None
            )
        else:
            entry = next((v for v in versions if v.status == "published"), None)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"No matching version of governed metric "
                    f"{payload.governed_metric_concept_key!r} was found."
                ),
            )
        governed_version = entry.version
        snapshot = entry_to_snapshot(entry)
        violations = validate_governed_metric_binding(
            snapshot, validation.normalized_sql, dialect=dialect
        )
        if violations:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(violations)
            )

    connector = _create_connector(
        session,
        tenant_id=tenant_id,
        target_type=target_type.value,
        display_name=payload.display_name,
        sql_text=validation.normalized_sql,
        database_id=database_name,
        push_config=payload.push_config,
        governed_metric_concept_key=payload.governed_metric_concept_key,
        governed_metric_version=governed_version,
        owner_user_id=user.id,
    )
    log_security_event(
        "bi_connector_created",
        "info",
        "A BI connector was created.",
        connector_id=str(connector.id),
        target_type=target_type.value,
        actor_user_id=str(user.id),
    )
    return _out(connector)


@router.get("/connectors", response_model=list[BiConnectorOut])
def list_connectors_route(
    target_type: str | None = Query(default=None),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[BiConnectorOut]:
    user, session = user_and_session
    _require_enabled()
    tenant_id = _authorize(user, session, BiAction.LIST, None)
    connectors = _list_connectors(session, tenant_id=tenant_id, target_type=target_type)
    return [_out(c) for c in connectors]


@router.get("/connectors/{connector_id}", response_model=BiConnectorOut)
def get_connector_route(
    connector_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> BiConnectorOut:
    user, session = user_and_session
    _require_enabled()
    connector = _require_connector(session, connector_id)
    _authorize(user, session, BiAction.GET, connector)
    return _out(connector)


@router.delete("/connectors/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector_route(
    connector_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> Response:
    user, session = user_and_session
    _require_enabled()
    connector = _require_connector(session, connector_id)
    _authorize(user, session, BiAction.DELETE, connector)
    _delete_connector(session, connector)
    get_export_cache().discard(str(connector_id))
    log_security_event(
        "bi_connector_deleted",
        "info",
        "A BI connector was deleted.",
        connector_id=str(connector_id),
        actor_user_id=str(user.id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/connectors/{connector_id}/refresh", response_model=BiConnectorOut)
def refresh_connector(
    connector_id: uuid.UUID,
    request: Request,
    payload: RefreshConnectorRequest = _DEFAULT_REFRESH_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> BiConnectorOut:
    user, session = user_and_session
    _require_enabled()
    settings = get_settings()
    connector = _require_connector(session, connector_id)
    _authorize(user, session, BiAction.REFRESH, connector)
    enforce_api_action_rate_limit(request, "bi_connector_refresh", settings, identity=None)

    visible_databases = settings.databases_for_tenant(connector.tenant_id)
    db_config = next((db for db in visible_databases if db.name == connector.database_id), None)
    if db_config is None:
        updated = _record_refresh_result(
            session,
            connector,
            success=False,
            error=f"Database {connector.database_id!r} is no longer configured for this tenant.",
        )
        return _out(updated)

    dialect = get_sqlglot_dialect(db_config.db_type)
    validation = validate_sql(connector.sql_text, dialect=dialect)
    if not validation.is_valid:
        updated = _record_refresh_result(
            session, connector, success=False, error=f"Rejected: {validation.error}"
        )
        return _out(updated)
    if validation.normalized_sql is None:
        raise RuntimeError("validate_sql returned is_valid=True with no normalized_sql")

    safe_sql = enforce_row_limit(
        validation.normalized_sql, settings.bi_export_max_rows, dialect=dialect
    )
    execution_sql = qualify_table_schema(safe_sql, db_config.db_schema, dialect=dialect)

    try:
        columns, rows = execute_readonly_sql(
            execution_sql,
            settings.query_timeout_seconds,
            settings.bi_export_max_rows,
            engine=get_read_only_engine(db_config),
        )
    except (SQLAlchemyError, TimeoutError) as exc:
        safe_detail = redact_secrets(str(exc), db_config)
        updated = _record_refresh_result(
            session, connector, success=False, error=f"Execution failed: {safe_detail}"
        )
        return _out(updated)

    if settings.enable_result_governance:
        caller_roles = get_user_roles(session, user.id)
        rows = govern_rows_for_caller(columns, rows, caller_roles)

    target_type = BiTargetType(connector.target_type)
    if target_type in PUSH_TARGETS:
        try:
            if target_type is BiTargetType.POWER_BI:
                push_power_bi(
                    settings,
                    connector.push_config["dataset_id"],
                    connector.push_config["table_name"],
                    columns,
                    rows,
                )
            else:  # LOOKER
                push_looker_webhook(settings, connector.push_config["webhook_url"], columns, rows)
        except (BiPushNotConfiguredError, OutboundUrlBlockedError) as exc:
            updated = _record_refresh_result(session, connector, success=False, error=str(exc))
            return _out(updated)
        except Exception as exc:  # noqa: BLE001 - an outbound push failure is reported, not raised
            safe_detail = redact_secrets(str(exc))
            updated = _record_refresh_result(
                session, connector, success=False, error=f"Push failed: {safe_detail}"
            )
            return _out(updated)
    else:
        get_export_cache(settings.bi_export_cache_max_entries).put(
            str(connector.id),
            CachedExport(
                columns=tuple(columns),
                rows=tuple(tuple(row) for row in rows),
                generated_at=datetime.now(UTC),
                row_count=len(rows),
            ),
        )

    updated = _record_refresh_result(session, connector, success=True, row_count=len(rows))
    log_security_event(
        "bi_connector_refreshed",
        "info",
        "A BI connector was refreshed.",
        connector_id=str(connector.id),
        target_type=target_type.value,
        row_count=len(rows),
        actor_user_id=str(user.id),
    )
    return _out(updated)


@router.get("/connectors/{connector_id}/export")
def export_connector(
    connector_id: uuid.UUID,
    format: str = Query(default="csv"),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> Response:
    """Pull-targets only (`tableau`/`excel`) -- a push-target connector
    has no file to download (its rows already left this system on its
    last successful refresh). 409 if this connector has never been
    successfully refreshed -- there is nothing cached to serve yet."""
    user, session = user_and_session
    _require_enabled()
    connector = _require_connector(session, connector_id)
    _authorize(user, session, BiAction.EXPORT, connector)

    target_type = BiTargetType(connector.target_type)
    if target_type not in PULL_TARGETS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{target_type.value!r} connectors are push-style and have no file export.",
        )
    try:
        export_format = BiExportFormat(format)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported format {format!r}. Supported: csv, xlsx, json.",
        ) from None

    cached = get_export_cache().get(str(connector_id))
    if cached is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connector has never been successfully refreshed -- nothing to export yet.",
        )

    if export_format is BiExportFormat.CSV:
        data = render_csv(cached.columns, cached.rows)
        media_type = "text/csv"
        extension = "csv"
    elif export_format is BiExportFormat.XLSX:
        data = render_xlsx(cached.columns, cached.rows, connector.display_name)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"
    else:
        data = render_json(cached.columns, cached.rows)
        media_type = "application/json"
        extension = "json"

    log_security_event(
        "bi_connector_exported",
        "info",
        "A BI connector's cached export was downloaded.",
        connector_id=str(connector_id),
        format=export_format.value,
        row_count=cached.row_count,
        actor_user_id=str(user.id),
    )
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{connector.id}.{extension}"',
        },
    )
