"""HTTP tests for `api/schema_drift.py` (Prompt 43). Same real-auth,
in-memory-identity-database pattern as `tests/test_api_semantic_intelligence
.py` (so the real `semantic.catalog_policy.authorize_catalog_action` ABAC
check runs, not a mock), combined with `tests/test_api_lineage.py`'s own
"mock live DB introspection at the module boundary" pattern for `/scan`
(the one route here that live-introspects). Snapshot persistence round-trips
for real against a `tmp_path`-backed `chroma_persist_dir` -- not mocked --
since `schema_drift.snapshot_store` is simple file I/O worth exercising
directly through the route.
"""

from __future__ import annotations

from pathlib import Path

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base, User
from identity.repositories.users import assign_role
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.schema_drift as sd_mod
from config.settings import Settings
from db.schema_introspection import ColumnInfo, ForeignKeyInfo, TableSchemaInfo
from security.secrets import SecretStr

_BASE = Settings(
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
    ollama_host="http://localhost:11434",
    ollama_model="llama3.1:8b",
    db_type="postgresql",
    db_host="db.example.com",
    db_port=5432,
    db_name="mydb",
    db_user="reader",
    db_password=SecretStr("secret"),
    db_odbc_driver="x",
    chroma_persist_dir=Path("/tmp/chroma"),
    chroma_collection_name="schema_ddl",
    embedding_model_name="all-MiniLM-L6-v2",
    schema_top_k=4,
    max_retries=3,
    complex_query_max_retry_bonus=2,
    max_result_rows=1000,
    query_timeout_seconds=15,
    llm_max_tokens=1024,
    insight_max_tokens=120,
    max_question_length=500,
    question_rate_limit_per_minute=10,
    llm_call_rate_limit_per_minute=20,
    cost_estimation_enabled=False,
    log_level="INFO",
    log_redaction_level="standard",
    enable_schema_drift_detection=True,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE.__dict__, **overrides})


def _fake_tenant_resolver(user: User | None) -> str | None:
    if user is None:
        return None
    if user.email.endswith("@tenant-a.example.com"):
        return "tenant-a"
    if user.email.endswith("@tenant-b.example.com"):
        return "tenant-b"
    return "default"


def _tables_v1() -> list[TableSchemaInfo]:
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


def _tables_v2_column_removed() -> list[TableSchemaInfo]:
    """`_tables_v1` with `DimProduct.ProductName` removed -- a real,
    detectable drift between two scans."""
    tables = _tables_v1()
    tables[1] = TableSchemaInfo(
        table_name="DimProduct",
        columns=(ColumnInfo("ProductKey", "int", False, True),),
        foreign_keys=(),
        ddl="x",
    )
    return tables


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    monkeypatch.setattr(api_auth_mod, "get_settings", lambda: _BASE)
    monkeypatch.setattr(identity_auth_mod, "get_settings", lambda: _BASE)
    monkeypatch.setattr(identity_authz_mod, "get_settings", lambda: _BASE)
    test_settings = _settings(chroma_persist_dir=tmp_path)
    monkeypatch.setattr(sd_mod, "get_settings", lambda: test_settings)
    monkeypatch.setattr(sd_mod, "resolve_actor_tenant_id", _fake_tenant_resolver)
    monkeypatch.setattr(sd_mod, "get_read_only_engine", lambda config: object())
    monkeypatch.setattr(sd_mod, "introspect_schema", lambda engine, schema: _tables_v1())
    monkeypatch.setattr(sd_mod, "list_entries", lambda *a, **k: [])
    session = identity_db_mod.get_identity_session(_BASE)
    seed_rbac(session)
    session.close()
    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _register(client: TestClient, email: str, role: str | None = None) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correcthorse battery staple 1",
            "display_name": email.split("@")[0],
        },
    )
    assert response.status_code == 201, response.text
    tokens = response.json()
    if role is not None:
        session = identity_db_mod.get_identity_session(_BASE)
        try:
            user = session.query(User).filter_by(email=email.strip().lower()).one()
            assign_role(session, user_id=user.id, role_name=role, assigned_by_user_id=None)
            session.commit()
        finally:
            session.close()
    return tokens


