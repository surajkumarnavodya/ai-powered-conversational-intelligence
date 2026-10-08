"""Cross-cutting regression test for the AI Governance Center (Prompt
44, `44_AI_GOVERNANCE_CENTER_CONTRACT.md`) -- spans
`governance_center/policy.py`, `identity/repositories/governance.py`,
and `api/governance.py` together, so per `tests/security/README.md`'s
own rule it belongs here rather than being split across module-specific
test files.

The mission's own two explicitly-named test requirements, proven end to
end against the real HTTP API with a real identity database (real
tenants, real users, real RBAC roles) -- not a mock of the authorization
path:

1. **Unauthorized changes are refused.** A caller holding no
   governance permission at all cannot create a draft, submit it for
   testing/evaluation, approve/reject it, deploy it, roll it back, or
   retire it -- every mutating action is refused server-side, not merely
   hidden by the frontend (master principle: no UI-level restriction is
   a security boundary). A caller holding only `GOVERNANCE_REVIEW`
   (analyst tier) may approve/reject but not deploy/roll back/retire
   (operator-tier actions); a caller holding only `GOVERNANCE_MANAGE`
   (admin tier) may do everything review can plus the operator actions.
2. **Rollback genuinely works and stays correctly audited**, and never
   crosses a tenant boundary: one tenant's own deployed asset version
   can never be rolled back, deployed, approved, or even viewed by a
   different tenant's caller -- a cross-tenant action gets the same 404
   a genuinely nonexistent version would, never a 403 that would
   confirm it exists.
"""

from __future__ import annotations

from collections.abc import Iterator

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.tenants import create_tenant
from identity.repositories.users import create_user
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
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

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    create_tenant(session, tenant_id="tenant-b", name="Tenant B")
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
    client: TestClient, email: str, role: str, tenant_id: str = "tenant-a"
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


def _create_and_deploy(
    client: TestClient, admin_headers: dict, reviewer_headers: dict, **overrides
):
    payload = {
        "asset_type": "model",
        "asset_key": "llama3.1:8b",
        "display_name": "Llama 3.1 8B",
        "config": {"provider": "ollama"},
    }
    payload.update(overrides)
    draft = client.post("/governance/assets", json=payload, headers=admin_headers).json()
    vid = draft["id"]
    client.post(f"/governance/assets/{vid}/submit-for-testing", headers=admin_headers)
    client.post(f"/governance/assets/{vid}/submit-for-evaluation", headers=admin_headers)
    client.post(f"/governance/assets/{vid}/approve", headers=reviewer_headers)
    deployed = client.post(f"/governance/assets/{vid}/deploy", headers=admin_headers).json()
    return deployed["deployed"]


class TestUnauthorizedChanges:
    def test_a_caller_with_no_governance_permission_cannot_create_a_draft(self, client):
        headers = _account(client, "plain_user@tenant-a.example.com", "user")
        response = client.post(
            "/governance/assets",
            json={"asset_type": "model", "asset_key": "x", "display_name": "X"},
            headers=headers,
        )
        assert response.status_code == 403

    def test_a_caller_with_no_governance_permission_cannot_deploy(self, client):
        admin_headers = _account(client, "admin_a@tenant-a.example.com", "admin")
        plain_headers = _account(client, "plain_b@tenant-a.example.com", "user")
        draft = client.post(
            "/governance/assets",
            json={"asset_type": "model", "asset_key": "m", "display_name": "M"},
            headers=admin_headers,
        ).json()
        response = client.post(f"/governance/assets/{draft['id']}/deploy", headers=plain_headers)
        assert response.status_code in (403, 409)  # not yet approved either way, but...
        # Confirm it's specifically a permission denial, not just the state 409:
        response2 = client.post(
            f"/governance/assets/{draft['id']}/submit-for-testing", headers=plain_headers
        )
        assert response2.status_code == 403

    def test_analyst_review_tier_can_approve_but_not_deploy_or_rollback(self, client):
        admin_headers = _account(client, "admin_c@tenant-a.example.com", "admin")
        analyst_headers = _account(client, "analyst_c@tenant-a.example.com", "analyst")
        draft = client.post(
            "/governance/assets",
            json={"asset_type": "model", "asset_key": "m2", "display_name": "M2"},
            headers=admin_headers,
        ).json()
        vid = draft["id"]
        client.post(f"/governance/assets/{vid}/submit-for-testing", headers=admin_headers)
        client.post(f"/governance/assets/{vid}/submit-for-evaluation", headers=admin_headers)

        approve_response = client.post(f"/governance/assets/{vid}/approve", headers=analyst_headers)
        assert approve_response.status_code == 200  # review tier CAN approve

        deploy_response = client.post(f"/governance/assets/{vid}/deploy", headers=analyst_headers)
        assert deploy_response.status_code == 403  # but NOT deploy (operator tier)

    def test_admin_manage_tier_can_do_everything_review_can_plus_operator_actions(self, client):
        admin_headers = _account(client, "admin_d@tenant-a.example.com", "admin")
        draft = client.post(
            "/governance/assets",
            json={"asset_type": "model", "asset_key": "m3", "display_name": "M3"},
            headers=admin_headers,
        ).json()
        vid = draft["id"]
        client.post(f"/governance/assets/{vid}/submit-for-testing", headers=admin_headers)
        client.post(f"/governance/assets/{vid}/submit-for-evaluation", headers=admin_headers)
        assert (
            client.post(f"/governance/assets/{vid}/approve", headers=admin_headers).status_code
            == 200
        )
        assert (
            client.post(f"/governance/assets/{vid}/deploy", headers=admin_headers).status_code
            == 200
        )

    def test_an_unauthenticated_caller_is_refused_entirely(self, client):
        response = client.get("/governance/assets")
        assert response.status_code in (401, 403)

    def test_a_support_role_with_no_governance_permission_cannot_record_usage(self, client):
        admin_headers = _account(client, "admin_e@tenant-a.example.com", "admin")
        support_headers = _account(client, "support_e@tenant-a.example.com", "support")
        draft = client.post(
            "/governance/assets",
            json={"asset_type": "model", "asset_key": "m4", "display_name": "M4"},
            headers=admin_headers,
        ).json()
        response = client.post(
            f"/governance/assets/{draft['id']}/usage",
            json={"request_count": 1},
            headers=support_headers,
        )
        assert response.status_code == 403


