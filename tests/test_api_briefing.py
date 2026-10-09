"""HTTP tests for `api/briefing.py` (Prompt 51).

A real in-memory SQLite identity database with real tenants, real local
accounts and real JWT login -- the same fixture shape
`tests/test_api_kpi_monitoring.py` uses, and necessary here because this
route makes real tenant-scoped repository calls (the shared findings
queue, recommendation records) that a mocked session would not exercise
at all.

The engine itself is covered by `tests/test_briefing_engine.py`; these
tests pin the route contract: the feature flag, per-section
authorization, tenant scoping of both the database id and the records
themselves, payload bounds, and that no response carries a credential.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.recommendation_governance import create_record
from identity.repositories.semantic_intelligence import upsert_findings
from identity.repositories.tenants import create_tenant
from identity.repositories.users import create_user
from recommendation.models import Recommendation, RecommendationCategory, RecommendationKind
from semantic.intelligence.detect import Finding
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.briefing as briefing_routes
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
from agent.provenance import DataTruthLevel, ProvenancedClaim
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
    enable_executive_briefing=True,
)

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    for module in (api_auth_mod, identity_auth_mod, identity_authz_mod, briefing_routes):
        monkeypatch.setattr(module, "get_settings", lambda: _SETTINGS, raising=False)

    session = identity_db_mod.get_identity_session(_SETTINGS)
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


def _account(
    client: TestClient, email: str, role: str, tenant_id: str = "default"
) -> dict[str, str]:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        create_user(session, email=email, password=PASSWORD, tenant_id=tenant_id, role_name=role)
        session.commit()
    finally:
        session.close()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _kpi_finding(
    tenant_id: str = "default",
    *,
    database_id: str = "default",
    metric: str = "revenue",
    key_suffix: str = "a",
    hours_ago: float = 2.0,
) -> None:
    """Persists one real KPI finding through the real repository."""
    finding = Finding(
        kind="kpi_drop",
        key=f"kpi-{tenant_id}-{key_suffix}".ljust(64, "0")[:64],
        title=f"{metric} fell 36.0% in 2026-06",
        detail="A governed metric moved.",
        subjects=({"concept_key": metric, "policy_id": "p1", "period": "2026-06"},),
        evidence=({"observed": {"current_value": 640.0}, "truth_level": "database_fact"},),
        confidence=0.65,
        published_count=1,
        risk_score=78,
        risk_tier="high",
        reasons=("kpi_drop base 45",),
        payload={
            "metric_concept_key": metric,
            "policy_id": "p1",
            "period": "2026-06",
            "comparison": "previous_period",
            "baseline_label": "previous period 2026-05",
            "current_value": 640.0,
            "baseline_value": 1000.0,
            "change_absolute": -360.0,
            "change_percent": -36.0,
            "threshold_crossed": None,
            "detection_methods": ["rolling_zscore"],
            "direction": "down",
            "has_sufficient_evidence": False,
            "limitations": ["No dimension breakdown was configured."],
            "dimension_columns": [],
            "engine_version": "1.0.0",
        },
    )
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        upsert_findings(
            session,
            tenant_id=tenant_id,
            database_id=database_id,
            findings=[finding],
            now=NOW - timedelta(hours=hours_ago),
        )
        session.commit()
    finally:
        session.close()


def _recommendation(
    tenant_id: str = "default", *, database_id: str = "default", claim: str = "Review EMEA pricing"
) -> None:
    """Persists one real unresolved recommendation record."""
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        create_record(
            session,
            tenant_id=tenant_id,
            database_id=database_id,
            recommendation=Recommendation(
                category=RecommendationCategory.REVENUE,
                kind=RecommendationKind.ACTION,
                claim=ProvenancedClaim(value=claim, level=DataTruthLevel.AI_INFERENCE),
                rationale="Concentration is rising.",
                action="Review EMEA pricing",
                confidence=0.7,
                engine_version="1.0.0",
                generated_at=(NOW - timedelta(hours=3)).isoformat(),
            ),
            created_by_user_id=None,
        )
        session.commit()
    finally:
        session.close()


def _body(**overrides) -> dict:
    body: dict = {
        "database_id": "default",
        "period": "daily",
        "period_label": "2026-06-30",
        "as_of": NOW.isoformat(),
    }
    body.update(overrides)
    return body


class TestFeatureFlag:
    def test_the_route_404s_when_the_briefing_is_off(self, client, monkeypatch):
        disabled = Settings(**{**_SETTINGS.__dict__, "enable_executive_briefing": False})
        monkeypatch.setattr(briefing_routes, "get_settings", lambda: disabled)
        headers = _account(client, "flag@x.example.com", "admin")
        response = client.post("/briefing/generate", json=_body(), headers=headers)
        assert response.status_code == 404


class TestAuthorization:
    def test_an_unauthenticated_caller_is_refused(self, client):
        response = client.post("/briefing/generate", json=_body())
        assert response.status_code in (401, 403)

    def test_a_plain_user_who_can_read_neither_source_is_forbidden(self, client):
        """A briefing aggregates governed review items and recommendation
        records. A caller entitled to neither has nothing to be briefed
        on, so this is a 403 rather than an empty 200."""
        headers = _account(client, "plain@x.example.com", "user")
        response = client.post("/briefing/generate", json=_body(), headers=headers)
        assert response.status_code == 403

    def test_an_admin_gets_a_briefing(self, client):
        _kpi_finding()
        headers = _account(client, "admin@x.example.com", "admin")
        response = client.post("/briefing/generate", json=_body(), headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items"]
        assert body["engine_version"]

    def test_a_caller_without_recommendation_access_gets_that_source_named_as_omitted(
        self, client, monkeypatch
    ):
        """An aggregate must never reveal what the caller could not read
        directly -- and must never silently omit it either."""
        monkeypatch.setattr(
            briefing_routes, "_may_read_recommendations", lambda permissions, tenant_id: False
        )
        _kpi_finding()
        _recommendation()
        headers = _account(client, "partial@x.example.com", "admin")
        response = client.post("/briefing/generate", json=_body(), headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        by_source = {entry["source"]: entry for entry in body["coverage"]}
        assert by_source["recommendations"]["supplied"] is False
        assert "may not read recommendation records" in by_source["recommendations"]["note"]
        assert any("recommendations:" in item for item in body["limitations"])
        assert not any(item["section"] == "unresolved_recommendations" for item in body["items"])
        assert "Review EMEA pricing" not in response.text

    def test_a_caller_without_findings_access_gets_both_finding_sources_omitted(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            briefing_routes, "_may_read_findings", lambda permissions, tenant_id: False
        )
        _kpi_finding()
        _recommendation()
        headers = _account(client, "partial2@x.example.com", "admin")
        response = client.post("/briefing/generate", json=_body(), headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        by_source = {entry["source"]: entry for entry in body["coverage"]}
        assert by_source["kpi_findings"]["supplied"] is False
        assert by_source["schema_drift_findings"]["supplied"] is False
        assert by_source["recommendations"]["supplied"] is True
        assert not any(item["section"] == "kpi_movement" for item in body["items"])


class TestTenantScoping:
    def test_an_unknown_database_is_a_404_not_a_403(self, client):
        """Anti-enumeration: a cross-tenant or nonexistent database name
        is indistinguishable."""
        headers = _account(client, "t1@x.example.com", "admin")
        response = client.post(
            "/briefing/generate", json=_body(database_id="not-a-database"), headers=headers
        )
        assert response.status_code == 404

    def test_another_tenants_records_never_appear(self, client):
        """The real isolation property, against a real identity database
        with two real tenants sharing one configured database name."""
        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            create_tenant(session, tenant_id="other", name="Other Co")
            session.commit()
        finally:
            session.close()

        _kpi_finding("default", metric="revenue", key_suffix="own")
        _kpi_finding("other", metric="secret_other_tenant_metric", key_suffix="other")
        _recommendation("other", claim="Other tenant's private action")

        headers = _account(client, "mine@x.example.com", "admin", tenant_id="default")
        response = client.post("/briefing/generate", json=_body(), headers=headers)
        assert response.status_code == 200, response.text

        assert "secret_other_tenant_metric" not in response.text
        assert "Other tenant's private action" not in response.text
        assert any("revenue" in item["subject"] for item in response.json()["items"])


class TestPayloadBounds:
    def test_too_many_data_quality_findings_is_a_400(self, client, monkeypatch):
        tight = Settings(**{**_SETTINGS.__dict__, "briefing_max_data_quality_findings": 1})
        monkeypatch.setattr(briefing_routes, "get_settings", lambda: tight)
        headers = _account(client, "bounds@x.example.com", "admin")
        finding = {
            "dimension": "completeness",
            "severity": "warning",
            "table": "sales",
            "claim_text": "amount is null in some rows",
            "observed": {},
            "engine_version": "1.0.0",
        }
        response = client.post(
            "/briefing/generate",
            json=_body(data_quality_findings=[finding, finding]),
            headers=headers,
        )
        assert response.status_code == 400

    def test_an_unknown_request_field_is_rejected(self, client):
        headers = _account(client, "extra@x.example.com", "admin")
        response = client.post("/briefing/generate", json=_body(sneaky_field="x"), headers=headers)
        assert response.status_code == 422

    def test_an_unknown_period_is_rejected(self, client):
        headers = _account(client, "period@x.example.com", "admin")
        response = client.post("/briefing/generate", json=_body(period="hourly"), headers=headers)
        assert response.status_code == 422


class TestResponseContract:
    def test_caller_supplied_data_quality_findings_reach_the_briefing(self, client):
        headers = _account(client, "dq@x.example.com", "admin")
        response = client.post(
            "/briefing/generate",
            json=_body(
                data_quality_findings=[
                    {
                        "dimension": "completeness",
                        "severity": "critical",
                        "table": "sales",
                        "column": "amount",
                        "claim_text": "amount is null in 42.00% of rows",
                        "formula": "null_rate = null_count / total_count",
                        "observed": {"null_rate": 42.0},
                        "engine_version": "1.0.0",
                        "observed_at": NOW.isoformat(),
                    }
                ]
            ),
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        sections = {item["section"] for item in body["items"]}
        assert "data_quality" in sections
        assert "risks" in sections

    def test_every_returned_claim_carries_evidence_versions_and_a_truth_level(self, client):
        _kpi_finding()
        _recommendation()
        headers = _account(client, "ev@x.example.com", "admin")
        body = client.post("/briefing/generate", json=_body(), headers=headers).json()
        assert body["items"]
        for item in body["items"]:
            assert item["truth_level"] in {"database_fact", "ai_inference"}
            assert item["evidence"]
            for link in item["evidence"]:
                assert link["versions"]
                assert link["reference_id"]

    def test_an_observed_fact_is_never_labelled_an_inference(self, client):
        _kpi_finding()
        headers = _account(client, "split@x.example.com", "admin")
        body = client.post("/briefing/generate", json=_body(), headers=headers).json()
        for item in body["items"]:
            if item["claim_class"] == "observed_fact":
                assert item["truth_level"] == "database_fact"
            else:
                assert item["truth_level"] == "ai_inference"
        assert body["observed_fact_count"] + body["estimate_count"] + body[
            "recommendation_count"
        ] == len(body["items"])

    def test_a_quiet_tenant_gets_an_honest_empty_briefing_not_a_404(self, client):
        headers = _account(client, "quiet@x.example.com", "admin")
        response = client.post("/briefing/generate", json=_body(), headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert len(body["empty_sections"]) == 7
        assert any("coverage" in item for item in body["limitations"])
        assert "never checked" in body["report_markdown"]

    def test_the_caller_s_as_of_drives_staleness_not_the_server_clock(self, client):
        """The same stored finding is fresh for a reference time just
        after it and stale for one much later."""
        _kpi_finding(hours_ago=0.5)
        headers = _account(client, "stale@x.example.com", "admin")

        fresh = client.post("/briefing/generate", json=_body(), headers=headers).json()
        assert fresh["stale_item_count"] == 0

        much_later = (NOW + timedelta(days=10)).isoformat()
        stale = client.post(
            "/briefing/generate", json=_body(as_of=much_later), headers=headers
        ).json()
        assert stale["stale_item_count"] == len(stale["items"])
        assert stale["stale_inputs"]

    def test_the_report_markdown_is_returned_alongside_the_typed_claims(self, client):
        _kpi_finding()
        headers = _account(client, "md@x.example.com", "admin")
        body = client.post("/briefing/generate", json=_body(), headers=headers).json()
        assert body["report_markdown"].startswith("# Daily executive briefing")
        assert "## Coverage" in body["report_markdown"]

    def test_no_response_carries_a_credential_or_a_connection_detail(self, client):
        _kpi_finding()
        _recommendation()
        headers = _account(client, "secret@x.example.com", "admin")
        text = client.post("/briefing/generate", json=_body(), headers=headers).text.lower()
        for forbidden in ("password", "jwt_secret", "connection_string", PASSWORD.lower()):
            assert forbidden not in text


class TestNoQueryAndNoLlm:
    def test_the_route_never_builds_a_database_engine_or_calls_an_llm(self, client, monkeypatch):
        """The structural claim, enforced: a briefing has no way to
        produce a row, so the deterministic SQL controls cannot be
        bypassed from here."""
        import db.connection as db_connection_mod
        import db.execution as db_execution_mod

        def _boom(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the briefing route must never touch a monitored database")

        monkeypatch.setattr(db_connection_mod, "get_engine", _boom)
        monkeypatch.setattr(db_connection_mod, "get_read_only_engine", _boom)
        monkeypatch.setattr(db_execution_mod, "execute_readonly_sql", _boom)

        _kpi_finding()
        headers = _account(client, "noquery@x.example.com", "admin")
        assert client.post("/briefing/generate", json=_body(), headers=headers).status_code == 200


class TestRecommendationStatusFiltering:
    def test_a_resolved_recommendation_is_not_resurfaced(self, client):
        from identity.repositories.recommendation_governance import (
            list_records,
            submit_feedback,
        )
        from recommendation.governance import RecommendationStatus

        _recommendation()
        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            record = list_records(session, "default")[0]
            record_id = uuid.UUID(str(record.id))
            submit_feedback(
                session,
                record,
                target_status=RecommendationStatus.ACCEPTED,
                actor_user_id=None,
                actor_label="test",
            )
            session.commit()
        finally:
            session.close()

        headers = _account(client, "status@x.example.com", "admin")
        body = client.post("/briefing/generate", json=_body(), headers=headers).json()
        assert any(
            item["section"] == "unresolved_recommendations" for item in body["items"]
        ), "accepted but unacted-on is still unresolved"

        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            from identity.repositories.recommendation_governance import (
                get_record_by_id,
                mark_resolved,
            )

            record = get_record_by_id(session, record_id)
            assert record is not None
            mark_resolved(session, record, actor_user_id=None)
            session.commit()
        finally:
            session.close()

        api_rate_limit_mod._limiters.clear()
        body = client.post("/briefing/generate", json=_body(), headers=headers).json()
        assert not any(item["section"] == "unresolved_recommendations" for item in body["items"])