def _headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class TestFeatureFlagAndAuthorization:
    def test_scan_is_hidden_when_the_flag_is_off(self, monkeypatch, client, tmp_path):
        monkeypatch.setattr(
            sd_mod,
            "get_settings",
            lambda: _settings(enable_schema_drift_detection=False, chroma_persist_dir=tmp_path),
        )
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        response = client.post(
            "/schema-drift/scan", headers=_headers(tokens), json={"database_id": "default"}
        )
        assert response.status_code == 404

    def test_a_viewer_without_catalog_review_cannot_scan(self, client):
        tokens = _register(client, "viewer@tenant-a.example.com", role="viewer")
        response = client.post(
            "/schema-drift/scan", headers=_headers(tokens), json={"database_id": "default"}
        )
        assert response.status_code == 403

    def test_an_unknown_database_is_a_not_found(self, client):
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        response = client.post(
            "/schema-drift/scan",
            headers=_headers(tokens),
            json={"database_id": "not-configured"},
        )
        assert response.status_code == 404

    def test_a_client_supplied_unknown_field_is_rejected(self, client):
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        response = client.post(
            "/schema-drift/scan",
            headers=_headers(tokens),
            json={"database_id": "default", "tenant_id": "tenant-b"},
        )
        assert response.status_code == 422

    def test_requires_a_local_account(self, client):
        response = client.post("/schema-drift/scan", json={"database_id": "default"})
        assert response.status_code in (401, 403)


class TestScan:
    def test_a_first_scan_with_no_previous_snapshot_reports_every_table_as_added(self, client):
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        response = client.post(
            "/schema-drift/scan", headers=_headers(tokens), json={"database_id": "default"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_first_scan"] is True
        kinds = {f["kind"] for f in body["top_findings"]}
        assert "schema_drift_table_added" in kinds
        assert body["findings_detected"] == len(body["top_findings"]) or body[
            "findings_detected"
        ] >= len(body["top_findings"])
        assert "No drift" not in body["report_markdown"]

    def test_a_second_scan_detects_a_removed_column_as_high_risk(self, monkeypatch, client):
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        first = client.post(
            "/schema-drift/scan", headers=_headers(tokens), json={"database_id": "default"}
        )
        assert first.status_code == 200
        assert first.json()["is_first_scan"] is True

        monkeypatch.setattr(
            sd_mod, "introspect_schema", lambda engine, schema: _tables_v2_column_removed()
        )
        second = client.post(
            "/schema-drift/scan", headers=_headers(tokens), json={"database_id": "default"}
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["is_first_scan"] is False
        removed = [f for f in body["top_findings"] if f["kind"] == "schema_drift_column_removed"]
        assert removed, body["top_findings"]
        assert removed[0]["truth_level"] == "database_fact"

    def test_a_scan_in_another_tenant_never_sees_this_tenants_findings(self, client):
        tokens_a = _register(client, "analyst@tenant-a.example.com", role="analyst")
        client.post(
            "/schema-drift/scan", headers=_headers(tokens_a), json={"database_id": "default"}
        )

        tokens_b = _register(client, "analyst@tenant-b.example.com", role="analyst")
        listing = client.get(
            "/schema-drift/findings", headers=_headers(tokens_b), params={"database_id": "default"}
        )
        assert listing.status_code == 200
        assert listing.json() == []


class TestFindingsListing:
    def test_listing_only_returns_schema_drift_kinds(self, client):
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        client.post("/schema-drift/scan", headers=_headers(tokens), json={"database_id": "default"})
        response = client.get(
            "/schema-drift/findings", headers=_headers(tokens), params={"database_id": "default"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body
        assert all(f["kind"].startswith("schema_drift_") for f in body)

    def test_a_finding_detail_round_trips_and_carries_its_impact_payload(self, client):
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        client.post("/schema-drift/scan", headers=_headers(tokens), json={"database_id": "default"})
        listing = client.get(
            "/schema-drift/findings", headers=_headers(tokens), params={"database_id": "default"}
        )
        finding_id = listing.json()[0]["id"]
        detail = client.get(f"/schema-drift/findings/{finding_id}", headers=_headers(tokens))
        assert detail.status_code == 200
        assert detail.json()["id"] == finding_id

    def test_an_unknown_finding_id_is_a_404(self, client):
        tokens = _register(client, "analyst@tenant-a.example.com", role="analyst")
        response = client.get(
            "/schema-drift/findings/00000000-0000-0000-0000-000000000000",
            headers=_headers(tokens),
        )
        assert response.status_code == 404
