"""The execution lifecycle: which states exist, and which transitions are legal.

A loose status string is how a runtime ends up in a state nobody
designed. `AgentState["status"]` has four values
(`pending`/`succeeded`/`failed`/`needs_clarification`) and no notion of
"cancelled", "timed out", or "retrying" -- so three genuinely different
outcomes all report `failed` and a reader cannot tell a provider outage
from a user hanging up.

This module is the harness's own vocabulary, deliberately kept separate
from `AgentState["status"]`: that field is part of the response contract
the frontend reads and must not change meaning. The two are related by
`ExecutionState.to_agent_status()`, which is the only place the mapping
lives.

**Transitions are a whitelist, not a blacklist.** `_ALLOWED` names every
legal edge; anything else raises `InvalidStateTransition`. A state
machine that merely *warns* on a bad transition is a state machine that
drifts, and the bug it is meant to catch -- a node marking an execution
`COMPLETED` while a tool is still in flight -- is exactly the kind that
only shows up under load.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionState(StrEnum):
    """One execution's lifecycle position. Terminal states are final."""

    CREATED = "created"
    VALIDATING = "validating"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RETRYING = "retrying"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATES

    def to_agent_status(self) -> str:
        """This state as the `AgentState["status"]` the response contract uses.

        The frontend and `api/schemas.py` understand four values. Three
        distinct harness outcomes (failed, cancelled, timed out) all map
        to `"failed"` there -- the distinction is not lost, it is carried
        separately in `ExecutionEnvelope.termination_reason`, which is
        what telemetry reads. Widening the response contract instead
        would be a breaking change for no caller-visible benefit.
        """
        if self is ExecutionState.COMPLETED:
            return "succeeded"
        if self.is_terminal:
            return "failed"
        return "pending"


#: States no transition may leave.
TERMINAL_STATES: frozenset[ExecutionState] = frozenset(
    {
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
    }
)

#: Every legal edge. Read as "from -> the set it may enter".
#:
#: Three rules shape this table:
#:   1. Any non-terminal state may be cancelled or time out. Those are
#:      external events, not something the graph chooses, so they are
#:      reachable from everywhere rather than from a privileged few.
#:   2. Terminal states have no outgoing edges at all.
#:   3. `RETRYING` and `RECOVERING` must return to `RUNNING` rather than
#:      jumping straight to a result, so a retried execution is counted
#:      and bounded exactly like the first attempt.
_ALLOWED: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.CREATED: frozenset(
        {
            ExecutionState.VALIDATING,
            ExecutionState.RUNNING,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.VALIDATING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.RUNNING: frozenset(
        {
            ExecutionState.WAITING_FOR_TOOL,
            ExecutionState.WAITING_FOR_APPROVAL,
            ExecutionState.RETRYING,
            ExecutionState.RECOVERING,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.WAITING_FOR_TOOL: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.RETRYING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.WAITING_FOR_APPROVAL: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.RETRYING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.RECOVERING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.COMPLETED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.TIMED_OUT: frozenset(),
}


class InvalidStateTransition(RuntimeError):
    """An illegal lifecycle edge was attempted. Always a harness bug, never user input."""

    def __init__(self, source: ExecutionState, target: ExecutionState) -> None:
        self.source = source
        self.target = target
        super().__init__(f"Illegal execution state transition: {source.value} -> {target.value}.")


def can_transition(source: ExecutionState, target: ExecutionState) -> bool:
    """Whether `source -> target` is legal. Pure; safe to call for a check."""
    return target in _ALLOWED[source]


def assert_transition(source: ExecutionState, target: ExecutionState) -> None:
    """Raises `InvalidStateTransition` unless `source -> target` is legal."""
    if not can_transition(source, target):
        raise InvalidStateTransition(source, target)


__all__ = [
    "TERMINAL_STATES",
    "ExecutionState",
    "InvalidStateTransition",
    "assert_transition",
    "can_transition",
]
