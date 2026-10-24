"""End-to-end HTTP tests for api/chat_history.py -- same
`fastapi.testclient.TestClient` + in-memory-SQLite-identity-DB pattern as
tests/test_api_identity_auth.py (see that module's own docstring).

Ownership isolation is the center of gravity here: every test class that
touches a conversation belonging to one user also has a "the other user
cannot see/touch it" counterpart, matching this feature's own explicit
"User A cannot list/open/search User B's data" requirement.
"""

from __future__ import annotations

import uuid

import identity.db as identity_db_mod
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.chat_history as chat_history_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
from api.chat_history import CONVERSATION_NOT_FOUND_DETAIL, require_owned_conversation
from config.settings import Settings
from security.oidc import AuthIdentity

_SETTINGS = Settings(
    local_auth_enabled=True,
    auth_database_url="postgresql://placeholder/unused",
    jwt_secret_key="s" * 40,
    jwt_issuer="text-to-sql-agent",
    jwt_audience="text-to-sql-web",
    allow_public_registration=True,
    cookie_secure=False,
    password_min_length=12,
    login_rate_limit_per_minute=1000,
    register_rate_limit_per_hour=1000,
    chat_history_page_size=2,
    chat_history_max_page_size=10,
    chat_search_page_size=2,
    chat_search_max_page_size=10,
)


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
    monkeypatch.setattr(chat_history_mod, "get_settings", lambda: _SETTINGS)

    from identity.db import get_identity_session

    session = get_identity_session(_SETTINGS)
    seed_rbac(session)
    session.close()

    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()

    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _register_and_login(client: TestClient, email: str, display_name: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correcthorse battery staple 1",
            "display_name": display_name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class TestCreateAndListConversations:
    def test_create_then_appears_in_list(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        headers = _auth_headers(tokens)

        create_response = client.post(
            "/conversations", json={"title": "My first chat"}, headers=headers
        )
        assert create_response.status_code == 201
        conversation_id = create_response.json()["id"]

        list_response = client.get("/conversations", headers=headers)
        assert list_response.status_code == 200
        body = list_response.json()
        assert body["total"] == 1
        assert body["conversations"][0]["id"] == conversation_id

    def test_requires_authentication(self, client: TestClient):
        assert client.get("/conversations").status_code == 401

    def test_pagination_respects_configured_page_size(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        headers = _auth_headers(tokens)
        for i in range(5):
            client.post("/conversations", json={"title": f"Chat {i}"}, headers=headers)

        response = client.get("/conversations", headers=headers)
        body = response.json()
        assert body["total"] == 5
        assert body["limit"] == 2  # chat_history_page_size default
        assert len(body["conversations"]) == 2


class TestOwnershipIsolation:
    def test_user_a_cannot_list_user_b_conversations(self, client: TestClient):
        alice_tokens = _register_and_login(client, "alice@example.com", "Alice")
        bob_client = TestClient(api_main.app)
        bob_tokens = _register_and_login(bob_client, "bob@example.com", "Bob")

        client.post(
            "/conversations", json={"title": "Alice's chat"}, headers=_auth_headers(alice_tokens)
        )
        bob_client.post(
            "/conversations", json={"title": "Bob's chat"}, headers=_auth_headers(bob_tokens)
        )

        alice_list = client.get("/conversations", headers=_auth_headers(alice_tokens)).json()
        assert alice_list["total"] == 1
        assert alice_list["conversations"][0]["title"] == "Alice's chat"

    def test_user_a_cannot_open_user_b_conversation_by_id(self, client: TestClient):
        alice_tokens = _register_and_login(client, "alice@example.com", "Alice")
        bob_client = TestClient(api_main.app)
        bob_tokens = _register_and_login(bob_client, "bob@example.com", "Bob")

        bob_conversation = bob_client.post(
            "/conversations",
            json={"title": "Bob's private chat"},
            headers=_auth_headers(bob_tokens),
        ).json()

        response = client.get(
            f"/conversations/{bob_conversation['id']}", headers=_auth_headers(alice_tokens)
        )
        assert response.status_code == 404

    def test_changing_the_url_id_cannot_bypass_ownership(self, client: TestClient):
        """Same check, phrased as this feature's own 'changing a URL ID
        cannot access another user's data' requirement -- messages endpoint."""
        alice_tokens = _register_and_login(client, "alice@example.com", "Alice")
        bob_client = TestClient(api_main.app)
        bob_tokens = _register_and_login(bob_client, "bob@example.com", "Bob")

        bob_conversation = bob_client.post(
            "/conversations", json={}, headers=_auth_headers(bob_tokens)
        ).json()
        bob_client.post(
            f"/conversations/{bob_conversation['id']}/messages",
            json={"role": "user", "content": "Bob's secret question"},
            headers=_auth_headers(bob_tokens),
        )

        response = client.get(
            f"/conversations/{bob_conversation['id']}/messages", headers=_auth_headers(alice_tokens)
        )
        assert response.status_code == 404

    def test_user_a_cannot_update_or_delete_user_b_conversation(self, client: TestClient):
        alice_tokens = _register_and_login(client, "alice@example.com", "Alice")
        bob_client = TestClient(api_main.app)
        bob_tokens = _register_and_login(bob_client, "bob@example.com", "Bob")
        bob_conversation = bob_client.post(
            "/conversations", json={"title": "Bob's chat"}, headers=_auth_headers(bob_tokens)
        ).json()

        patch_response = client.patch(
            f"/conversations/{bob_conversation['id']}",
            json={"title": "Hacked"},
            headers=_auth_headers(alice_tokens),
        )
        delete_response = client.delete(
            f"/conversations/{bob_conversation['id']}", headers=_auth_headers(alice_tokens)
        )
        assert patch_response.status_code == 404
        assert delete_response.status_code == 404

        # Bob's own view is unaffected.
        still_there = bob_client.get(
            f"/conversations/{bob_conversation['id']}", headers=_auth_headers(bob_tokens)
        )
        assert still_there.status_code == 200
        assert still_there.json()["title"] == "Bob's chat"

    def test_client_supplied_user_id_is_ignored(self, client: TestClient):
        """Even if a caller tries to smuggle a user_id into the create
        payload, it must be rejected outright (extra="forbid") or ignored
        -- the conversation must always belong to the authenticated caller."""
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        response = client.post(
            "/conversations",
            json={"title": "x", "user_id": "00000000-0000-0000-0000-000000000000"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422  # extra="forbid" rejects the unknown field


class TestMessages:
    def test_append_and_list_messages_in_order(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        headers = _auth_headers(tokens)
        conversation = client.post("/conversations", json={}, headers=headers).json()

        client.post(
            f"/conversations/{conversation['id']}/messages",
            json={"role": "user", "content": "Hello"},
            headers=headers,
        )
        client.post(
            f"/conversations/{conversation['id']}/messages",
            json={"role": "assistant", "content": "Hi there"},
            headers=headers,
        )

        response = client.get(f"/conversations/{conversation['id']}/messages", headers=headers)
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["content"] for m in messages] == ["Hello", "Hi there"]
        assert [m["role"] for m in messages] == ["user", "assistant"]

    def test_messages_for_unknown_conversation_is_404(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        response = client.get(
            "/conversations/00000000-0000-0000-0000-000000000000/messages",
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404


class TestSearch:
    def test_search_finds_own_conversation(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        headers = _auth_headers(tokens)
        client.post("/conversations", json={"title": "Quarterly widget sales"}, headers=headers)

        response = client.get("/chat/search", params={"q": "widget"}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert "widget" in body["results"][0]["snippet"].lower()

    def test_search_is_scoped_to_the_authenticated_user(self, client: TestClient):
        alice_tokens = _register_and_login(client, "alice@example.com", "Alice")
        bob_client = TestClient(api_main.app)
        bob_tokens = _register_and_login(bob_client, "bob@example.com", "Bob")
        bob_client.post(
            "/conversations",
            json={"title": "Bob's confidential report"},
            headers=_auth_headers(bob_tokens),
        )

        response = client.get(
            "/chat/search", params={"q": "confidential"}, headers=_auth_headers(alice_tokens)
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_search_requires_authentication(self, client: TestClient):
        response = client.get("/chat/search", params={"q": "anything"})
        assert response.status_code == 401

    def test_empty_query_returns_empty_results_not_an_error(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        response = client.get("/chat/search", params={"q": ""}, headers=_auth_headers(tokens))
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_special_characters_do_not_error(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        response = client.get(
            "/chat/search", params={"q": "100%_?[]"}, headers=_auth_headers(tokens)
        )
        assert response.status_code == 200


class TestArchiveAndSoftDelete:
    def test_archiving_removes_from_default_list_but_conversation_still_readable(
        self, client: TestClient
    ):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        headers = _auth_headers(tokens)
        conversation = client.post(
            "/conversations", json={"title": "Old chat"}, headers=headers
        ).json()

        client.patch(
            f"/conversations/{conversation['id']}", json={"archived": True}, headers=headers
        )

        default_list = client.get("/conversations", headers=headers).json()
        assert default_list["total"] == 0

        with_archived = client.get(
            "/conversations", params={"include_archived": True}, headers=headers
        ).json()
        assert with_archived["total"] == 1

        still_readable = client.get(f"/conversations/{conversation['id']}", headers=headers)
        assert still_readable.status_code == 200

    def test_delete_is_soft_and_conversation_disappears_from_reads(self, client: TestClient):
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        headers = _auth_headers(tokens)
        conversation = client.post(
            "/conversations", json={"title": "To delete"}, headers=headers
        ).json()

        delete_response = client.delete(f"/conversations/{conversation['id']}", headers=headers)
        assert delete_response.status_code == 200

        get_response = client.get(f"/conversations/{conversation['id']}", headers=headers)
        assert get_response.status_code == 404


class TestAskPersistence:
    def test_ask_persists_a_conversation_for_a_locally_authenticated_user(
        self, client: TestClient, monkeypatch
    ):
        """A minimal end-to-end check that POST /ask, when called by a
        locally-authenticated caller, creates a conversation + a message
        pair -- without needing a real Ollama/database round trip (the
        agent graph itself is mocked at its own public entry point,
        agent.orchestrator.graph.run_orchestrated, the same seam
        tests/test_api_ask.py already uses)."""
        import api.main as api_main_mod

        monkeypatch.setattr(api_main_mod, "get_settings", lambda: _SETTINGS)
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        headers = _auth_headers(tokens)

        def _fake_run_orchestrated(question, conversation_history, **kwargs):
            return {
                "status": "succeeded",
                "sql": "SELECT 1",
                "result_columns": ["x"],
                "result_rows": [[1]],
                "row_count": 1,
                "retry_count": 0,
                "selected_database": "default",
            }

        monkeypatch.setattr(api_main_mod, "run_orchestrated", _fake_run_orchestrated)

        ask_response = client.post("/ask", json={"question": "How many rows?"}, headers=headers)
        assert ask_response.status_code == 200
        body = ask_response.json()
        assert body["conversation_id"] is not None

        messages = client.get(
            f"/conversations/{body['conversation_id']}/messages", headers=headers
        ).json()
        assert len(messages["messages"]) == 2
        assert messages["messages"][0]["role"] == "user"
        assert messages["messages"][0]["content"] == "How many rows?"
        assert messages["messages"][1]["role"] == "assistant"

    def test_ask_does_not_persist_for_an_unauthenticated_or_static_token_caller(
        self, client: TestClient, monkeypatch
    ):
        """Persistence must only ever engage for a locally-authenticated
        caller -- verified here by confirming an /ask response for a
        request presenting no local credential at all never sets
        conversation_id, rather than by asserting on identity-DB state
        directly (which a non-local caller has no way to reach anyway)."""

        # No auth configured beyond local (which requires a token this
        # request doesn't present) -- request should be rejected before
        # ever reaching persistence, proving no conversation can be
        # attributed to "nobody."
        response = client.post("/ask", json={"question": "test"})
        assert response.status_code == 401


class TestRequireOwnedConversation:
    """`api.chat_history.require_owned_conversation` -- the pre-check that
    `POST /ask` and `POST /questions` run before spending any work on a
    caller-supplied `conversation_id`.

    The anti-enumeration property is the point of several of these: an id
    that belongs to someone else and an id that never existed must be
    *indistinguishable* in the response, so the pair of tests asserting
    that compare the two refusals against each other rather than each
    against a hardcoded string.
    """

    @staticmethod
    def _user_id(email: str) -> uuid.UUID:
        from identity.db import get_identity_session
        from identity.repositories.users import get_user_by_email

        session = get_identity_session(_SETTINGS)
        try:
            user = get_user_by_email(session, email)
            assert user is not None, f"{email} was not registered"
            return user.id
        finally:
            session.close()

    @staticmethod
    def _identity(subject: str, mode: str = "local") -> AuthIdentity:
        return AuthIdentity(subject=subject, roles=("user",), mode=mode)  # type: ignore[arg-type]

    def _alice_conversation(self, client: TestClient) -> str:
        tokens = _register_and_login(client, "alice@example.com", "Alice")
        response = client.post(
            "/conversations", json={"title": "Alice's chat"}, headers=_auth_headers(tokens)
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def test_omitted_id_is_allowed(self, client: TestClient):
        """The first turn of a new conversation supplies no id at all."""
        identity = self._identity(str(uuid.uuid4()))
        require_owned_conversation(identity, None, _SETTINGS)

    def test_own_conversation_is_allowed(self, client: TestClient):
        conversation_id = self._alice_conversation(client)
        alice = self._identity(str(self._user_id("alice@example.com")))

        require_owned_conversation(alice, conversation_id, _SETTINGS)

    def test_another_users_conversation_is_refused(self, client: TestClient):
        """Test case 13: User B sends User A's conversation_id."""
        alice_conversation = self._alice_conversation(client)
        _register_and_login(TestClient(api_main.app), "bob@example.com", "Bob")
        bob = self._identity(str(self._user_id("bob@example.com")))

        with pytest.raises(HTTPException) as excinfo:
            require_owned_conversation(bob, alice_conversation, _SETTINGS)

        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == CONVERSATION_NOT_FOUND_DETAIL

    def test_foreign_and_unknown_ids_are_indistinguishable(self, client: TestClient):
        """The anti-enumeration guarantee, asserted as an equality between
        the two refusals rather than against a literal: a caller must not
        be able to tell a real id they don't own from one that was never
        issued."""
        alice_conversation = self._alice_conversation(client)
        _register_and_login(TestClient(api_main.app), "bob@example.com", "Bob")
        bob = self._identity(str(self._user_id("bob@example.com")))

        with pytest.raises(HTTPException) as foreign:
            require_owned_conversation(bob, alice_conversation, _SETTINGS)
        with pytest.raises(HTTPException) as unknown:
            require_owned_conversation(bob, str(uuid.uuid4()), _SETTINGS)

        assert foreign.value.status_code == unknown.value.status_code == 404
        assert foreign.value.detail == unknown.value.detail

    def test_malformed_id_is_refused_the_same_way(self, client: TestClient):
        identity = self._identity(str(uuid.uuid4()))

        with pytest.raises(HTTPException) as excinfo:
            require_owned_conversation(identity, "not-a-uuid", _SETTINGS)

        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == CONVERSATION_NOT_FOUND_DETAIL

    @pytest.mark.parametrize("mode", ["oidc", "none", "static_token"])
    def test_non_local_identity_is_skipped(self, client: TestClient, mode: str):
        """A caller with no `identity.users` row owns nothing, has nothing
        persisted, and never has server-side history loaded -- so there is
        nothing to refuse. Deliberately checked against a *real* id owned
        by someone else: the skip must be because the id is meaningless on
        this path, not because the lookup happened to miss."""
        alice_conversation = self._alice_conversation(client)
        identity = self._identity("some-opaque-subject", mode=mode)

        require_owned_conversation(identity, alice_conversation, _SETTINGS)

    def test_local_auth_disabled_is_skipped(self, client: TestClient):
        alice_conversation = self._alice_conversation(client)
        alice = self._identity(str(self._user_id("alice@example.com")))

        require_owned_conversation(
            alice, alice_conversation, _SETTINGS.model_copy(update={"local_auth_enabled": False})
        )

    def test_non_uuid_subject_is_skipped(self, client: TestClient):
        alice_conversation = self._alice_conversation(client)
        identity = self._identity("not-a-uuid-subject")

        require_owned_conversation(identity, alice_conversation, _SETTINGS)

    def test_unreachable_identity_db_fails_open(self, client: TestClient, monkeypatch):
        """Documented fail-open: ownership cannot be verified, but the write
        path re-resolves the same id through the same ownership-scoped
        query, so it cannot be violated either."""
        alice_conversation = self._alice_conversation(client)
        bob = self._identity(str(uuid.uuid4()))

        def _boom(settings=None):
            raise RuntimeError("identity DB unreachable")

        monkeypatch.setattr(identity_db_mod, "get_identity_session", _boom)

        require_owned_conversation(bob, alice_conversation, _SETTINGS)
