# Agent Harness — Management Baseline (Audit)

An audit of the **runtime control layer** around this platform's agent
execution: what controls exist, where they live, what they actually
bound, and what they do not. Prompt 01's deliverable; documentation
only, no behaviour changed.

**Scope boundary.** This audits how the harness *controls* schema
retrieval, RAG, the semantic layer, analytical planning, SQL generation,
SQL validation and the database adapters. It does not audit or redesign
those capabilities themselves.

**Headline finding.** This repository already has a real harness. It is
not a blank slate and it must not be rebuilt: LangGraph is the
orchestration framework, `AgentState` is the state container,
`agent/tools/registry.py` already does per-tool permission + timeout +
bounded retry, `agent/rate_limit.py` already does admission control and
concurrency limiting, and `agent/multiagent/policy.py` already has a
working circuit breaker. What is missing is not a framework. It is a
**single execution identity and budget that spans a whole request**, and
**cancellation that actually stops work**.

---

## 1. Current harness architecture

### 1.1 Entry points

| Entry | File | Notes |
|---|---|---|
| `POST /ask` | `api/main.py::ask` | The main path. Admission-controlled, then dispatched to a bounded thread pool |
| `POST /execute` | `api/main.py` | "Confirm and Run" — no graph, direct governed SQL execution |
| `run_orchestrated` | `agent/orchestrator/graph.py` | Multi-source router; a pass-through to `run_agent` unless `ENABLE_MULTI_SOURCE_ROUTER` |
| `run_agent` | `agent/graph.py:~500` | The 18-node SQL graph's only invoker |
| `build_analyst_graph` | `agent/analyst/graph.py:543` | The AI Data Analyst loop, its own graph and budget |
| Supervisor | `agent/multiagent/` | Fixed-routing specialist supervisor |
| Tool registry | `agent/tools/registry.py::execute` | MCP-shaped tool invocation |

### 1.2 Graphs and state

| Graph | State | Compiled |
|---|---|---|
| SQL pipeline, 18 nodes | `agent/state.py::AgentState` (763 lines, TypedDict) | `agent/graph.py:388`, process-lifetime singleton |
| Orchestrator | `agent/orchestrator/state.py::OrchestratorState` | `agent/orchestrator/graph.py` |
| Analyst loop | `AnalystState` | `agent/analyst/graph.py:563` |

`AgentState` already carries several harness-shaped fields:
`status`, `retry_count`, `max_retries`, `caller_roles`, `tenant_id`,
`session_id`, `stage_timings` (an `operator.add` reducer channel), and
`error_history`/`attempt_history`.

### 1.3 Execution lifecycle, as it exists today

```
POST /ask
  → auth (api/auth.py) → tenant resolution (security/tenancy.py)
  → input validation            (before any slot is acquired)
  → global concurrency limiter  (agent/rate_limit.py::get_ask_concurrency_limiter)
  → per-caller limiter          (get_per_caller_ask_concurrency_limiter)
  → submit to bounded ThreadPoolExecutor (api/main.py::_get_ask_executor)
  → [worker thread] set_audit_tenant_id(tenant) contextvar
  → run_orchestrated → run_agent
  → compiled_graph.invoke(state, config={"recursion_limit": N})
  → per-node: LLM calls (agent/llm_client.py), DB calls (db/execution.py)
  → finally: reset_audit_tenant_id
  → caller waits future.result(request_timeout_seconds)
```

### 1.4 Controls that already exist and work

