"""End-to-end HTTP tests for Prompt 49's governed workflow lifecycle --
`api/workflow.py`. Mirrors `tests/test_api_recommendation_actions.py`'s
own fixture/login pattern exactly: real tenant resolution against real
`tenants`/`users.tenant_id` rows, authenticated through the real
`/auth/login` route.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base, User
from identity.repositories.recommendation_governance import create_record, submit_feedback
from identity.repositories.tenants import create_tenant
from identity.repositories.users import assign_role, create_user
from recommendation.governance import RecommendationStatus
from recommendation.models import Recommendation, RecommendationCategory, RecommendationKind
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
import api.workflow as workflow_mod
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
    monkeypatch.setattr(workflow_mod, "get_settings", lambda: _SETTINGS)

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    create_tenant(session, tenant_id="tenant-b", name="Tenant B")
    session.commit()
    session.close()

    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    api_rate_limit_mod._limiters.clear()

    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _make_user(email: str, *, tenant_id: str, role: str | None = None) -> uuid.UUID:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        user = create_user(session, email=email, password=PASSWORD, tenant_id=tenant_id)
        if role is not None:
            assign_role(session, user_id=user.id, role_name=role, assigned_by_user_id=None)
        session.commit()
        return user.id
    finally:
        session.close()


def _email(user_id: uuid.UUID) -> str:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        return session.get(User, user_id).email
    finally:
        session.close()


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _recommendation(**overrides) -> Recommendation:
    defaults = dict(
        kind=RecommendationKind.ACTION,
        claim=ProvenancedClaim(
            value="Investigate the anomalous spike.",
            level=DataTruthLevel.AI_INFERENCE,
            source="recommendation.engine.AnomalyRule",
        ),
        category=RecommendationCategory.ANOMALY,
        evidence=(
            ProvenancedClaim(
                value="Value at 2024 flagged anomalous.",
                level=DataTruthLevel.DATABASE_FACT,
                source="analytics.engine",
            ),
        ),
        affected_entity="2024",
        action="Investigate the root cause with the finance team.",
        confidence=0.8,
        rule_or_model="recommendation.engine.AnomalyRule",
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


def _seed_accepted_recommendation(tenant_id: str = "tenant-a", **overrides) -> str:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        record = create_record(
            session,
            tenant_id=tenant_id,
            database_id="hr",
            recommendation=_recommendation(**overrides),
            created_by_user_id=None,
        )
        record = submit_feedback(
            session, record, target_status=RecommendationStatus.ACCEPTED, actor_user_id=None
        )
        return str(record.id)
    finally:
        session.close()


def _seed_generated_recommendation(tenant_id: str = "tenant-a") -> str:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        record = create_record(
            session,
            tenant_id=tenant_id,
            database_id="hr",
            recommendation=_recommendation(),
            created_by_user_id=None,
        )
        return str(record.id)
    finally:
        session.close()


class TestCreate:
    def test_admin_creates_a_workflow(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        headers = _login(
            client, _email(_make_user("m1@a.example", tenant_id="tenant-a", role="admin"))
        )
        response = client.post("/workflows", json={"recommendation_id": rec_id}, headers=headers)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "proposed"
        assert body["recommendation_id"] == rec_id

    def test_analyst_alone_cannot_create(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        headers = _login(
            client, _email(_make_user("m2@a.example", tenant_id="tenant-a", role="analyst"))
        )
        response = client.post("/workflows", json={"recommendation_id": rec_id}, headers=headers)
        assert response.status_code == 403

    def test_plain_user_cannot_create(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        headers = _login(
            client, _email(_make_user("m3@a.example", tenant_id="tenant-a", role="user"))
        )
        response = client.post("/workflows", json={"recommendation_id": rec_id}, headers=headers)
        assert response.status_code == 403

    def test_an_unaccepted_recommendation_is_refused(self, client: TestClient):
        rec_id = _seed_generated_recommendation()
        headers = _login(
            client, _email(_make_user("m4@a.example", tenant_id="tenant-a", role="admin"))
        )
        response = client.post("/workflows", json={"recommendation_id": rec_id}, headers=headers)
        assert response.status_code == 409

    def test_a_cross_tenant_recommendation_is_a_404_not_a_403(self, client: TestClient):
        rec_id = _seed_accepted_recommendation(tenant_id="tenant-b")
        headers = _login(
            client, _email(_make_user("m5@a.example", tenant_id="tenant-a", role="admin"))
        )
        response = client.post("/workflows", json={"recommendation_id": rec_id}, headers=headers)
        assert response.status_code == 404

    def test_a_nonexistent_recommendation_is_also_a_404(self, client: TestClient):
        headers = _login(
            client, _email(_make_user("m6@a.example", tenant_id="tenant-a", role="admin"))
        )
        response = client.post(
            "/workflows", json={"recommendation_id": str(uuid.uuid4())}, headers=headers
        )
        assert response.status_code == 404


class TestDuplicateActiveWorkflow:
    """The mission's own 'test ... duplicate events' requirement."""

    def test_a_second_active_workflow_is_refused(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        headers = _login(
            client, _email(_make_user("d1@a.example", tenant_id="tenant-a", role="admin"))
        )
        first = client.post("/workflows", json={"recommendation_id": rec_id}, headers=headers)
        assert first.status_code == 201
        second = client.post("/workflows", json={"recommendation_id": rec_id}, headers=headers)
        assert second.status_code == 409


class TestCrossTenantAccess:
    def test_getting_another_tenants_workflow_is_a_404(self, client: TestClient):
        rec_id = _seed_accepted_recommendation(tenant_id="tenant-b")
        b_headers = _login(
            client, _email(_make_user("x1@b.example", tenant_id="tenant-b", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=b_headers
        ).json()["id"]

        a_headers = _login(
            client, _email(_make_user("x2@a.example", tenant_id="tenant-a", role="admin"))
        )
        response = client.get(f"/workflows/{workflow_id}", headers=a_headers)
        assert response.status_code == 404

    def test_listing_never_leaks_another_tenants_workflows(self, client: TestClient):
        rec_a = _seed_accepted_recommendation(tenant_id="tenant-a")
        rec_b = _seed_accepted_recommendation(tenant_id="tenant-b")
        a_headers = _login(
            client, _email(_make_user("x3@a.example", tenant_id="tenant-a", role="admin"))
        )
        b_headers = _login(
            client, _email(_make_user("x4@b.example", tenant_id="tenant-b", role="admin"))
        )
        client.post("/workflows", json={"recommendation_id": rec_a}, headers=a_headers)
        client.post("/workflows", json={"recommendation_id": rec_b}, headers=b_headers)

        a_list = client.get("/workflows", headers=a_headers).json()
        assert all(w["tenant_id"] == "tenant-a" for w in a_list)
        assert len(a_list) == 1


class TestLifecycleAuthorizationAndHappyPath:
    def test_full_lifecycle(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("l1@a.example", tenant_id="tenant-a", role="admin"))
        )
        reviewer = _login(
            client, _email(_make_user("l2@a.example", tenant_id="tenant-a", role="analyst"))
        )
        owner_id = _make_user("l3@a.example", tenant_id="tenant-a", role="admin")

        created = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()
        workflow_id = created["id"]

        submitted = client.post(f"/workflows/{workflow_id}/submit-for-approval", headers=manager)
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "approval_required"

        approved = client.post(f"/workflows/{workflow_id}/approve", headers=reviewer)
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        assigned = client.post(
            f"/workflows/{workflow_id}/assign",
            json={"owner_user_id": str(owner_id)},
            headers=manager,
        )
        assert assigned.status_code == 200
        assert assigned.json()["status"] == "assigned"
        assert assigned.json()["owner_user_id"] == str(owner_id)

        owner_headers = _login(client, "l3@a.example")
        started = client.post(f"/workflows/{workflow_id}/start", headers=owner_headers)
        assert started.status_code == 200
        assert started.json()["status"] == "in_progress"

        completed = client.post(
            f"/workflows/{workflow_id}/complete",
            json={
                "outcome_summary": "Root cause found and resolved.",
                "outcome_measured_impact": {"metric": "revenue", "delta_percent": 4.2},
            },
            headers=owner_headers,
        )
        assert completed.status_code == 200
        body = completed.json()
        assert body["status"] == "completed"
        assert body["outcome_summary"] == "Root cause found and resolved."
        assert body["outcome_measured_impact"] == {"metric": "revenue", "delta_percent": 4.2}

        events = client.get(f"/workflows/{workflow_id}/events", headers=manager).json()
        status_changes = [e for e in events if e["event_type"] == "status_change"]
        assert [e["to_status"] for e in status_changes] == [
            "proposed",
            "approval_required",
            "approved",
            "in_progress",
            "completed",
        ]

    def test_an_analyst_can_approve_but_not_create_or_assign(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("l4@a.example", tenant_id="tenant-a", role="admin"))
        )
        reviewer = _login(
            client, _email(_make_user("l5@a.example", tenant_id="tenant-a", role="analyst"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        client.post(f"/workflows/{workflow_id}/submit-for-approval", headers=manager)

        # Analyst CAN approve (WORKFLOW_REVIEW).
        approved = client.post(f"/workflows/{workflow_id}/approve", headers=reviewer)
        assert approved.status_code == 200

        # Analyst CANNOT assign (WORKFLOW_MANAGE only).
        owner_id = _make_user("l6@a.example", tenant_id="tenant-a", role="admin")
        forbidden = client.post(
            f"/workflows/{workflow_id}/assign",
            json={"owner_user_id": str(owner_id)},
            headers=reviewer,
        )
        assert forbidden.status_code == 403

    def test_approval_cannot_be_skipped(self, client: TestClient):
        """`proposed -> assigned` directly is illegal."""
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("l7@a.example", tenant_id="tenant-a", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        owner_id = _make_user("l8@a.example", tenant_id="tenant-a", role="admin")
        response = client.post(
            f"/workflows/{workflow_id}/assign",
            json={"owner_user_id": str(owner_id)},
            headers=manager,
        )
        assert response.status_code == 409

    def test_rejection_is_terminal(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("l9@a.example", tenant_id="tenant-a", role="admin"))
        )
        reviewer = _login(
            client, _email(_make_user("l10@a.example", tenant_id="tenant-a", role="analyst"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        client.post(f"/workflows/{workflow_id}/submit-for-approval", headers=manager)
        rejected = client.post(f"/workflows/{workflow_id}/reject", headers=reviewer)
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"

        retry = client.post(f"/workflows/{workflow_id}/approve", headers=reviewer)
        assert retry.status_code == 409


class TestRetrySafety:
    """The mission's own 'test ... retries' requirement at the HTTP
    layer: a retried mutation gets a clean 409, never a duplicated
    event."""

    def test_retrying_submit_for_approval_is_refused_and_writes_no_second_event(
        self, client: TestClient
    ):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("rt1@a.example", tenant_id="tenant-a", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]

        first = client.post(f"/workflows/{workflow_id}/submit-for-approval", headers=manager)
        assert first.status_code == 200
        second = client.post(f"/workflows/{workflow_id}/submit-for-approval", headers=manager)
        assert second.status_code == 409

        events = client.get(f"/workflows/{workflow_id}/events", headers=manager).json()
        approval_required_events = [e for e in events if e["to_status"] == "approval_required"]
        assert len(approval_required_events) == 1


class TestCommentsOwnerDueDateExternalReference:
    def test_comment_never_changes_status(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("c1@a.example", tenant_id="tenant-a", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        response = client.post(
            f"/workflows/{workflow_id}/comments",
            json={"comment": "Checked with finance."},
            headers=manager,
        )
        assert response.status_code == 200
        assert response.json()["event_type"] == "comment"
        assert response.json()["from_status"] == response.json()["to_status"] == "proposed"

    def test_assigning_an_unknown_owner_is_a_404(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("c2@a.example", tenant_id="tenant-a", role="admin"))
        )
        reviewer_headers = manager
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        client.post(f"/workflows/{workflow_id}/submit-for-approval", headers=manager)
        client.post(f"/workflows/{workflow_id}/approve", headers=manager)
        response = client.post(
            f"/workflows/{workflow_id}/assign",
            json={"owner_user_id": str(uuid.uuid4())},
            headers=reviewer_headers,
        )
        assert response.status_code == 404

    def test_due_date_can_be_set_independently(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("c3@a.example", tenant_id="tenant-a", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        response = client.post(
            f"/workflows/{workflow_id}/due-date",
            json={"due_date": "2026-12-31T00:00:00Z"},
            headers=manager,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "proposed"
        assert response.json()["due_date"] is not None

    def test_external_reference_round_trips(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("c4@a.example", tenant_id="tenant-a", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        response = client.post(
            f"/workflows/{workflow_id}/external-reference",
            json={
                "external_system": "jira",
                "external_reference_id": "OPS-123",
                "external_reference_url": "https://example.atlassian.net/browse/OPS-123",
            },
            headers=manager,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["external_system"] == "jira"
        assert body["external_reference_id"] == "OPS-123"
        assert body["status"] == "proposed"


class TestAudit:
    def test_create_and_transition_are_audit_logged_without_content(
        self, client: TestClient, monkeypatch
    ):
        calls: list[dict] = []
        monkeypatch.setattr(
            workflow_mod,
            "log_security_event",
            lambda event_type, severity, message, **kw: calls.append(
                {"event_type": event_type, **kw}
            ),
        )
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("au1@a.example", tenant_id="tenant-a", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        client.post(f"/workflows/{workflow_id}/submit-for-approval", headers=manager)

        assert len(calls) >= 2
        for call in calls:
            assert call["event_type"] == "workflow_action"
            # Never the recommendation's own claim/action text.
            assert "Investigate" not in str(call)


class TestExpire:
    def test_admin_can_force_expire(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("e1@a.example", tenant_id="tenant-a", role="admin"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        response = client.post(f"/workflows/{workflow_id}/expire", headers=manager)
        assert response.status_code == 200
        assert response.json()["status"] == "expired"

    def test_analyst_alone_cannot_expire(self, client: TestClient):
        rec_id = _seed_accepted_recommendation()
        manager = _login(
            client, _email(_make_user("e2@a.example", tenant_id="tenant-a", role="admin"))
        )
        reviewer = _login(
            client, _email(_make_user("e3@a.example", tenant_id="tenant-a", role="analyst"))
        )
        workflow_id = client.post(
            "/workflows", json={"recommendation_id": rec_id}, headers=manager
        ).json()["id"]
        response = client.post(f"/workflows/{workflow_id}/expire", headers=reviewer)
        assert response.status_code == 403