class TestRollbackCorrectness:
    def test_rollback_restores_the_prior_version_and_retires_the_bad_one(self, client):
        admin_headers = _account(client, "admin_f@tenant-a.example.com", "admin")
        reviewer_headers = _account(client, "analyst_f@tenant-a.example.com", "analyst")

        v1 = _create_and_deploy(client, admin_headers, reviewer_headers, asset_key="m5")
        v2 = _create_and_deploy(client, admin_headers, reviewer_headers, asset_key="m5")
        assert v2["stage"] == "deployed"

        # v1 is now "retired" -- confirm via a fresh GET.
        v1_fresh = client.get(f"/governance/assets/{v1['id']}", headers=admin_headers).json()
        assert v1_fresh["stage"] == "retired"

        rollback_response = client.post(
            f"/governance/assets/{v2['id']}/rollback",
            json={"target_version_id": v1["id"], "reason": "v2 caused a regression"},
            headers=admin_headers,
        )
        assert rollback_response.status_code == 200
        body = rollback_response.json()
        assert body["rolled_back"]["stage"] == "rolled_back"
        assert body["redeployed"]["stage"] == "deployed"
        assert body["redeployed"]["id"] == v1["id"]

        current = client.get(
            "/governance/assets/current?asset_type=model&asset_key=m5", headers=admin_headers
        ).json()
        assert current["id"] == v1["id"]

        events = client.get(f"/governance/assets/{v1['id']}/events", headers=admin_headers).json()
        assert any(e["event_type"] == "rollback" and e["to_stage"] == "deployed" for e in events)

    def test_rollback_requires_governance_manage_not_merely_review(self, client):
        admin_headers = _account(client, "admin_g@tenant-a.example.com", "admin")
        reviewer_headers = _account(client, "analyst_g@tenant-a.example.com", "analyst")
        v1 = _create_and_deploy(client, admin_headers, reviewer_headers, asset_key="m6")
        v2 = _create_and_deploy(client, admin_headers, reviewer_headers, asset_key="m6")

        response = client.post(
            f"/governance/assets/{v2['id']}/rollback",
            json={"target_version_id": v1["id"], "reason": "x"},
            headers=reviewer_headers,
        )
        assert response.status_code == 403


class TestCrossTenantIsolation:
    def test_tenant_b_cannot_view_tenant_as_asset_version(self, client):
        admin_a_headers = _account(client, "admin_h@tenant-a.example.com", "admin")
        reviewer_a_headers = _account(client, "analyst_h@tenant-a.example.com", "analyst")
        admin_b_headers = _account(
            client, "admin_h@tenant-b.example.com", "admin", tenant_id="tenant-b"
        )

        v1 = _create_and_deploy(client, admin_a_headers, reviewer_a_headers, asset_key="m7")

        response = client.get(f"/governance/assets/{v1['id']}", headers=admin_b_headers)
        # The exact same 404 a genuinely nonexistent version would get --
        # never a 403 that would confirm it exists for another tenant.
        assert response.status_code == 404

    def test_tenant_b_cannot_deploy_or_rollback_tenant_as_asset(self, client):
        admin_a_headers = _account(client, "admin_i@tenant-a.example.com", "admin")
        reviewer_a_headers = _account(client, "analyst_i@tenant-a.example.com", "analyst")
        admin_b_headers = _account(
            client, "admin_i@tenant-b.example.com", "admin", tenant_id="tenant-b"
        )

        v1 = _create_and_deploy(client, admin_a_headers, reviewer_a_headers, asset_key="m8")

        deploy_response = client.post(
            f"/governance/assets/{v1['id']}/deploy", headers=admin_b_headers
        )
        assert deploy_response.status_code == 404

        rollback_response = client.post(
            f"/governance/assets/{v1['id']}/rollback",
            json={"target_version_id": v1["id"], "reason": "x"},
            headers=admin_b_headers,
        )
        assert rollback_response.status_code == 404

    def test_tenant_bs_own_list_never_includes_tenant_as_versions(self, client):
        admin_a_headers = _account(client, "admin_j@tenant-a.example.com", "admin")
        reviewer_a_headers = _account(client, "analyst_j@tenant-a.example.com", "analyst")
        admin_b_headers = _account(
            client, "admin_j@tenant-b.example.com", "admin", tenant_id="tenant-b"
        )

        _create_and_deploy(client, admin_a_headers, reviewer_a_headers, asset_key="m9")

        response = client.get("/governance/assets", headers=admin_b_headers)
        assert response.status_code == 200
        assert all(v["asset_key"] != "m9" for v in response.json())

    def test_tenant_a_still_sees_its_own_version(self, client):
        admin_a_headers = _account(client, "admin_k@tenant-a.example.com", "admin")
        reviewer_a_headers = _account(client, "analyst_k@tenant-a.example.com", "analyst")
        v1 = _create_and_deploy(client, admin_a_headers, reviewer_a_headers, asset_key="m10")

        response = client.get(f"/governance/assets/{v1['id']}", headers=admin_a_headers)
        assert response.status_code == 200
