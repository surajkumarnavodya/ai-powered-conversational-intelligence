"""How a harness-level failure is classified, and whether retrying it can help.

`agent/error_classification.py` already classifies *SQL* errors so a
retry prompt can be targeted ("you referenced a column that does not
exist"). This is its sibling one layer up: it classifies *runtime*
failures -- model timeout, provider outage, budget exhausted, tool
malformed -- which is what a retry policy and telemetry both need and
neither currently has.

**Retryability is a property of the class, not a judgement call at the
call site.** Scattering `if isinstance(exc, X): retry` across nodes is
how a codebase ends up retrying an authorization failure. `is_retryable`
is declared once, here, and the two things that must never be retried --
anything the caller is not allowed to do, and anything that will fail
identically next time -- are non-retryable by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureClass(StrEnum):
    """What went wrong, at the granularity a retry policy cares about."""

    #: The model did not answer in time. Usually transient.
    MODEL_TIMEOUT = "model_timeout"
    #: The provider is unreachable or returned 5xx. Transient, and the
    #: signal a circuit breaker trips on.
    MODEL_UNAVAILABLE = "model_unavailable"
    #: The model answered, but not in a shape we can use.
    MODEL_MALFORMED_OUTPUT = "model_malformed_output"
    #: A tool exceeded its own timeout.
    TOOL_TIMEOUT = "tool_timeout"
    #: A tool raised, or returned something unusable.
    TOOL_FAILED = "tool_failed"
    #: A tool's result was larger than the harness allows.
    TOOL_RESULT_TOO_LARGE = "tool_result_too_large"
    #: The database did not answer in time.
    DATABASE_TIMEOUT = "database_timeout"
    #: The database refused the connection, or dropped it.
    DATABASE_UNAVAILABLE = "database_unavailable"
    #: Deterministic SQL error -- syntax, unknown column. Retrying the
    #: *same* statement cannot help; the graph's own self-correction
    #: loop generates a *different* statement, which is a different
    #: thing and is budgeted separately.
    SQL_DETERMINISTIC = "sql_deterministic"
    #: The caller may not do this. Never retried.
    AUTHORIZATION_DENIED = "authorization_denied"
    #: Input or policy rejected the request. Never retried.
    VALIDATION_REJECTED = "validation_rejected"
    #: A harness budget ran out: steps, retries, tool calls, tokens.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: The execution deadline passed.
    DEADLINE_EXCEEDED = "deadline_exceeded"
    #: The caller went away, or the operator cancelled.
    CANCELLED = "cancelled"
    #: An illegal lifecycle transition. Always a harness bug.
    INVALID_STATE = "invalid_state"
    #: Anything unrecognized. Not retried -- see `is_retryable`.
    UNKNOWN = "unknown"


#: Classes a bounded retry may help with. Everything absent from this
#: set is non-retryable, which makes the default for a *new* class
#: "do not retry" rather than "retry". That direction matters: an
#: unrecognized failure retried three times is a retry storm, while an
#: unrecognized failure surfaced once is a bug report.
_RETRYABLE: frozenset[FailureClass] = frozenset(
    {
        FailureClass.MODEL_TIMEOUT,
        FailureClass.MODEL_UNAVAILABLE,
        FailureClass.MODEL_MALFORMED_OUTPUT,
        FailureClass.TOOL_TIMEOUT,
        FailureClass.TOOL_FAILED,
        FailureClass.DATABASE_TIMEOUT,
        FailureClass.DATABASE_UNAVAILABLE,
    }
)

#: Classes that mean "this provider is sick", as opposed to "this one
#: call went wrong". Only these advance a circuit breaker toward open --
#: a malformed model answer says nothing about the provider's health,
#: and counting it would let one awkward question disable a model for
#: everybody. (The same reasoning `model_routing.models` already
#: applies to its own health signal.)
_PROVIDER_HEALTH_SIGNALS: frozenset[FailureClass] = frozenset(
    {
        FailureClass.MODEL_TIMEOUT,
        FailureClass.MODEL_UNAVAILABLE,
    }
)


def is_retryable(failure: FailureClass) -> bool:
    """Whether a bounded retry of this failure class can plausibly succeed."""
    return failure in _RETRYABLE


def is_provider_health_signal(failure: FailureClass) -> bool:
    """Whether this failure should count toward opening a circuit breaker."""
    return failure in _PROVIDER_HEALTH_SIGNALS


@dataclass(frozen=True)
class HarnessFailure:
    """One classified failure. `detail` is for protected logs, never a response.

    `safe_message` follows `agent.exceptions.AgentError`'s established
    split: a short, fixed, user-facing string that never echoes internal
    state, alongside the full detail that only reaches a log.
    """

    failure_class: FailureClass
    safe_message: str
    detail: str = ""
    node: str | None = None

    @property
    def retryable(self) -> bool:
        return is_retryable(self.failure_class)


class HarnessTerminated(RuntimeError):
    """Deterministic termination demanded by the harness.

    Raised from the control boundary when an execution must stop:
    a budget ran out, the deadline passed, or the caller cancelled.
    `agent.graph.run_agent` catches this and returns a well-formed
    terminal state -- it must never reach a route handler as an
    unhandled exception, because that would turn an orderly, expected
    stop into a 500. The `GraphRecursionError` this codebase already hit
    once (see `agent/graph.py`'s `recursion_limit` comment) is exactly
    that failure mode, and this is the general form of the fix.
    """

    def __init__(self, failure: HarnessFailure) -> None:
        self.failure = failure
        super().__init__(failure.safe_message)


__all__ = [
    "FailureClass",
    "HarnessFailure",
    "HarnessTerminated",
    "is_provider_health_signal",
    "is_retryable",
]