| Control | Where | What it bounds |
|---|---|---|
| Step bound | `agent/graph.py:588` — `recursion_limit = 20 + effective_max_retries * 10` | Total node executions. Sized against the worst-case 12-node `missing_reference` retry cycle after a real reproduced `GraphRecursionError` |
| Retry budget | `AgentState["max_retries"]`, `agent/complexity.compute_max_retries` | Graph-level self-correction retries |
| Admission control | `agent/rate_limit.py::ConcurrencyLimiter` ×2 (global + per-caller) | Concurrent `/ask`; immediate 429 before any LLM/DB work |
| Bounded worker pool | `api/main.py::_get_ask_executor` | Worker threads; drained on shutdown |
| LLM rate limit | `get_llm_call_limiter` | LLM calls per minute |
| DB concurrency | `get_database_execution_limiter` | Per-database concurrent executions |
| LLM timeout | `Settings.ollama_request_timeout_seconds` (300) → `ollama.Client(timeout=...)` | One Ollama call |
| DB timeout | `Settings.query_timeout_seconds` (15) → `db/execution.py::_execute_with_timeout` | One query; **forced connection abort**, which genuinely works |
| Request ceiling | `Settings.request_timeout_seconds` (600) → `future.result(timeout)` | How long the **caller waits** — see §4.1 |
| Tool governance | `agent/tools/registry.py::execute` | Permission (fail-closed), `tool.timeout_seconds`, `tool.retry_policy.max_attempts`, overall deadline |
| Circuit breaker | `agent/multiagent/policy.py::CircuitBreaker` | Closed/open/half-open with cooldown. **Only wired into the multiagent supervisor** |
| Provider fallback | `model_routing/router.py` | Circuit + budget + fallback. **Off by default** |
| Tenant propagation | `set_audit_tenant_id` contextvar, bound in `run_agent` | Audit events; explicitly bound on the worker side because `ThreadPoolExecutor` does not copy contextvars |
| Metrics | `observability/metrics.py::PerformanceMetrics` | Per-tenant windows, stage timings, p50/p95/p99 |
| Token capture | `observability/llm_timing_capture.py` | Ollama's own `prompt_tokens`/`completion_tokens`, parsed from log lines |

---

## 2. Control gaps

Numbered for reference by the later prompts. Severity is production
risk, not effort.

### H1 — A timed-out request is abandoned, not cancelled · **High**

`api/main.py::_run_ask_with_timeout` is explicit about this in its own
docstring:

> *"There is still no clean cross-thread cancellation of an in-flight
> Ollama call or DB query once the task is running."*

`future.result(timeout_seconds)` stops the **caller** waiting. The
worker keeps running: it still holds a pool thread, may still be inside
a 300s Ollama call, and may still execute SQL *after the user received
a timeout response*. The concurrency slot is correctly held until the
task really finishes (`on_done`), which prevents pile-up — but the work
itself is unbounded by the request deadline.

**Consequence:** `request_timeout_seconds=600` is not an execution
bound. A single `/ask` can keep consuming a worker well past it.

### H2 — No end-to-end execution deadline · **High**

Timeouts are **per-operation**, never composed. A graph run can make
many LLM calls, each allowed its own full 300s, plus DB calls at 15s
each. Nothing computes "this execution must end by T" and nothing
propagates a *remaining* budget down to the next call. A child timeout
can therefore exceed what is left of the parent's.

### H3 — No execution identity · **High**

There is no `execution_id`. `session_id`, `conversation_id` and a
correlation id exist in places, but no single id identifies one agent
run across API → graph → node → model → tool → DB. Diagnosing one slow
or failed execution from logs means correlating by timestamp.

### H4 — Budgets are per-mechanism, not per-execution · **Medium**

Tokens are *captured* (`llm_timing_capture`) but never *budgeted*.
There is no token budget, no cost budget, and no per-execution cap on
total LLM calls or total tool calls. `max_retries` bounds graph retries;
`tool.retry_policy` bounds one tool. Nothing bounds their sum.

### H5 — No circuit breaker on the primary LLM path · **Medium**

`agent/llm_client.py` calls Ollama directly. If Ollama is down, every
node that calls it waits its full timeout, every request, with no
fast-fail. A working `CircuitBreaker` already exists in
`agent/multiagent/policy.py` but is not reused here. Model routing's
breaker is off by default.

### H6 — No idempotency mechanism anywhere · **Medium**

`grep -rl "idempoten"` matches nothing in first-party code. SQL
execution is `SELECT`-only, which bounds the blast radius considerably
— but retried/duplicated executions still cost real DB time, and
side-effecting tools have no duplicate-execution protection.

### H7 — No checkpointing, no resume · **Low (disclosed)**

`agent/state.py:740` records this deliberately: no checkpointer ships
for SQL Server at the pinned `langgraph==0.2.62`. CLAUDE.md §13 already
discloses it. A crashed execution is simply lost. This is a **known,
accepted** gap, not an oversight — the later prompts should not invent
a custom `BaseCheckpointSaver` as a side effect.

