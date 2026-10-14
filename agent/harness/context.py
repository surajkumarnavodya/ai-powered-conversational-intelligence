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

from contextvars import ContextVar, Token

from agent.harness.envelope import ExecutionEnvelope

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


__all__ = [
    "get_current_envelope",
    "reset_current_envelope",
    "set_current_envelope",
]
