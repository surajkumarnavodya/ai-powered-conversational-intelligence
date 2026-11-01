"""The current execution's envelope, as a context variable.

`agent/llm_client.py` has seven `client.chat(...)` call sites, reached
from a dozen node functions. Threading an envelope parameter through
all of them would touch far more code than the control it adds, and
every new call site would be one more place to forget it.

This is the same mechanism `security.audit_log`'s
`set_audit_tenant_id` already uses for exactly the same reason, bound
in exactly the same place (`agent.graph.run_agent`, on the worker
thread) for exactly the same reason: `api/main.py`'s `/ask` pool is a
raw `ThreadPoolExecutor`, which does **not** copy `contextvars` across
the submit boundary, so the value has to be bound on the worker side.

**Always reset in a `finally`.** A pool thread is reused by the next
request, and a leaked envelope would mean one request's budget and
deadline silently applied to another's work -- the cross-request
contamination the harness exists to prevent, introduced by the harness
itself. `run_agent` resets unconditionally.

Reading this is always optional: `get_current_envelope()` returning
`None` means "unguarded", which every direct caller and every test
that builds a bare state relies on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar, Token

from agent.harness.envelope import ExecutionEnvelope

logger = logging.getLogger(__name__)

_current_envelope: ContextVar[ExecutionEnvelope | None] = ContextVar(
    "harness_current_envelope", default=None
)


def set_current_envelope(envelope: ExecutionEnvelope | None) -> Token[ExecutionEnvelope | None]:
    """Binds `envelope` for this thread/task. Reset with the returned token."""
    return _current_envelope.set(envelope)


def reset_current_envelope(token: Token[ExecutionEnvelope | None]) -> None:
    """Restores whatever was bound before. Call in a `finally`."""
    _current_envelope.reset(token)


def get_current_envelope() -> ExecutionEnvelope | None:
    """This execution's envelope, or `None` when running unguarded."""
    return _current_envelope.get()


_envelope_observer: ContextVar[Callable[[ExecutionEnvelope], None] | None] = ContextVar(
    "harness_envelope_observer", default=None
)


def set_envelope_observer(
    observer: Callable[[ExecutionEnvelope], None] | None,
) -> Token[Callable[[ExecutionEnvelope], None] | None]:
    """Registers a callback to receive the envelope built on this thread.

    The problem this solves. An envelope is created *inside* the request
    handler, on the worker thread, from values that handler resolved --
    which is the only place it can be, since nothing above it knows the
    tenant, the roles or the resolved model yet. But a caller wrapping
    that handler sometimes needs a reference to it: `api/streaming.py`
    has to cancel the execution when the SSE client disconnects, and the
    disconnect is noticed on the event loop, not on the worker thread.

    Before this existed, `/ask/stream` noticed the disconnect and
    deliberately did nothing, because it had no handle on the run --
    measured 2026-10-28 (UAT-readiness Phase 6, `LOP-005`): pressing
    Stop in the dashboard aborted the HTTP request and the server kept
    generating and querying to completion.

    A callback rather than a second "last envelope" variable on purpose:
    there is no moment at which a single stored value is unambiguous
    under concurrency, whereas a callback is handed exactly the envelope
    its own call created.

    Reset with the returned token, in a `finally`, for the same reason
    `set_current_envelope` must be: a pool thread is reused.
    """
    return _envelope_observer.set(observer)


def reset_envelope_observer(
    token: Token[Callable[[ExecutionEnvelope], None] | None],
) -> None:
    """Restores whatever was bound before. Call in a `finally`."""
    _envelope_observer.reset(token)


def publish_envelope(envelope: ExecutionEnvelope | None) -> None:
    """Hands `envelope` to the registered observer, if there is one.

    A no-op when nothing is observing, which is every path but the SSE
    one. Never allowed to fail the request it is reporting: an observer
    that raises costs the caller its handle on the envelope, which is a
    lost cancellation, not a lost answer.
    """
    observer = _envelope_observer.get()
    if observer is None or envelope is None:
        return
    try:
        observer(envelope)
    except Exception:  # noqa: BLE001 - never breaks the execution it describes
        logger.warning("[harness] envelope observer raised; continuing", exc_info=True)


__all__ = [
    "get_current_envelope",
    "publish_envelope",
    "reset_current_envelope",
    "reset_envelope_observer",
    "set_current_envelope",
    "set_envelope_observer",
]
