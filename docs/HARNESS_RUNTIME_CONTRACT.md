# Agent Harness — Runtime Contract

What the harness guarantees about an execution, and how. Prompt 02's
deliverable, built on the gaps `docs/HARNESS_MANAGEMENT_BASELINE.md`
found.

**One sentence:** every `/ask` execution now carries a server-owned
envelope that gives it an identity, a deadline and a set of budgets
that nothing inside the graph can widen.

---

## 1. What was added, and what was reused

| Added | Reused unchanged |
|---|---|
| `agent/harness/envelope.py` — identity, budgets, counters, deadline, cancellation | LangGraph as the orchestrator |
| `agent/harness/lifecycle.py` — 11 states, whitelisted transitions | `AgentState` as the state container |
| `agent/harness/failures.py` — failure taxonomy, retryability | `agent/rate_limit.py` admission control |
| `agent/harness/control.py` — `guard_node`, the boundary | `agent/tools/registry.py` tool governance |
| 8 `Settings` fields + a master switch | `db/execution.py` forced-abort timeout |
| | `agent/sql_validator.py`, `authz`, tenancy, governance |

No second orchestration framework, no second state container, no
checkpointer. `docs/HARNESS_MANAGEMENT_BASELINE.md` §4.3 listed what
must not be built; none of it was.

---

## 2. The execution envelope

```python
ExecutionEnvelope
├── identity: ExecutionIdentity      # frozen
│     execution_id · request_id · correlation_id · conversation_id
│     session_id · user_id · tenant_id · caller_roles
│     graph_name · graph_version · provider · model
├── budgets: ExecutionBudgets        # frozen
│     max_steps · max_retries · max_llm_calls · max_tool_calls
│     max_duration_seconds · max_tokens · max_cost_units
│     max_result_bytes · max_context_chars
└── mutable, method-guarded
      state · step_count · retry_count · llm_call_count · tool_call_count
      token_estimate · cost_estimate · current_node
      termination_reason · failure · started_at · ended_at
      _deadline (monotonic) · _cancelled (threading.Event) · _node_visits
```

### Why identity and budgets are frozen dataclasses

This is the enforcement mechanism for the prompt's hard rule — *the LLM
must never control tenant_id, user_id, authorization scope, budgets,
retry budget, security policy, timeout policy, credentials or tool
permissions.*

Those values are set once, at the request boundary in
`api/main.py::ask`, from what the server already resolved: an
authenticated account row, `security.tenancy`, `agent.authz`, and
`Settings`. A frozen dataclass makes "a node cannot widen its own
budget" a property of the type rather than a rule a reviewer has to
notice. And because the budgets are **not in the graph state**, there
is no key a node could return to change one —
`test_a_node_cannot_widen_its_own_step_budget` proves exactly that by
having a node try.

Authorization is unchanged: the harness bounds *consumption*, it never
grants *access*. `agent/authz.py` remains the only permission decision
point.

---

## 3. Lifecycle

```
CREATED ──► VALIDATING ──► RUNNING ──┬──► COMPLETED
                             ▲       ├──► FAILED
                             │       ├──► CANCELLED
    WAITING_FOR_TOOL ────────┤       └──► TIMED_OUT
    WAITING_FOR_APPROVAL ────┤
    RETRYING ────────────────┤
    RECOVERING ──────────────┘
```

Three rules shape the transition table:

1. **Any non-terminal state may be cancelled or time out.** Those are
   external events, not something the graph elects, so they are
   reachable from everywhere.
2. **Terminal states have no outgoing edges.** Asserted exhaustively
   over the full cross-product in
   `test_a_terminal_state_has_no_outgoing_edges`.
3. **`RETRYING`/`RECOVERING` must return to `RUNNING`**, never jump to
   a result, so a retried attempt is counted and bounded like the first.

`ExecutionState.to_agent_status()` is the **only** place the harness
vocabulary maps onto `AgentState["status"]`. That field is part of the
response contract the frontend reads, so it keeps its four values;
`failed`, `cancelled` and `timed_out` all surface there as `"failed"`
while the real reason travels separately in
`envelope.termination_reason`, which telemetry reads. Widening the
response model would have been a breaking change for no caller benefit.

**Terminal races are tolerated, not raised.** A deadline can expire at
the same instant the final node succeeds. `finish()` lets the first
terminal state win rather than raising — turning a completed execution
into a 500 over a race would be the worse failure.

---

## 4. The control boundary

`guard_node(name, fn)` wraps a node. `agent/graph.py` applies it at the
**single registration site**, so all 18 nodes are guarded and a
19th cannot be added unguarded by accident:

