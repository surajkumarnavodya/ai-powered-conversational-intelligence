"""Tests for POST /data-quality/scan and POST /data-quality/lineage
(Prompt 41, `api/data_quality.py`).

Mirrors `tests/test_api_predictive.py`'s own structure: the engine itself
is covered by `tests/test_data_quality_engine.py`/
`tests/test_data_quality_lineage.py`; these tests pin the route's own
contract -- the feature flag, request validation, payload bounds, and
the response shape. `/lineage` touches the identity database, so its own
identity-DB dependency (`api.identity_authz.require_local_user`) is
overridden with a lightweight stand-in rather than standing up a real
database -- this codebase's own established "fully mocked" posture for
`tests/` (see that directory's own module docstring).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.data_quality as api_data_quality
import api.main as api_main
from api.identity_authz import require_local_user
from config.settings import Settings
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
    enable_data_quality_engine=True,
    data_quality_max_tables=20,
    data_quality_max_rows_per_table=5000,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE.__dict__, **overrides})


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr("api.data_quality.get_settings", lambda: _settings())
    monkeypatch.setattr("api.main.get_settings", lambda: _settings())


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _scan_payload(**overrides: object) -> dict:
    base: dict = {
        "tables": [
            {
                "table": "customers",
                "columns": ["id", "email"],
                "rows": [[1, "a@x.com"], [2, None], [3, None]],
            }
        ]
    }
    base.update(overrides)
    return base


class TestFeatureFlag:
    def test_scan_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.data_quality.get_settings", lambda: _settings(enable_data_quality_engine=False)
        )
        response = client.post("/data-quality/scan", json=_scan_payload())
        assert response.status_code == 404

    def test_lineage_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.data_quality.get_settings", lambda: _settings(enable_data_quality_engine=False)
        )
        response = client.post(
            "/data-quality/lineage",
            json={
                "database_id": "default",
                "tables": [{"table": "customers", "severity_counts": {}}],
            },
        )
        assert response.status_code == 404


class TestScan:
    def test_a_valid_scan_finds_the_hand_verified_null_spike(self, client):
        response = client.post("/data-quality/scan", json=_scan_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["truth_level"] == "database_fact"
        findings = body["tables"][0]["findings"]
        completeness = [f for f in findings if f["dimension"] == "completeness"]
        assert len(completeness) == 1
        assert completeness[0]["null_spike"]["null_count"] == 2
        assert completeness[0]["null_spike"]["total_count"] == 3
        assert completeness[0]["claim"]["level"] == "database_fact"

    def test_too_many_tables_is_rejected(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.data_quality.get_settings", lambda: _settings(data_quality_max_tables=1)
        )
        payload = _scan_payload()
        payload["tables"] = payload["tables"] * 2
        response = client.post("/data-quality/scan", json=payload)
        assert response.status_code == 422

    def test_too_many_rows_for_one_table_is_rejected(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.data_quality.get_settings", lambda: _settings(data_quality_max_rows_per_table=1)
        )
        response = client.post("/data-quality/scan", json=_scan_payload())
        assert response.status_code == 422

    def test_an_unknown_field_is_rejected(self, client):
        payload = _scan_payload()
        payload["tables"][0]["not_a_real_field"] = True
        response = client.post("/data-quality/scan", json=payload)
        assert response.status_code == 422

    def test_consistency_and_referential_and_freshness_rules_round_trip(self, client):
        payload = {
            "tables": [
                {
                    "table": "orders",
                    "columns": ["id", "start_date", "end_date", "product_id"],
                    "rows": [
                        [1, "2024-01-01", "2024-01-05", "P1"],
                        [2, "2024-02-10", "2024-02-05", "P9"],
                    ],
                    "consistency_rules": [
                        {
                            "name": "start_before_end",
                            "left_column": "start_date",
                            "operator": "<=",
                            "right_column": "end_date",
                        }
                    ],
                    "referential_checks": [
                        {
                            "child_column": "product_id",
                            "parent_table": "products",
                            "parent_key_values": ["P1"],
                        }
                    ],
                    "freshness_rule": {
                        "timestamp_column": "start_date",
                        "as_of": "2024-06-01T00:00:00",
                        "max_staleness_seconds": 86400,
                    },
                    "enable_column_statistics": False,
                }
            ]
        }
        response = client.post("/data-quality/scan", json=payload)
        assert response.status_code == 200
        dimensions = {f["dimension"] for f in response.json()["tables"][0]["findings"]}
        assert dimensions == {"consistency", "referential_integrity", "freshness"}


class TestLineage:
    @pytest.fixture(autouse=True)
    def _override_local_user(self):
        fake_user = type("FakeUser", (), {"id": "u1", "tenant_id": "tenant-a"})()
        api_main.app.dependency_overrides[require_local_user] = lambda: (fake_user, object())
        yield
        api_main.app.dependency_overrides.pop(require_local_user, None)

    def test_a_table_is_linked_to_a_published_concept_declaring_it(self, monkeypatch, client):
        fake_entry = type(
            "FakeEntry",
            (),
            {
                "concept_key": "customer_ltv",
                "business_name": "Customer LTV",
                "concept_type": "metric",
                "status": "published",
                "owner": "analyst",
                "database_id": "default",
                "source_tables": ["customers"],
            },
        )()
        monkeypatch.setattr(api_data_quality, "list_entries", lambda *a, **k: [fake_entry])

        response = client.post(
            "/data-quality/lineage",
            json={
                "database_id": "default",
                "tables": [{"table": "customers", "severity_counts": {"warning": 2}}],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["impacts"][0]["table"] == "customers"
        assert body["impacts"][0]["affected_concepts"][0]["concept_key"] == "customer_ltv"
        assert body["unmapped_tables"] == []
        assert body["limitations"]

    def test_an_unmapped_table_is_disclosed(self, monkeypatch, client):
        monkeypatch.setattr(api_data_quality, "list_entries", lambda *a, **k: [])
        response = client.post(
            "/data-quality/lineage",
            json={
                "database_id": "default",
                "tables": [{"table": "orders", "severity_counts": {"critical": 1}}],
            },
        )
        assert response.status_code == 200
        assert response.json()["unmapped_tables"] == ["orders"]

    def test_an_unrecognized_min_severity_is_a_422_not_a_500(self, client):
        response = client.post(
            "/data-quality/lineage",
            json={
                "database_id": "default",
                "tables": [{"table": "customers", "severity_counts": {}}],
                "min_severity": "catastrophic",
            },
        )
        assert response.status_code == 422
