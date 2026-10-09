"""HTTP tests for `api/kpi_monitoring.py` (Prompt 50).

A real in-memory SQLite identity database with real tenants, real local
accounts and real JWT login -- the same fixture shape
`tests/test_api_governance.py` and `tests/test_api_schema_drift.py` use,
and necessary here because these routes make real tenant-scoped
repository calls (published catalog metrics, the shared findings queue)
that a mocked session would not exercise at all.

The engine itself is covered by `tests/test_kpi_monitoring.py`; these
tests pin the route contract: the feature flag, authorization, tenant
scoping of the database id, persistence into the shared queue, and that
nothing in a response carries a credential.
"""

from __future__ import annotations

from collections.abc import Iterator

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.semantic_catalog import (
    create_entry,
    mark_reviewed,
    publish_entry,
)
from identity.repositories.users import create_user
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.kpi_monitoring as kpi_routes
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
from config.settings import Settings

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
    enable_kpi_monitoring=True,
)

SERIES = [
    ["2026-01", 1000.0],
    ["2026-02", 1010.0],
    ["2026-03", 995.0],
    ["2026-04", 1005.0],
    ["2026-05", 1000.0],
    ["2026-06", 640.0],
]


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    for module in (api_auth_mod, identity_auth_mod, identity_authz_mod, kpi_routes):
        monkeypatch.setattr(module, "get_settings", lambda: _SETTINGS, raising=False)

    session = identity_db_mod.get_identity_session(_SETTINGS)
    # `seed_rbac` already calls `ensure_default_tenant`, so the "default"
    # tenant these accounts belong to exists without creating it here.
    seed_rbac(session)
    session.commit()
    session.close()

    api_rate_limit_mod._limiters.clear()
    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _account(client: TestClient, email: str, role: str) -> dict[str, str]:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        create_user(session, email=email, password=PASSWORD, tenant_id="default", role_name=role)
        session.commit()
    finally:
        session.close()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _publish_metric(concept_key: str = "revenue", name: str = "Net Revenue") -> None:
    """Creates and publishes a catalog metric so a policy may target it."""
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        entry = create_entry(
            session,
            tenant_id="default",
            database_id="default",
            concept_key=concept_key,
            concept_type="metric",
            business_name=name,
            technical_name=None,
            description="A governed metric.",
            grain=None,
            keys=[],
            relationships=[],
            domain=None,
            synonyms=[],
            business_rules=[],
            examples=[],
            evidence=[],
            confidence=0.9,
            owner="finance",
            created_by_user_id=None,
        )
        # The real governance path: a metric is only publishable once it
        # has been reviewed (Prompt 09's own draft -> reviewed ->
        # published chain). Monitoring depends on that approval having
        # actually happened, so the fixture performs it rather than
        # inserting a published row behind the workflow's back.
        mark_reviewed(session, entry, reviewed_by_user_id=None)
        publish_entry(session, entry, published_by_user_id=None)
        session.commit()
    finally:
        session.close()


def _scan_body(**overrides: object) -> dict:
    body: dict = {
        "database_id": "default",
        "policies": [
            {
                "policy_id": "p-revenue",
                "metric_concept_key": "revenue",
                "comparison": "previous_period",
                "min_change_percent": 10.0,
            }
        ],
        "series": [{"policy_id": "p-revenue", "points": SERIES}],
        "preferences": [{"user_id": "ops"}],
    }
    body.update(overrides)
    return body


class TestFeatureFlag:
    def test_both_routes_404_when_monitoring_is_off(self, client, monkeypatch):
        disabled = Settings(**{**_SETTINGS.__dict__, "enable_kpi_monitoring": False})
        monkeypatch.setattr(kpi_routes, "get_settings", lambda: disabled)
        headers = _account(client, "a1@x.example.com", "admin")

        assert (
            client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).status_code
            == 404
        )
        assert (
            client.get("/kpi-monitoring/findings?database_id=default", headers=headers).status_code
            == 404
        )


class TestAuthorization:
    def test_an_unauthenticated_caller_is_refused(self, client):
        response = client.post("/kpi-monitoring/scan", json=_scan_body())
        assert response.status_code in (401, 403)

    def test_a_plain_user_cannot_scan(self, client):
        """Monitoring reads and writes governed review items, so it
        answers to the catalog-review role, not to Permission.ASK."""
        headers = _account(client, "u1@x.example.com", "user")
        response = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers)
        assert response.status_code == 403

    def test_an_analyst_can_scan(self, client):
        _publish_metric()
        headers = _account(client, "an1@x.example.com", "analyst")
        response = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers)
        assert response.status_code == 200, response.text

    def test_an_unknown_database_is_a_404_not_a_403(self, client):
        """Anti-enumeration: a database this tenant may not use is
        indistinguishable from one that does not exist."""
        _publish_metric()
        headers = _account(client, "an2@x.example.com", "analyst")
        response = client.post(
            "/kpi-monitoring/scan",
            json=_scan_body(database_id="someone-elses-db"),
            headers=headers,
        )
        assert response.status_code == 404


