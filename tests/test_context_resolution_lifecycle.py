"""Lifecycle tests for context resolution: streaming, concurrency, editing.

`tests/test_context_resolution.py` covers the resolver as a pure function
and `tests/test_context_resolution_integration.py` covers its wiring into
the graph. Three rows of the feature's acceptance criteria were left
without a test of their own, and each fails in a different way:

- **Streaming.** `POST /ask/stream` is a second route onto the same
  handler. `tests/test_api_streaming.py::TestItReusesTheGovernedPath`
  already pins that it *calls* `api.main.ask`, which is what makes the
  resolution happen at all; what was unpinned is that the resolution
  block survives the SSE serialization, and that the rewritten query --
  an internal representation -- never reaches the person as prose.
- **Concurrency.** The resolver is a pure function with no store and no
  module state, which is *why* two simultaneous conversations cannot
  cross. That is an easy property to lose later (one memoization keyed on
  the message alone would do it), and a sequential test would pass
  anyway, so this uses real threads released from a barrier (CLAUDE.md
  §7).
- **Editing and regeneration.** `CONVERSATION_HISTORY_SOURCE=server`
  exists so that editing or deleting an earlier turn changes the next
  answer's context. `tests/test_conversation_context_source.py` proves
  the loader reads the store; these prove the *consequence* -- that an
  edited turn resolves against its new text, a deleted one stops being
  reachable, and regenerating a question twice reaches the same answer
  without mutating the history it was given.
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from agent.context_resolution import ContextRelation, resolve_context
from agent.state import ConversationExchange
from api.conversation_context import load_server_history
from api.schemas import AskResponse, ContextResolutionOut
from config.settings import Settings
from security.oidc import AuthIdentity
from security.secrets import SecretStr


def turn(question: str, *, answer: str | None = None) -> ConversationExchange:
    exchange: ConversationExchange = {
        "question": question,
        "sql": None,
        "tables": [],
        "status": "succeeded",
    }
    if answer is not None:
        exchange["answer"] = answer
    return exchange


# --- Streaming parity -------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


@pytest.fixture
def stub_ask(monkeypatch):
    """Replaces `api.main.ask`, the handler the streaming route calls.

    Patched on `api.main` rather than `api.streaming`, because the route
    imports it lazily inside the function -- the same reason
    `tests/test_api_streaming.py` does it this way.
    """

    def _install(fn):
        monkeypatch.setattr(api_main, "ask", fn, raising=True)

    return _install


RESOLUTION = ContextResolutionOut(
    relation="followup",
    resolved_question="What is the price of the Google Pixel 10a?",
    rewritten=True,
    antecedent_question="Tell me about the Google Pixel 10a",
    confidence=0.82,
    source="deterministic",
    signals=["attribute_only"],
)


def _response(**overrides) -> AskResponse:
    base: dict[str, Any] = dict(
        session_id="s-1",
        status="succeeded",
        insight="Listed below.",
        followup_classification="followup",
        context_resolution=RESOLUTION,
    )
    base.update(overrides)
    return AskResponse(**base)


def _events(body: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name is not None:
            out.append((name, data if data is not None else {}))
    return out


def _ask_stream(client: TestClient, question: str = "Price?") -> list[tuple[str, dict]]:
    response = client.post("/ask/stream", json={"question": question})
    assert response.status_code == 200
    return _events(response.text)


class TestStreamingCarriesTheResolution:
    """The SSE route must not be a less-informative way to ask."""

    def test_the_result_event_carries_the_resolution_block(self, client, stub_ask):
        stub_ask(lambda payload, request, identity: _response())

        results = [data for name, data in _ask_stream(client) if name == "result"]

        assert len(results) == 1
        resolution = results[0]["context_resolution"]
        assert resolution["relation"] == "followup"
        assert resolution["resolved_question"] == "What is the price of the Google Pixel 10a?"
        assert resolution["rewritten"] is True
        assert resolution["antecedent_question"] == "Tell me about the Google Pixel 10a"

    def test_the_legacy_classification_still_travels_too(self, client, stub_ask):
        # The frontend's existing context panel switches on this field; a
        # route that dropped it would silently blank that panel.
        stub_ask(lambda payload, request, identity: _response())

        results = [data for name, data in _ask_stream(client) if name == "result"]

        assert results[0]["followup_classification"] == "followup"

    def test_a_clarification_reaches_the_caller_as_a_clarification(self, client, stub_ask):
        # An ambiguous reference short-circuits the graph. Streaming has to
        # deliver that as the same status the non-streaming route reports,
        # not as an empty successful answer.
        ambiguous = ContextResolutionOut(
            relation="ambiguous",
            resolved_question="Which one do you mean?",
            rewritten=False,
            confidence=0.2,
            source="deterministic",
            candidate_topics=["Google Pixel 10a", "Samsung Galaxy A56"],
        )
        stub_ask(
            lambda payload, request, identity: _response(
                status="needs_clarification",
                insight=None,
                followup_classification="ambiguous",
                context_resolution=ambiguous,
                clarification_message="Which phone do you mean -- the Pixel 10a or the Galaxy A56?",
            )
        )

        results = [data for name, data in _ask_stream(client) if name == "result"]

        assert results[0]["status"] == "needs_clarification"
        assert "Pixel 10a" in results[0]["clarification_message"]
        assert results[0]["context_resolution"]["candidate_topics"] == [
            "Google Pixel 10a",
            "Samsung Galaxy A56",
        ]

    def test_the_rewritten_query_is_never_streamed_as_prose(self, client, stub_ask):
        """The internal representation stays metadata, in every event.

        The resolved question appears in exactly one place -- the
        `context_resolution` block of the result -- and never in a
        progress event's human-readable text, which the UI renders
        directly as the agent's running commentary.
        """
        stub_ask(lambda payload, request, identity: _response())

        events = _ask_stream(client)

        progress_text = json.dumps(
            [data for name, data in events if name not in ("result",)],
        )
        assert "What is the price of the Google Pixel 10a?" not in progress_text


# --- Concurrency, with real threads ----------------------------------------


class TestConcurrentConversationsDoNotCross:
    """Resolution is per-call state, never process-global (CLAUDE.md §4.6).

    Sequential calls already prove isolation in
    `tests/test_context_resolution.py::TestIsolation`. These release real
    threads from a barrier so the calls genuinely overlap, which is the
    only version of this test that would fail if a future change
    introduced a shared cache keyed on the message text -- the message
    text is *identical* across these conversations, and only the window
    differs.
    """

    CONVERSATIONS = {
        "pixel": "Tell me about the Google Pixel 10a",
        "galaxy": "Tell me about the Samsung Galaxy A56",
        "thinkpad": "Tell me about the Lenovo ThinkPad X1",
        "pixelbook": "Tell me about the Google Pixelbook Go",
    }

    def _resolve_under_barrier(self, barrier: threading.Barrier, history_question: str):
        barrier.wait(timeout=10)
        return resolve_context(
            "Price?",
            [turn(history_question)],
            recent_turns=6,
            llm=None,
        )

    def test_each_thread_resolves_against_its_own_window(self):
        names = list(self.CONVERSATIONS)
        barrier = threading.Barrier(len(names))

        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = {
                name: pool.submit(self._resolve_under_barrier, barrier, self.CONVERSATIONS[name])
                for name in names
            }
            resolved = {name: future.result(timeout=30) for name, future in futures.items()}

        # Each resolution names its own subject and no other conversation's.
        assert "Pixel 10a" in resolved["pixel"].resolved_question
        assert "Galaxy A56" in resolved["galaxy"].resolved_question
        assert "ThinkPad X1" in resolved["thinkpad"].resolved_question
        assert "Pixelbook Go" in resolved["pixelbook"].resolved_question

        assert "Galaxy" not in resolved["pixel"].resolved_question
        assert "Pixel" not in resolved["galaxy"].resolved_question
        assert "Pixel" not in resolved["thinkpad"].resolved_question
        assert "ThinkPad" not in resolved["pixelbook"].resolved_question

    def test_a_concurrent_new_topic_stays_independent(self):
        # One thread asks a genuinely unrelated question while three
        # resolve follow-ups. The independent one must carry nothing.
        barrier = threading.Barrier(4)

        def new_topic():
            barrier.wait(timeout=10)
            return resolve_context(
                "What is the price of crude oil?",
                [turn("Tell me about the Google Pixel 10a")],
                recent_turns=6,
                llm=None,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            followups = [
                pool.submit(self._resolve_under_barrier, barrier, self.CONVERSATIONS["pixel"])
                for _ in range(3)
            ]
            independent = pool.submit(new_topic)
            independent_result = independent.result(timeout=30)
            followup_results = [f.result(timeout=30) for f in followups]

        assert independent_result.relation is ContextRelation.NEW_TOPIC
        assert "Pixel" not in independent_result.resolved_question
        assert all(r.relation is ContextRelation.FOLLOWUP for r in followup_results)


# --- Editing, deletion and regeneration ------------------------------------


def _settings(**overrides: Any) -> Settings:
    """Server-authoritative history. Mirrors the owning file's fixture
    (`tests/test_conversation_context_source.py`), which is where the
    loader's own behaviour is tested."""
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
        "auth_database_url": "postgresql://placeholder/unused",
        "jwt_secret_key": "s" * 40,
        "conversation_history_source": "server",
    }
    base.update(overrides)
    return Settings(**base)


