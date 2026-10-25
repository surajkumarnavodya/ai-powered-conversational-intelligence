"""Tests for `api.conversation_context.load_server_history`.

`/ask` has always taken its context from the array the client resends, so
editing or deleting an earlier message changed the next answer's context
only because the client happened to send something different. These cover
the opt-in server-authoritative path: when it engages, when it
deliberately does not, and that it never fails a question.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from api.conversation_context import _pair_rows, load_server_history
from config.settings import Settings
from security.oidc import AuthIdentity
from security.secrets import SecretStr


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "ollama_host": "http://localhost:11434",
        "ollama_model": "llama3.1:8b",
        "db_type": "postgresql",
        "db_host": "db.example.com",
        "db_name": "mydb",
        "db_user": "reader",
        "db_password": SecretStr("secret"),
        "chroma_persist_dir": Path("/tmp/chroma"),
        "chroma_collection_name": "schema_ddl",
        "embedding_model_name": "all-MiniLM-L6-v2",
        "local_auth_enabled": True,
        # `Settings` refuses LOCAL_AUTH_ENABLED without an identity
        # database, and rightly so -- self-hosted accounts have nowhere to
        # live without one. The URL is never connected to here: the
        # session and the repository are both stubbed.
        "auth_database_url": "postgresql://placeholder/unused",
        "jwt_secret_key": "s" * 40,
        "conversation_history_source": "server",
    }
    base.update(overrides)
    return Settings(**base)


USER_ID = uuid.uuid4()
CONVERSATION_ID = str(uuid.uuid4())


def local_identity(subject: str | None = None) -> AuthIdentity:
    return AuthIdentity(subject=subject or str(USER_ID), roles=("analyst",), mode="local")


@dataclass
class FakeRow:
    """Shaped like `identity.repositories.history.MessageRow`."""

    role: str
    content: str
    sequence_number: int
    status: str | None = None
    metadata: dict | None = None
    id: uuid.UUID = uuid.uuid4()
    conversation_id: uuid.UUID = uuid.uuid4()
    created_at: datetime = datetime.now(UTC)
    model_name: str | None = None
    error_code: str | None = None


class FakeSession:
    closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_store(monkeypatch):
    """Installs a fake identity session and `list_messages`.

    Patched at `identity.db` / `identity.repositories.history` because
    `load_server_history` imports both lazily at call time -- the same
    convention the rest of this codebase's identity call sites follow.
    """
    state: dict[str, Any] = {"rows": [], "calls": [], "session": FakeSession()}

    monkeypatch.setattr("identity.db.get_identity_session", lambda settings: state["session"])

    def _list_messages(session, *, conversation_id, user_id, limit, offset):
        state["calls"].append(
            {"conversation_id": conversation_id, "user_id": user_id, "limit": limit}
        )
        return list(state["rows"]), len(state["rows"])

    monkeypatch.setattr("identity.repositories.history.list_messages", _list_messages)
    return state


class TestWhenItEngages:
    def test_a_stored_conversation_becomes_the_history(self, stub_store):
        stub_store["rows"] = [
            FakeRow("user", "Show total sales by year", 1),
            FakeRow(
                "assistant",
                "Sales grew from 2011 to 2013.",
                2,
                status="succeeded",
                metadata={"sql": "SELECT 1", "tables": ["FactResellerSales"]},
            ),
        ]
        history = load_server_history(local_identity(), CONVERSATION_ID, _settings())

        assert history is not None
        assert len(history) == 1
        assert history[0]["question"] == "Show total sales by year"
        assert history[0]["answer"] == "Sales grew from 2011 to 2013."
        assert history[0]["sql"] == "SELECT 1"
        assert history[0]["tables"] == ["FactResellerSales"]
        assert history[0]["status"] == "succeeded"

    def test_the_query_is_scoped_to_the_caller_and_the_conversation(self, stub_store):
        load_server_history(local_identity(), CONVERSATION_ID, _settings())
        call = stub_store["calls"][0]
        # Ownership is carried in the query itself, not checked afterwards
        # -- the same principle `get_conversation` follows.
        assert call["user_id"] == USER_ID
        assert call["conversation_id"] == uuid.UUID(CONVERSATION_ID)

    def test_the_session_is_always_closed(self, stub_store):
        load_server_history(local_identity(), CONVERSATION_ID, _settings())
        assert stub_store["session"].closed is True

    def test_an_empty_conversation_returns_an_empty_list_not_none(self, stub_store):
        """The distinction is load-bearing.

        `None` means "no server history to use, fall back to the client's
        array". `[]` means "the server's record is authoritative and it is
        empty", which must not silently re-admit a client-supplied array.
        """
        stub_store["rows"] = []
        assert load_server_history(local_identity(), CONVERSATION_ID, _settings()) == []

    def test_a_question_whose_answer_was_never_stored_is_marked_failed(self, stub_store):
        stub_store["rows"] = [FakeRow("user", "Show total sales by year", 1)]
        history = load_server_history(local_identity(), CONVERSATION_ID, _settings())

        assert history is not None
        # Visible as an earlier topic, but it established nothing, so
        # `build_memory` will not treat it as settled context.
        assert history[0]["status"] == "failed"

    def test_a_turn_that_failed_is_reported_as_failed(self, stub_store):
        stub_store["rows"] = [
            FakeRow("user", "Show total sales by year", 1),
            FakeRow("assistant", "I couldn't build a query.", 2, status="failed"),
        ]
        history = load_server_history(local_identity(), CONVERSATION_ID, _settings())
        assert history is not None
        assert history[0]["status"] == "failed"


class TestWhenItDoesNotEngage:
    def test_the_default_setting_leaves_the_client_array_in_charge(self, stub_store):
        assert (
            load_server_history(
                local_identity(), CONVERSATION_ID, _settings(conversation_history_source="client")
            )
            is None
        )
        assert stub_store["calls"] == []

    def test_no_conversation_id_means_a_first_turn(self, stub_store):
        assert load_server_history(local_identity(), None, _settings()) is None
        assert stub_store["calls"] == []

    def test_an_oidc_caller_has_nothing_stored(self, stub_store):
        identity = AuthIdentity(subject="sub", roles=("analyst",), mode="oidc")
        assert load_server_history(identity, CONVERSATION_ID, _settings()) is None
        assert stub_store["calls"] == []

    def test_local_auth_disabled_means_nothing_stored(self, stub_store):
        assert (
            load_server_history(
                local_identity(), CONVERSATION_ID, _settings(local_auth_enabled=False)
            )
            is None
        )
        assert stub_store["calls"] == []

    def test_a_non_uuid_subject_owns_nothing(self, stub_store):
        assert load_server_history(local_identity("not-a-uuid"), CONVERSATION_ID, _settings()) is (
            None
        )
        assert stub_store["calls"] == []

    def test_a_malformed_conversation_id_is_not_queried(self, stub_store):
        assert load_server_history(local_identity(), "not-a-uuid", _settings()) is None
        assert stub_store["calls"] == []


class TestItNeverFailsAQuestion:
    def test_an_unreachable_identity_database_falls_back(self, monkeypatch):
        def _raise(settings):
            raise RuntimeError("identity DB is down")

        monkeypatch.setattr("identity.db.get_identity_session", _raise)
        assert load_server_history(local_identity(), CONVERSATION_ID, _settings()) is None

    def test_a_repository_error_falls_back_and_still_closes(self, monkeypatch):
        session = FakeSession()
        monkeypatch.setattr("identity.db.get_identity_session", lambda settings: session)

        def _raise(*_a: object, **_k: object) -> object:
            raise RuntimeError("query blew up")

        monkeypatch.setattr("identity.repositories.history.list_messages", _raise)

        assert load_server_history(local_identity(), CONVERSATION_ID, _settings()) is None
        assert session.closed is True


class TestPairing:
    def test_rows_are_paired_in_sequence_order_not_arrival_order(self):
        rows = [
            FakeRow("assistant", "A2", 4, status="succeeded"),
            FakeRow("user", "Q1", 1),
            FakeRow("assistant", "A1", 2, status="succeeded"),
            FakeRow("user", "Q2", 3),
        ]
        paired = _pair_rows(rows)
        assert [e["question"] for e in paired] == ["Q1", "Q2"]
        assert [e["answer"] for e in paired] == ["A1", "A2"]

    def test_an_orphan_assistant_row_is_skipped_not_guessed_at(self):
        # There is no question to attribute it to, and inventing one would
        # put words in the user's mouth.
        paired = _pair_rows([FakeRow("assistant", "A1", 1, status="succeeded")])
        assert paired == []

    def test_a_blank_question_is_dropped(self):
        paired = _pair_rows(
            [FakeRow("user", "   ", 1), FakeRow("assistant", "A", 2, status="succeeded")]
        )
        assert paired == []

    def test_table_refs_stored_as_dicts_are_flattened_to_names(self):
        # `chat_persistence` records schema table refs as objects; the
        # resolver wants plain names.
        paired = _pair_rows(
            [
                FakeRow("user", "Q", 1),
                FakeRow(
                    "assistant",
                    "A",
                    2,
                    status="succeeded",
                    metadata={"schema_tables": [{"table_name": "orders"}, {"table_name": "items"}]},
                ),
            ]
        )
        assert paired[0]["tables"] == ["orders", "items"]

    def test_missing_metadata_is_not_an_error(self):
        paired = _pair_rows(
            [FakeRow("user", "Q", 1), FakeRow("assistant", "A", 2, status="succeeded")]
        )
        assert paired[0]["sql"] is None
        assert paired[0]["tables"] == []


class TestWiredIntoAsk:
    def test_ask_prefers_the_stored_history_when_one_is_returned(self, monkeypatch):
        from fastapi.testclient import TestClient

        import api.main as main

        captured: dict[str, Any] = {}
        stored = [
            {
                "question": "Show total sales by year",
                "sql": "SELECT 1",
                "tables": ["orders"],
                "status": "succeeded",
                "answer": "Sales grew.",
            }
        ]
        monkeypatch.setattr(main, "load_server_history", lambda *a, **k: stored)
        monkeypatch.setattr(
            main,
            "run_orchestrated",
            lambda question, history, **kw: (
                captured.update(history=history),
                {
                    "status": "succeeded",
                    "sql": "SELECT 1",
                    "result_rows": [[1]],
                    "result_columns": ["x"],
                    "schema_tables": [],
                    "error_history": [],
                    "attempt_history": [],
                    "stage_timings": [],
                    "retry_count": 0,
                },
            )[1],
        )

        response = TestClient(main.app).post(
            "/ask",
            json={
                "question": "Now by month",
                # Deliberately different from what the store holds.
                "conversation_history": [{"question": "something else entirely"}],
            },
        )

        assert response.status_code == 200
        assert captured["history"] == stored

    def test_ask_uses_the_client_array_when_no_stored_history_is_returned(self, monkeypatch):
        from fastapi.testclient import TestClient

        import api.main as main

        captured: dict[str, Any] = {}
        monkeypatch.setattr(main, "load_server_history", lambda *a, **k: None)
        monkeypatch.setattr(
            main,
            "run_orchestrated",
            lambda question, history, **kw: (
                captured.update(history=history),
                {
                    "status": "succeeded",
                    "sql": "SELECT 1",
                    "result_rows": [[1]],
                    "result_columns": ["x"],
                    "schema_tables": [],
                    "error_history": [],
                    "attempt_history": [],
                    "stage_timings": [],
                    "retry_count": 0,
                },
            )[1],
        )

        response = TestClient(main.app).post(
            "/ask",
            json={
                "question": "Now by month",
                "conversation_history": [{"question": "Show total sales by year"}],
            },
        )

        assert response.status_code == 200
        assert captured["history"][0]["question"] == "Show total sales by year"
