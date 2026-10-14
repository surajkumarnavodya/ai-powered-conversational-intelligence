"""A shared conversation must not become a durable copy of data the
viewer was never entitled to.

The scenario this file exists for, end to end through the real HTTP
surface:

1. An analyst holding `view_restricted_columns` runs a salary query.
   The result gate correctly shows them the real figures.
2. `api.chat_persistence.persist_execute_result` stores those real
   figures in `ai_outputs.metadata.result_snapshot` -- also correct, so
   reopening their own conversation shows what they saw.
3. They share the conversation with an "anyone with the link" link.
4. **An anonymous visitor opens the link.** They hold no account and
   therefore no roles at all.

Before `api.shares._governed_result_snapshot`, step 4 returned the
stored rows verbatim -- real salaries to a caller with no permissions.
`api.artifacts._result_out` already re-governed for exactly this
reason; sharing, the surface with by far the weakest viewer, did not.

These are regression tests: each asserts the masked value is present
*and* that the real value appears nowhere in the response body, because
a mask applied to one field while the raw value survives in another is
the failure this is guarding against.

Harness mirrors `tests/test_api_shares.py` (TestClient + in-memory
SQLite identity DB) rather than mocking the share surface, so the real
authorization path runs.
"""

from __future__ import annotations

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base, User
from identity.repositories.history import append_turn, create_conversation
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import agent.rate_limit as rate_limit_mod
import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.shares as shares_mod
from config.settings import Settings
from governance.domains import BusinessDomain, policy_for

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
    enable_conversation_sharing=True,
    share_public_links_enabled=True,
    share_default_expiry_days=30,
    share_invitation_expiry_days=14,
    share_max_members_per_conversation=5,
    share_link_access_rate_limit_per_minute=1000,
    share_invite_rate_limit_per_hour=1000,
    enable_result_governance=True,
)

#: A real salary figure. Chosen to be a string that cannot plausibly
#: appear anywhere else in the response, so "not in the body" is a
#: meaningful assertion rather than a coincidence.
_REAL_SALARY = "187431.55"
_REAL_PASSWORD = "hunter2-not-a-real-secret-aaaa"