class TestScan:
    def test_a_published_metric_produces_a_ranked_finding(self, client):
        _publish_metric()
        headers = _account(client, "an3@x.example.com", "analyst")
        body = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()

        assert body["policies_evaluated"] == 1
        assert body["findings_detected"] == 1
        change = body["changes"][0]
        assert change["kind"] == "kpi_drop"
        assert change["period"] == "2026-06"
        assert change["change_percent"] == pytest.approx(-36.0)
        assert change["risk_score"] > 0
        assert change["truth_level"] == "ai_inference"
        assert "previous period 2026-05" in change["baseline_label"]

    def test_an_unpublished_metric_is_refused_with_a_reason(self, client):
        headers = _account(client, "an4@x.example.com", "analyst")
        body = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()

        assert body["findings_detected"] == 0
        assert [v["code"] for v in body["violations"]] == ["metric_not_found"]

    def test_evidence_is_absent_with_a_limitation_when_no_breakdown_is_given(self, client):
        _publish_metric()
        headers = _account(client, "an5@x.example.com", "analyst")
        body = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()

        evidence = body["changes"][0]["evidence"]
        assert evidence["has_sufficient_evidence"] is False
        assert evidence["contributors"] == []
        assert evidence["confidence"] is None
        assert evidence["limitations"]

    def test_a_breakdown_produces_attributed_evidence(self, client):
        _publish_metric()
        headers = _account(client, "an6@x.example.com", "analyst")
        body = client.post(
            "/kpi-monitoring/scan",
            json=_scan_body(
                policies=[
                    {
                        "policy_id": "p-revenue",
                        "metric_concept_key": "revenue",
                        "dimension_columns": ["region"],
                    }
                ],
                series=[
                    {
                        "policy_id": "p-revenue",
                        "points": SERIES,
                        "value_column": "revenue",
                        "current_breakdown": {
                            "columns": ["region", "revenue"],
                            "rows": [["East", 160.0], ["West", 290.0], ["North", 190.0]],
                        },
                        "baseline_breakdown": {
                            "columns": ["region", "revenue"],
                            "rows": [["East", 500.0], ["West", 300.0], ["North", 200.0]],
                        },
                    }
                ],
            ),
            headers=headers,
        ).json()

        evidence = body["changes"][0]["evidence"]
        assert evidence["has_sufficient_evidence"] is True
        assert evidence["contributors"][0]["label"] == "East"
        assert evidence["confidence"] is not None
        assert evidence["truth_level"] == "ai_inference"

    def test_findings_are_persisted_into_the_shared_queue(self, client):
        _publish_metric()
        headers = _account(client, "an7@x.example.com", "analyst")
        body = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()
        assert body["persisted"] is True
        assert body["created"] == 1

        queue = client.get("/kpi-monitoring/findings?database_id=default", headers=headers).json()
        assert len(queue) == 1
        assert queue[0]["kind"] == "kpi_drop"
        assert queue[0]["status"] == "open"
        assert queue[0]["truth_level"] == "ai_inference"

    def test_persist_false_runs_everything_and_stores_nothing(self, client):
        _publish_metric()
        headers = _account(client, "an8@x.example.com", "analyst")
        body = client.post(
            "/kpi-monitoring/scan", json=_scan_body(persist=False), headers=headers
        ).json()
        assert body["findings_detected"] == 1
        assert body["persisted"] is False
        assert body["created"] == 0
        assert (
            client.get("/kpi-monitoring/findings?database_id=default", headers=headers).json() == []
        )

    def test_a_rerun_records_unchanged_rather_than_duplicating(self, client):
        _publish_metric()
        headers = _account(client, "an9@x.example.com", "analyst")
        client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers)
        second = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()

        assert second["created"] == 0
        assert second["unchanged"] == 1
        queue = client.get("/kpi-monitoring/findings?database_id=default", headers=headers).json()
        assert len(queue) == 1, "a rerun must not add a second row"

    def test_a_rerun_does_not_re_alert_the_same_open_finding(self, client):
        _publish_metric()
        headers = _account(client, "an10@x.example.com", "analyst")
        first = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()
        second = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()

        assert first["alert_bundles"][0]["alerted_count"] == 1
        assert second["alert_bundles"][0]["alerted_count"] == 0
        assert (
            "duplicate_of_open_finding"
            in second["alert_bundles"][0]["decisions"][0]["suppressed_by"]
        )

    def test_a_quiet_period_suppresses_the_alert_but_keeps_the_finding(self, client):
        _publish_metric()
        headers = _account(client, "an11@x.example.com", "analyst")
        body = client.post(
            "/kpi-monitoring/scan",
            json=_scan_body(
                quiet_periods=[
                    {"name": "freeze", "periods": ["2026-06"], "reason": "release freeze"}
                ]
            ),
            headers=headers,
        ).json()

        assert body["findings_detected"] == 1
        assert body["persisted"] is True
        decision = body["alert_bundles"][0]["decisions"][0]
        assert decision["alerted"] is False
        assert "quiet_period" in decision["suppressed_by"]
        assert "release freeze" in decision["suppression_detail"]

    def test_the_report_is_returned_and_names_no_cause(self, client):
        _publish_metric()
        headers = _account(client, "an12@x.example.com", "analyst")
        body = client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()
        report = body["report_markdown"]
        assert "KPI monitoring" in report
        assert "caused by" not in report.lower()

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"database_id": ""},
            {"database_id": "default", "unexpected": 1},
            {"database_id": "default", "policies": [{"policy_id": "p"}]},
        ],
    )
    def test_a_malformed_request_is_a_422(self, client, payload):
        headers = _account(client, f"bad{abs(hash(str(payload))) % 9999}@x.example.com", "analyst")
        assert client.post("/kpi-monitoring/scan", json=payload, headers=headers).status_code == 422


class TestNoSecretLeak:
    def test_no_response_carries_a_credential_or_connection_detail(self, client):
        _publish_metric()
        headers = _account(client, "an13@x.example.com", "analyst")
        rendered = str(
            client.post("/kpi-monitoring/scan", json=_scan_body(), headers=headers).json()
        ).lower()
        for forbidden in ("password", "secret", "jwt", "token", "connection string"):
            assert forbidden not in rendered