USER_ID = uuid.uuid4()
CONVERSATION_ID = str(uuid.uuid4())


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
    state: dict[str, Any] = {"rows": [], "session": FakeSession()}
    monkeypatch.setattr("identity.db.get_identity_session", lambda settings: state["session"])

    def _list_messages(session, *, conversation_id, user_id, limit, offset):
        return list(state["rows"]), len(state["rows"])

    monkeypatch.setattr("identity.repositories.history.list_messages", _list_messages)
    return state


def _exchange_rows(pairs: list[tuple[str, str]]) -> list[FakeRow]:
    """Stored rows for `n` completed exchanges.

    The assistant row's `status` has to be the literal `"succeeded"`:
    `api.conversation_context._exchange_status` maps anything else to
    `"failed"`, and `agent.conversation_memory.build_memory` deliberately
    keeps a failed turn out of the recent window (quoting it in the older
    digest instead), because a turn that never produced an answer is not
    something a follow-up can resolve against. Omitting it here made these
    tests resolve to `missing_context` -- correct behaviour, wrong fixture.
    """
    rows: list[FakeRow] = []
    for index, (question, answer) in enumerate(pairs):
        rows.append(FakeRow(role="user", content=question, sequence_number=index * 2))
        rows.append(
            FakeRow(
                role="assistant",
                content=answer,
                sequence_number=index * 2 + 1,
                status="succeeded",
            )
        )
    return rows


