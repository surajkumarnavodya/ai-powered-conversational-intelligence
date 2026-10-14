"""The execution envelope: one server-owned record of what an execution may do.

Every control the harness enforces reads from here, and **nothing a
model emits can write to it**. That is the whole point of the split
below:

- `ExecutionIdentity` and `ExecutionBudgets` are **frozen**. Tenant,
  user, roles, deadline, every budget -- fixed at construction from
  values the server resolved (an authenticated account row, a
  `Settings` field), never from a question, a tool result, a retrieved
  document or a database cell. A frozen dataclass makes
  "the LLM must never control tenant_id" a property the type system
  holds rather than a rule a reviewer has to notice.
- `ExecutionEnvelope` is mutable, because counters and lifecycle state
  genuinely change -- but only through methods that enforce the budget
  and the transition table.

**Deadlines are monotonic.** `time.monotonic()`, never wall-clock: an
NTP step or a DST change must not extend or truncate a running
execution's budget.

**Cancellation is cooperative, and this module is honest about it.**
Python cannot pre-emptively kill a thread blocked inside a C extension,
so `cancel()` sets a `threading.Event` that the control boundary checks
at each node edge. An execution cancelled mid-LLM-call stops at the
*next* boundary, not instantly. The one place that can do better
already does -- `db.execution._execute_with_timeout` force-closes the
connection -- and that asymmetry is real, not something to paper over.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from agent.harness.failures import FailureClass, HarnessFailure, HarnessTerminated
from agent.harness.lifecycle import ExecutionState, assert_transition

#: Returned by `remaining_seconds()` when no deadline is set.
UNLIMITED = float("inf")


@dataclass(frozen=True)
class ExecutionIdentity:
    """Who and what this execution is. Frozen: resolved server-side, once.

    `tenant_id`, `user_id` and `caller_roles` are the three fields a
    prompt-injection attempt would most like to change. They are set
    from `security.tenancy`/`api.auth` output and are unreachable from
    model output by construction.
    """

    execution_id: str
    request_id: str
    graph_name: str
    graph_version: str = "1"
    user_id: str | None = None
    tenant_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    caller_roles: tuple[str, ...] = ()
    model: str | None = None
    provider: str = "ollama"


@dataclass(frozen=True)
class ExecutionBudgets:
    """Everything this execution may consume. Frozen: server-owned.

    `None` means "this layer imposes no limit", which is not the same as
    zero. A zero budget would make the harness refuse every execution;
    the absence of a budget leaves whatever lower-level cap already
    applies (the global row cap, the per-call Ollama timeout) in force.

    These are deliberately *not* read from the graph state. A node that
    could raise its own ceiling is not a bounded node.
    """

    max_steps: int = 60
    max_retries: int = 3
    max_llm_calls: int = 40
    max_tool_calls: int = 30
    max_duration_seconds: float | None = 600.0
    max_tokens: int | None = None
    max_cost_units: float | None = None
    max_result_bytes: int | None = 8_000_000
    max_context_chars: int | None = 400_000


@dataclass
class ExecutionEnvelope:
    """One execution's live control record.

    Created by the harness at the request boundary, threaded through the
    graph in `AgentState["harness_envelope"]`, and read by the control
    boundary before each node. Mutable only through the methods below.
    """

    identity: ExecutionIdentity
    budgets: ExecutionBudgets

    state: ExecutionState = ExecutionState.CREATED
    step_count: int = 0
    retry_count: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0
    token_estimate: int = 0
    cost_estimate: float = 0.0

    current_node: str | None = None
    termination_reason: str | None = None
    failure: HarnessFailure | None = None

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None

    #: Monotonic instant the execution must stop by. `None` = no deadline.
    _deadline: float | None = field(default=None, repr=False)
    _started_monotonic: float = field(default_factory=time.monotonic, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    #: Node name -> times entered. Powers the duplicate-execution signal
    #: without keeping any of the node's data.
    _node_visits: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self._deadline is None and self.budgets.max_duration_seconds is not None:
            self._deadline = self._started_monotonic + self.budgets.max_duration_seconds

    # ---------------------------------------------------------------- time

    def remaining_seconds(self) -> float:
        """Seconds left before the deadline, floored at 0.0. `UNLIMITED` if none."""
        if self._deadline is None:
            return UNLIMITED
        return max(0.0, self._deadline - time.monotonic())

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started_monotonic

    def is_expired(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def timeout_for(self, requested_seconds: float) -> float:
        """The timeout a child operation may actually use.

        **A child timeout never extends the parent deadline.** Asking for
        300 seconds with 12 left returns 12. This is the single rule that
        turns a pile of independent per-call timeouts into a real
        end-to-end bound: without it, one execution can chain many
        full-length LLM timeouts and outlive any request ceiling.

        Returns at least a small positive value so a caller never passes
        `0` to a library that reads it as "no timeout" -- a rounding
        detail that would otherwise invert the control completely.
        """
        remaining = self.remaining_seconds()
        if remaining is UNLIMITED or remaining == UNLIMITED:
            return requested_seconds
        return max(0.001, min(requested_seconds, remaining))

    # --------------------------------------------------------- cancellation

    def cancel(self, reason: str = "cancelled_by_caller") -> None:
        """Requests cancellation. Idempotent, and safe from any thread.

        Does not itself transition the lifecycle: the execution is still
        running until it reaches a checkpoint and notices. `is_cancelled`
        is what the control boundary reads.
        """
        with self._lock:
            if self.termination_reason is None:
                self.termination_reason = reason
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ------------------------------------------------------------ lifecycle

    def transition_to(self, target: ExecutionState) -> None:
        """Moves to `target`, raising `InvalidStateTransition` if illegal."""
        with self._lock:
            assert_transition(self.state, target)
            self.state = target
            if target.is_terminal and self.ended_at is None:
                self.ended_at = datetime.now(UTC)

    def finish(self, target: ExecutionState, reason: str | None = None) -> None:
        """Moves to a terminal state, tolerating an already-terminal envelope.

        Terminal-state races are real: a deadline can expire on the
        control boundary at the same moment the final node succeeds.
        Rather than raise -- which would turn a completed execution into
        a 500 -- the first terminal state wins and the second is ignored.
        """
        with self._lock:
            if self.state.is_terminal:
                return
            self.transition_to(target)
            if reason is not None and self.termination_reason is None:
                self.termination_reason = reason

    # -------------------------------------------------------------- budgets

    def begin_step(self, node: str) -> None:
        """The control boundary. Called once before each node runs.

        Checks, in this order: cancellation, deadline, step budget. The
        order is deliberate -- a cancelled execution should report
        `CANCELLED`, not `BUDGET_EXHAUSTED`, even when both are true,
        because the first is what an operator needs to see.

        Raises:
            HarnessTerminated: the execution must stop now.
        """
        with self._lock:
            if self.is_cancelled:
                raise self._terminate(
                    ExecutionState.CANCELLED,
                    FailureClass.CANCELLED,
                    "The request was cancelled before this step ran.",
                    node,
                )
            if self.is_expired():
                raise self._terminate(
                    ExecutionState.TIMED_OUT,
                    FailureClass.DEADLINE_EXCEEDED,
                    "The request exceeded its time budget and was stopped.",
                    node,
                )
            if self.step_count >= self.budgets.max_steps:
                raise self._terminate(
                    ExecutionState.FAILED,
                    FailureClass.BUDGET_EXHAUSTED,
                    "The request reached its maximum number of steps and was stopped.",
                    node,
                )
            self.step_count += 1
            self.current_node = node
            self._node_visits[node] = self._node_visits.get(node, 0) + 1

    def record_llm_call(self, tokens: int = 0, cost_units: float = 0.0) -> None:
        """Counts one model call against the per-execution budget."""
        with self._lock:
            if self.llm_call_count >= self.budgets.max_llm_calls:
                raise self._terminate(
                    ExecutionState.FAILED,
                    FailureClass.BUDGET_EXHAUSTED,
                    "The request reached its maximum number of model calls.",
                    self.current_node,
                )
            self.llm_call_count += 1
            self.token_estimate += max(0, tokens)
            self.cost_estimate += max(0.0, cost_units)
            if (
                self.budgets.max_tokens is not None
                and self.token_estimate > self.budgets.max_tokens
            ):
                raise self._terminate(
                    ExecutionState.FAILED,
                    FailureClass.BUDGET_EXHAUSTED,
                    "The request reached its token budget.",
                    self.current_node,
                )

    def record_tool_call(self) -> None:
        """Counts one tool invocation against the per-execution budget."""
        with self._lock:
            if self.tool_call_count >= self.budgets.max_tool_calls:
                raise self._terminate(
                    ExecutionState.FAILED,
                    FailureClass.BUDGET_EXHAUSTED,
                    "The request reached its maximum number of tool calls.",
                    self.current_node,
                )
            self.tool_call_count += 1

    def record_retry(self) -> None:
        """Counts one harness-level retry. Does not itself stop the run.

        Exceeding the retry budget is reported by `retry_budget_exhausted`
        rather than raised, because the caller is usually a retry loop
        that wants to fall through to its own handling rather than
        unwind.
        """
        with self._lock:
            self.retry_count += 1

    @property
    def retry_budget_exhausted(self) -> bool:
        return self.retry_count >= self.budgets.max_retries

    def visits(self, node: str) -> int:
        """How many times `node` has been entered in this execution."""
        return self._node_visits.get(node, 0)

    # --------------------------------------------------------------- internals

    def _terminate(
        self,
        target: ExecutionState,
        failure_class: FailureClass,
        safe_message: str,
        node: str | None,
    ) -> HarnessTerminated:
        """Records a terminal failure and builds the exception to raise.

        Returns rather than raises so the call sites above read as
        `raise self._terminate(...)` -- which keeps the raise visible at
        the point of decision instead of hidden one frame down.
        """
        failure = HarnessFailure(
            failure_class=failure_class,
            safe_message=safe_message,
            detail=(
                f"execution_id={self.identity.execution_id} node={node} "
                f"step={self.step_count}/{self.budgets.max_steps} "
                f"llm_calls={self.llm_call_count}/{self.budgets.max_llm_calls} "
                f"tool_calls={self.tool_call_count}/{self.budgets.max_tool_calls} "
                f"elapsed={self.elapsed_seconds():.1f}s"
            ),
            node=node,
        )
        self.failure = failure
        if self.termination_reason is None:
            self.termination_reason = failure_class.value
        if not self.state.is_terminal:
            # `finish` rather than `transition_to`: the deadline can fire
            # from a state the strict table would refuse, and refusing a
            # *stop* is never the safe direction.
            self.finish(target)
        return HarnessTerminated(failure)

    # -------------------------------------------------------------- telemetry

    def snapshot(self) -> dict[str, Any]:
        """A log/metric-safe view. Identifiers and counts only -- never content.

        Deliberately excludes the question, the SQL, any row, any tool
        result and the caller's roles: §27 of the governing prompt and
        CLAUDE.md §5.6 both require telemetry to count decisions, not
        carry data. `tenant_id` is included because every existing audit
        event already carries it and isolation diagnosis is impossible
        without it.
        """
        return {
            "execution_id": self.identity.execution_id,
            "request_id": self.identity.request_id,
            "correlation_id": self.identity.correlation_id,
            "conversation_id": self.identity.conversation_id,
            "tenant_id": self.identity.tenant_id,
            "graph": self.identity.graph_name,
            "graph_version": self.identity.graph_version,
            "provider": self.identity.provider,
            "model": self.identity.model,
            "state": self.state.value,
            "current_node": self.current_node,
            "step_count": self.step_count,
            "max_steps": self.budgets.max_steps,
            "retry_count": self.retry_count,
            "llm_call_count": self.llm_call_count,
            "tool_call_count": self.tool_call_count,
            "token_estimate": self.token_estimate,
            "cost_estimate": round(self.cost_estimate, 4),
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
            "remaining_seconds": (
                None if self._deadline is None else round(self.remaining_seconds(), 3)
            ),
            "cancelled": self.is_cancelled,
            "termination_reason": self.termination_reason,
            "failure_class": (self.failure.failure_class.value if self.failure else None),
        }


def new_execution_id() -> str:
    return uuid.uuid4().hex


def create_envelope(
    *,
    graph_name: str,
    request_id: str | None = None,
    budgets: ExecutionBudgets | None = None,
    **identity_fields: Any,
) -> ExecutionEnvelope:
    """Builds an envelope with a fresh `execution_id`.

    The only supported way to make one. Every identity field is a
    keyword, so adding `tenant_id` to a call site is explicit rather
    than a positional argument that could silently take the wrong value.
    """
    identity = ExecutionIdentity(
        execution_id=new_execution_id(),
        request_id=request_id or new_execution_id(),
        graph_name=graph_name,
        **identity_fields,
    )
    return ExecutionEnvelope(identity=identity, budgets=budgets or ExecutionBudgets())


def derive_child_budgets(
    parent: ExecutionEnvelope, *, max_steps: int | None = None
) -> ExecutionBudgets:
    """Budgets for a subgraph, never looser than the parent's.

    A child may lower a limit; it may not raise one. `max_duration_seconds`
    is set from whatever the parent has *left*, which is what stops a
    subgraph from outliving the request that started it.
    """
    remaining = parent.remaining_seconds()
    return replace(
        parent.budgets,
        max_steps=min(max_steps or parent.budgets.max_steps, parent.budgets.max_steps),
        max_duration_seconds=(None if remaining == UNLIMITED else remaining),
    )


__all__ = [
    "UNLIMITED",
    "ExecutionBudgets",
    "budgets_from_settings",
    "ExecutionEnvelope",
    "ExecutionIdentity",
    "create_envelope",
    "derive_child_budgets",
    "new_execution_id",
]


def budgets_from_settings(settings: Any) -> ExecutionBudgets:
    """The operator-configured budgets for one execution.

    `max_duration_seconds` comes from `request_timeout_seconds`, which
    is deliberate: that setting's name has always promised a request
    ceiling, while all it actually did was stop the *caller* waiting
    (its own docstring says so). Wiring it here is what finally makes
    the name true -- the same number now also bounds the work.

    A `harness_max_tokens` of 0 means "no token budget", not "zero
    tokens". See that field's own comment for why it is off by default.
    """
    return ExecutionBudgets(
        max_steps=settings.harness_max_steps,
        max_retries=settings.max_retries,
        max_llm_calls=settings.harness_max_llm_calls,
        max_tool_calls=settings.harness_max_tool_calls,
        max_duration_seconds=float(settings.request_timeout_seconds),
        max_tokens=settings.harness_max_tokens or None,
        max_result_bytes=settings.harness_max_result_bytes,
        max_context_chars=settings.harness_max_context_chars,
    )
