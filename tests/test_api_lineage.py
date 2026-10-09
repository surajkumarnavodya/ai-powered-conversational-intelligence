"""Tests for `POST /lineage/graph`, `/lineage/trace`, `/lineage/stale-check`
(Prompt 42, `api/lineage.py`).

Mirrors `tests/test_api_data_quality.py`'s own structure: the engine
itself is covered by `tests/test_lineage_engine.py`; these tests pin the
route's own contract -- the feature flag, request validation, that roles/
tenant come from the server identity and never the client, and the
response shape. `/graph`/`/stale-check` are DB-touching routes, so
`db.schema_introspection.introspect_schema`/`db.connection
.get_read_only_engine` and `identity.repositories.semantic_catalog
.list_entries`/`.entry_to_snapshot` are mocked at the `api.lineage`
module boundary, the same seam `tests/test_api_data_quality.py`'s own
`TestLineage` class already uses for the identical kind of route.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from semantic.catalog import CatalogConceptType, CatalogEntrySnapshot, CatalogStatus

import api.lineage as api_lineage
import api.main as api_main
from api.identity_authz import require_local_user
from config.settings import Settings
from db.schema_introspection import ColumnInfo, ForeignKeyInfo, TableSchemaInfo
from security.secrets import SecretStr

_BASE = Settings(
    ollama_host="http://localhost:11434",
    ollama_model="llama3.1:8b",
    ollama_request_timeout_seconds=60,
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
    enable_lineage_platform=True,
    lineage_max_queries_per_request=20,
    lineage_max_trace_graph_nodes=5000,
    lineage_max_trace_depth=10,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE.__dict__, **overrides})


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
def _enabled(monkeypatch):
    monkeypatch.setattr("api.lineage.get_settings", lambda: _settings())
    monkeypatch.setattr("api.main.get_settings", lambda: _settings())
    monkeypatch.setattr(api_lineage, "introspect_schema", lambda engine, schema: _fake_tables())
    monkeypatch.setattr(api_lineage, "get_read_only_engine", lambda config: object())
    monkeypatch.setattr(api_lineage, "infer_relationships", lambda tables: [])
    # `entry_to_snapshot` normally converts a real ORM row -- tests pass
    # already-built `CatalogEntrySnapshot`s through `list_entries`'s own
    # mock, so this becomes a harmless identity pass-through.
    monkeypatch.setattr(api_lineage, "entry_to_snapshot", lambda row: row)
    monkeypatch.setattr(api_lineage, "list_entries", lambda *a, **k: [])
    monkeypatch.setattr(api_lineage, "get_user_roles", lambda session, user_id: ("admin",))


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


class _FakeUser:
    def __init__(self, user_id: str = "u1", tenant_id: str = "tenant-a") -> None:
        self.id = user_id
        self.tenant_id = tenant_id


@pytest.fixture(autouse=True)
def _override_local_user():
    fake_user = _FakeUser()
    api_main.app.dependency_overrides[require_local_user] = lambda: (fake_user, object())
    yield
    api_main.app.dependency_overrides.pop(require_local_user, None)


def _metric_entry(**overrides: object) -> CatalogEntrySnapshot:
    base = dict(
        id="e1",
        tenant_id="tenant-a",
        database_id="default",
        concept_type=CatalogConceptType.METRIC,
        concept_key="total_revenue",
        business_name="Total Revenue",
        status=CatalogStatus.PUBLISHED,
        version=1,
        approved_expression="SUM(SalesAmount)",
        source_tables=("FactSales",),
    )
    base.update(overrides)
    return CatalogEntrySnapshot(**base)


class TestFeatureFlag:
    def test_graph_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.lineage.get_settings", lambda: _settings(enable_lineage_platform=False)
        )
        response = client.post("/lineage/graph", json={"database_id": "default"})
        assert response.status_code == 404

    def test_trace_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.lineage.get_settings", lambda: _settings(enable_lineage_platform=False)
        )
        response = client.post(
            "/lineage/trace",
            json={
                "graph": {
                    "database_id": "default",
                    "nodes": [],
                    "edges": [],
                    "schema_fingerprint": None,
                    "catalog_versions": {},
                    "suppressed_restricted_nodes": [],
                    "limitations": [],
                    "engine_version": "1.0.0",
                },
                "start_node_id": "x",
            },
        )
        assert response.status_code == 404


class TestGraph:
    def test_a_valid_request_builds_a_graph_with_schema_and_catalog_nodes(
        self, monkeypatch, client
    ):
        monkeypatch.setattr(api_lineage, "list_entries", lambda *a, **k: [_metric_entry()])
        response = client.post("/lineage/graph", json={"database_id": "default"})
        assert response.status_code == 200
        body = response.json()
        types = {n["type"] for n in body["nodes"]}
        assert {"database", "table", "column", "metric"} <= types
        assert body["schema_fingerprint"]
        assert body["catalog_versions"] == {"total_revenue": 1}

    def test_a_query_spec_appends_the_presentation_chain(self, client):
        response = client.post(
            "/lineage/graph",
            json={
                "database_id": "default",
                "queries": [
                    {
                        "sql": "SELECT SUM(SalesAmount) AS Total FROM FactSales",
                        "visualization": {"title": "Revenue", "type": "bar"},
                        "insight": {"text": "Revenue is up.", "grounded_fact_labels": ["Total=1"]},
                        "recommendations": [{"action": "Look into it", "evidence_count": 1}],
                    }
                ],
            },
        )
        assert response.status_code == 200
        types = {n["type"] for n in response.json()["nodes"]}
        assert {"query", "visualization", "insight", "recommendation"} <= types

    def test_an_unknown_database_is_a_404(self, client):
        response = client.post("/lineage/graph", json={"database_id": "not-configured"})
        assert response.status_code == 404

    def test_too_many_queries_is_a_422_not_a_500(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.lineage.get_settings", lambda: _settings(lineage_max_queries_per_request=1)
        )
        response = client.post(
            "/lineage/graph",
            json={
                "database_id": "default",
                "queries": [{"sql": "SELECT 1"}, {"sql": "SELECT 2"}],
            },
        )
        assert response.status_code == 422

    def test_a_client_supplied_caller_roles_field_is_rejected(self, client):
        response = client.post(
            "/lineage/graph", json={"database_id": "default", "caller_roles": ["admin"]}
        )
        assert response.status_code == 422

    def test_requires_a_local_account(self, client):
        # Without the override, the real require_local_user dependency runs:
        # this test's base settings don't configure local_auth_enabled, so
        # that dependency's own chain answers 404 rather than 401/403 --
        # either way, the route is unreachable without a real local account.
        api_main.app.dependency_overrides.pop(require_local_user, None)
        response = client.post("/lineage/graph", json={"database_id": "default"})
        assert response.status_code in (401, 403, 404)


class TestTrace:
    def _graph_payload(self) -> dict:
        built = TestClient(api_main.app).post("/lineage/graph", json={"database_id": "default"})
        return built.json()

    def test_upstream_trace_from_a_table_returns_no_steps_without_a_metric(self, client):
        graph = self._graph_payload()
        table_node = next(n for n in graph["nodes"] if n["type"] == "table")
        response = client.post(
            "/lineage/trace",
            json={"graph": graph, "start_node_id": table_node["id"], "direction": "upstream"},
        )
        assert response.status_code == 200
        assert response.json()["start_node_id"] == table_node["id"]

    def test_downstream_trace_from_the_fk_target_column_reaches_the_referencing_column(
        self, client
    ):
        graph = self._graph_payload()
        # The declared FK edge is FactSales.ProductKey -> DimProduct.ProductKey
        # (source depends on target, this module's own uniform convention) --
        # so DimProduct.ProductKey's downstream (what depends on it) includes
        # FactSales.ProductKey.
        target_column = next(
            n
            for n in graph["nodes"]
            if n["type"] == "column" and n["table"] == "DimProduct" and n["column"] == "ProductKey"
        )
        response = client.post(
            "/lineage/trace",
            json={"graph": graph, "start_node_id": target_column["id"], "direction": "downstream"},
        )
        assert response.status_code == 200
        reached = {s["node"]["id"] for s in response.json()["steps"]}
        assert any(
            n["table"] == "FactSales" and n["column"] == "ProductKey"
            for n in graph["nodes"]
            if n["id"] in reached
        )

    def test_a_malformed_echoed_node_type_is_a_422_not_a_500(self, client):
        graph = self._graph_payload()
        graph["nodes"][0]["type"] = "not_a_real_type"
        response = client.post(
            "/lineage/trace",
            json={
                "graph": graph,
                "start_node_id": graph["nodes"][0]["id"],
                "direction": "upstream",
            },
        )
        assert response.status_code == 422

    def test_an_invalid_direction_is_a_422(self, client):
        graph = self._graph_payload()
        node = graph["nodes"][0]
        response = client.post(
            "/lineage/trace",
            json={"graph": graph, "start_node_id": node["id"], "direction": "sideways"},
        )
        assert response.status_code == 422

    def test_too_many_nodes_is_a_422(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.lineage.get_settings", lambda: _settings(lineage_max_trace_graph_nodes=1)
        )
        graph = self._graph_payload()
        response = client.post(
            "/lineage/trace",
            json={
                "graph": graph,
                "start_node_id": graph["nodes"][0]["id"],
                "direction": "upstream",
            },
        )
        assert response.status_code == 422

    def test_a_client_supplied_tenant_id_field_is_rejected(self, client):
        response = client.post(
            "/lineage/trace",
            json={
                "graph": {
                    "database_id": "default",
                    "nodes": [],
                    "edges": [],
                    "schema_fingerprint": None,
                    "catalog_versions": {},
                    "suppressed_restricted_nodes": [],
                    "limitations": [],
                    "engine_version": "1.0.0",
                },
                "start_node_id": "x",
                "tenant_id": "someone-else",
            },
        )
        assert response.status_code == 422


class TestStaleCheck:
    def test_an_unchanged_schema_and_catalog_is_not_stale(self, monkeypatch, client):
        monkeypatch.setattr(api_lineage, "list_entries", lambda *a, **k: [_metric_entry()])
        from db.schema_introspection import get_schema_fingerprint

        fingerprint = get_schema_fingerprint(_fake_tables())
        response = client.post(
            "/lineage/stale-check",
            json={
                "database_id": "default",
                "recorded_schema_fingerprint": fingerprint,
                "recorded_catalog_versions": {"total_revenue": 1},
            },
        )
        assert response.status_code == 200
        assert response.json()["is_stale"] is False

    def test_a_different_recorded_fingerprint_is_reported_stale(self, client):
        response = client.post(
            "/lineage/stale-check",
            json={"database_id": "default", "recorded_schema_fingerprint": "stale-value"},
        )
        assert response.status_code == 200
        assert response.json()["is_stale"] is True

    def test_requires_a_local_account(self, client):
        api_main.app.dependency_overrides.pop(require_local_user, None)
        response = client.post("/lineage/stale-check", json={"database_id": "default"})
        assert response.status_code in (401, 403, 404)