### H8 — Lifecycle states are a loose string · **Medium**

`AgentState["status"]` is `"pending" | "succeeded" | "failed" |
"needs_clarification"`. There is no state machine, no invalid-transition
guard, and no representation for `CANCELLED`, `TIMED_OUT`,
`WAITING_FOR_TOOL` or `RECOVERING`.

### H9 — No harness-level failure taxonomy · **Medium**

`agent/error_classification.py` classifies *SQL* errors for retry
targeting. There is no equivalent classifying *harness* failures
(model timeout vs provider outage vs tool malformed vs budget
exhausted), which is what a retry policy and telemetry both need.

### H10 — Termination reason is not recorded · **Low**

When a run ends, nothing records *why* in machine-readable form:
completed, max-steps, deadline, retry exhaustion, cancellation,
circuit-open. `status` conflates all failures into `"failed"`.

---

## 3. Failure-mode review

Against the specific modes Prompt 01 names:

| Failure mode | Current behaviour | Risk |
|---|---|---|
| Infinite agent loop | **Bounded** — `recursion_limit`, sized to the retry budget | Low |
| Retry storm | **Partly bounded** — graph retries capped; no backoff, no jitter, no global retry budget | Medium |
| Duplicate tool calls | Bounded per tool (`max_attempts`); no cross-execution dedupe | Medium |
| Duplicate SQL execution | Possible on retry; `SELECT`-only limits harm | Medium |
| Repeated LLM calls | Rate-limited per minute; **not** capped per execution | Medium |
| Context explosion | Prompt sections bounded individually; no total context budget | Medium |
| Orphaned checkpoints | N/A — no checkpointing (H7) | None |
| Stale state | `AgentState` is per-request; compiled graph is stateless | Low |
| Partial execution | Terminal nodes set `status`; a `GraphRecursionError` path was fixed | Low |
| **Cancellation during tool execution** | **Not supported** (H1) | **High** |
| Model timeout | Per-call timeout only (H2) | Medium |
| Database timeout | **Well handled** — forced connection abort | Low |
| Provider outage | No fast-fail on the primary path (H5) | Medium |
| Malformed tool result | Caught per tool; result size not bounded | Medium |
| Invalid graph state | No transition guard (H8) | Medium |
| Cross-request contamination | Low — state is per-request; `tests/conftest.py` clears the three process-lifetime caches | Low |
| Cross-tenant contamination | Low — tenant comes only from a server-loaded row; metrics are per-tenant windows; contextvar is reset in `finally` | Low |

---

## 4. Specific risks worth stating plainly

### 4.1 `request_timeout_seconds` does not mean what its name implies

It is a **"stop waiting"** timeout, and `Settings`' own docstring says
so. An operator reading the name will reasonably assume it bounds
execution. It does not. This is the single most consequential gap
(H1 + H2) and the one most likely to produce a production surprise:
under load, abandoned-but-running executions accumulate against the
pool while callers see timeouts.

### 4.2 Reusable mechanisms that must not be duplicated

Later prompts should **extend these, not replace them**:

- `agent/rate_limit.py::ConcurrencyLimiter` / `SlidingWindowRateLimiter`
  / `BoundedLimiterCache` — already correct, already bounded-memory.
- `agent/multiagent/policy.py::CircuitBreaker` — correct closed/open/
  half-open implementation; promote and reuse it.
- `agent/tools/registry.py::execute` — already the tool governance
  boundary with permission + timeout + bounded retry.
- `db/execution.py::_execute_with_timeout` — the only timeout in the
  codebase that genuinely aborts work. Its idiom is the model to follow.
- `observability/metrics.py::PerformanceMetrics` — per-tenant windows
  and percentiles already exist.
- `security/audit_log.py` + the `set_audit_tenant_id` contextvar — the
  established context-propagation mechanism across the thread boundary.
- `agent/error_classification.py` — the pattern for a failure taxonomy.

### 4.3 What must not be built

- **No second orchestration framework.** LangGraph stays.
- **No second state container.** The envelope references `AgentState`;
  it does not replace it.