def _identity() -> AuthIdentity:
    return AuthIdentity(subject=str(USER_ID), roles=("analyst",), mode="local")


class TestEditingAnEarlierTurn:
    """What `CONVERSATION_HISTORY_SOURCE=server` is actually for.

    The loader's own tests prove it reads the store. These prove the
    consequence the setting exists to buy: the *next* resolution follows
    the edit, because the store is what the resolver reads.
    """

    def _resolve_from_store(self, settings, rows):
        history = load_server_history(_identity(), CONVERSATION_ID, settings)
        assert history is not None
        return resolve_context("Price?", history, recent_turns=6, llm=None)

    def test_an_edited_question_changes_what_a_followup_resolves_against(self, stub_store):
        settings = _settings()

        stub_store["rows"] = _exchange_rows(
            [("Tell me about the Google Pixel 10a", "It is a mid-range phone.")]
        )
        before = self._resolve_from_store(settings, stub_store["rows"])
        assert "Pixel 10a" in before.resolved_question

        # The person edits that turn to ask about a different phone.
        stub_store["rows"] = _exchange_rows(
            [("Tell me about the Samsung Galaxy A56", "It is a mid-range phone.")]
        )
        after = self._resolve_from_store(settings, stub_store["rows"])

        assert "Galaxy A56" in after.resolved_question
        assert "Pixel" not in after.resolved_question

    def test_deleting_the_only_turn_leaves_a_followup_with_nothing_to_resolve(self, stub_store):
        settings = _settings()
        stub_store["rows"] = _exchange_rows(
            [("Tell me about the Google Pixel 10a", "It is a mid-range phone.")]
        )
        assert (
            "Pixel 10a" in self._resolve_from_store(settings, stub_store["rows"]).resolved_question
        )

        stub_store["rows"] = []
        after = self._resolve_from_store(settings, stub_store["rows"])

        # Fails closed into a question rather than resolving against a
        # turn the person removed.
        assert after.relation is ContextRelation.MISSING_CONTEXT
        assert after.antecedent is None

    def test_deleting_an_intervening_turn_makes_the_older_one_the_antecedent(self, stub_store):
        settings = _settings()
        stub_store["rows"] = _exchange_rows(
            [
                ("Tell me about the Google Pixel 10a", "A mid-range phone."),
                ("Tell me about the Samsung Galaxy A56", "Also a mid-range phone."),
            ]
        )
        # Two phones in the window is the ambiguous case, and it asks
        # rather than guessing -- which is the behaviour deletion changes.
        before = self._resolve_from_store(settings, stub_store["rows"])
        assert before.relation is ContextRelation.AMBIGUOUS

        stub_store["rows"] = _exchange_rows(
            [("Tell me about the Google Pixel 10a", "A mid-range phone.")]
        )
        after = self._resolve_from_store(settings, stub_store["rows"])

        # One candidate left, so there is nothing to ask about.
        assert after.relation is ContextRelation.FOLLOWUP
        assert "Pixel 10a" in after.resolved_question

    def test_a_turn_that_failed_is_not_offered_as_an_antecedent(self, stub_store):
        """A question that never produced an answer cannot anchor a follow-up.

        `build_memory` keeps a failed turn out of the recent window on
        purpose: resolving "Price?" against a question that errored would
        answer about a subject the person never actually got an answer
        for. It stays quoted in the older digest, so it is not *lost* --
        just not an antecedent.
        """
        settings = _settings()
        stub_store["rows"] = [
            FakeRow(role="user", content="Tell me about the Google Pixel 10a", sequence_number=0),
            FakeRow(
                role="assistant",
                content="Something went wrong.",
                sequence_number=1,
                status="failed",
            ),
        ]

        result = self._resolve_from_store(settings, stub_store["rows"])

        assert result.relation is ContextRelation.MISSING_CONTEXT
        assert result.antecedent is None


