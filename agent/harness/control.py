"""The control boundary: the one place a node is admitted or refused.

`guard_node` wraps a LangGraph node function so that every node in a
graph passes the same budget, deadline and cancellation checks without
any node containing a line of harness code. That separation is the
point -- `agent/nodes.py` stays about turning a question into SQL, and
nothing in it can forget to check a budget, because checking is not
its job.

**Why wrap at registration rather than decorate each node.**
`agent/graph.py` registers 19 nodes with `graph.add_node(name, fn)`. A
decorator on each would be 19 places to forget one, and a new node
would silently be unguarded. Wrapping at the single registration site
makes the guard the default and an unguarded node impossible to add by
accident.

**The envelope travels in the state, not in a closure.** The compiled
graph is a process-lifetime singleton shared by every request (see
`agent/graph.py`'s own note that `compile()`'s result is stateless), so
a closed-over envelope would be one request's budget applied to every
other request's execution -- the exact cross-request contamination the
harness exists to prevent. `AgentState["harness_envelope"]` is
per-request, so reading it from the state is both correct and the only
safe option.

A state with no envelope runs completely unguarded, by design: that is
what every existing direct caller of `run_agent` and every test that
builds a bare state does, and breaking them to add a harness would be a
rewrite rather than a control layer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from agent.harness.envelope import ExecutionEnvelope
from agent.harness.failures import FailureClass, HarnessTerminated
from agent.harness.lifecycle import ExecutionState
from agent.harness.telemetry import get_harness_telemetry

logger = logging.getLogger(__name__)

#: Where the envelope lives inside the graph state.
ENVELOPE_STATE_KEY = "harness_envelope"

NodeFn = Callable[..., dict[str, Any]]


def envelope_from_state(state: Any) -> ExecutionEnvelope | None:
    """The envelope carried by `state`, or `None` when unguarded.

    Tolerates a plain dict, a TypedDict, and an object without `.get`,
    because the node signature is `AgentState` in production but a bare
    dict in a great many tests.
    """
    if not isinstance(state, dict):
        return None
    candidate = state.get(ENVELOPE_STATE_KEY)
    return candidate if isinstance(candidate, ExecutionEnvelope) else None


def guard_node(name: str, fn: NodeFn) -> NodeFn:
    """Wraps one node so the harness admits it, counts it, and times it.

    The wrapper deliberately does **not** catch the node's own
    exceptions. An accuracy aid that fails open already handles its own
    errors internally (this codebase's standing posture), and swallowing
    anything else here would hide a real bug behind a harness message.
    The only exception it is interested in is `HarnessTerminated`, which
    it lets propagate to `run_agent` for orderly termination.
    """

    def _guarded(state: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        envelope = envelope_from_state(state)
        if envelope is None:
            return fn(state, *args, **kwargs)

        envelope.begin_step(name)
        if envelope.state is ExecutionState.CREATED:
            envelope.transition_to(ExecutionState.RUNNING)

        started = time.perf_counter()
        failed = False
        try:
            return fn(state, *args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            # Timing is recorded on the way out whatever happened, so a
            # node that always fails still shows up in the latency
            # breakdown -- a node missing from telemetry precisely when
            # it is broken is the opposite of useful. Never allowed to
            # fail the run it is measuring, matching this codebase's
            # standing posture for every other observability aid.
            try:
                get_harness_telemetry().record_node(
                    name, (time.perf_counter() - started) * 1000, failed=failed
                )
            except Exception:  # noqa: BLE001 - telemetry must not break execution
                logger.debug("[harness] node telemetry failed for %s", name, exc_info=True)

    _guarded.__name__ = getattr(fn, "__name__", name)
    _guarded.__qualname__ = getattr(fn, "__qualname__", name)
    _guarded.__doc__ = fn.__doc__
    # Deliberately NOT `__wrapped__`. `functools`-style unwrapping is
    # followed by `inspect.signature` and `typing.get_type_hints`, and
    # LangGraph calls the latter on every node it registers. Pointing it
    # at the wrapped function makes it resolve *that* function's string
    # annotations against *that* function's module globals -- which
    # breaks the moment a caller passes a locally-defined node (a test's
    # monkeypatched fake, whose module never imported `Any`), with a
    # `ForwardRef` evaluation error far from its cause. A private name
    # keeps the escape hatch without joining that protocol.
    _guarded.__harness_node__ = fn  # type: ignore[attr-defined]
    return _guarded


def check_cancelled(envelope: ExecutionEnvelope | None, where: str) -> None:
    """A mid-node cancellation checkpoint, for long-running work.

    `guard_node` checks at node edges, which bounds how long an
    abandoned execution keeps running to "one node". A node that makes
    several model or tool calls can call this between them to tighten
    that further. No-op when unguarded.
    """
    if envelope is None or not envelope.is_cancelled:
        return
    raise envelope._terminate(  # noqa: SLF001 - same package, deliberate
        ExecutionState.CANCELLED,
        FailureClass.CANCELLED,
        "The request was cancelled.",
        where,
    )


def resolve_timeout(envelope: ExecutionEnvelope | None, requested_seconds: float) -> float:
    """The timeout a child call may use, clamped to the parent deadline.

    The shape every call site wants: pass the configured timeout, get
    back one that cannot outlive the execution. Returns `requested_seconds`
    unchanged when unguarded, so an existing call site keeps its exact
    current behaviour until an envelope is actually present.
    """
    if envelope is None:
        return requested_seconds
    return envelope.timeout_for(requested_seconds)


def terminal_state_for(envelope: ExecutionEnvelope, exc: HarnessTerminated) -> dict[str, Any]:
    """The graph-state patch describing an orderly harness termination.

    Shaped to merge into `AgentState` so a terminated run returns the
    same response contract a normally-failed run does -- `status`,
    `failure_explanation` -- rather than surfacing as a 500. The
    machine-readable reason stays on the envelope, where telemetry reads
    it, instead of widening the public response model.
    """
    logger.warning(
        "[harness] execution terminated: reason=%s node=%s detail=%s",
        exc.failure.failure_class.value,
        exc.failure.node,
        exc.failure.detail,
    )
    return {
        "status": envelope.state.to_agent_status(),
        "failure_explanation": exc.failure.safe_message,
        "harness_termination_reason": exc.failure.failure_class.value,
    }


__all__ = [
    "ENVELOPE_STATE_KEY",
    "check_cancelled",
    "envelope_from_state",
    "guard_node",
    "resolve_timeout",
    "terminal_state_for",
]