- **No custom `BaseCheckpointSaver`** (H7 is a disclosed, accepted gap).
- **No re-implementation** of SQL validation, authz, tenant isolation or
  result governance. The harness enforces *around* them.

---

## 5. Target harness architecture

```
                  ┌──────────────────────────────────┐
   POST /ask ───► │ ExecutionEnvelope (server-owned) │
                  │  ids · budgets · deadline        │
                  │  lifecycle state · cancellation  │
                  └───────────────┬──────────────────┘
                                  │ bound once, never model-writable
                  ┌───────────────▼──────────────────┐
                  │ Harness control boundary         │
                  │  step/deadline/budget checks     │
                  │  cancellation checkpoints        │
                  │  failure classification          │
                  └───────────────┬──────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          ▼                       ▼                       ▼
   LangGraph nodes          LLM client              Tool registry
   (unchanged logic)     (+ circuit breaker)      (already governed)
                                  │
                                  ▼
                         Existing security stack
            (sql_validator · authz · tenancy · governance)
```

The envelope is **server-owned and immutable from the model's side**.
Nothing a model emits may alter `tenant_id`, `user_id`, authorization
scope, any budget, any deadline, or any security policy.

---

## 6. Implementation priorities

| # | Work | Closes | Prompt |
|---|---|---|---|
| 1 | Execution envelope + ids + server-owned budgets | H3, H4 | 02 |
| 2 | Lifecycle state machine with guarded transitions | H8, H10 | 02 |
| 3 | Monotonic deadline, propagated as *remaining* budget | H2 | 02 |
| 4 | Cooperative cancellation checked at node boundaries | H1 | 02 |
| 5 | Harness failure taxonomy | H9 | 02 |
| 6 | Retry policy: classification, backoff, jitter, budget | — | 03 |
| 7 | Circuit breaker on the primary LLM path (reuse existing) | H5 | 03 |
| 8 | Result-size limits, idempotency keys | H6 | 03 |
| 9 | Execution telemetry keyed by `execution_id` | H3 | 04 |
| 10 | Harness evaluation suite, stress + failure injection | — | 04, 05 |

**On H1 specifically:** Python cannot pre-emptively kill a thread
blocked in a C extension. The honest target is *cooperative*
cancellation — a flag the envelope carries, checked at every node
boundary and before every LLM/tool call, so an abandoned execution stops
at the next checkpoint instead of running to completion. The one place
that can do better already does: `db/execution.py` force-closes the
connection. That asymmetry should be stated, not papered over.

---

## 7. Files likely to change

**New:** `agent/harness/` (envelope, lifecycle, budgets, cancellation,
failure classification, telemetry), plus its tests.

**Modified (small, additive):** `agent/graph.py` (`run_agent` accepts
and threads an envelope), `agent/state.py` (one envelope reference
field), `api/main.py` (construct the envelope, propagate cancellation),
`agent/llm_client.py` (deadline-aware timeout + breaker),
`agent/orchestrator/graph.py` (pass the envelope through),
`config/settings.py` (new bounded settings), `observability/metrics.py`
(harness counters).

**Explicitly unchanged:** `agent/sql_validator.py`, `agent/authz.py`,
`identity/rbac.py`, `security/*`, `governance/*`, `db/adapters/*`,
`embeddings/*`, `retrieval/*`.

---

## 8. Test coverage today

Harness-adjacent suites that exist: `test_rate_limit.py`,
`test_api_rate_limit.py`, `test_ask_concurrency_pool_warning.py`,
`test_model_selection_concurrency.py`, `test_orchestrator.py`,
`test_multiagent_supervisor.py` (covers the circuit breaker).

No suite today covers: execution deadline, cancellation, a harness
lifecycle transition, a per-execution budget, or termination reason —
because none of those exist yet.

---

## 9. Acceptance

- Repository-wide audit completed; every finding cites a real file,
  function or line.
- No speculative architecture claimed as present. H7 is recorded as a
  deliberate, already-disclosed limitation rather than a defect.
- Existing mechanisms catalogued for reuse (§4.2) with an explicit
  do-not-build list (§4.3).
- Harness problems are kept distinct from application/AI logic
  throughout; nothing in §6 changes how a question becomes SQL.