class TestRegeneration:
    """Asking the same thing again must reach the same answer.

    Regeneration re-runs a turn with the history it already had. Two
    properties matter: the result is identical (so a regenerated answer is
    not silently resolved against something else), and the history the
    caller passed is not mutated (so a client that reuses its own array --
    which is the default `client` mode -- is not corrupted by having asked
    once).
    """

    HISTORY = [
        turn("Analyze a project budget of 2 crore", answer="Broken down by phase."),
        turn("Draft a LinkedIn post about the result", answer="Here is a draft."),
    ]

    def test_resolving_twice_reaches_the_same_resolution(self):
        first = resolve_context("Make it more professional", self.HISTORY, recent_turns=6, llm=None)
        second = resolve_context(
            "Make it more professional", self.HISTORY, recent_turns=6, llm=None
        )

        assert first.relation is second.relation
        assert first.resolved_question == second.resolved_question
        assert first.antecedent == second.antecedent
        assert first.confidence == pytest.approx(second.confidence)

    def test_resolving_does_not_mutate_the_history_it_was_given(self):
        snapshot = json.dumps(self.HISTORY, sort_keys=True)

        resolve_context("Make it more professional", self.HISTORY, recent_turns=6, llm=None)

        assert json.dumps(self.HISTORY, sort_keys=True) == snapshot

    def test_a_regenerated_refinement_still_names_the_artifact_not_the_budget(self):
        # Two plausible antecedents, one of them more recent *and* the
        # actual referent of "it". Regeneration must keep choosing it.
        for _ in range(3):
            result = resolve_context(
                "Make it more professional", self.HISTORY, recent_turns=6, llm=None
            )
            assert result.relation is ContextRelation.REFINEMENT
            assert result.antecedent is not None
            assert "LinkedIn" in result.antecedent["question"]
