"""HTTP tests for `api/sources.py`.

Uses real local accounts and a real in-memory identity database, so the
permission path under test is the actual one -- the same approach
`tests/test_api_navigation.py` takes, and for the same reason: mocking
authorization here would test the mock.

What matters most in this file is the last class. `GET /sources` is a UX
affordance, not a boundary, and `TestItIsNotABoundary` pins that: what the
endpoint reports has no effect on what the router will actually let a
caller reach.
"""

from __future__ import annotations

from collections.abc import Iterator

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.tenants import create_tenant
from identity.repositories.users import assign_role, create_user
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
import api.sources as sources_mod
from agent.authz import Permission, permissions_for
from agent.orchestrator.nodes import SOURCE_PERMISSIONS
from config.settings import Settings
from security.secrets import SecretStr

PASSWORD = "correcthorse battery staple 1"

_BASE = dict(
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
)

#: Nothing optional configured -- only `sql` is available.
#: `enable_media_search`/`media_library_path` default on/set at the
#: `Settings` level (see `config/settings.py`), unlike every other optional
#: source, so they are disabled explicitly here -- the same thing
#: `tests/test_orchestrator.py`'s own fixture does, and for the same reason.
_SETTINGS = Settings(**_BASE, enable_media_search=False, media_library_path=None)

