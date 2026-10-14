"""The agent harness: the runtime control layer around agent execution.

**This is a control layer, not an orchestration framework.** LangGraph
remains the orchestrator, `AgentState` remains the state container, and
`agent/nodes.py` remains where a question becomes SQL. The harness
decides whether a step may run, how long it has, and what happens when
it does not finish -- nothing about *what* the step does.

Module map:

- `lifecycle.py` -- `ExecutionState` and the legal-transition table.
- `failures.py` -- `FailureClass`, what is retryable, and
  `HarnessTerminated`, the orderly-stop signal.
- `envelope.py` -- `ExecutionEnvelope`: one execution's identity,
  budgets, counters, deadline and cancellation flag.
- `control.py` -- `guard_node`, the boundary every node passes through.

### What this closes

`docs/HARNESS_MANAGEMENT_BASELINE.md` found the existing runtime already
had step bounds, admission control, per-tool governance, a circuit
breaker and per-operation timeouts -- all reused, none replaced. What it
lacked was a single execution identity and budget spanning a whole
request, and cancellation that reaches the work. That is what lives
here.

### Three properties worth stating

**The model cannot widen a limit.** `ExecutionIdentity` and
`ExecutionBudgets` are frozen dataclasses built from server-resolved
values. Tenant, user, roles, deadline and every budget are fixed before
the first node runs and are unreachable from a question, a retrieved
document, a tool result or a database cell. Authorization still comes
only from `agent.authz`; the harness bounds consumption, it does not
grant access.

**A child timeout never extends the parent deadline.**
`ExecutionEnvelope.timeout_for` clamps any requested timeout to the
time actually left. Without it, independent per-call timeouts compose
into no bound at all -- the gap `request_timeout_seconds` has today,
where it stops the *caller waiting* but not the work.

**Cancellation is cooperative, and the limit is admitted.** Python
cannot pre-empt a thread inside a C extension, so a cancelled execution
stops at its next node boundary rather than instantly. `db.execution`
can do better for SQL (it force-closes the connection) and does. The
harness bounds how long an abandoned execution keeps running to roughly
one node, which is the honest improvement available -- not an
instantaneous kill.

### Unguarded by design

A state without an envelope runs exactly as it did before. Every
existing direct caller of `run_agent`, and every test that builds a
bare state, keeps working untouched. The harness is opt-in at the
request boundary, which is what makes it additive rather than a rewrite.
"""

from agent.harness.breakers import (
    ProviderUnavailable,
    breaker_states,
    check_provider,
    get_provider_breaker,
    record_outcome,
    reset_breakers,
)
from agent.harness.control import (
    ENVELOPE_STATE_KEY,
    check_cancelled,
    envelope_from_state,
    guard_node,
    resolve_timeout,
    terminal_state_for,
)
from agent.harness.envelope import (
    UNLIMITED,
    ExecutionBudgets,
    ExecutionEnvelope,
    ExecutionIdentity,
    budgets_from_settings,
    create_envelope,
    derive_child_budgets,
    new_execution_id,
)
from agent.harness.failures import (
    FailureClass,
    HarnessFailure,
    HarnessTerminated,
    is_provider_health_signal,
    is_retryable,
)
from agent.harness.idempotency import IdempotencyStore, build_key
from agent.harness.lifecycle import (
    TERMINAL_STATES,
    ExecutionState,
    InvalidStateTransition,
    assert_transition,
    can_transition,
)
from agent.harness.limits import (
    SizedPayload,
    enforce_result_limit,
    enforce_text_limit,
    exceeds_limit,
    measure_bytes,
)
from agent.harness.retry import (
    DATABASE_RETRY_POLICY,
    DEFAULT_RETRY_POLICY,
    RetryDecision,
    RetryPolicy,
    decide_retry,
    never_retry,
)

__all__ = [
    "DATABASE_RETRY_POLICY",
    "DEFAULT_RETRY_POLICY",
    "IdempotencyStore",
    "ProviderUnavailable",
    "RetryDecision",
    "RetryPolicy",
    "SizedPayload",
    "breaker_states",
    "build_key",
    "check_provider",
    "decide_retry",
    "enforce_result_limit",
    "enforce_text_limit",
    "exceeds_limit",
    "get_provider_breaker",
    "measure_bytes",
    "never_retry",
    "record_outcome",
    "reset_breakers",
    "ENVELOPE_STATE_KEY",
    "TERMINAL_STATES",
    "UNLIMITED",
    "ExecutionBudgets",
    "ExecutionEnvelope",
    "ExecutionIdentity",
    "ExecutionState",
    "FailureClass",
    "HarnessFailure",
    "HarnessTerminated",
    "InvalidStateTransition",
    "assert_transition",
    "budgets_from_settings",
    "can_transition",
    "check_cancelled",
    "create_envelope",
    "derive_child_budgets",
    "envelope_from_state",
    "guard_node",
    "is_provider_health_signal",
    "is_retryable",
    "new_execution_id",
    "resolve_timeout",
    "terminal_state_for",
]