```python
graph.add_node("generate_sql", guard_node("generate_sql", generate_sql_node))
```

Checks run before each node, in this order:

1. **Cancelled?** → `CANCELLED`
2. **Deadline passed?** → `TIMED_OUT`
3. **Step budget spent?** → `FAILED` / `BUDGET_EXHAUSTED`

The order matters: when an execution is both cancelled and over its
deadline, the operator needs to see *cancelled*.

### Why the envelope travels in the state

The compiled graph is a **process-lifetime singleton** shared by every
request (`agent/graph.py` says so itself). A closed-over envelope would
apply one request's budget to every other request — precisely the
cross-request contamination the harness exists to prevent.
`AgentState["harness_envelope"]` is per-request, so reading it from the
state is the only safe option.

### Unguarded is a supported mode

A state with no envelope runs exactly as before. Every existing direct
caller of `run_agent` (`eval/runner.py`, `scripts/`, the analyst loop,
the supervisor) and every test that builds a bare state keeps working
untouched. That is what makes this a control layer rather than a
rewrite — and `ENABLE_AGENT_HARNESS=false` restores it globally.

The wrapper deliberately **does not catch the node's own exceptions**.
Swallowing them would hide a real bug behind a harness message.

---

## 5. Deadlines: the rule that makes the bound real

`envelope.timeout_for(requested)` returns `min(requested, remaining)`,
floored just above zero.

Without this, the per-operation timeouts the baseline catalogued
compose into no bound at all: a graph run making eight model calls at
`ollama_request_timeout_seconds=300` each can legitimately run 40
minutes under a `request_timeout_seconds` of 600. With it, the eighth
call gets whatever is left of the 600 and no more.

The floor matters more than it looks: `0` means *no timeout* to several
libraries, so returning it on an expired deadline would invert the
control completely. `test_a_child_timeout_is_never_zero_or_negative`
pins it.

Deadlines are **monotonic** (`time.monotonic`), never wall-clock — an
NTP step must not extend or truncate a running execution.

---

## 6. Cancellation, honestly

`ExecutionEnvelope.cancel()` sets a `threading.Event`. The control
boundary checks it at every node edge; `check_cancelled()` is available
for a mid-node checkpoint in a node that makes several calls.

**What this fixes.** `api/main.py::_run_ask_with_timeout`'s own
docstring recorded the gap: on timeout the caller stopped waiting but
the worker kept running — still holding a pool thread, still able to
finish an Ollama call and execute SQL *after the user already got a
timeout response*. `/ask` now calls
`harness_envelope.cancel("request_deadline_exceeded")` in that handler.

**What this does not fix.** Python cannot pre-empt a thread blocked
inside a C extension. A cancelled execution stops at its **next node
boundary**, not instantly — so wasted work is bounded to roughly one
node instead of a whole graph run. That is the honest improvement
available, and it is a real one; it is not an instantaneous kill.

The one layer that does better already does: `db/execution.py`
force-closes the connection from the calling thread. That asymmetry is
deliberate and documented rather than papered over.

---

## 7. Budgets

| Budget | Setting | Default | Bounds |
|---|---|---|---|
| Steps | `HARNESS_MAX_STEPS` | 120 | Node executions per request |
| Retries | `MAX_RETRIES` (existing) | 3 | Harness-level retries |
| Model calls | `HARNESS_MAX_LLM_CALLS` | 40 | Model invocations per request |
| Tool calls | `HARNESS_MAX_TOOL_CALLS` | 30 | Tool invocations per request |
| Duration | `REQUEST_TIMEOUT_SECONDS` (existing) | 600 | Wall-clock per request |
| Tokens | `HARNESS_MAX_TOKENS` | 0 (off) | Estimated tokens per request |
| Result bytes | `HARNESS_MAX_RESULT_BYTES` | 8 MB | One carried result |
| Context chars | `HARNESS_MAX_CONTEXT_CHARS` | 400 000 | Assembled context |

Two defaults deserve their reasoning:

- **`harness_max_steps=120` sits *above* the graph's own
  `recursion_limit` (20 + max_retries×10).** LangGraph's limit is the
  hard structural stop; the harness budget is the operator's. A harness
  budget that fired first would make the structural stop unreachable
  and untestable.
- **The token budget is off by default.** Ollama's counts are captured
  opportunistically from log lines
  (`observability/llm_timing_capture.py`), not guaranteed. Enforcing a
  budget on a best-effort number would fail executions for a
  measurement gap. The counter runs regardless, so an operator can
  observe real usage before switching enforcement on.

