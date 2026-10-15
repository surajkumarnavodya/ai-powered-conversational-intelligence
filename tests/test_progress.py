"""Tests for `agent/progress.py` -- the streaming progress sink.

Two things matter most and have their own classes:

- **`TestNoSinkIsAZeroCostNoOp`**: every existing caller (`POST /ask`,
  `eval/runner.py`, every script) binds no sink, and must be completely
  unaffected.
- **`TestEventsCarryNoSensitiveData`**: a progress event reaches the client
  *as it happens*, before the result gate has run inside
  `execute_sql_node`. So it may carry stage metadata and nothing else.
"""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

from agent.progress import (
    ProgressEvent,
    ProgressSink,
    current_sink,
    emit_progress,
    reset_progress_sink,
    set_progress_sink,
)


@pytest.fixture(autouse=True)
def _no_leaked_sink():
    """Guarantees no test leaves a sink bound for the next one."""
    yield
    assert current_sink() is None, "a test leaked a progress sink"


class TestNoSinkIsAZeroCostNoOp:
    def test_nothing_is_bound_by_default(self):
        assert current_sink() is None

    def test_emitting_without_a_sink_does_not_raise(self):
        emit_progress("generate_sql", attempt=1, duration_ms=1.0, outcome="ok")

    def test_emitting_without_a_sink_records_nothing_anywhere(self):
        sink = ProgressSink()
        emit_progress("generate_sql", attempt=1, duration_ms=1.0, outcome="ok")
        assert sink.drain() == ()


class TestSinkBinding:
    def test_a_bound_sink_receives_events(self):
        sink = ProgressSink()
        token = set_progress_sink(sink)
        try:
            emit_progress("validate_sql", attempt=2, duration_ms=3.5, outcome="ok")
        finally:
            reset_progress_sink(token)

        events = sink.drain()
        assert len(events) == 1
        assert events[0].stage == "validate_sql"
        assert events[0].attempt == 2
        assert events[0].outcome == "ok"

    def test_reset_restores_the_previous_binding(self):
        outer, inner = ProgressSink(), ProgressSink()
        t1 = set_progress_sink(outer)
        try:
            t2 = set_progress_sink(inner)
            try:
                emit_progress("a", attempt=1, duration_ms=1.0, outcome="ok")
            finally:
                reset_progress_sink(t2)
            emit_progress("b", attempt=1, duration_ms=1.0, outcome="ok")
        finally:
            reset_progress_sink(t1)

        assert [e.stage for e in inner.drain()] == ["a"]
        assert [e.stage for e in outer.drain()] == ["b"]

    def test_drain_clears(self):
        sink = ProgressSink()
        sink.emit(ProgressEvent("a", 1, 1.0, "ok"))
        assert len(sink.drain()) == 1
        assert sink.drain() == ()

    def test_a_sink_does_not_leak_across_threads(self):
        """A `ContextVar`, not a module global: this process serves
        concurrent requests on a bounded pool, and a global would leak one
        caller's progress into another's stream."""
        sink = ProgressSink()
        seen: list[object] = []

        def _other_thread() -> None:
            seen.append(current_sink())

        token = set_progress_sink(sink)
        try:
            thread = threading.Thread(target=_other_thread)
            thread.start()
            thread.join()
        finally:
            reset_progress_sink(token)

        assert seen == [None], "a sink bound on one thread was visible on another"