#: Policy and web configured, so the configured/authorized split is
#: actually exercised rather than hidden behind "nothing is configured".
_SETTINGS_WITH_SOURCES = Settings(
    **_BASE,
    enable_media_search=False,
    media_library_path=None,
    enable_policy_rag=True,
    rag_store_connection_string=SecretStr("mssql+pyodbc:///?odbc_connect=secret-value"),
    enable_web_search=True,
    web_search_api_key=SecretStr("tvly-super-secret-key"),
)


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    monkeypatch.setattr(api_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_authz_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(sources_mod, "get_settings", lambda: _SETTINGS)

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    session.commit()
    session.close()

    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    api_rate_limit_mod._limiters.clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _account(email: str, roles: str | tuple[str, ...]) -> str:
    role_names = (roles,) if isinstance(roles, str) else roles
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        user = create_user(
            session,
            email=email,
            password=PASSWORD,
            tenant_id="tenant-a",
            role_name=role_names[0],
        )
        for extra in role_names[1:]:
            assign_role(session, user_id=user.id, role_name=extra, assigned_by_user_id=None)
        session.commit()
        return email
    finally:
        session.close()


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _sources(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.get("/sources", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _by_name(body: dict) -> dict[str, dict]:
    return {s["name"]: s for s in body["sources"]}


class TestShape:
    def test_reports_every_registered_source_not_only_usable_ones(self, client):
        """So the UI can show a source as unavailable *with a reason*
        rather than silently omitting it."""
        headers = _login(client, _account("viewer@example.com", "viewer"))
        names = set(_by_name(_sources(client, headers)))
        assert {
            "sql",
            "documents",
            "policy",
            "web",
            "generation",
            "media_search",
            "attachments",
        } <= names

    def test_every_source_carries_the_full_contract(self, client):
        headers = _login(client, _account("viewer@example.com", "viewer"))
        for source in _sources(client, headers)["sources"]:
            assert set(source) == {
                "name",
                "kind",
                "description",
                "capabilities",
                "configured",
                "authorized",
                "required_permission",
                "per_request",
            }
            assert set(source["capabilities"]) == {
                "operations",
                "supports_citations",
                "supports_pagination",
                "supports_incremental_sync",
                "has_side_effects",
                "preserves_source_acl",
            }

    def test_operations_are_sorted_for_a_stable_body(self, client):
        headers = _login(client, _account("viewer@example.com", "viewer"))
        for source in _sources(client, headers)["sources"]:
            ops = source["capabilities"]["operations"]
            assert ops == sorted(ops)

    def test_per_request_source_is_flagged(self, client):
        headers = _login(client, _account("viewer@example.com", "viewer"))
        by_name = _by_name(_sources(client, headers))
        assert by_name["attachments"]["per_request"] is True
        assert by_name["sql"]["per_request"] is False

    def test_usable_excludes_per_request_sources(self, client):
        """`attachments` depends on the individual request, so it cannot be
        reported as usable for some future question."""
        headers = _login(client, _account("viewer@example.com", "viewer"))
        body = _sources(client, headers)
        assert "attachments" not in body["usable"]


class TestAuthentication:
    def test_unauthenticated_is_refused(self, client):
        assert client.get("/sources").status_code in (401, 403)


class TestConfiguredVersusAuthorized:
    """The two are reported separately because they are different failures
    with different remedies -- an operator fixes one, an administrator the
    other."""

    def test_nothing_optional_is_configured_by_default(self, client):
        headers = _login(client, _account("admin@example.com", "admin"))
        by_name = _by_name(_sources(client, headers))
        assert by_name["sql"]["configured"] is True
        for name in ("documents", "policy", "web", "generation", "media_search"):
            assert by_name[name]["configured"] is False, name

    def test_an_admin_is_authorized_but_still_not_able_when_unconfigured(self, client):
        """Permission never substitutes for configuration."""
        headers = _login(client, _account("admin@example.com", "admin"))
        body = _sources(client, headers)
        by_name = _by_name(body)
        assert by_name["web"]["authorized"] is True
        assert by_name["web"]["configured"] is False
        assert "web" not in body["usable"]

    def test_a_viewer_is_not_authorized_for_the_gated_sources(self, client):
        headers = _login(client, _account("viewer@example.com", "viewer"))
        by_name = _by_name(_sources(client, headers))
        assert by_name["sql"]["authorized"] is True
        assert by_name["policy"]["authorized"] is False
        assert by_name["web"]["authorized"] is False

    def test_configured_sources_become_usable_for_a_permitted_role(self, client, monkeypatch):
        monkeypatch.setattr(sources_mod, "get_settings", lambda: _SETTINGS_WITH_SOURCES)
        headers = _login(client, _account("admin@example.com", "admin"))
        body = _sources(client, headers)
        by_name = _by_name(body)
        assert by_name["web"]["configured"] is True
        assert by_name["policy"]["configured"] is True
        assert "web" in body["usable"]
        assert "policy" in body["usable"]

    def test_configured_but_unauthorized_is_not_usable(self, client, monkeypatch):
        monkeypatch.setattr(sources_mod, "get_settings", lambda: _SETTINGS_WITH_SOURCES)
        headers = _login(client, _account("viewer@example.com", "viewer"))
        body = _sources(client, headers)
        by_name = _by_name(body)
        assert by_name["policy"]["configured"] is True
        assert by_name["policy"]["authorized"] is False
        assert "policy" not in body["usable"]

    def test_required_permission_is_reported_for_a_gated_source(self, client):
        headers = _login(client, _account("viewer@example.com", "viewer"))
        by_name = _by_name(_sources(client, headers))
        assert by_name["policy"]["required_permission"] == Permission.POLICY_RAG_QUERY.value
        assert by_name["sql"]["required_permission"] is None


class TestNoSecretsLeak:
    """`CLAUDE.md` 5.6: a connection string or API key must never reach a
    response body. `_SETTINGS_WITH_SOURCES` deliberately contains both, so
    this is a real check rather than a check against empty settings."""

    def test_the_response_contains_no_configured_secret(self, client, monkeypatch):
        monkeypatch.setattr(sources_mod, "get_settings", lambda: _SETTINGS_WITH_SOURCES)
        headers = _login(client, _account("admin@example.com", "admin"))
        raw = client.get("/sources", headers=headers).text
        assert "tvly-super-secret-key" not in raw
        assert "secret-value" not in raw
        assert "odbc_connect" not in raw

    def test_the_response_contains_no_masked_secret_either(self, client, monkeypatch):
        """A leftover `str(secret)` sends the literal mask, which would
        mean a secret field was serialized at all."""
        monkeypatch.setattr(sources_mod, "get_settings", lambda: _SETTINGS_WITH_SOURCES)
        headers = _login(client, _account("admin@example.com", "admin"))
        assert "**********" not in client.get("/sources", headers=headers).text


class TestItIsNotABoundary:
    """The endpoint is an affordance. The gate is `router_node`'s filter.

    These assertions exist so a later change cannot quietly make the UI's
    view of sources the thing that decides access.
    """

    def test_the_endpoint_and_the_router_read_the_same_permission_table(self, client):
        """If these drifted, the UI could offer a source the router denies,
        or hide one it allows."""
        headers = _login(client, _account("viewer@example.com", "viewer"))
        by_name = _by_name(_sources(client, headers))
        for name, permission in SOURCE_PERMISSIONS.items():
            assert by_name[name]["required_permission"] == permission.value

    def test_a_source_hidden_from_a_viewer_is_still_gated_by_the_router(self):
        """The server-side filter is what refuses, independently of what
        the endpoint reported."""
        viewer = permissions_for(frozenset({"viewer"}))
        assert SOURCE_PERMISSIONS["policy"] not in viewer
        assert SOURCE_PERMISSIONS["web"] not in viewer

    def test_an_unrecognized_role_grants_no_source_permission_at_all(self):
        """`CLAUDE.md` 5.2: an unrecognized role grants zero permissions.

        Asserted against `permissions_for` directly rather than over HTTP,
        because a local account can only hold a seeded role -- there is no
        way to log in *as* an unrecognized role, which is itself the
        desired property.
        """
        bogus = permissions_for(frozenset({"not-a-real-role"}))
        assert bogus == frozenset()
        for permission in SOURCE_PERMISSIONS.values():
            assert permission not in bogus

    def test_a_viewer_is_authorized_only_for_what_its_role_grants(self, client):
        """Pins the endpoint against the role map rather than a guess. A
        viewer does hold MEDIA_SEARCH, so `media_search` is authorized for
        it -- the point is that the endpoint agrees with `agent/authz.py`,
        not that a viewer is denied everything.
        """
        headers = _login(client, _account("viewer@example.com", "viewer"))
        by_name = _by_name(_sources(client, headers))
        viewer = permissions_for(frozenset({"viewer"}))
        for name, permission in SOURCE_PERMISSIONS.items():
            assert by_name[name]["authorized"] is (permission in viewer), name
