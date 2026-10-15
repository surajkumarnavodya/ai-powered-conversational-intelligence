"""HTTP tests for `api/streaming.py` -- `POST /ask/stream`.

The property that matters most is `TestItReusesTheGovernedPath`: the
streaming route must not be a second, less-governed way to ask a question.
It calls `api.main.ask`, so every control there applies because it is the
same code -- these tests prove the call actually happens and that a
refusal inside it still refuses.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
import api.streaming as streaming_mod
from agent.progress import ProgressEvent, current_sink
from api.schemas import AskResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _events(body: str) -> list[tuple[str, dict]]:
    """Parses an SSE body into `(event, data)` pairs, ignoring comments."""
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


def _fake_response(**overrides) -> AskResponse:
    base = dict(
        session_id="s-1",
        status="succeeded",
        insight="42 orders.",
    )
    base.update(overrides)
    return AskResponse(**base)  # type: ignore[arg-type]


@pytest.fixture
def stub_ask(monkeypatch):
    """Replaces `api.main.ask` with a stub that can emit progress.

    Patched on `api.main` rather than on `api.streaming`, because the
    route imports it lazily inside the function -- so patching the source
    module is what a real call would see.
    """

    def _install(fn):
        monkeypatch.setattr(api_main, "ask", fn, raising=True)

    return _install


class TestEventSequence:
    def test_a_successful_run_streams_open_result_done(self, client, stub_ask):
        stub_ask(lambda payload, request, identity: _fake_response())
        response = client.post("/ask/stream", json={"question": "how many orders?"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        names = [name for name, _ in _events(response.text)]
        assert names[0] == "open"
        assert "result" in names
        assert names[-1] == "done"

    def test_the_result_event_is_a_full_ask_response(self, client, stub_ask):
        stub_ask(lambda payload, request, identity: _fake_response(insight="42 orders."))
        response = client.post("/ask/stream", json={"question": "q"})
        result = next(data for name, data in _events(response.text) if name == "result")
        assert result["insight"] == "42 orders."
        assert result["status"] == "succeeded"
        # The same shape /ask returns -- it round-trips through the model.
        assert AskResponse(**result).insight == "42 orders."

    def test_progress_events_are_forwarded(self, client, stub_ask):
        def _ask(payload, request, identity):
            sink = current_sink()
            assert sink is not None, "the sink must be bound on the worker thread"
            sink.emit(ProgressEvent("retrieve_schema", 1, 12.5, "ok"))
            sink.emit(ProgressEvent("generate_sql", 1, 90.0, "ok"))
            return _fake_response()

        stub_ask(_ask)
        response = client.post("/ask/stream", json={"question": "q"})
        progress = [data for name, data in _events(response.text) if name == "progress"]
        assert [p["stage"] for p in progress] == ["retrieve_schema", "generate_sql"]
        assert progress[0]["duration_ms"] == 12.5
        assert progress[0]["outcome"] == "ok"

    def test_done_reports_the_dropped_count(self, client, stub_ask):
        def _ask(payload, request, identity):
            sink = current_sink()
            assert sink is not None
            for i in range(3):
                sink.emit(ProgressEvent(f"s{i}", 1, 1.0, "ok"))
            return _fake_response()

        stub_ask(_ask)
        response = client.post("/ask/stream", json={"question": "q"})
        done = next(data for name, data in _events(response.text) if name == "done")
        assert done["dropped"] == 0

    def test_done_is_always_last_even_on_error(self, client, stub_ask):
        def _boom(payload, request, identity):
            raise RuntimeError("kaboom")

        stub_ask(_boom)
        response = client.post("/ask/stream", json={"question": "q"})
        names = [name for name, _ in _events(response.text)]
        assert names[-1] == "done"
        assert "error" in names
        assert "result" not in names


class TestErrorsBecomeEvents:
    def test_an_http_exception_becomes_an_error_event_with_its_status(self, client, stub_ask):
        """Once the stream opens the HTTP status is already 200, so a
        refusal has to arrive in-band. A client must read the stream, not
        just the status."""
        from fastapi import HTTPException

        def _rate_limited(payload, request, identity):
            raise HTTPException(status_code=429, detail="Too many requests.")

        stub_ask(_rate_limited)
        response = client.post("/ask/stream", json={"question": "q"})
        assert response.status_code == 200
        error = next(data for name, data in _events(response.text) if name == "error")
        assert error["status"] == 429
        assert error["detail"] == "Too many requests."

    def test_an_unexpected_error_does_not_leak_internal_detail(self, client, stub_ask):
        """`CLAUDE.md` 5.6 -- the stream honours the same rule the global
        handlers enforce for ordinary responses."""

        def _boom(payload, request, identity):
            raise RuntimeError("connection to postgresql://user:hunter2@db failed")

        stub_ask(_boom)
        response = client.post("/ask/stream", json={"question": "q"})
        assert "hunter2" not in response.text
        assert "postgresql://" not in response.text
        error = next(data for name, data in _events(response.text) if name == "error")
        assert error["status"] == 500
        assert error["detail"] == "The request could not be completed."


class TestItReusesTheGovernedPath:
    """The streaming route must not be a second, less-governed way to ask."""

    def test_it_calls_api_main_ask(self, client, stub_ask):
        called: list[tuple] = []

        def _ask(payload, request, identity):
            called.append((payload.question, identity))
            return _fake_response()

        stub_ask(_ask)
        client.post("/ask/stream", json={"question": "trace me"})
        assert len(called) == 1
        assert called[0][0] == "trace me"

    def test_it_passes_the_validated_request_model_through(self, client, stub_ask):
        """So `AskRequest`'s own validation (including `sources`) applies
        identically -- it is the same model."""
        seen: list = []
        stub_ask(lambda payload, request, identity: (seen.append(payload), _fake_response())[1])
        client.post("/ask/stream", json={"question": "q", "sources": ["sql"]})
        assert seen[0].sources == ["sql"]

    def test_an_invalid_request_is_rejected_before_the_stream_opens(self, client):
        """Request-model validation happens at the FastAPI boundary, so it
        is still a real 422 rather than an in-band error event."""
        response = client.post("/ask/stream", json={"question": "q", "sources": ["nope"]})
        assert response.status_code == 422

    def test_a_missing_question_is_a_422(self, client):
        assert client.post("/ask/stream", json={}).status_code == 422

    def test_the_route_declares_the_same_ask_permission(self):
        """Structural: the dependency is what enforces authorization, so a
        route that declared a weaker one would be a bypass."""
        import inspect

        from agent.authz import Permission

        source = inspect.getsource(streaming_mod.ask_stream)
        assert "require_permission(Permission.ASK)" in source
        assert Permission.ASK.value == "ask"


class TestSinkLifecycle:
    def test_the_sink_is_unbound_after_the_request(self, client, stub_ask):
        stub_ask(lambda payload, request, identity: _fake_response())
        client.post("/ask/stream", json={"question": "q"})
        assert current_sink() is None

    def test_the_sink_is_unbound_even_when_the_handler_raises(self, client, stub_ask):
        def _boom(payload, request, identity):
            raise RuntimeError("x")

        stub_ask(_boom)
        client.post("/ask/stream", json={"question": "q"})
        assert current_sink() is None


class TestFraming:
    def test_every_data_line_is_single_line_json(self, client, stub_ask):
        """An embedded newline would split one SSE message into two
        malformed ones."""

        def _ask(payload, request, identity):
            sink = current_sink()
            assert sink is not None
            sink.emit(ProgressEvent("multi\nline", 1, 1.0, "ok"))
            return _fake_response(insight="line one\nline two")

        stub_ask(_ask)
        response = client.post("/ask/stream", json={"question": "q"})
        for line in response.text.splitlines():
            if line.startswith("data: "):
                json.loads(line[len("data: ") :])  # each data line parses alone

    def test_buffering_is_disabled_for_intermediaries(self, client, stub_ask):
        stub_ask(lambda payload, request, identity: _fake_response())
        response = client.post("/ask/stream", json={"question": "q"})
        assert response.headers.get("x-accel-buffering") == "no"
        assert "no-cache" in response.headers.get("cache-control", "")