class TestBounding:
    def test_the_buffer_is_capped_and_drops_are_counted(self):
        """A slow or disconnected reader must never apply backpressure into
        the graph -- a question's execution should not slow down because a
        browser tab is busy."""
        sink = ProgressSink(max_events=3)
        for i in range(10):
            sink.emit(ProgressEvent(f"s{i}", 1, 1.0, "ok"))
        assert len(sink.drain()) == 3
        assert sink.dropped == 7

    def test_emit_never_blocks_when_full(self):
        sink = ProgressSink(max_events=1)
        for _ in range(1000):
            sink.emit(ProgressEvent("s", 1, 1.0, "ok"))
        assert sink.dropped == 999

    def test_a_closed_sink_accepts_nothing_further(self):
        sink = ProgressSink()
        sink.close()
        sink.emit(ProgressEvent("s", 1, 1.0, "ok"))
        assert sink.drain() == ()

    def test_close_is_idempotent(self):
        sink = ProgressSink()
        sink.close()
        sink.close()

    def test_concurrent_emits_are_all_accounted_for(self):
        """Real threads, not a sequential loop: the orchestrator fans out
        to several subgraphs in one graph step, so emits are genuinely
        concurrent and a sequential test would pass even with a race."""
        sink = ProgressSink(max_events=10_000)
        threads = [
            threading.Thread(
                target=lambda n=n: [
                    sink.emit(ProgressEvent(f"t{n}", 1, 1.0, "ok")) for _ in range(200)
                ]
            )
            for n in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(sink.drain()) + sink.dropped == 8 * 200


class TestEventsCarryNoSensitiveData:
    """An event is sent to the client before the result gate has run."""

    def test_the_event_shape_is_exactly_five_metadata_fields(self):
        """A new field is a deliberate decision, not an accident -- this
        fails if one is added without reconsidering what may be streamed."""
        assert set(ProgressEvent.__dataclass_fields__) == {
            "stage",
            "attempt",
            "duration_ms",
            "outcome",
            "error_category",
        }

    def test_an_event_is_frozen(self):
        event = ProgressEvent("a", 1, 1.0, "ok")
        with pytest.raises(FrozenInstanceError):
            event.stage = "mutated"  # type: ignore[misc]

    def test_emit_progress_accepts_no_sql_or_row_argument(self):
        """Structural: the signature itself is the control. A caller cannot
        attach a row or a statement to an event because there is nowhere to
        put one."""
        import inspect

        params = set(inspect.signature(emit_progress).parameters)
        assert params == {"stage", "attempt", "duration_ms", "outcome", "error_category"}

    def test_error_category_is_a_category_never_a_message(self):
        """A message can embed a database identifier or a fragment of the
        question; a category cannot."""
        sink = ProgressSink()
        token = set_progress_sink(sink)
        try:
            emit_progress(
                "execute_sql",
                attempt=1,
                duration_ms=5.0,
                outcome="error",
                error_category="missing_table",
            )
        finally:
            reset_progress_sink(token)
        event = sink.drain()[0]
        assert event.error_category == "missing_table"
        assert " " not in (event.error_category or "")


class TestNodeIntegration:
    """`_timed_node` is the emit site -- proven against the real decorator
    rather than by reading it."""

    def test_a_decorated_node_emits_exactly_one_event(self):
        from agent.nodes import _timed_node

        @_timed_node("fake_stage")
        def _node(state):
            return {"status": "succeeded"}

        sink = ProgressSink()
        token = set_progress_sink(sink)
        try:
            result = _node({"retry_count": 0})
        finally:
            reset_progress_sink(token)

        events = sink.drain()
        assert len(events) == 1
        assert events[0].stage == "fake_stage"
        assert events[0].attempt == 1
        # The node's own contract is untouched.
        assert result["status"] == "succeeded"
        assert "stage_timings" in result

    def test_the_attempt_number_follows_retry_count(self):
        from agent.nodes import _timed_node

        @_timed_node("s")
        def _node(state):
            return {}

        sink = ProgressSink()
        token = set_progress_sink(sink)
        try:
            _node({"retry_count": 2})
        finally:
            reset_progress_sink(token)
        assert sink.drain()[0].attempt == 3

    def test_a_node_runs_identically_with_no_sink_bound(self):
        """The default path for every existing caller."""
        from agent.nodes import _timed_node

        @_timed_node("s")
        def _node(state):
            return {"status": "succeeded"}

        result = _node({"retry_count": 0})
        assert result["status"] == "succeeded"
        assert result["stage_timings"][0]["stage"] == "s"


class TestTheGuardIsNotVacuous:
    def test_removing_the_binding_would_lose_the_event(self):
        """Shows the sink is what captures events, not some ambient
        collection that would pass regardless."""
        from agent.nodes import _timed_node

        @_timed_node("s")
        def _node(state):
            return {}

        unbound = ProgressSink()
        _node({"retry_count": 0})
        assert unbound.drain() == ()

        token = set_progress_sink(unbound)
        try:
            _node({"retry_count": 0})
        finally:
            reset_progress_sink(token)
        assert len(unbound.drain()) == 1
