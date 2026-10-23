"""Reloading your own conversation must not outlive your permissions.

`api.chat_history.list_messages_route` is the fourth surface in this
codebase that serves an **already-persisted** result, alongside
`api.artifacts._result_out`, `api.shares._governed_result_snapshot` and
`api.jobs._regovern_job_result`. The first three re-govern against the
caller's *current* roles; this one returned `metadata=row.metadata`
verbatim, so a stored `result_snapshot` was served exactly as captured.

Ownership is enforced here and was never the gap -- `get_conversation`
and `list_messages` are both scoped to `user.id`. The gap is time: the
rows were governed for the roles the owner held when they clicked
"Confirm and Run", and CLAUDE.md 5.4 is explicit that a stored result is
re-governed on every read against the *current* caller's roles, never
trusted because it was governed when captured, because roles change.

So the scenario is the one
`tests/security/test_artifacts_cannot_bypass_authorization.py` exists to
refuse, reached through a different door: an analyst runs a salary query,
is later demoted, and reopens their own conversation. An artifact saved
from that turn masks the figures; the conversation it was saved from did
not.

Harness mirrors that file's (TestClient + in-memory SQLite identity DB,
one hand-classified restricted column, because the shipped
`config/sensitive_columns.yaml` is an empty template and a test relying
on it would pass vacuously).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.history import append_turn, create_conversation
from identity.repositories.tenants import create_tenant
from identity.repositories.users import create_user
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.chat_history as chat_history_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
import config.sensitive_columns as sensitive_columns_mod
import governance.result_policy as result_policy_mod
from config.settings import Settings
from security.secrets import SecretStr

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
    query_timeout_seconds=15,
    max_result_rows=100,
    chat_history_max_result_rows=50,
    enable_result_governance=True,
)

RESTRICTED = {("employees", "salary"): "restricted"}

#: Distinctive enough that "absent from the body" is a real assertion.
_REAL_SALARY = 187431


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
    monkeypatch.setattr(chat_history_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(sensitive_columns_mod, "load_sensitive_columns", lambda: RESTRICTED)
    monkeypatch.setattr(result_policy_mod, "load_sensitive_columns", lambda: RESTRICTED)

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    session.commit()
    session.close()

    api_rate_limit_mod._limiters.clear()
    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _account(client: TestClient, email: str, role: str) -> tuple[dict, uuid.UUID]:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        user = create_user(
            session, email=email, password=PASSWORD, tenant_id="tenant-a", role_name=role
        )
        session.commit()
        user_id = user.id
    finally:
        session.close()
    return _login(client, email), user_id


def _login(client: TestClient, email: str) -> dict:
    """A fresh token: permissions are read per request from the account,
    so a demotion has to be observed through a new session."""
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _restricted_turn(user_id: uuid.UUID) -> str:
    """A persisted turn carrying a confirmed result over a restricted
    column, plus the insight an authorized run really writes from it."""
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        conversation = create_conversation(
            session, user_id=user_id, feature_type="text_to_sql", tenant_id="tenant-a"
        )
        insight = f"Ada has the highest salary at {_REAL_SALARY}."
        append_turn(
            session,
            conversation=conversation,
            user_id=user_id,
            question="What does each employee earn?",
            answer_text=insight,
            output_type="sql_query",
            output_metadata={
                "schema_version": 2,
                "status": "succeeded",
                "sources_used": ["sql"],
                "database": "default",
                "sql": "SELECT name, salary FROM employees",
                "row_count": 2,
                "insight": insight,
                "result_snapshot": {
                    "columns": ["name", "salary"],
                    "rows": [["Ada", _REAL_SALARY], ["Linus", 165000]],
                    "row_count": 2,
                    "returned_rows": 2,
                    "truncated": False,
                    "normalized_sql": "SELECT name, salary FROM employees",
                },
            },
        )
        return str(conversation.id)
    finally:
        session.close()


def _unconfirmed_turn(user_id: uuid.UUID) -> str:
    """An `/ask` the user never confirmed: an insight written from real
    rows, no `result_snapshot`, but the column evidence capture records."""
    return _seed(
        user_id,
        {
            "insight_source_columns": ["name", "salary"],
            "result_snapshot": None,
        },
    )


def _legacy_turn_without_evidence(user_id: uuid.UUID) -> str:
    """A turn stored before `insight_source_columns` existed -- narrative,
    and nothing at all to judge it by."""
    return _seed(user_id, {"result_snapshot": None})


def _seed(user_id: uuid.UUID, extra: dict) -> str:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        conversation = create_conversation(
            session, user_id=user_id, feature_type="text_to_sql", tenant_id="tenant-a"
        )
        insight = f"Ada has the highest salary at {_REAL_SALARY}."
        metadata = {
            "schema_version": 2,
            "status": "succeeded",
            "sources_used": ["sql"],
            "database": "default",
            "sql": "SELECT name, salary FROM employees",
            "insight": insight,
        }
        metadata.update(extra)
        append_turn(
            session,
            conversation=conversation,
            user_id=user_id,
            question="What does each employee earn?",
            answer_text=insight,
            output_type="sql_query",
            output_metadata=metadata,
        )
        return str(conversation.id)
    finally:
        session.close()


def _reassign_role(user_id: uuid.UUID, *, from_role: str, to_role: str) -> None:
    from identity.repositories.users import assign_role, remove_role

    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        assign_role(session, user_id=user_id, role_name=to_role, assigned_by_user_id=None)
        remove_role(session, user_id=user_id, role_name=from_role)
        session.commit()
    finally:
        session.close()


def _assistant_turn(response) -> dict:  # noqa: ANN001
    return [m for m in response.json()["messages"] if m["role"] == "assistant"][-1]


class TestReloadingOwnConversationIsRegoverned:
    def test_an_entitled_analyst_still_reads_their_own_figures(self, client):
        """The control must not break the ordinary case: this is the
        caller's own conversation and they hold the permission."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _restricted_turn(user_id)

        response = client.get(f"/conversations/{conversation_id}/messages", headers=headers)

        assert response.status_code == 200, response.text
        assistant = _assistant_turn(response)
        rows = assistant["metadata"]["result_snapshot"]["rows"]
        assert [row[1] for row in rows] == [_REAL_SALARY, 165000]
        assert assistant["metadata"]["insight"] is not None

    def test_a_demoted_owner_no_longer_reads_the_stored_figures(self, client):
        """The whole reason this file exists. Ownership never lapsed --
        the permission did."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _restricted_turn(user_id)
        # Confirm the capture really is there before the demotion, so a
        # later absence means "governed", not "never stored".
        before = client.get(f"/conversations/{conversation_id}/messages", headers=headers)
        assert str(_REAL_SALARY) in before.text

        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        response = client.get(f"/conversations/{conversation_id}/messages", headers=demoted)

        assert response.status_code == 200, response.text
        assert str(_REAL_SALARY) not in response.text, (
            "a demoted owner kept a durable, unprivileged window onto a restricted "
            "value through their own stored conversation"
        )
        rows = _assistant_turn(response)["metadata"]["result_snapshot"]["rows"]
        assert "[restricted]" in str(rows)
        # Masks a field; does not withhold the conversation.
        assert "Ada" in str(rows)

    def test_the_narrative_is_withheld_from_a_demoted_owner_too(self, client):
        """`insight` was written from the same rows before governing, so
        masking only the table would leave the figure in the prose."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _restricted_turn(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        response = client.get(f"/conversations/{conversation_id}/messages", headers=demoted)

        assistant = _assistant_turn(response)
        assert assistant["metadata"]["insight"] is None
        assert assistant["metadata"]["narrative_withheld_reason"] is not None
        assert str(_REAL_SALARY) not in assistant["content"]

    def test_an_unconfirmed_ask_carries_its_own_column_evidence(self, client):
        """F-01's residual edge. An `/ask` the user never confirmed stores
        no `result_snapshot`, so the narrative gate had nothing to judge
        and served the prose. Capture now records
        `insight_source_columns`, which is evidence enough."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _unconfirmed_turn(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        response = client.get(f"/conversations/{conversation_id}/messages", headers=demoted)

        assert response.status_code == 200, response.text
        assistant = _assistant_turn(response)
        assert assistant["metadata"]["insight"] is None
        assert assistant["metadata"]["narrative_withheld_reason"] is not None
        assert "salary" in assistant["metadata"]["narrative_withheld_reason"]
        assert str(_REAL_SALARY) not in response.text

    def test_an_entitled_analyst_still_reads_an_unconfirmed_turn(self, client):
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _unconfirmed_turn(user_id)

        response = client.get(f"/conversations/{conversation_id}/messages", headers=headers)

        assistant = _assistant_turn(response)
        assert assistant["metadata"].get("narrative_withheld_reason") is None
        assert str(_REAL_SALARY) in assistant["metadata"]["insight"]

    def test_a_legacy_turn_with_no_evidence_is_served_by_default(self, client):
        """Backward compatibility: a turn stored before this evidence
        existed keeps working, because withholding the whole backlog would
        silently strip the answer from conversations already shared."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _legacy_turn_without_evidence(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        response = client.get(f"/conversations/{conversation_id}/messages", headers=demoted)

        assert _assistant_turn(response)["metadata"].get("narrative_withheld_reason") is None
        assert str(_REAL_SALARY) in response.text

    def test_the_strict_flag_withholds_a_legacy_turn_from_a_lower_priv_reader(
        self, client, monkeypatch
    ):
        """`GOVERN_NARRATIVE_WITHOUT_EVIDENCE=true` is the opt-in
        fail-closed answer for the backlog."""
        import api.shares as shares_mod

        strict = _SETTINGS.model_copy(update={"govern_narrative_without_evidence": True})
        monkeypatch.setattr(chat_history_mod, "get_settings", lambda: strict)
        monkeypatch.setattr(shares_mod, "get_settings", lambda: strict)

        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _legacy_turn_without_evidence(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        response = client.get(f"/conversations/{conversation_id}/messages", headers=demoted)

        assistant = _assistant_turn(response)
        assert (
            assistant["metadata"]["narrative_withheld_reason"] == "derived_from_unrecorded_columns"
        )
        assert assistant["metadata"]["insight"] is None
        assert str(_REAL_SALARY) not in response.text

    def test_the_strict_flag_never_hides_prose_from_an_entitled_caller(self, client, monkeypatch):
        import api.shares as shares_mod

        strict = _SETTINGS.model_copy(update={"govern_narrative_without_evidence": True})
        monkeypatch.setattr(chat_history_mod, "get_settings", lambda: strict)
        monkeypatch.setattr(shares_mod, "get_settings", lambda: strict)

        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _legacy_turn_without_evidence(user_id)

        response = client.get(f"/conversations/{conversation_id}/messages", headers=headers)

        assert _assistant_turn(response)["metadata"].get("narrative_withheld_reason") is None
        assert str(_REAL_SALARY) in response.text

    def test_governance_can_be_rolled_back_by_its_own_switch(self, client, monkeypatch):
        """One switch for result governance, not a second one to find."""
        monkeypatch.setattr(
            chat_history_mod,
            "get_settings",
            lambda: _SETTINGS.model_copy(update={"enable_result_governance": False}),
        )
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _restricted_turn(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        response = client.get(f"/conversations/{conversation_id}/messages", headers=demoted)

        assert str(_REAL_SALARY) in response.text


class TestSearchSnippetsAreGovernedToo:
    """`GET /chat/search` cuts its snippet from `AiOutput.content`, which
    holds the insight prose the reload surface withholds -- so governing
    the reload and not the search would only move the channel, with the
    caller's own query terms selecting the fragment.
    """

    def _search(self, client, headers: dict, q: str):
        return client.get(f"/chat/search?q={q}", headers=headers)

    def test_a_restricted_narrative_is_not_returned_as_a_snippet(self, client):
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        _restricted_turn(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        response = self._search(client, demoted, "salary")

        assert response.status_code == 200, response.text
        assert (
            str(_REAL_SALARY) not in response.text
        ), "a withheld narrative came back as a search snippet"

    def test_the_hit_itself_is_still_returned_so_search_keeps_working(self, client):
        """Fail-closed on the snippet, not on the result: the user must
        still be able to find and open their own conversation."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id = _restricted_turn(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        body = self._search(client, demoted, "salary").json()

        assert body["total"] >= 1
        hit = body["results"][0]
        assert hit["conversation_id"] == conversation_id
        assert "Preview hidden" in hit["snippet"]

    def test_an_entitled_analyst_still_sees_the_snippet(self, client):
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        _restricted_turn(user_id)

        body = self._search(client, headers, "salary").json()

        assert body["total"] >= 1
        assert str(_REAL_SALARY) in body["results"][0]["snippet"]

    def test_an_ordinary_snippet_is_unaffected(self, client):
        """Over-blocking is its own failure mode."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            conversation = create_conversation(
                session, user_id=user_id, feature_type="text_to_sql", tenant_id="tenant-a"
            )
            insight = "The West region leads with 4812 units sold."
            append_turn(
                session,
                conversation=conversation,
                user_id=user_id,
                question="Which region sold the most?",
                answer_text=insight,
                output_type="sql_query",
                output_metadata={
                    "schema_version": 2,
                    "sources_used": ["sql"],
                    "sql": "SELECT region, units_sold FROM sales",
                    "insight": insight,
                    "result_snapshot": {
                        "columns": ["region", "units_sold"],
                        "rows": [["West", 4812]],
                        "row_count": 1,
                        "returned_rows": 1,
                        "truncated": False,
                        "normalized_sql": "SELECT region, units_sold FROM sales",
                    },
                },
            )
        finally:
            session.close()
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        # "4812" appears only in the answer, so this is a message match
        # rather than a question match.
        body = self._search(client, demoted, "4812").json()

        assert body["total"] >= 1
        assert "4812" in body["results"][0]["snippet"]

    def test_a_question_match_is_never_governed(self, client):
        """A title or question match is the user's own words, not text
        derived from a result -- governing it would hide their own query
        back from them."""
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        _restricted_turn(user_id)
        _reassign_role(user_id, from_role="analyst", to_role="user")
        demoted = _login(client, "analyst@example.com")

        body = self._search(client, demoted, "earn").json()

        assert body["total"] >= 1
        assert "earn" in body["results"][0]["snippet"].lower()
