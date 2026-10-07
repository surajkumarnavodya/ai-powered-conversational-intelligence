"""Tests for POST /optimization/validate and POST /optimization/solve
(Prompt 39, `api/optimization.py`).

Mirrors `tests/test_api_scenarios.py`'s own structure: the engine itself
is covered by `tests/test_optimization_engine.py`; these tests pin the
route's own contract -- the feature flag, request validation, payload
bounds, that roles come from the server identity and never the client,
and the response shape.
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
    enable_optimization_engine=True,
    optimization_max_variables=50,
    optimization_max_constraints=100,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE.__dict__, **overrides})


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr("api.optimization.get_settings", lambda: _settings())
    monkeypatch.setattr("api.main.get_settings", lambda: _settings())


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _problem(**overrides: object) -> dict:
    base: dict[str, object] = {
        "problem_id": "p1",
        "name": "Textbook LP",
        "domain": "other",
        "decision_variables": [
            {"name": "x", "var_type": "continuous", "lower_bound": 0.0},
            {"name": "y", "var_type": "continuous", "lower_bound": 0.0},
        ],
        "objective": {
            "sense": "maximize",
            "terms": [
                {"variable": "x", "coefficient": 3.0},
                {"variable": "y", "coefficient": 5.0},
            ],
        },
        "constraints": [
            {
                "name": "c1",
                "terms": [{"variable": "x", "coefficient": 1.0}],
                "operator": "<=",
                "rhs": 4.0,
            },
            {
                "name": "c2",
                "terms": [{"variable": "y", "coefficient": 2.0}],
                "operator": "<=",
                "rhs": 12.0,
            },
            {
                "name": "c3",
                "terms": [
                    {"variable": "x", "coefficient": 3.0},
                    {"variable": "y", "coefficient": 2.0},
                ],
                "operator": "<=",
                "rhs": 18.0,
            },
        ],
    }
    base.update(overrides)
    return base


class TestFeatureFlag:
    def test_validate_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.optimization.get_settings", lambda: _settings(enable_optimization_engine=False)
        )
        response = client.post("/optimization/validate", json={"problem": _problem()})
        assert response.status_code == 404

    def test_solve_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.optimization.get_settings", lambda: _settings(enable_optimization_engine=False)
        )
        response = client.post("/optimization/solve", json={"problem": _problem()})
        assert response.status_code == 404


class TestValidate:
    def test_a_valid_problem_is_reported_valid(self, client):
        response = client.post("/optimization/validate", json={"problem": _problem()})
        assert response.status_code == 200
        body = response.json()
        assert body["is_valid"] is True
        assert body["violations"] == []

    def test_an_undeclared_variable_reference_is_invalid(self, client):
        problem = _problem(
            objective={
                "sense": "maximize",
                "terms": [{"variable": "not_declared", "coefficient": 1.0}],
            }
        )
        response = client.post("/optimization/validate", json={"problem": problem})
        assert response.status_code == 200
        body = response.json()
        assert body["is_valid"] is False
        assert any("undeclared variable" in v for v in body["violations"])

    def test_an_unrecognized_var_type_is_a_422_not_a_500(self, client):
        problem = _problem(
            decision_variables=[{"name": "x", "var_type": "not_a_real_type", "lower_bound": 0.0}]
        )
        response = client.post("/optimization/validate", json={"problem": problem})
        assert response.status_code == 422

    def test_a_client_supplied_caller_roles_field_is_rejected(self, client):
        response = client.post(
            "/optimization/validate",
            json={"problem": _problem(), "caller_roles": ["admin"]},
        )
        assert response.status_code == 422

    def test_too_many_variables_is_a_422_not_a_500(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.optimization.get_settings", lambda: _settings(optimization_max_variables=1)
        )
        response = client.post("/optimization/validate", json={"problem": _problem()})
        assert response.status_code == 422

    def test_too_many_constraints_is_a_422_not_a_500(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.optimization.get_settings", lambda: _settings(optimization_max_constraints=1)
        )
        response = client.post("/optimization/validate", json={"problem": _problem()})
        assert response.status_code == 422


class TestSolve:
    def test_a_valid_problem_solves_to_the_hand_verified_optimum(self, client):
        response = client.post("/optimization/solve", json={"problem": _problem()})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "optimal"
        assert body["objective_value"] == pytest.approx(36.0)
        values = {v["variable"]: v["value"] for v in body["variables"]}
        assert values == pytest.approx({"x": 2.0, "y": 6.0})
        assert body["sensitivity_available"] is True
        assert body["truth_level"] == "ai_inference"

    def test_sensitivity_includes_the_hand_verified_shadow_prices(self, client):
        response = client.post("/optimization/solve", json={"problem": _problem()})
        body = response.json()
        sensitivity = {c["constraint"]: c for c in body["constraint_sensitivity"]}
        assert sensitivity["c1"]["binding"] is False
        assert sensitivity["c2"]["shadow_price"] == pytest.approx(1.5)
        assert sensitivity["c3"]["shadow_price"] == pytest.approx(1.0)

    def test_an_invalid_problem_is_never_partially_solved(self, client):
        problem = _problem(
            objective={
                "sense": "maximize",
                "terms": [{"variable": "not_declared", "coefficient": 1.0}],
            }
        )
        response = client.post("/optimization/solve", json={"problem": problem})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "invalid"
        assert body["variables"] == []
        assert body["objective_value"] is None

    def test_an_infeasible_problem_is_reported_distinctly_from_invalid(self, client):
        problem = _problem(
            decision_variables=[{"name": "x", "var_type": "continuous", "lower_bound": 5.0}],
            objective={"sense": "minimize", "terms": [{"variable": "x", "coefficient": 1.0}]},
            constraints=[
                {
                    "name": "c",
                    "terms": [{"variable": "x", "coefficient": 1.0}],
                    "operator": "<=",
                    "rhs": 2.0,
                }
            ],
        )
        response = client.post("/optimization/solve", json={"problem": problem})
        body = response.json()
        assert body["status"] == "infeasible"
        assert body["infeasibility_reasons"]

    def test_a_mixed_integer_problem_never_reports_sensitivity(self, client):
        problem = _problem(
            decision_variables=[
                {"name": "x", "var_type": "integer", "lower_bound": 0.0, "upper_bound": 10.0},
                {"name": "y", "var_type": "continuous", "lower_bound": 0.0, "upper_bound": 10.0},
            ],
            objective={
                "sense": "maximize",
                "terms": [
                    {"variable": "x", "coefficient": 1.0},
                    {"variable": "y", "coefficient": 1.0},
                ],
            },
            constraints=[
                {
                    "name": "c",
                    "terms": [
                        {"variable": "x", "coefficient": 1.0},
                        {"variable": "y", "coefficient": 1.0},
                    ],
                    "operator": "<=",
                    "rhs": 10.5,
                }
            ],
        )
        response = client.post("/optimization/solve", json={"problem": problem})
        body = response.json()
        assert body["status"] == "optimal"
        assert body["sensitivity_available"] is False
        assert all(c["shadow_price"] is None for c in body["constraint_sensitivity"])
        assert all(v["reduced_cost"] is None for v in body["variables"])

    def test_a_client_supplied_tenant_id_field_is_rejected(self, client):
        response = client.post(
            "/optimization/solve",
            json={"problem": _problem(), "tenant_id": "someone-else"},
        )
        assert response.status_code == 422
