"""`POST /ask/stream`'s progress must survive the thread `/ask` runs on.

## The defect these tests were written against (`UI-004`)

`api/streaming.py` binds `agent.progress`'s sink with a `ContextVar`, on
its own worker thread, "which is the thread the graph actually runs on"
(its own docstring). That was true when the sink was added and is not true
now: `api.main.ask` hands `run_orchestrated` to a *second*, bounded
`ThreadPoolExecutor` for admission control, and
`ThreadPoolExecutor.submit` gives its worker a **fresh, empty** context.
So `agent.nodes`' `emit_progress` read `None` for the sink on every real
request and returned early.

Measured against the live application on 2026-10-29: a 58-second question
on `POST /ask/stream` produced `open`, three keepalive comments, `result`
and `done` -- and **zero** `progress` events, with `done` reporting
`{"dropped": 0}`, so they were never emitted rather than dropped under
backpressure. In the browser the consequence was that the composer's
"Thinking Ns…" badge never named a stage for 123 seconds, which is the
one signal a user has that a slow answer is advancing.

## Why the existing suite did not catch it

`tests/test_api_streaming.py::TestEventSequence
::test_progress_events_are_forwarded` replaces `api.main.ask` wholesale
and emits from the stub. The stub runs on the *streaming* worker thread,
where the sink is bound -- so it passes whether or not the sink survives
the hop into the ask executor. The thread boundary was the whole bug, and
mocking `ask` removes it.

These tests therefore keep the real `ask` path and stub only
`run_orchestrated`, which is where the graph would run, so the sink has to
cross the executor to be seen.
"""

from __future__ import annotations

import threading

import pytest

import api.main as api_main
from agent.progress import ProgressEvent, ProgressSink, current_sink, set_progress_sink


@pytest.fixture
def sink() -> ProgressSink:
    return ProgressSink()


def _run_through_the_executor(sink: ProgressSink, body):
    """Calls `_run_orchestrated_with_timeout` with the sink bound here, the
    way `api/streaming.py` binds it before calling `ask`."""
    token = set_progress_sink(sink)
    try:
        return api_main._run_orchestrated_with_timeout(
            "how many orders?",
            [],
            enable_insight=False,
            session_id="s-1",
            caller_roles=("user",),
            caller_subject=None,
            timeout_seconds=30,
            max_workers=2,
            on_done=lambda: None,
            **body,
        )
    finally:
        sink.close()
        from agent.progress import reset_progress_sink

        reset_progress_sink(token)


class TestTheSinkReachesTheGraphsThread:
    def test_a_node_on_the_ask_executor_can_see_the_sink(self, monkeypatch, sink):
        """The regression itself: `emit_progress` runs on the pool worker."""
        seen: dict[str, object] = {}

        def _fake_run_orchestrated(*_args, **_kwargs):
            seen["thread"] = threading.current_thread().name
            seen["sink"] = current_sink()
            return {"status": "succeeded"}

        monkeypatch.setattr(api_main, "run_orchestrated", _fake_run_orchestrated)
        _run_through_the_executor(sink, {})

        # It really did cross a thread -- otherwise this test proves nothing.
        assert seen["thread"] != threading.current_thread().name
        assert seen["thread"].startswith("ask-worker")
        assert seen["sink"] is sink

    def test_events_emitted_inside_the_graph_are_drainable_afterwards(self, monkeypatch, sink):
        def _fake_run_orchestrated(*_args, **_kwargs):
            current = current_sink()
            assert current is not None, "the sink must be visible on the pool worker"
            current.emit(ProgressEvent("retrieve_schema", 1, 12.5, "ok"))
            current.emit(ProgressEvent("generate_sql", 1, 90.0, "ok"))
            return {"status": "succeeded"}

        monkeypatch.setattr(api_main, "run_orchestrated", _fake_run_orchestrated)
        _run_through_the_executor(sink, {})

        assert [event.stage for event in sink.drain()] == ["retrieve_schema", "generate_sql"]

    def test_emit_progress_itself_reaches_the_sink_across_the_hop(self, monkeypatch, sink):
        """Through the real `emit_progress`, not a hand-held `sink.emit`.

        `agent.nodes` calls `emit_progress`, which reads the ContextVar
        itself -- so this is the call shape that was silently no-oping.
        """
        from agent.progress import emit_progress

        def _fake_run_orchestrated(*_args, **_kwargs):
            emit_progress("execute_sql", attempt=2, duration_ms=7.0, outcome="ok")
            return {"status": "succeeded"}

        monkeypatch.setattr(api_main, "run_orchestrated", _fake_run_orchestrated)
        _run_through_the_executor(sink, {})

        events = sink.drain()
        assert len(events) == 1
        assert events[0].stage == "execute_sql"
        assert events[0].attempt == 2


