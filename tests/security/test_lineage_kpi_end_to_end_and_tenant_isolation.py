"""Cross-cutting regression test for the Data Lineage Platform (Prompt
42, `42_DATA_LINEAGE_PLATFORM_CONTRACT.md`) -- spans `lineage/graph.py`,
`api/lineage.py`, and the real, tenant-scoped semantic-catalog repository
together, so per `tests/security/README.md`'s own rule it belongs here
rather than being split across module-specific test files.

Two things the mission names explicitly, proven end to end against the
real HTTP API with a real identity database (an in-memory SQLite engine,
real tenants, real users, real catalog rows) -- not a mock of the
authorization path:

1. **A governed KPI traces end to end** -- from its real source column,
   through the published metric definition, through an executed query,
   to that query's own visualization/insight/recommendation -- entirely
   through the authorized `/lineage/graph` + `/lineage/trace` routes a
   real signed-in caller would use.
2. **Cross-tenant authorization holds** -- a published catalog entry
   belonging to one tenant never appears in another tenant's lineage
   graph for the same (shared) database connection, and a database
   outside a tenant's own configured set is a 404, never a leak of
   whether it exists.

Only `db.schema_introspection.introspect_schema`/`db.connection
.get_read_only_engine` are mocked (no real database is available in this
environment) -- everything else (identity DB, tenant resolution, catalog
repository, the real `lineage.graph` engine) is exercised for real.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.semantic_catalog import create_entry, mark_reviewed, publish_entry
from identity.repositories.tenants import create_tenant
from identity.repositories.users import create_user
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.lineage as api_lineage_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
from config.settings import Settings
from db.schema_introspection import ColumnInfo, ForeignKeyInfo, TableSchemaInfo
from security.secrets import SecretStr

PASSWORD = "correcthorse battery staple 1"

_SETTINGS = Settings(
    local_auth_enabled=True,
    auth_database_url="postgresql://placeholder/unused",
    jwt_secret_key="s" * 40,
    jwt_issuer="text-to-sql-agent",
    jwt_audience="text-to-sql-web",
    allow_public_registration=True,
    cookie_secure=False,
    password_min_length=8,
    login_rate_limit_per_minute=1000,
    register_rate_limit_per_hour=1000,
    api_action_rate_limit_per_minute=1000,
    enable_lineage_platform=True,
    db_type="postgresql",
    db_host="db.example.com",
    db_port=5432,
    db_name="mydb",
    db_user="reader",
    db_password=SecretStr("secret"),
    db_connection_string=None,
    db_schema=None,
    db_odbc_driver="x",
    chroma_persist_dir=Path("/tmp/chroma"),
)


def _fake_tables() -> list[TableSchemaInfo]:
    return [
        TableSchemaInfo(
            table_name="FactSales",
            columns=(
                ColumnInfo("SalesAmount", "decimal", True, False),
                ColumnInfo("ProductKey", "int", False, False),
            ),
            foreign_keys=(ForeignKeyInfo(("ProductKey",), "DimProduct", ("ProductKey",)),),
            ddl="x",
        ),
        TableSchemaInfo(
            table_name="DimProduct",
            columns=(
                ColumnInfo("ProductKey", "int", False, True),
                ColumnInfo("ProductName", "text", False, False),
            ),
            foreign_keys=(),
            ddl="x",
        ),
    ]


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    monkeypatch.setattr(api_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_authz_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(api_lineage_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr("api.main.get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(api_lineage_mod, "introspect_schema", lambda engine, schema: _fake_tables())
    monkeypatch.setattr(api_lineage_mod, "get_read_only_engine", lambda config: object())
    monkeypatch.setattr(api_lineage_mod, "infer_relationships", lambda tables: [])

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    create_tenant(session, tenant_id="tenant-b", name="Tenant B")
    session.commit()
    session.close()

    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    api_rate_limit_mod._limiters.clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _account(email: str, tenant_id: str) -> str:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        create_user(
            session, email=email, password=PASSWORD, tenant_id=tenant_id, role_name="analyst"
        )
        session.commit()
        return email
    finally:
        session.close()


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _publish_revenue_metric(tenant_id: str) -> None:
    """Creates and publishes a real `total_revenue` metric for `tenant_id`,
    reading `FactSales.SalesAmount` -- the full real draft -> reviewed ->
    published lifecycle, through the real repository."""
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        entry = create_entry(
            session,
            tenant_id=tenant_id,
            database_id="default",
            concept_type="metric",
            concept_key="total_revenue",
            business_name="Total Revenue",
            technical_name=None,
            description="Sum of sales amount.",
            grain=None,
            keys=[],
            relationships=[],
            domain=None,
            synonyms=[],
            business_rules=[],
            examples=[],
            evidence=[],
            confidence=1.0,
            owner=None,
            created_by_user_id=None,
            approved_expression="SUM(SalesAmount)",
            source_tables=["FactSales"],
        )
        session.commit()
        reviewer_id = uuid.uuid4()
        entry = mark_reviewed(session, entry, reviewed_by_user_id=reviewer_id)
        session.commit()
        publish_entry(session, entry, published_by_user_id=reviewer_id)
        session.commit()
    finally:
        session.close()


class TestKpiEndToEnd:
    def test_a_published_kpi_traces_upstream_to_its_source_column_and_downstream_to_its_insight(
        self, client
    ):
        _publish_revenue_metric("tenant-a")
        headers = _login(client, _account("analyst@tenant-a.example.com", "tenant-a"))

        graph_response = client.post(
            "/lineage/graph",
            headers=headers,
            json={
                "database_id": "default",
                "queries": [
                    {
                        "sql": "SELECT SUM(SalesAmount) AS TotalRevenue FROM FactSales",
                        "visualization": {"title": "Revenue", "type": "bar"},
                        "insight": {
                            "text": "Revenue is trending up.",
                            "grounded_fact_labels": ["TotalRevenue=1000"],
                        },
                        "recommendations": [{"action": "Review top products", "evidence_count": 1}],
                    }
                ],
            },
        )
        assert graph_response.status_code == 200
        graph = graph_response.json()

        metric_node = next(n for n in graph["nodes"] if n["type"] == "metric")
        assert metric_node["truth_level"] == "confirmed_business_truth"

        upstream = client.post(
            "/lineage/trace",
            headers=headers,
            json={"graph": graph, "start_node_id": metric_node["id"], "direction": "upstream"},
        ).json()
        assert any(
            step["node"]["table"] == "FactSales" and step["node"]["column"] == "SalesAmount"
            for step in upstream["steps"]
        )

        query_node = next(n for n in graph["nodes"] if n["type"] == "query")
        downstream = client.post(
            "/lineage/trace",
            headers=headers,
            json={"graph": graph, "start_node_id": query_node["id"], "direction": "downstream"},
        ).json()
        downstream_types = {step["node"]["type"] for step in downstream["steps"]}
        assert downstream_types == {"visualization", "insight", "recommendation"}
        # Every downstream insight/recommendation stays disclosed as an
        # inference, never promoted, even reached through the real API.
        for step in downstream["steps"]:
            if step["node"]["type"] in ("insight", "recommendation"):
                assert step["node"]["truth_level"] == "ai_inference"


class TestCrossTenantIsolation:
    def test_tenant_bs_lineage_graph_never_contains_tenant_as_published_metric(self, client):
        _publish_revenue_metric("tenant-a")
        headers_b = _login(client, _account("analyst@tenant-b.example.com", "tenant-b"))

        response = client.post("/lineage/graph", headers=headers_b, json={"database_id": "default"})
        assert response.status_code == 200
        body = response.json()
        concept_keys = {n["concept_key"] for n in body["nodes"] if n["concept_key"]}
        assert "total_revenue" not in concept_keys

    def test_tenant_a_still_sees_its_own_published_metric(self, client):
        _publish_revenue_metric("tenant-a")
        headers_a = _login(client, _account("analyst2@tenant-a.example.com", "tenant-a"))

        response = client.post("/lineage/graph", headers=headers_a, json={"database_id": "default"})
        assert response.status_code == 200
        concept_keys = {n["concept_key"] for n in response.json()["nodes"] if n["concept_key"]}
        assert "total_revenue" in concept_keys

    def test_a_database_outside_the_tenants_own_set_is_a_404_not_a_leak(self, client, monkeypatch):
        # A tenant-restricted connection named "other" that tenant-a may not
        # use -- confirms the exact anti-enumeration convention
        # GET /schema/tables already establishes is applied here too.
        monkeypatch.setattr(
            Settings,
            "databases_for_tenant",
            lambda self, tenant_id: () if tenant_id == "tenant-a" else self.databases,
        )
        headers_a = _login(client, _account("analyst3@tenant-a.example.com", "tenant-a"))
        response = client.post("/lineage/graph", headers=headers_a, json={"database_id": "default"})
        assert response.status_code == 404

    def test_a_stale_check_also_never_crosses_tenants(self, client):
        _publish_revenue_metric("tenant-a")
        headers_b = _login(client, _account("analyst4@tenant-b.example.com", "tenant-b"))

        response = client.post(
            "/lineage/stale-check",
            headers=headers_b,
            json={"database_id": "default", "recorded_catalog_versions": {"total_revenue": 1}},
        )
        assert response.status_code == 200
        # Tenant B never published this concept, so from tenant B's own
        # vantage point it was "removed" (never existed for them) --
        # never silently matched against tenant A's real row.
        assert any(f["kind"] == "catalog_entry_removed" for f in response.json()["findings"])
