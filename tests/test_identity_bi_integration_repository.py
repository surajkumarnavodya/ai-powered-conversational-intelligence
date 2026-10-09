"""Tests for `identity/repositories/bi_integration.py` -- Prompt 48
(`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`). A real in-memory SQLite
engine (`Base.metadata.create_all`), never a mock of the ORM -- the
same convention every other `identity/repositories/` test file in this
codebase already uses.
"""

from __future__ import annotations

import uuid

import pytest
from identity.models import Base
from identity.repositories import bi_integration as repo
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from bi_integration.models import BiTargetType, ConnectorStatus, RefreshOutcome


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def _connector(session: Session, tenant_id: str = "tenant-a", **overrides):
    defaults = dict(
        tenant_id=tenant_id,
        target_type="power_bi",
        display_name="Revenue Push",
        sql_text="SELECT region, SUM(amount) AS revenue FROM sales GROUP BY region",
        database_id="default",
        push_config={"dataset_id": "ds1", "table_name": "tbl1"},
    )
    defaults.update(overrides)
    return repo.create_connector(session, **defaults)


class TestCreateAndGet:
    def test_create_persists_every_field(self, session: Session):
        connector = _connector(
            session,
            governed_metric_concept_key="net_revenue",
            governed_metric_version=2,
        )
        assert connector.id is not None
        assert connector.status == ConnectorStatus.NEVER_REFRESHED.value
        assert connector.target_type == "power_bi"
        assert connector.governed_metric_concept_key == "net_revenue"
        assert connector.governed_metric_version == 2
        assert connector.push_config == {"dataset_id": "ds1", "table_name": "tbl1"}

    def test_get_by_id_returns_none_for_an_unknown_id(self, session: Session):
        assert repo.get_connector_by_id(session, uuid.uuid4()) is None

    def test_get_by_id_returns_the_real_row_regardless_of_tenant(self, session: Session):
        """The repository itself never tenant-scopes a get-by-id lookup --
        that's the API layer's own job (see this module's own docstring)."""
        connector = _connector(session, tenant_id="tenant-a")
        fetched = repo.get_connector_by_id(session, connector.id)
        assert fetched is not None
        assert fetched.tenant_id == "tenant-a"


class TestListConnectors:
    def test_list_is_tenant_scoped_at_the_query(self, session: Session):
        _connector(session, tenant_id="tenant-a")
        _connector(session, tenant_id="tenant-b")
        assert len(repo.list_connectors(session, tenant_id="tenant-a")) == 1
        assert len(repo.list_connectors(session, tenant_id="tenant-b")) == 1
        assert len(repo.list_connectors(session, tenant_id="tenant-c")) == 0

    def test_list_filters_by_target_type(self, session: Session):
        _connector(session, target_type="power_bi")
        _connector(session, target_type="tableau", push_config={})
        power_bi_only = repo.list_connectors(session, tenant_id="tenant-a", target_type="power_bi")
        assert len(power_bi_only) == 1
        assert power_bi_only[0].target_type == "power_bi"


class TestDelete:
    def test_delete_removes_the_row(self, session: Session):
        connector = _connector(session)
        repo.delete_connector(session, connector)
        assert repo.get_connector_by_id(session, connector.id) is None


class TestRecordRefreshResult:
    def test_a_successful_refresh_updates_status_and_row_count(self, session: Session):
        connector = _connector(session)
        updated = repo.record_refresh_result(session, connector, success=True, row_count=42)
        assert updated.status == ConnectorStatus.HEALTHY.value
        assert updated.last_refresh_status == RefreshOutcome.SUCCESS.value
        assert updated.last_row_count == 42
        assert updated.last_error is None
        assert updated.last_refresh_at is not None

    def test_a_failed_refresh_records_the_error_and_flips_to_error_status(self, session: Session):
        connector = _connector(session)
        updated = repo.record_refresh_result(
            session, connector, success=False, error="connection refused"
        )
        assert updated.status == ConnectorStatus.ERROR.value
        assert updated.last_refresh_status == RefreshOutcome.FAILED.value
        assert updated.last_error == "connection refused"

    def test_a_connector_can_recover_from_error_to_healthy(self, session: Session):
        connector = _connector(session)
        repo.record_refresh_result(session, connector, success=False, error="transient")
        updated = repo.record_refresh_result(session, connector, success=True, row_count=10)
        assert updated.status == ConnectorStatus.HEALTHY.value
        assert updated.last_error is None


class TestSetDisabled:
    def test_disable_and_re_enable(self, session: Session):
        connector = _connector(session)
        disabled = repo.set_disabled(session, connector, disabled=True)
        assert disabled.status == ConnectorStatus.DISABLED.value
        enabled = repo.set_disabled(session, connector, disabled=False)
        assert enabled.status == ConnectorStatus.NEVER_REFRESHED.value


class TestConnectorToSnapshot:
    def test_snapshot_carries_every_field(self, session: Session):
        connector = _connector(
            session,
            target_type="looker",
            push_config={"webhook_url": "https://company.looker.com/webhook"},
            governed_metric_concept_key="net_revenue",
            governed_metric_version=3,
        )
        snapshot = repo.connector_to_snapshot(connector)
        assert snapshot.target_type == BiTargetType.LOOKER
        assert snapshot.governed_metric_concept_key == "net_revenue"
        assert snapshot.governed_metric_version == 3
        assert snapshot.status == ConnectorStatus.NEVER_REFRESHED

    def test_snapshot_of_a_connector_with_no_governed_metric(self, session: Session):
        connector = _connector(session)
        snapshot = repo.connector_to_snapshot(connector)
        assert snapshot.governed_metric_concept_key is None
        assert snapshot.governed_metric_version is None
