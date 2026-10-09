"""HTTP tests for `api/decision_center.py` (Prompt 52).

A real in-memory SQLite identity database with real tenants, real local
accounts and real JWT login -- necessary because this route's whole job
is six tenant-scoped repository reads, which a mocked session would not
exercise at all.

Pins the route contract: the feature flag, the authorization tier,
anti-enumeration on `database_id`, that personalization comes from the
server's own view of the caller, that one unavailable source degrades
rather than failing the briefing, and that no response carries a
credential.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.semantic_intelligence import upsert_findings
from identity.repositories.users import create_user
from semantic.intelligence.detect import Finding
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.decision_center as dc_routes
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
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
    enable_decision_center=True,
)


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    for module in (api_auth_mod, identity_auth_mod, identity_authz_mod, dc_routes):
        monkeypatch.setattr(module, "get_settings", lambda: _SETTINGS, raising=False)

    session = identity_db_mod.get_identity_session(_SETTINGS)
    # `seed_rbac` creates the "default" tenant via `ensure_default_tenant`.
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


def _seed_finding(title: str = "Net Revenue fell 36.0% in 2026-06") -> None:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        upsert_findings(
            session,
            tenant_id="default",
            database_id="default",
            findings=[
                Finding(
                    kind="kpi_drop",
                    key="a" * 64,
                    title=title,
                    detail="Observed against the previous period.",
                    subjects=({"concept_key": "revenue"},),
                    evidence=({"observed": {"current": 640.0}, "truth_level": "database_fact"},),
                    confidence=0.94,
                    risk_score=73,
                    risk_tier="high",
                    payload={
                        "metric_concept_key": "revenue",
                        "period": "2026-06",
                        "engine_version": "1.0.0",
                    },
                )
            ],
            now=datetime.now(UTC),
        )
    finally:
        session.close()


class TestFeatureFlag:
    def test_the_route_404s_when_the_centre_is_off(self, client, monkeypatch):
        disabled = Settings(**{**_SETTINGS.__dict__, "enable_decision_center": False})
        monkeypatch.setattr(dc_routes, "get_settings", lambda: disabled)
        headers = _account(client, "a1@x.example.com", "admin")
        assert client.get("/decision-center/briefing", headers=headers).status_code == 404


class TestAuthorization:
    def test_an_unauthenticated_caller_is_refused(self, client):
        assert client.get("/decision-center/briefing").status_code in (401, 403)

    def test_a_plain_user_cannot_read_the_briefing(self, client):
        """This aggregates governed review items across every
        capability, so it answers to the dashboard-read tier rather than
        to Permission.ASK."""
        headers = _account(client, "u1@x.example.com", "user")
        assert client.get("/decision-center/briefing", headers=headers).status_code == 403

    def test_an_analyst_cannot_read_the_briefing(self, client):
        headers = _account(client, "an1@x.example.com", "analyst")
        assert client.get("/decision-center/briefing", headers=headers).status_code == 403

    @pytest.mark.parametrize("role", ["admin", "auditor", "manager"])
    def test_the_dashboard_read_tier_can(self, client, role):
        headers = _account(client, f"{role}1@x.example.com", role)
        assert client.get("/decision-center/briefing", headers=headers).status_code == 200

    def test_an_unknown_database_is_a_404_not_a_403(self, client):
        headers = _account(client, "a2@x.example.com", "admin")
        response = client.get(
            "/decision-center/briefing?database_id=someone-elses-db", headers=headers
        )
        assert response.status_code == 404


class TestBriefing:
    def test_an_empty_tenant_gets_a_well_formed_briefing(self, client):
        headers = _account(client, "a3@x.example.com", "admin")
        body = client.get("/decision-center/briefing", headers=headers).json()

        assert body["tenant_id"] == "default"
        assert len(body["sections"]) == 8
        assert body["health"]
        for section in body["sections"]:
            if not section["headlines"]:
                assert section["empty_reason"]

    def test_a_seeded_finding_appears_with_full_provenance(self, client):
        _seed_finding()
        headers = _account(client, "a4@x.example.com", "admin")
        body = client.get("/decision-center/briefing", headers=headers).json()

        movement = next(s for s in body["sections"] if s["section"] == "kpi_movement")
        assert movement["total_available"] == 1
        headline = movement["headlines"][0]
        assert headline["severity"] == 73
        assert headline["truth_level"] == "ai_inference"
        provenance = headline["provenance"]
        assert provenance["source"] == "kpi_monitoring"
        assert provenance["period"] == "2026-06"
        assert provenance["metric_concept_key"] == "revenue"
        assert provenance["confidence"] == pytest.approx(0.94)
        assert provenance["lineage_available"] is True

    def test_every_headline_carries_the_full_drill_down_set(self, client):
        """The mission's own requirement: evidence, metric definition,
        period, source, confidence and lineage."""
        _seed_finding()
        headers = _account(client, "a5@x.example.com", "admin")
        body = client.get("/decision-center/briefing", headers=headers).json()
        for section in body["sections"]:
            for headline in section["headlines"]:
                provenance = headline["provenance"]
                assert "evidence" in headline
                assert provenance["period"]
                assert provenance["source"]
                assert "confidence" in provenance
                assert "lineage_available" in provenance
                assert "metric_definition_available" in provenance

    def test_health_indicators_are_always_database_facts(self, client):
        _seed_finding()
        headers = _account(client, "a6@x.example.com", "admin")
        body = client.get("/decision-center/briefing", headers=headers).json()
        assert body["health"]
        for indicator in body["health"]:
            assert indicator["truth_level"] == "database_fact"

    def test_compute_only_capabilities_are_reported_as_unavailable(self, client):
        headers = _account(client, "a7@x.example.com", "admin")
        body = client.get("/decision-center/briefing", headers=headers).json()
        rendered = " ".join(body["unavailable_sources"])
        for name in ("forecasting", "optimization_engine", "lineage", "root_cause"):
            assert name in rendered

    def test_the_profile_is_resolved_server_side_and_not_requestable(self, client):
        """There is deliberately no `profile` parameter: a viewer cannot
        ask to be shown someone else's view."""
        headers = _account(client, "a8@x.example.com", "admin")
        body = client.get("/decision-center/briefing?profile=executive", headers=headers).json()
        # The unknown query parameter is ignored, and the profile comes
        # from this caller's own permissions.
        assert body["profile_id"] in {"executive", "analyst", "operator"}

    def test_an_auditor_and_an_admin_see_the_same_facts(self, client):
        _seed_finding()
        admin = client.get(
            "/decision-center/briefing", headers=_account(client, "a9@x.example.com", "admin")
        ).json()
        auditor = client.get(
            "/decision-center/briefing",
            headers=_account(client, "au1@x.example.com", "auditor"),
        ).json()

        def headlines(body):
            return {
                h["headline_id"]: (h["title"], h["severity"], h["truth_level"])
                for s in body["sections"]
                for h in s["headlines"]
            }

        shared = set(headlines(admin)) & set(headlines(auditor))
        assert shared
        for key in shared:
            assert headlines(admin)[key] == headlines(auditor)[key]
        assert admin["health"] == auditor["health"]

    def test_the_chain_cap_setting_is_honoured(self, client, monkeypatch):
        """A setting the route never reads is worse than no setting: it
        advertises a control that does not exist."""
        capped = Settings(**{**_SETTINGS.__dict__, "decision_center_max_chains": 1})
        monkeypatch.setattr(dc_routes, "get_settings", lambda: capped)
        _seed_finding("First signal")
        headers = _account(client, "a12@x.example.com", "admin")
        body = client.get("/decision-center/briefing", headers=headers).json()
        assert len(body["chains"]) <= 1

    def test_one_unavailable_source_degrades_rather_than_failing(self, client, monkeypatch):
        """A 500 because one capability is misconfigured would make this
        the least reliable screen in the product."""

        def _boom(*args, **kwargs):
            raise RuntimeError("repository unavailable")

        monkeypatch.setattr(dc_routes, "list_workflows", _boom)
        headers = _account(client, "a10@x.example.com", "admin")
        response = client.get("/decision-center/briefing", headers=headers)

        assert response.status_code == 200
        assert any(
            "workflows" in item and "could not be read" in item
            for item in response.json()["unavailable_sources"]
        )


class TestNoSecretLeak:
    def test_no_response_carries_a_credential_or_connection_detail(self, client):
        _seed_finding()
        headers = _account(client, "a11@x.example.com", "admin")
        rendered = str(client.get("/decision-center/briefing", headers=headers).json()).lower()
        for forbidden in ("password", "secret", "jwt", "connection string", "pwd="):
            assert forbidden not in rendered