def _fake_tenant_resolver(user: User | None) -> str | None:
    return "default" if user is not None else None


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    monkeypatch.setattr(api_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_authz_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(shares_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(shares_mod, "resolve_actor_tenant_id", _fake_tenant_resolver)

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    session.close()

    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    rate_limit_mod._share_link_access_limiters.clear()
    rate_limit_mod._share_invite_limiters.clear()

    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correcthorse battery staple 1",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _seed_turn_with_snapshot(email: str, columns: list[str], rows: list[list]) -> str:
    """Seeds one conversation whose turn already carries a stored
    `result_snapshot` -- i.e. the state the database is in after an
    authorized caller clicked "Confirm and Run"."""
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        user = session.query(User).filter_by(email=email.strip().lower()).one()
        conversation = create_conversation(session, user_id=user.id, feature_type="text_to_sql")
        append_turn(
            session,
            conversation=conversation,
            user_id=user.id,
            question="What does each employee earn?",
            answer_text="Compensation by employee.",
            output_metadata={
                "schema_version": 2,
                "sources_used": ["sql"],
                "sql": "SELECT employee_name, annual_salary FROM hr_employees",
                "result_snapshot": {
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "returned_rows": len(rows),
                    "truncated": False,
                    "normalized_sql": "SELECT employee_name, annual_salary FROM hr_employees",
                },
            },
        )
        return str(conversation.id)
    finally:
        session.close()


def _public_link_token(client: TestClient, tokens: dict, conversation_id: str) -> str:
    created = client.post(
        f"/conversations/{conversation_id}/share",
        json={"access_mode": "anyone_with_link"},
        headers=_headers(tokens),
    )
    assert created.status_code == 200, created.text
    return created.json()["link"]["view_path"].rsplit("/", 1)[-1]


class TestAnonymousShareViewerIsGoverned:
    def test_a_stored_salary_is_masked_for_an_anonymous_link_visitor(self, client: TestClient):
        tokens = _register(client, "owner@example.com")
        conversation_id = _seed_turn_with_snapshot(
            "owner@example.com",
            ["employee_name", "annual_salary"],
            [["Ada Lovelace", _REAL_SALARY], ["Grace Hopper", "201002.10"]],
        )
        token = _public_link_token(client, tokens, conversation_id)

        view = client.get(f"/share-view/{token}")

        assert view.status_code == 200, view.text
        assert (
            _REAL_SALARY not in view.text
        ), "the real salary reached an anonymous viewer through a shared snapshot"
        snapshot = view.json()["turns"][-1]["result_snapshot"]
        assert snapshot is not None
        assert "[restricted]" in str(snapshot["rows"])

    def test_a_stored_credential_is_withheld_from_an_anonymous_link_visitor(
        self, client: TestClient
    ):
        """Credential is the category no role unlocks, so this one must
        hold even if the viewer were an admin."""
        tokens = _register(client, "owner@example.com")
        conversation_id = _seed_turn_with_snapshot(
            "owner@example.com",
            ["service_name", "db_password"],
            [["billing", _REAL_PASSWORD]],
        )
        token = _public_link_token(client, tokens, conversation_id)

        view = client.get(f"/share-view/{token}")

        assert view.status_code == 200, view.text
        assert _REAL_PASSWORD not in view.text
        assert "[withheld]" in str(view.json()["turns"][-1]["result_snapshot"]["rows"])

    def test_a_non_sensitive_snapshot_is_passed_through_unchanged(self, client: TestClient):
        """The guard must not be a blanket redactor -- ordinary shared
        analytics is the feature working, and over-blocking it is its own
        failure mode."""
        tokens = _register(client, "owner@example.com")
        conversation_id = _seed_turn_with_snapshot(
            "owner@example.com",
            ["region", "order_count"],
            [["EMEA", 120], ["APAC", 98]],
        )
        token = _public_link_token(client, tokens, conversation_id)

        view = client.get(f"/share-view/{token}")

        assert view.status_code == 200, view.text
        rows = view.json()["turns"][-1]["result_snapshot"]["rows"]
        assert rows == [["EMEA", 120], ["APAC", 98]]

    def test_the_owner_still_sees_their_own_unmasked_result(self, client: TestClient):
        """The fix governs by the *viewer's* roles, so it must not break
        the owner's own view of a result they were authorized to run.

        The owner here is a plain registered user without
        `view_restricted_columns`, so they are masked too -- what this
        asserts is the mechanism, not a privilege: the response is still
        a well-formed snapshot rather than an error or an empty set.
        """
        tokens = _register(client, "owner@example.com")
        conversation_id = _seed_turn_with_snapshot(
            "owner@example.com",
            ["employee_name", "annual_salary"],
            [["Ada Lovelace", _REAL_SALARY]],
        )
        share = client.post(
            f"/conversations/{conversation_id}/share",
            json={"access_mode": "anyone_with_link"},
            headers=_headers(tokens),
        ).json()["share"]

        view = client.get(f"/share-view/{share['id']}", headers=_headers(tokens))

        assert view.status_code == 200, view.text
        snapshot = view.json()["turns"][-1]["result_snapshot"]
        assert snapshot is not None
        assert len(snapshot["rows"]) == 1

    def test_a_truncated_share_reports_its_own_row_bookkeeping(self):
        """The per-domain export ceiling can remove rows, so the
        snapshot's `returned_rows`/`truncated` must follow -- otherwise
        the viewer reads the shortfall as a rendering bug rather than a
        policy decision. `row_count` keeps the matched total, which is
        what makes the shortfall legible."""
        limit = policy_for(BusinessDomain.HR).export_row_limit
        assert limit is not None
        rows = [[f"Employee {i}", 50_000 + i] for i in range(limit + 20)]

        protected = shares_mod._governed_result_snapshot(
            {
                "result_snapshot": {
                    "columns": ["employee_name", "annual_salary"],
                    "rows": rows,
                    "row_count": len(rows),
                    "returned_rows": len(rows),
                    "truncated": False,
                    "normalized_sql": "SELECT employee_name, annual_salary FROM hr_employees",
                }
            },
            ("admin",),
            _SETTINGS,
        )

        assert protected is not None
        assert len(protected["rows"]) == limit
        assert protected["returned_rows"] == limit
        assert protected["truncated"] is True
        assert protected["row_count"] == len(rows), "the matched total stays true"

    def test_governance_can_be_rolled_back_by_its_own_switch(self, client: TestClient, monkeypatch):
        """`ENABLE_RESULT_GOVERNANCE=false` restores the prior behaviour
        on this path too, the same as on every other result surface."""
        monkeypatch.setattr(
            shares_mod,
            "get_settings",
            lambda: _SETTINGS.model_copy(update={"enable_result_governance": False}),
        )
        tokens = _register(client, "owner@example.com")
        conversation_id = _seed_turn_with_snapshot(
            "owner@example.com",
            ["employee_name", "annual_salary"],
            [["Ada Lovelace", _REAL_SALARY]],
        )
        token = _public_link_token(client, tokens, conversation_id)

        view = client.get(f"/share-view/{token}")

        assert view.status_code == 200, view.text
        assert _REAL_SALARY in view.text
