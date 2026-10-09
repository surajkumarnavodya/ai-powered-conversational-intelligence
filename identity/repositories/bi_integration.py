"""Repository layer for the Enterprise BI Integrations feature --
`BiConnector`, Prompt 48 (`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`).

**This module never makes an authorization decision itself** --
`bi_integration.policy.authorize_bi_action` is the only place a caller
may or may not do something is decided, mirroring `identity/repositories
/eval_platform.py`/`identity/repositories/governance.py`'s own identical
split: this repository returns a row (or `None`) regardless of tenant,
and the caller (`api/bi_integration.py`) is responsible for the
tenant-match check before acting on what it got back.

**No status-transition race protection like `identity/repositories
/governance.py`'s own atomic `_apply_transition`** -- a connector's own
`status`/`last_refresh_*` fields are only ever written by `record_refresh
_result`, and refreshing the *same* connector twice concurrently is not
a correctness hazard the way two callers racing to deploy/publish the
*same version* of a governed asset is (whichever refresh's own
`execute_readonly_sql` call returns last simply becomes "the current
status," the same way two browser tabs independently clicking "Confirm
and Run" against the same database would already behave with no
special coordination anywhere else in this codebase).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from bi_integration.models import (
    BiConnectorSnapshot,
    BiTargetType,
    ConnectorStatus,
    RefreshOutcome,
)
from identity.models import BiConnector


def create_connector(
    session: Session,
    *,
    tenant_id: str,
    target_type: str,
    display_name: str,
    sql_text: str,
    database_id: str,
    push_config: dict[str, Any],
    governed_metric_concept_key: str | None = None,
    governed_metric_version: int | None = None,
    owner_user_id: uuid.UUID | None = None,
) -> BiConnector:
    connector = BiConnector(
        tenant_id=tenant_id,
        target_type=target_type,
        display_name=display_name,
        sql_text=sql_text,
        database_id=database_id,
        push_config=push_config,
        governed_metric_concept_key=governed_metric_concept_key,
        governed_metric_version=governed_metric_version,
        owner_user_id=owner_user_id,
    )
    session.add(connector)
    session.commit()
    session.refresh(connector)
    return connector


def get_connector_by_id(session: Session, connector_id: uuid.UUID) -> BiConnector | None:
    return session.get(BiConnector, connector_id)


def list_connectors(
    session: Session, *, tenant_id: str, target_type: str | None = None
) -> list[BiConnector]:
    """Always tenant-scoped at the query itself (never a post-hoc
    filter) -- the identical convention `identity.repositories
    .eval_platform.list_runs` already establishes."""
    stmt = (
        select(BiConnector)
        .where(BiConnector.tenant_id == tenant_id)
        .order_by(BiConnector.created_at.desc())
    )
    if target_type is not None:
        stmt = stmt.where(BiConnector.target_type == target_type)
    return list(session.execute(stmt).scalars().all())


def delete_connector(session: Session, connector: BiConnector) -> None:
    session.delete(connector)
    session.commit()


def record_refresh_result(
    session: Session,
    connector: BiConnector,
    *,
    success: bool,
    row_count: int | None = None,
    error: str | None = None,
) -> BiConnector:
    """Updates a connector's own refresh status in one commit --
    `error` must already be redacted by the caller (`security.redaction
    .redact_secrets`, the identical treatment `api/main.py`'s own
    `/execute` route already gives a raw driver error) before reaching
    here; this function stores it verbatim, it does not re-check."""
    connector.last_refresh_at = datetime.now(UTC)
    connector.last_refresh_status = (
        RefreshOutcome.SUCCESS.value if success else RefreshOutcome.FAILED.value
    )
    connector.status = ConnectorStatus.HEALTHY.value if success else ConnectorStatus.ERROR.value
    # `last_row_count`/`last_error` are nullable columns annotated with
    # their bare (non-Optional) type on the ORM model -- the same
    # Python-3.14 `Mapped[...]` union-annotation workaround
    # `identity/models.py`'s own module docstring documents (mypy sees
    # the attribute as non-Optional even though `nullable=True` is set),
    # identical to the pattern `identity/repositories/governance.py`'s
    # own nullable-FK assignments already establish.
    connector.last_row_count = row_count  # type: ignore[assignment]
    connector.last_error = error  # type: ignore[assignment]
    session.add(connector)
    session.commit()
    session.refresh(connector)
    return connector


def set_disabled(session: Session, connector: BiConnector, *, disabled: bool) -> BiConnector:
    """Soft-disable/re-enable -- a disabled connector is never deleted
    (its evidence/history stays inspectable) but its own `refresh`
    route refuses to run (see `api/bi_integration.py`)."""
    connector.status = (
        ConnectorStatus.DISABLED.value if disabled else ConnectorStatus.NEVER_REFRESHED.value
    )
    session.add(connector)
    session.commit()
    session.refresh(connector)
    return connector


def connector_to_snapshot(connector: BiConnector) -> BiConnectorSnapshot:
    """Converts a real `BiConnector` ORM row into the ORM-independent
    `BiConnectorSnapshot` shape -- the one place this conversion
    happens, mirroring `identity.repositories.semantic_catalog
    .entry_to_snapshot`'s own identical role for `CatalogEntrySnapshot`.
    Carries the connector's own *stored* governed-metric reference
    verbatim (see `BiConnectorSnapshot`'s own docstring for why this
    never eagerly resolves a `GovernedMetricBinding` here).
    """
    return BiConnectorSnapshot(
        id=str(connector.id),
        tenant_id=connector.tenant_id,
        target_type=BiTargetType(connector.target_type),
        display_name=connector.display_name,
        sql_text=connector.sql_text,
        database_id=connector.database_id,
        governed_metric_concept_key=connector.governed_metric_concept_key,
        governed_metric_version=connector.governed_metric_version,
        status=ConnectorStatus(connector.status),
        last_refresh_at=connector.last_refresh_at,
        last_refresh_status=(
            RefreshOutcome(connector.last_refresh_status) if connector.last_refresh_status else None
        ),
        last_error=connector.last_error,
        last_row_count=connector.last_row_count,
        owner_user_id=str(connector.owner_user_id) if connector.owner_user_id else None,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
    )
