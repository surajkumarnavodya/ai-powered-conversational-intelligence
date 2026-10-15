"""Per-node progress events, for streaming a run's intermediate state.

Closes the "no streaming" gap `CLAUDE.md` §13 discloses: `POST /ask` is a
single request with no mid-run progress signal, so a long multi-source run
looks hung.

**Nothing here changes how the graph runs.** A sink is bound per request
by `api/streaming.py`; when none is bound -- which is every existing
caller, including `POST /ask`, `eval/runner.py` and every script -- both
`emit` and `current_sink` are a contextvar read and an early return. There
is no queue, no thread and no allocation on the default path.

The emit site is `agent.nodes._timed_node`, the decorator already wrapping
every node. It already computes the stage name, attempt number, duration
and outcome, so progress reporting needs no change to any node body --
the same "cross-cutting, no node needs to know this exists" principle
that decorator's own docstring states for `stage_timings` and `[trace]`.

**What an event may carry is deliberately narrow.** A progress event is
sent to the client *as it happens*, before the result gate has run
(`CLAUDE.md` §5.4: protection happens before the model sees the values, and
the gate runs inside `execute_sql_node`). So an event carries stage name,
attempt number, duration and a coarse outcome -- never SQL, never a row,
never an error message, never anything derived from retrieved content.
Everything a caller actually receives still goes through the ordinary
governed response path. `tests/test_progress.py` pins this.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal

#: Coarse per-stage outcome. Mirrors `agent.nodes._stage_outcome`'s own
#: vocabulary so the two cannot describe the same event differently.
ProgressOutcome = Literal["ok", "no_op", "error"]


@dataclass(frozen=True)
class ProgressEvent:
    """One node transition.

    Every field is safe to send to the caller that triggered the run:

    - `stage` is a fixed node name from the graph wiring, not user input.
    - `attempt` is an integer.
    - `duration_ms` is a float.
    - `outcome` is one of three fixed strings.
    - `error_category` is a fixed enum-ish category name (the same one
      `stage_timings` records), **never an error message** -- a message
      can embed a database identifier or a fragment of the question.

    Nothing here is derived from retrieved content, a database value, or
    an LLM response.
    """

    stage: str
    attempt: int
    duration_ms: float
    outcome: ProgressOutcome
    error_category: str | None = None


class ProgressSink:
    """A bounded, thread-safe collector of `ProgressEvent`s.

    Bounded on purpose. The orchestrator fans out to several subgraphs in
    one graph step, so emits are genuinely concurrent, and a slow or
    disconnected SSE reader must never apply backpressure *into the graph*
    -- a question's execution should not slow down because a browser tab
    is busy. Once full, an event is dropped and counted rather than
    blocking, and the drop count is reported so the UI can say its
    progress view is incomplete instead of silently missing steps.
    """

    def __init__(self, max_events: int = 500) -> None:
        self._events: list[ProgressEvent] = []
        self._lock = threading.Lock()
        self._max_events = max_events
        self._dropped = 0
        #: Set when a reader wants the producer to stop caring (client
        #: disconnected). Emits become no-ops; the graph is unaffected.
        self._closed = False

    def emit(self, event: ProgressEvent) -> None:
        """Records one event. Never raises, never blocks."""
        with self._lock:
            if self._closed:
                return
            if len(self._events) >= self._max_events:
                self._dropped += 1
                return
            self._events.append(event)

    def drain(self) -> tuple[ProgressEvent, ...]:
        """Returns and clears everything recorded so far."""
        with self._lock:
            events = tuple(self._events)
            self._events.clear()
            return events

    def close(self) -> None:
        """Stops accepting events. Idempotent."""
        with self._lock:
            self._closed = True

    @property
    def dropped(self) -> int:
        """How many events were discarded because the buffer was full."""
        with self._lock:
            return self._dropped


#: The per-run sink. A `ContextVar` rather than a module global for the
#: same reason `agent.harness.context` uses one for the execution envelope
#: and `security.audit_log` for the tenant id: this process serves
#: concurrent requests on a bounded thread pool, and a module global would
#: leak one caller's progress into another's stream. Bound on the worker
#: thread that runs the graph, never at the request boundary.
_current_sink: ContextVar[ProgressSink | None] = ContextVar("current_progress_sink", default=None)


def set_progress_sink(sink: ProgressSink | None) -> Token[ProgressSink | None]:
    """Binds `sink` for this context. Returns a token for `reset`."""
    return _current_sink.set(sink)


def reset_progress_sink(token: Token[ProgressSink | None]) -> None:
    """Unbinds, restoring whatever was bound before."""
    _current_sink.reset(token)


def current_sink() -> ProgressSink | None:
    """The sink bound to this context, or None."""
    return _current_sink.get()


def emit_progress(
    stage: str,
    *,
    attempt: int,
    duration_ms: float,
    outcome: ProgressOutcome,
    error_category: str | None = None,
) -> None:
    """Records a node transition, if anything is listening.

    A no-op when no sink is bound, which is the default for every caller
    that is not an SSE stream. Deliberately swallows nothing else: there
    is no try/except here because `ProgressSink.emit` cannot raise, and a
    silent except would hide a real bug in a path that runs on every node
    of every request.
    """
    sink = _current_sink.get()
    if sink is None:
        return
    sink.emit(
        ProgressEvent(
            stage=stage,
            attempt=attempt,
            duration_ms=duration_ms,
            outcome=outcome,
            error_category=error_category,
        )
    )
