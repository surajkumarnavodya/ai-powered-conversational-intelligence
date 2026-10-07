"""Tests for POST /scenarios/validate and POST /scenarios/run (Prompt 38,
`api/scenarios.py`).

Mirrors `tests/test_api_analyst.py`'s own structure: the engine itself is
covered by `tests/test_analytics_scenario.py`; these tests pin the
route's own contract -- the feature flag, request validation, payload
bounds, that roles come from the server identity and never the client,
and the response shape (including that the chart spec, when present,
discloses the scenario nature).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
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
    enable_what_if_scenarios=True,
    scenario_max_parameters=20,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE.__dict__, **overrides})


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr("api.scenarios.get_settings", lambda: _settings())
    monkeypatch.setattr("api.main.get_settings", lambda: _settings())


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _payload(**overrides: object) -> dict:
    base = {
        "columns": ["Region", "Revenue"],
        "rows": [["North", 100.0], ["South", 200.0]],
        "dimension_columns": ["Region"],
        "parameters": [
            {
                "name": "North +20%",
                "affected_metric": "Revenue",
                "unit": "percent",
                "change_type": "percent_change",
                "scenario_value": 20.0,
                "target_label": "North",
            }
        ],
    }
    base.update(overrides)
    return base


class TestFeatureFlag:
    def test_validate_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.scenarios.get_settings", lambda: _settings(enable_what_if_scenarios=False)
        )
        response = client.post("/scenarios/validate", json=_payload())
        assert response.status_code == 404

    def test_run_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.scenarios.get_settings", lambda: _settings(enable_what_if_scenarios=False)
        )
        response = client.post(
            "/scenarios/run", json={**_payload(), "scenario_id": "s1", "name": "Test"}
        )
        assert response.status_code == 404


class TestValidate:
    def test_a_valid_parameter_is_reported_valid_with_its_real_baseline(self, client):
        response = client.post("/scenarios/validate", json=_payload())
        assert response.status_code == 200
        body = response.json()
        validation = body["parameter_validations"][0]
        assert validation["is_valid"] is True
        assert validation["baseline_value"] == 100.0
        assert validation["implied_scenario_value"] == 120.0

    def test_an_unknown_column_is_reported_invalid(self, client):
        payload = _payload(
            parameters=[
                {
                    "name": "Bad",
                    "affected_metric": "NoSuchColumn",
                    "unit": "percent",
                    "change_type": "percent_change",
                    "scenario_value": 10.0,
                }
            ]
        )
        response = client.post("/scenarios/validate", json=payload)
        assert response.status_code == 200
        validation = response.json()["parameter_validations"][0]
        assert validation["is_valid"] is False
        assert any("unknown column" in v for v in validation["violations"])

    def test_an_unrecognized_change_type_is_a_422_not_a_500(self, client):
        payload = _payload(
            parameters=[
                {
                    "name": "Bad",
                    "affected_metric": "Revenue",
                    "unit": "percent",
                    "change_type": "not_a_real_type",
                    "scenario_value": 10.0,
                }
            ]
        )
        response = client.post("/scenarios/validate", json=payload)
        assert response.status_code == 422

    def test_a_client_supplied_caller_roles_field_is_rejected(self, client):
        response = client.post(
            "/scenarios/validate", json={**_payload(), "caller_roles": ["admin"]}
        )
        assert response.status_code == 422

    def test_too_many_rows_is_a_422_not_a_500(self, monkeypatch, client):
        monkeypatch.setattr("api.scenarios.get_settings", lambda: _settings(max_result_rows=1))
        response = client.post("/scenarios/validate", json=_payload())
        assert response.status_code == 422

    def test_too_many_parameters_is_a_422_not_a_500(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.scenarios.get_settings", lambda: _settings(scenario_max_parameters=1)
        )
        payload = _payload(
            parameters=[
                {
                    "name": "P1",
                    "affected_metric": "Revenue",
                    "unit": "percent",
                    "change_type": "percent_change",
                    "scenario_value": 10.0,
                },
                {
                    "name": "P2",
                    "affected_metric": "Revenue",
                    "unit": "percent",
                    "change_type": "percent_change",
                    "scenario_value": 5.0,
                },
            ]
        )
        response = client.post("/scenarios/validate", json=payload)
        assert response.status_code == 422


class TestRun:
    def test_a_valid_scenario_runs_and_returns_a_comparison(self, client):
        response = client.post(
            "/scenarios/run", json={**_payload(), "scenario_id": "s1", "name": "North +20%"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        comparison = body["comparisons"][0]
        assert comparison["metric"] == "Revenue"
        assert comparison["baseline_value"] == 300.0
        assert comparison["scenario_value"] == 320.0
        assert comparison["baseline_kind"] == "observed"
        assert comparison["scenario_kind"] == "modeled_estimate"
        assert body["truth_level"] == "ai_inference"

    def test_an_invalid_parameter_rejects_the_whole_scenario(self, client):
        payload = _payload(
            parameters=[
                {
                    "name": "Bad",
                    "affected_metric": "NoSuchColumn",
                    "unit": "percent",
                    "change_type": "percent_change",
                    "scenario_value": 10.0,
                }
            ]
        )
        response = client.post(
            "/scenarios/run", json={**payload, "scenario_id": "s2", "name": "Bad scenario"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert body["comparisons"] == []
        assert body["rejection_reasons"]

    def test_the_response_includes_a_visualization_spec_that_discloses_the_scenario_nature(
        self, client
    ):
        response = client.post(
            "/scenarios/run", json={**_payload(), "scenario_id": "s3", "name": "North +20%"}
        )
        assert response.status_code == 200
        spec = response.json()["visualization_spec"]
        assert spec is not None
        assert "modeled estimate" in " ".join(spec["notices"]).lower()

    def test_a_client_supplied_tenant_id_field_is_rejected(self, client):
        response = client.post(
            "/scenarios/run",
            json={**_payload(), "scenario_id": "s4", "name": "X", "tenant_id": "someone-else"},
        )
        assert response.status_code == 422
