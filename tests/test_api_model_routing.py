"""Tests for `/model-routing` (Prompt 47, `api/model_routing.py`).

The routing engine itself is covered by `tests/test_model_routing.py`;
these tests pin the routes' own contract -- the feature flag, request
validation, that the preview reflects the real policy rather than a
parallel implementation, that previewing consumes no budget or breaker
probe, and that nothing in a response carries question/answer content.

Mirrors `tests/test_api_optimization.py`'s structure, including its
`AUTH_MODE=none` posture (role-specific denial for `/model-routing/metrics`
is covered in `tests/test_api_authz.py`, alongside the identical existing
case for `GET /metrics/performance`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from config.settings import Settings
from model_routing.metrics import get_route_metrics
from model_routing.models import ModelTier, RouteFailureKind, RouteOutcome, RouteTask
from security.secrets import SecretStr

SMALL = "llama3.2:3b"
DEFAULT = "llama3.1:8b"
LARGE = "qwen2.5:14b"

_BASE = Settings(
    ollama_host="http://localhost:11434",
    ollama_model=DEFAULT,
    ollama_allowed_models=f"{SMALL},{LARGE}",
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
    max_result_rows=1000,
    query_timeout_seconds=15,
    llm_max_tokens=1024,
    insight_max_tokens=120,
    max_question_length=500,
    question_rate_limit_per_minute=10,
    llm_call_rate_limit_per_minute=20,
    api_action_rate_limit_per_minute=1000,
    cost_estimation_enabled=False,
    log_level="INFO",
    log_redaction_level="standard",
    enable_model_routing=True,
    model_routing_require_evaluation_evidence=False,
)

HARD_QUESTION = "top 5 products per region with year-over-year growth and total margin ratio"
SIMPLE_QUESTION = "how many employees are there"


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE.__dict__, **overrides})


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr("api.model_routing.get_settings", lambda: _settings())
    monkeypatch.setattr("api.main.get_settings", lambda: _settings())


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


class TestFeatureFlag:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("get", "/model-routing/policy", None),
            ("post", "/model-routing/preview", {"question": SIMPLE_QUESTION}),
            ("get", "/model-routing/metrics", None),
        ],
    )
    def test_every_route_404s_when_routing_is_off(self, monkeypatch, client, method, path, payload):
        monkeypatch.setattr(
            "api.model_routing.get_settings", lambda: _settings(enable_model_routing=False)
        )
        response = getattr(client, method)(path, **({"json": payload} if payload else {}))
        assert response.status_code == 404


class TestPolicyRoute:
    def test_it_reports_the_resolved_configuration_and_candidates(self, client):
        body = client.get("/model-routing/policy").json()
        assert body["enabled"] is True
        assert body["default_model"] == DEFAULT
        assert body["upgrade_complexity_threshold"] == 2
        model_ids = {model["model_id"] for model in body["models"]}
        # The configured default is always a candidate, even when the
        # operator only listed the others in OLLAMA_ALLOWED_MODELS.
        assert model_ids == {SMALL, DEFAULT, LARGE}
        default_entry = next(m for m in body["models"] if m["model_id"] == DEFAULT)
        assert default_entry["is_default"] is True
        assert default_entry["tier"] == "medium"

    def test_every_task_is_listed_with_its_reviewed_risk_and_capabilities(self, client):
        body = client.get("/model-routing/policy").json()
        tasks = {entry["task"]: entry for entry in body["tasks"]}
        assert set(tasks) == {task.value for task in RouteTask}
        assert tasks["sql_generation"]["risk_level"] == "high"
        assert tasks["sql_insight"]["risk_level"] == "low"
        assert "sql" in tasks["sql_generation"]["required_capabilities"]

    def test_cost_units_are_ordinal_and_never_a_currency(self, client):
        body = client.get("/model-routing/policy").json()
        units = body["tier_cost_units"]
        assert units["small"] < units["medium"] < units["large"]
        # No currency symbol, code or money-shaped field anywhere.
        rendered = str(body)
        for forbidden in ("$", "usd", "USD", "price", "dollar"):
            assert forbidden not in rendered

    def test_no_credential_or_connection_detail_is_ever_exposed(self, client):
        rendered = str(client.get("/model-routing/policy").json())
        for forbidden in ("secret", "db.example.com", "reader", "mydb", "password"):
            assert forbidden not in rendered


class TestPreviewRoute:
    def test_a_hard_question_previews_an_escalation(self, client):
        body = client.post("/model-routing/preview", json={"question": HARD_QUESTION}).json()
        assert body["complexity_score"] >= 2
        generation = next(d for d in body["decisions"] if d["task"] == "sql_generation")
        assert generation["model_id"] == LARGE
        assert generation["reason"] == "upgraded_for_complexity"
        assert generation["truth_level"] == "ai_inference"

    def test_a_simple_question_previews_a_downgrade_only_for_low_risk_tasks(self, client):
        body = client.post("/model-routing/preview", json={"question": SIMPLE_QUESTION}).json()
        by_task = {d["task"]: d for d in body["decisions"]}
        assert by_task["sql_insight"]["model_id"] == SMALL
        assert by_task["sql_insight"]["reason"] == "downgraded_for_simplicity"
        assert by_task["sql_generation"]["model_id"] == DEFAULT
        assert by_task["sql_generation"]["reason"] == "default_no_change"

    def test_a_latency_sensitive_preview_blocks_escalation(self, client):
        body = client.post(
            "/model-routing/preview",
            json={"question": HARD_QUESTION, "latency_sensitive": True},
        ).json()
        generation = next(d for d in body["decisions"] if d["task"] == "sql_generation")
        assert generation["model_id"] == DEFAULT
        assert generation["reason"] == "upgrade_blocked_by_latency"

    def test_it_echoes_signal_names_but_never_the_question_itself(self, client):
        secret_phrase = "acme-confidential-pipeline-xyzzy"
        body = client.post(
            "/model-routing/preview",
            json={"question": f"show {secret_phrase} growth trend compared to last year"},
        ).json()
        assert "growth_comparison" in body["complexity_signals"]
        assert secret_phrase not in str(body)

    def test_previewing_consumes_no_budget_and_no_breaker_probe(self, client):
        """A preview must never be able to affect a live request. It is
        also not counted as a routing decision in the rollup, since no
        call was ever made."""
        before = get_route_metrics().snapshot()
        for _ in range(5):
            assert (
                client.post("/model-routing/preview", json={"question": HARD_QUESTION}).status_code
                == 200
            )
        after = get_route_metrics().snapshot()
        assert after["decisions"] == before["decisions"]
        assert after["budget_denials"] == before["budget_denials"]
        assert after["total_cost_units"] == before["total_cost_units"]

    def test_the_preview_matches_what_the_real_policy_decides(self, client):
        """The route calls `decide_route` itself rather than
        reimplementing it, so these two can never disagree. Asserted
        directly, because a drift here would make the preview a
        misleading operator tool."""
        from model_routing.catalog import build_routable_models
        from model_routing.policy import decide_route
        from model_routing.signals import assess_request

        settings = _settings()
        expected = {
            task.value: decide_route(
                task, assess_request(HARD_QUESTION), build_routable_models(settings), settings
            ).model_id
            for task in RouteTask
        }
        body = client.post("/model-routing/preview", json={"question": HARD_QUESTION}).json()
        assert {d["task"]: d["model_id"] for d in body["decisions"]} == expected

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"question": ""},
            {"question": SIMPLE_QUESTION, "unexpected_field": 1},
            {"question": SIMPLE_QUESTION, "model": "some-other-model"},
        ],
    )
    def test_a_malformed_request_is_a_422(self, client, payload):
        assert client.post("/model-routing/preview", json=payload).status_code == 422


class TestMetricsRoute:
    def test_an_empty_rollup_is_well_defined(self, client):
        body = client.get("/model-routing/metrics").json()
        assert body["routes"] == []
        assert body["decisions"] == 0
        assert body["total_cost_units"] == 0
        assert body["started_at"]

    def test_recorded_outcomes_surface_per_route(self, client):
        metrics = get_route_metrics()
        for latency, ok in ((120.0, True), (400.0, True), (250.0, False)):
            metrics.record_outcome(
                RouteOutcome(
                    task=RouteTask.SQL_GENERATION,
                    model_id=LARGE,
                    tier=ModelTier.LARGE,
                    succeeded=ok,
                    latency_ms=latency,
                    failure_kind=None if ok else RouteFailureKind.AVAILABILITY,
                    cost_units=5,
                )
            )
        body = client.get("/model-routing/metrics").json()
        route = next(
            r for r in body["routes"] if r["model_id"] == LARGE and r["task"] == "sql_generation"
        )
        assert route["calls"] == 3
        assert route["successes"] == 2
        assert route["availability_failures"] == 1
        # An availability failure is not a quality signal.
        assert route["quality_failures"] == 0
        assert route["quality_failure_rate"] == 0.0
        assert route["cost_units"] == 15
        assert body["total_cost_units"] == 15

    def test_the_rollup_carries_no_question_sql_or_answer_content(self, client):
        metrics = get_route_metrics()
        metrics.record_outcome(
            RouteOutcome(
                task=RouteTask.SQL_GENERATION,
                model_id=DEFAULT,
                tier=ModelTier.MEDIUM,
                succeeded=True,
                latency_ms=10.0,
            )
        )
        rendered = str(client.get("/model-routing/metrics").json())
        for forbidden in ("SELECT", "employees", "products", HARD_QUESTION):
            assert forbidden not in rendered
