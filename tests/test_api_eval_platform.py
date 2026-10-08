"""HTTP tests for `api/eval_platform.py` (Prompt 46,
`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`) -- a real in-memory SQLite
identity database (real tenants, real local accounts, real JWT login),
not a mock of the authorization/repository path, mirroring
`tests/test_api_governance.py`'s own fixture precedent exactly.
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


def _account(client: TestClient, email: str, role: str, tenant_id: str = "tenant-a") -> dict:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        create_user(session, email=email, password=PASSWORD, tenant_id=tenant_id, role_name=role)
        session.commit()
    finally:
        session.close()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _report_run(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload = {
        "source": "tenant_isolation_suite",
        "dataset_version": "abc123",
        "summary": {"pytest_output": "12 passed"},
        "total_cases": 12,
        "total_passed": 12,
        "quality": 1.0,
    }
    payload.update(overrides)
    response = client.post("/eval-platform/runs/report", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestComponentBenchmarkRun:
    def test_an_admin_can_run_and_record_the_component_benchmark(self, client):
        headers = _account(client, "admin1@tenant-a.example.com", "admin")
        response = client.post("/eval-platform/runs/component-benchmark", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "component_benchmark"
        assert body["status"] == "completed"
        assert body["total_cases"] is not None and body["total_cases"] > 0
        assert body["total_passed"] == body["total_cases"]
        assert body["quality"] == 1.0
        assert body["summary"]["regressions"] == []
        assert body["dataset_version"]

    def test_an_analyst_without_eval_manage_cannot_run_it(self, client):
        headers = _account(client, "analyst1@tenant-a.example.com", "analyst")
        response = client.post("/eval-platform/runs/component-benchmark", headers=headers)
        assert response.status_code == 403


class TestReportEvalRun:
    def test_an_admin_can_report_an_operator_run(self, client):
        headers = _account(client, "admin2@tenant-a.example.com", "admin")
        body = _report_run(client, headers)
        assert body["source"] == "tenant_isolation_suite"
        assert body["dataset_version"] == "abc123"
        assert body["total_passed"] == 12

    def test_an_unrecognized_source_is_a_422(self, client):
        headers = _account(client, "admin3@tenant-a.example.com", "admin")
        response = client.post(
            "/eval-platform/runs/report",
            json={
                "source": "not_a_real_source",
                "dataset_version": "x",
            },
            headers=headers,
        )
        assert response.status_code == 422

    def test_component_benchmark_is_not_an_allowed_report_source(self, client):
        """`component_benchmark` is only ever created via its own route
        (the server executes it) -- reporting one by hand would let a
        caller fabricate a result for the one domain this platform can
        actually verify itself."""
        headers = _account(client, "admin4@tenant-a.example.com", "admin")
        response = client.post(
            "/eval-platform/runs/report",
            json={"source": "component_benchmark", "dataset_version": "x"},
            headers=headers,
        )
        assert response.status_code == 422


class TestListAndGet:
    def test_list_returns_only_the_callers_own_tenant(self, client):
        headers_a = _account(client, "admin5@tenant-a.example.com", "admin")
        headers_b = _account(client, "admin6@tenant-b.example.com", "admin", tenant_id="tenant-b")
        _report_run(client, headers_a)
        _report_run(client, headers_b)

        response_a = client.get("/eval-platform/runs", headers=headers_a)
        assert response_a.status_code == 200
        assert len(response_a.json()) == 1
        assert response_a.json()[0]["tenant_id"] == "tenant-a"

        response_b = client.get("/eval-platform/runs", headers=headers_b)
        assert response_b.status_code == 200
        assert len(response_b.json()) == 1
        assert response_b.json()[0]["tenant_id"] == "tenant-b"

    def test_get_a_cross_tenant_run_is_a_404_not_a_403(self, client):
        headers_a = _account(client, "admin7@tenant-a.example.com", "admin")
        headers_b = _account(client, "admin8@tenant-b.example.com", "admin", tenant_id="tenant-b")
        run = _report_run(client, headers_a)

        response = client.get(f"/eval-platform/runs/{run['id']}", headers=headers_b)
        assert response.status_code == 404

    def test_get_a_nonexistent_run_is_also_a_404(self, client):
        headers = _account(client, "admin9@tenant-a.example.com", "admin")
        response = client.get(
            "/eval-platform/runs/00000000-0000-0000-0000-000000000000", headers=headers
        )
        assert response.status_code == 404

    def test_an_analyst_can_list_and_get_but_not_create(self, client):
        admin_headers = _account(client, "admin10@tenant-a.example.com", "admin")
        analyst_headers = _account(client, "analyst2@tenant-a.example.com", "analyst")
        run = _report_run(client, admin_headers)

        list_response = client.get("/eval-platform/runs", headers=analyst_headers)
        assert list_response.status_code == 200
        get_response = client.get(f"/eval-platform/runs/{run['id']}", headers=analyst_headers)
        assert get_response.status_code == 200

        create_response = client.post(
            "/eval-platform/runs/report",
            json={"source": "sql_benchmark", "dataset_version": "y"},
            headers=analyst_headers,
        )
        assert create_response.status_code == 403


class TestReleaseGate:
    def test_a_run_with_no_regressions_and_a_clean_mandatory_floor_passes(self, client):
        headers = _account(client, "admin11@tenant-a.example.com", "admin")
        run = client.post("/eval-platform/runs/component-benchmark", headers=headers).json()
        response = client.post(
            f"/eval-platform/runs/{run['id']}/release-gate",
            json={"security_benchmark_critical_findings": 0, "tenant_isolation_passed": True},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["release_gate_verdict"] == "pass"
        assert body["release_gate_reasons"]["blocking_reasons"] == []

    def test_any_critical_security_finding_blocks_regardless_of_everything_else(self, client):
        headers = _account(client, "admin12@tenant-a.example.com", "admin")
        run = client.post("/eval-platform/runs/component-benchmark", headers=headers).json()
        response = client.post(
            f"/eval-platform/runs/{run['id']}/release-gate",
            json={"security_benchmark_critical_findings": 1, "tenant_isolation_passed": True},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["release_gate_verdict"] == "block"
        assert any(
            "critical security" in reason
            for reason in body["release_gate_reasons"]["blocking_reasons"]
        )

    def test_a_failed_tenant_isolation_suite_blocks(self, client):
        headers = _account(client, "admin13@tenant-a.example.com", "admin")
        run = client.post("/eval-platform/runs/component-benchmark", headers=headers).json()
        response = client.post(
            f"/eval-platform/runs/{run['id']}/release-gate",
            json={"security_benchmark_critical_findings": 0, "tenant_isolation_passed": False},
            headers=headers,
        )
        assert response.json()["release_gate_verdict"] == "block"

    def test_release_gate_requires_eval_manage_not_just_eval_review(self, client):
        admin_headers = _account(client, "admin14@tenant-a.example.com", "admin")
        analyst_headers = _account(client, "analyst3@tenant-a.example.com", "analyst")
        run = client.post("/eval-platform/runs/component-benchmark", headers=admin_headers).json()
        response = client.post(
            f"/eval-platform/runs/{run['id']}/release-gate", headers=analyst_headers
        )
        assert response.status_code == 403

    def test_release_gate_on_a_cross_tenant_run_is_a_404(self, client):
        headers_a = _account(client, "admin15@tenant-a.example.com", "admin")
        headers_b = _account(client, "admin16@tenant-b.example.com", "admin", tenant_id="tenant-b")
        run = client.post("/eval-platform/runs/component-benchmark", headers=headers_a).json()
        response = client.post(f"/eval-platform/runs/{run['id']}/release-gate", headers=headers_b)
        assert response.status_code == 404


class TestCaseReviews:
    def test_an_analyst_can_submit_and_list_a_case_review(self, client):
        admin_headers = _account(client, "admin17@tenant-a.example.com", "admin")
        analyst_headers = _account(client, "analyst4@tenant-a.example.com", "analyst")
        run = _report_run(client, admin_headers)

        submit_response = client.post(
            f"/eval-platform/runs/{run['id']}/case-reviews",
            json={
                "domain": "sql",
                "case_name": "borderline_case_1",
                "original_verdict": True,
                "reviewed_verdict": False,
                "comment": "The gold answer was itself ambiguous here.",
            },
            headers=analyst_headers,
        )
        assert submit_response.status_code == 200, submit_response.text
        review = submit_response.json()
        assert review["original_verdict"] is True
        assert review["reviewed_verdict"] is False

        list_response = client.get(
            f"/eval-platform/runs/{run['id']}/case-reviews", headers=analyst_headers
        )
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1

    def test_submitting_a_review_never_changes_the_parent_run_summary(self, client):
        admin_headers = _account(client, "admin18@tenant-a.example.com", "admin")
        run = _report_run(client, admin_headers)
        original_summary = run["summary"]

        client.post(
            f"/eval-platform/runs/{run['id']}/case-reviews",
            json={
                "domain": "sql",
                "case_name": "case_x",
                "original_verdict": True,
                "reviewed_verdict": False,
            },
            headers=admin_headers,
        )

        refreshed = client.get(f"/eval-platform/runs/{run['id']}", headers=admin_headers).json()
        assert refreshed["summary"] == original_summary
        assert refreshed["total_passed"] == run["total_passed"]

    def test_a_viewer_with_neither_permission_cannot_submit_a_review(self, client):
        admin_headers = _account(client, "admin19@tenant-a.example.com", "admin")
        viewer_headers = _account(client, "viewer1@tenant-a.example.com", "viewer")
        run = _report_run(client, admin_headers)

        response = client.post(
            f"/eval-platform/runs/{run['id']}/case-reviews",
            json={"domain": "sql", "case_name": "x", "original_verdict": True},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_case_reviews_for_a_cross_tenant_run_is_a_404(self, client):
        headers_a = _account(client, "admin20@tenant-a.example.com", "admin")
        headers_b = _account(client, "admin21@tenant-b.example.com", "admin", tenant_id="tenant-b")
        run = _report_run(client, headers_a)

        response = client.get(f"/eval-platform/runs/{run['id']}/case-reviews", headers=headers_b)
        assert response.status_code == 404


class TestUnauthenticated:
    def test_every_route_requires_authentication(self, client):
        assert client.get("/eval-platform/runs").status_code in (401, 403)
        assert client.post("/eval-platform/runs/component-benchmark").status_code in (401, 403)