`derive_child_budgets()` gives a subgraph budgets that are **never
looser** than its parent's, with `max_duration_seconds` set from what
the parent has *left*.

---

## 8. Failure taxonomy

16 `FailureClass` values. Retryability is declared once, on the class,
rather than decided at each call site — scattering `if isinstance(exc,
X): retry` is how a codebase ends up retrying an authorization failure.

**Retryable:** model timeout/unavailable/malformed, tool
timeout/failed, database timeout/unavailable.

**Never retried:** authorization denied, validation rejected,
deterministic SQL error, budget exhausted, deadline exceeded,
cancelled, invalid state, unknown.

`UNKNOWN` being non-retryable is the important default: an unrecognized
failure retried three times is a retry storm; surfaced once, it is a
bug report. A *new* class is therefore non-retryable until someone
deliberately adds it.

**Provider-health signals** are a narrower set — only model timeout and
model unavailable advance a circuit breaker. A malformed answer says
nothing about provider health, and counting it would let one awkward
question disable a model for everyone. (The same reasoning
`model_routing.models` already applies.)

`HarnessTerminated` is the orderly-stop signal. `run_agent` catches it
and returns a well-formed terminal state — it must never reach a route
handler, because that converts a designed stop into a 500. This graph
already hit that exact failure once with `GraphRecursionError`; this is
the general form of the fix.

---

## 9. Integration points

| File | Change |
|---|---|
| `agent/graph.py` | Wraps all 18 `add_node` calls; `run_agent(..., envelope=None)`; catches `HarnessTerminated` |
| `agent/state.py` | `harness_envelope`, `harness_termination_reason` |
| `agent/orchestrator/graph.py` | Threads the envelope through both the pass-through and multi-source paths |
| `agent/orchestrator/nodes.py` | `sql_source_node` forwards it, so the multi-source route is not the one unguarded path |
| `api/main.py` | Builds the envelope; cancels on timeout; honours the switch |
| `config/settings.py` | 8 new fields |

---

## 10. Test coverage

`tests/test_harness_runtime.py` — **62 cases**, covering every case
Prompt 02 names:

| Required case | Covered by |
|---|---|
| Successful execution | `TestSuccessfulExecution` |
| Max-step termination | `TestStepBudget` |
| Timeout | `TestDeadline` |
| Cancellation | `TestCancellation` (incl. a real second thread) |
| Retry exhaustion | `test_retry_budget_is_reported_not_raised` |
| Malformed state | `TestMalformedAndUnguardedState` (5 shapes) |
| Repeated node execution | `test_repeated_execution_of_one_node_is_counted` |
| Tool / model / DB / provider failure | `TestFailureClassification` |
| Invalid state transition | `TestLifecycle` |
| Cross-request contamination | `TestCrossRequestIsolation` |

Plus the security property (`TestEnvelopeIsServerOwned`), child budgets,
telemetry safety, and settings wiring.

**Mutation-verified.** Disabling the step-budget check and the deadline
clamp fails 3 of the 62; restoring them returns to green. The
cross-thread cancellation test uses a **real thread** and a step budget
far beyond what the loop can reach, so it cannot pass by exhausting
steps instead — a sequential version would prove nothing about thread
visibility.

---

## 11. Acceptance against Prompt 02

| Criterion | Status |
|---|---|
| Harness controls execution limits | **Yes** — steps, duration, model calls, tool calls, tokens |
| No infinite loops | **Yes** — step budget plus the pre-existing `recursion_limit` |
| Timeouts enforced | **Yes** — and composed, via `timeout_for` |
| Cancellation propagated | **Yes, cooperatively** — bounded to one node; §6 states the limit |
| Retry budgets bounded | **Yes** |
| Security-sensitive controls server-owned | **Yes** — frozen dataclasses, not in graph state |
| Existing functionality intact | **Yes** — unguarded is a supported mode; full suite green |

---

## 12. Known limits carried forward

- **No checkpointing or resume** (baseline H7). No `BaseCheckpointSaver`
  ships for SQL Server at the pinned `langgraph==0.2.62`; CLAUDE.md §13
  already discloses this. Not invented here as a side effect.
- **Cancellation is cooperative** (§6).
- **Token budgets are best-effort** and therefore off by default (§7).
- **The envelope is process-local.** A multi-worker deployment has one
  per worker, like every other process-local control in this codebase
  (CLAUDE.md §13).

Resilience controls — backoff, jitter, circuit breaker on the primary
LLM path, result-size enforcement, idempotency — are Prompt 03's scope
and are not claimed here.