class TestTheContextCopyIsPerSubmit:
    def test_a_contextvar_set_inside_the_graph_does_not_leak_back(self, monkeypatch, sink):
        """`copy_context()` per submit, so a worker's `.set()` stays there.

        If the *same* context object were reused, a `ContextVar` the graph
        set would be visible to the next caller -- the cross-request leak
        `agent/progress.py`'s own docstring says a module global would
        cause.
        """

        def _fake_run_orchestrated(*_args, **_kwargs):
            set_progress_sink(ProgressSink())  # deliberately never reset
            return {"status": "succeeded"}

        monkeypatch.setattr(api_main, "run_orchestrated", _fake_run_orchestrated)
        token = set_progress_sink(sink)
        try:
            api_main._run_orchestrated_with_timeout(
                "q",
                [],
                enable_insight=False,
                session_id="s-1",
                caller_roles=("user",),
                caller_subject=None,
                timeout_seconds=30,
                max_workers=2,
                on_done=lambda: None,
            )
            # Still ours, not the one the worker bound.
            assert current_sink() is sink
        finally:
            from agent.progress import reset_progress_sink

            reset_progress_sink(token)

    def test_two_sequential_asks_each_get_their_own_copy(self, monkeypatch):
        """A `Context` cannot be entered twice; a shared one would raise."""
        monkeypatch.setattr(api_main, "run_orchestrated", lambda *_a, **_k: {"status": "succeeded"})
        for _ in range(3):
            first = ProgressSink()
            _run_through_the_executor(first, {})

    def test_concurrent_asks_do_not_share_a_sink(self, monkeypatch):
        """Real threads, because a sequential test passes with the bug.

        Two callers each bind their own sink and ask at the same time; each
        must drain only its own events.
        """

        def _fake_run_orchestrated(question, *_args, **_kwargs):
            current = current_sink()
            assert current is not None
            current.emit(ProgressEvent(f"stage_for_{question}", 1, 1.0, "ok"))
            return {"status": "succeeded"}

        monkeypatch.setattr(api_main, "run_orchestrated", _fake_run_orchestrated)

        results: dict[str, tuple[str, ...]] = {}
        barrier = threading.Barrier(2)

        def _caller(name: str) -> None:
            own = ProgressSink()
            token = set_progress_sink(own)
            try:
                barrier.wait(timeout=10)
                api_main._run_orchestrated_with_timeout(
                    name,
                    [],
                    enable_insight=False,
                    session_id=f"s-{name}",
                    caller_roles=("user",),
                    caller_subject=None,
                    timeout_seconds=30,
                    max_workers=4,
                    on_done=lambda: None,
                )
                own.close()
                results[name] = tuple(event.stage for event in own.drain())
            finally:
                from agent.progress import reset_progress_sink

                reset_progress_sink(token)

        threads = [threading.Thread(target=_caller, args=(name,)) for name in ("alice", "bob")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert results == {"alice": ("stage_for_alice",), "bob": ("stage_for_bob",)}
