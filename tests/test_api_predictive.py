"""Tests for POST /predictive/train and POST /predictive/drift (Prompt
40, `api/predictive.py`).

Mirrors `tests/test_api_optimization.py`'s own structure: the engine
itself is covered by `tests/test_predictive_analytics.py`; these tests
pin the route's own contract -- the feature flag, request validation,
payload bounds, that roles come from the server identity and never the
client, and the response shape.
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
    enable_predictive_analytics=True,
    predictive_max_features=20,
    predictive_max_predict_rows=200,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE.__dict__, **overrides})


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr("api.predictive.get_settings", lambda: _settings())
    monkeypatch.setattr("api.main.get_settings", lambda: _settings())


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _regression_rows() -> list[list[float]]:
    rows = []
    for i in range(1, 41):
        x1 = float(i)
        x2 = float((i * 7) % 13)
        y = 2 * x1 + 3 * x2 + 5.0
        rows.append([x1, x2, y])
    return rows


def _train_payload(**overrides: object) -> dict:
    base: dict[str, object] = {
        "model_id": "m1",
        "name": "Demand model",
        "domain": "demand",
        "task_type": "regression",
        "model_type": "linear_regression",
        "columns": ["x1", "x2", "y"],
        "rows": _regression_rows(),
        "feature_columns": ["x1", "x2"],
        "target_column": "y",
    }
    base.update(overrides)
    return base


class TestFeatureFlag:
    def test_train_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.predictive.get_settings", lambda: _settings(enable_predictive_analytics=False)
        )
        response = client.post("/predictive/train", json=_train_payload())
        assert response.status_code == 404

    def test_drift_is_hidden_when_the_feature_flag_is_off(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.predictive.get_settings", lambda: _settings(enable_predictive_analytics=False)
        )
        response = client.post(
            "/predictive/drift",
            json={
                "reference_columns": ["a"],
                "reference_rows": [[1.0]],
                "new_columns": ["a"],
                "new_rows": [[1.0]],
                "feature_columns": ["a"],
            },
        )
        assert response.status_code == 404


class TestTrain:
    def test_a_valid_regression_request_trains_to_the_hand_verified_fit(self, client):
        response = client.post("/predictive/train", json=_train_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "trained"
        assert body["regression_metrics"]["r2"] == pytest.approx(1.0, abs=1e-6)
        coefficients = body["metadata"]["coefficients"]
        assert coefficients["x1"] == pytest.approx(2.0, abs=1e-6)
        assert coefficients["x2"] == pytest.approx(3.0, abs=1e-6)
        assert body["metadata"]["intercept"] == pytest.approx(5.0, abs=1e-6)
        assert body["truth_level"] == "ai_inference"

    def test_predict_rows_are_scored_with_the_trained_model(self, client):
        response = client.post("/predictive/train", json=_train_payload(predict_rows=[[50.0, 2.0]]))
        body = response.json()
        assert len(body["predictions"]) == 1
        assert body["predictions"][0]["value"] == pytest.approx(111.0, abs=1e-6)

    def test_an_unrecognized_domain_is_a_422_not_a_500(self, client):
        response = client.post("/predictive/train", json=_train_payload(domain="not_a_domain"))
        assert response.status_code == 422

    def test_a_client_supplied_caller_roles_field_is_rejected(self, client):
        response = client.post(
            "/predictive/train", json={**_train_payload(), "caller_roles": ["admin"]}
        )
        assert response.status_code == 422

    def test_leakage_is_detected_and_rejected(self, client):
        payload = _train_payload(
            columns=["a", "b"],
            rows=[[float(i), float(i)] for i in range(1, 31)],
            feature_columns=["a"],
            target_column="b",
        )
        response = client.post("/predictive/train", json=payload)
        body = response.json()
        assert body["status"] == "leakage_detected"
        assert body["leakage_findings"]

    def test_insufficient_data_is_reported_distinctly(self, client):
        payload = _train_payload(rows=_regression_rows()[:5])
        response = client.post("/predictive/train", json=payload)
        body = response.json()
        assert body["status"] == "insufficient_data"

    def test_too_many_rows_is_a_422_not_a_500(self, monkeypatch, client):
        monkeypatch.setattr("api.predictive.get_settings", lambda: _settings(max_result_rows=1))
        response = client.post("/predictive/train", json=_train_payload())
        assert response.status_code == 422

    def test_too_many_features_is_a_422_not_a_500(self, monkeypatch, client):
        monkeypatch.setattr(
            "api.predictive.get_settings", lambda: _settings(predictive_max_features=1)
        )
        response = client.post("/predictive/train", json=_train_payload())
        assert response.status_code == 422

    def test_classification_task_trains_and_evaluates(self, client):
        rows = [[float(i), "yes" if i < 20 else "no"] for i in range(1, 61)]
        payload = _train_payload(
            task_type="classification",
            model_type="logistic_regression",
            columns=["tenure", "churn"],
            rows=rows,
            feature_columns=["tenure"],
            target_column="churn",
            domain="churn",
        )
        response = client.post("/predictive/train", json=payload)
        body = response.json()
        assert body["status"] == "trained"
        assert body["classification_metrics"] is not None
        assert body["regression_metrics"] is None


class TestDrift:
    def test_identical_distributions_report_no_drift(self, client):
        payload = {
            "reference_columns": ["a"],
            "reference_rows": [[float(i)] for i in range(1, 31)],
            "new_columns": ["a"],
            "new_rows": [[float(i) + 0.1] for i in range(1, 31)],
            "feature_columns": ["a"],
        }
        response = client.post("/predictive/drift", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["any_drifted"] is False

    def test_shifted_distributions_report_drift(self, client):
        payload = {
            "reference_columns": ["a"],
            "reference_rows": [[float(i)] for i in range(1, 31)],
            "new_columns": ["a"],
            "new_rows": [[float(i) + 50.0] for i in range(1, 31)],
            "feature_columns": ["a"],
        }
        response = client.post("/predictive/drift", json=payload)
        body = response.json()
        assert body["any_drifted"] is True
        assert body["findings"][0]["is_drifted"] is True

    def test_a_client_supplied_tenant_id_field_is_rejected(self, client):
        response = client.post(
            "/predictive/drift",
            json={
                "reference_columns": ["a"],
                "reference_rows": [[1.0]],
                "new_columns": ["a"],
                "new_rows": [[1.0]],
                "feature_columns": ["a"],
                "tenant_id": "someone-else",
            },
        )
        assert response.status_code == 422
