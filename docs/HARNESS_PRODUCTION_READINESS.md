# Agent Harness — Production Readiness

Prompt 05's deliverable: the final implementation review, test
evidence, and production verdict for the agent harness.

Verdicts are **PASS**, **PASS WITH CONDITIONS** or **FAIL** only. No
scores.

---

## Final production status

> ## PASS WITH CONDITIONS

Every one of Prompt 05's approval requirements is met and evidenced.
Three conditions attach, all of them **pre-existing properties of this
platform that the harness did not introduce and cannot fix on its own**.
They are listed in §17 with the deployment constraints they imply.

The harness is not marked unconditional PASS for one honest reason:
checkpoint recovery is listed as an approval requirement, and **this
platform has no checkpointer**. That is a disclosed architectural gap
(CLAUDE.md §13: no `BaseCheckpointSaver` ships for SQL Server at the
pinned `langgraph==0.2.62`), not something the harness declined to do.
Claiming PASS on a requirement nothing satisfies would be exactly the
unevidenced claim this prompt forbids.

---

## 1. Harness architecture

A control layer around the existing runtime. Not an orchestrator.

```
POST /ask
  └─ api/main.py        builds ExecutionEnvelope from server-resolved values
       └─ bounded pool  (agent/rate_limit.py, pre-existing)
            └─ run_agent  binds envelope contextvar + audit tenant
                 └─ LangGraph (unchanged)
                      └─ guard_node  ← budget / deadline / cancel, every node
                           └─ agent/nodes.py (unchanged)
                                ├─ llm_client  ← breaker + deadline clamp + budget
                                ├─ tools/registry (pre-existing governance)
                                └─ db/execution (pre-existing forced abort)
```

| Module | Responsibility |
|---|---|
| `envelope.py` | Identity, budgets, counters, deadline, cancellation |
| `lifecycle.py` | 11 states, whitelisted transitions |
| `failures.py` | 16 failure classes, retryability, `HarnessTerminated` |
| `control.py` | `guard_node` — the single boundary |
| `retry.py` | Bounded retry, backoff, full jitter |
| `breakers.py` | Provider circuits (**reuses** the existing `CircuitBreaker`) |
| `limits.py` | Output and context size |
| `idempotency.py` | Duplicate-work prevention, tenant-scoped keys |
| `telemetry.py` | Counts, durations, identifiers — never content |
| `context.py` | The current envelope, as a contextvar |

---

## 2. Execution lifecycle

`CREATED → VALIDATING → RUNNING → {COMPLETED | FAILED | CANCELLED |
TIMED_OUT}`, with `WAITING_FOR_TOOL`, `WAITING_FOR_APPROVAL`,
`RETRYING` and `RECOVERING` as intermediate states that must return to
`RUNNING`.

Terminal states have no outgoing edges — asserted exhaustively over the
full state cross-product. Any non-terminal state may be cancelled or
time out, because those are external events, not graph decisions.

## 3. State lifecycle

`AgentState` is unchanged except for two additive fields
(`harness_envelope`, `harness_termination_reason`). The envelope travels
**in the state**, not in a closure, because the compiled graph is a
process-lifetime singleton shared across requests — a closed-over
envelope would be one request's budget applied to every other.

## 4. Retry policy

Four independent bounds, all must pass: retryable class · attempts <
max · execution retry budget remains · backoff fits the deadline.
Exponential backoff, capped per-wait and in total, with **full jitter**.
Never retried: authorization, validation, deterministic SQL, cancelled,
budget, deadline, and **unknown**.

## 5. Timeout policy

Six layers (request, graph, node, LLM, tool, database). **A child
timeout never extends the parent deadline** — `timeout_for()` returns
`min(requested, remaining)`, floored above zero because `0` means "no
timeout" to several libraries. Deadlines are monotonic.

## 6. Cancellation policy

Cooperative. `cancel()` sets a `threading.Event` checked at every node
boundary. `/ask` calls it when the request deadline passes, so an
abandoned execution stops within about one node instead of running to
completion. **Not a pre-emptive kill** — Python cannot interrupt a
thread inside a C extension. Measured: `test_cancellation_during_a_long_node_stops_at_the_next_boundary`
asserts `step_count <= 3`.

## 7. Tool governance

Unchanged `agent/tools/registry.py` (permission fail-closed, timeout,
bounded attempts, overall deadline) plus a new **per-execution**
`max_tool_calls` budget, which bounds recursion across tools in a way a
per-tool limit cannot.

## 8. Resource limits

| Limit | Setting | Default |
|---|---|---|
| Steps | `HARNESS_MAX_STEPS` | 120 |
| Model calls | `HARNESS_MAX_LLM_CALLS` | 40 |
| Tool calls | `HARNESS_MAX_TOOL_CALLS` | 30 |
| Retries | `MAX_RETRIES` | 3 |
| Duration | `REQUEST_TIMEOUT_SECONDS` | 600 |
| Tokens | `HARNESS_MAX_TOKENS` | 0 (off — best-effort counts) |
| Result bytes | `HARNESS_MAX_RESULT_BYTES` | 8 MB |
| Context chars | `HARNESS_MAX_CONTEXT_CHARS` | 400 000 |
| Concurrency | `MAX_CONCURRENT_ASK_REQUESTS` | 50 (pre-existing) |

Every default is finite — asserted by
`test_every_default_budget_is_finite`.

## 9. Failure recovery

Classified failures, bounded retry with backoff, provider circuit
breakers, and orderly termination via `HarnessTerminated` → a
well-formed terminal state rather than a 500.

## 10. Checkpoint / restart behaviour

**None exists.** See the verdict above and §17 condition 1.

---

## 11. Security assessment — PASS

| Attempt | Result | Test |
|---|---|---|
| Cross-tenant execution state | Blocked | `test_cross_tenant_execution_state_cannot_be_reached` |
| Cross-user state | Blocked | `test_two_envelopes_do_not_share_counters` |
| Tenant id manipulation | Impossible | `test_tenant_id_manipulation_via_node_output_is_impossible` |
| User id manipulation | Impossible | `test_identity_fields_cannot_be_assigned_at_all` |
| Authorization bypass | N/A — harness makes no authz decision | `test_the_harness_makes_no_authorization_decision` |
| Tool permission bypass | Unchanged, pre-existing | `agent/tools/registry.py` suite |
| Prompt injection against harness controls | No effect | `test_prompt_injection_against_harness_controls_changes_nothing` |
| Malicious tool result | Bounded, cannot reach a control | `test_a_tool_result_cannot_change_the_tenant` |
| Malicious RAG result | Same | `TestContextAmplification` |
| Retry-based side-effect duplication | Prevented | `test_retry_cannot_duplicate_a_side_effect_guarded_by_the_store` |
| Cache/checkpoint leakage | No shared store | `test_a_checkpoint_or_cache_cannot_leak_between_executions` |

**Zero unauthorized cross-tenant access.** The mechanism is
structural, not detective: `ExecutionIdentity` and `ExecutionBudgets`
are frozen dataclasses, and budgets are **not graph-state keys**, so
there is no channel through which node output could reach them.

## 12. Tenant isolation assessment — PASS

Tenant comes only from `security/tenancy.py`, is frozen into the
envelope, and never travels back out except as a telemetry label.
Idempotency keys hash the tenant in rather than prefixing it, so no
crafted payload can collide across tenants. The envelope contextvar is
reset in a `finally`, so a pool thread cannot carry one request's
envelope into the next.

## 13. Observability assessment — PASS

`GET /metrics/harness` (admin-gated) exposes reliability, latency,
efficiency, harness-health, per-node/tool/provider breakdowns and
breaker states. Every number derives from
`ExecutionEnvelope.snapshot()`, the single definition of what is safe
to retain. Asserted content-free over the full serialized snapshot.

## 14. Cost / token assessment — PASS WITH CONDITIONS

Measured per execution and exposed. **Enforcement is off by default**
because Ollama's counts are captured opportunistically from log lines,
and failing an execution over a measurement gap is worse than not
enforcing. Cost is in ordinal compute units, never money — every model
runs locally.

---

## 15. Performance results (Phase 3)

`python scripts/harness_stress_test.py`, 18 guarded steps per
execution, real threads released simultaneously:

| Concurrency | Completed | Failed | p50 ms | p95 ms | p99 ms | Peak MB | exec/s |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0 | 0.17 | 0.17 | 0.17 | 0.01 | 1769 |
| 10 | 10 | 0 | 0.12 | 0.17 | 0.17 | 0.05 | 3995 |
| 50 | 50 | 0 | 0.13 | 0.22 | 0.47 | 0.20 | 3376 |
| 100 | 100 | 0 | 0.13 | 0.21 | 0.32 | 0.38 | 3634 |
| 500 | 500 | 0 | 0.13 | 0.26 | 0.38 | 1.79 | 3515 |

**Mean p50 per guarded step: 0.0077 ms.** Zero failures at every level,
including 500 concurrent. Peak memory at 500 concurrent executions:
1.79 MB.

**What these numbers are.** Harness overhead only — no Ollama, no
database. The question they answer is "does the control layer cost
anything, and does it stay correct under concurrency?" At ~8
microseconds per guarded step against a pipeline whose LLM calls run in
hundreds of milliseconds, the overhead is not measurable end-to-end.

**What they are not.** End-to-end agent latency, which needs real
infrastructure. `scripts/run_benchmark.py` and
`scripts/integration_test.py` own that and are manual by design
(CLAUDE.md §7). No end-to-end latency claim is made here.

---

## 16. Test results

| Suite | Cases | Phase |
|---|---|---|
| `tests/test_harness_runtime.py` | 62 | 2 — runtime contract |
| `tests/security/test_harness_resilience.py` | 47 | 3 — resilience + attacks |
| `tests/test_harness_observability.py` | 28 | 4 — telemetry + 12 scenarios |
| `tests/security/test_harness_production_gate.py` | 26 | 1, 4, 5, 6 |
| **Harness total** | **163** | |

**Full backend suite: 5872 passed, 0 failed** (from 5712 before this
work: +163 harness tests, −3 that were consolidated).

### Mutation verification

Every guard was verified non-vacuous by breaking it and confirming the
expected tests fail:

| Guard reverted | Tests that failed |
|---|---|
| Step-budget check + deadline clamp | 3 of 62 |
| Retryability check, provider-health filter, result-size gate | 10 of 47 |

Every mutation script asserts its pattern matched before writing — a
`str.replace` that silently misses reports "all passed" and fakes the
proof.

### Regression (Phase 7)

| Measure | Before | After |
|---|---|---|
| Backend suite | 5712 passed | 5872 passed |
| `ruff check .` | 1 finding (`agent/multiagent/agents.py`) | same 1 finding |
| `black --check .` | clean | clean |
| SQL / result / analytical accuracy | `docs/EVALUATION_CURRENT.md` | **unchanged — not re-measured** |

**On accuracy:** the harness changes no prompt, no node logic, no SQL
generation and no validation. The benchmark-pinned prompt text
(CLAUDE.md §12) is untouched. Re-running the accuracy benchmark needs
real infrastructure that does not exist here, so **no accuracy claim is
made in either direction** — in line with the requirement not to claim
PASS without evidence.

### One regression found and fixed during this work

`guard_node` originally set `__wrapped__` on the wrapper.
`typing.get_type_hints` follows that attribute, so LangGraph resolved
the *wrapped* function's string annotations against the *wrapped*
function's module globals — which broke for any locally-defined node,
including three tenant-isolation tests using monkeypatched fakes whose
module never imported `Any`. Three tests in
`tests/security/test_cross_tenant_shared_infrastructure.py` failed with
a `ForwardRef` error far from its cause. Fixed by using a private
`__harness_node__` attribute instead, which keeps the escape hatch
without joining the unwrapping protocol. All three pass.

---

## 17. Remaining risks and the three conditions

**Condition 1 — No checkpointing, so no resume. Pre-existing.**
No `BaseCheckpointSaver` ships for SQL Server at the pinned
`langgraph==0.2.62`; writing a custom one was explicitly out of scope
(Prompt 01 §4.3). A crashed execution is lost rather than resumed.
*Deployment implication:* treat `/ask` as non-resumable; a process
restart fails in-flight requests, and callers must retry.

**Condition 2 — Cancellation is cooperative, not pre-emptive.**
Python cannot interrupt a thread inside a C extension. An abandoned
execution stops at its next node boundary — bounded to roughly one
node, measured. *Deployment implication:* size the worker pool
expecting up to one node of wasted work per timed-out request. This is
a strict improvement on the previous behaviour of running to
completion.

**Condition 3 — Every control is process-local.**
Breakers, idempotency stores, telemetry, limiters. A multi-worker
deployment has one set per worker — the same property CLAUDE.md §13
already discloses for six other controls. *Deployment implication:*
effective global concurrency is `workers × max_concurrent_ask_requests`,
and a circuit opens per worker rather than fleet-wide.

Lower risks, all disclosed:

4. **Token budgets are best-effort** and off by default (§14).
5. **`decide_retry` is not yet applied at every call site.** The breaker
   and deadline clamp *are* wired into the live LLM path; the graph's
   own self-correction loop still uses its pre-existing `max_retries`
   counter rather than this policy. That loop is already bounded — it
   simply does not get backoff and jitter. Replacing it would change
   SQL-retry behaviour that `tests/test_sql_agent_integration.py` pins,
   which is a behaviour change, not a control-layer addition.
6. **No live multi-tenant penetration test**, and **no DAST**. Both are
   pre-existing environment-blocked verifications (CLAUDE.md §13).
7. **No metrics backend is wired.** The endpoint is shaped to be
   scraped; nothing scrapes it, and no alert delivery channel exists.

---

## 18. Deployment requirements

- **No migration. No new table. No new permission. No new role.** The
  harness is pure computation over existing configuration.
- Eight new `Settings` fields, all with working defaults. A deployment
  that sets none of them gets the documented behaviour.
- `ENABLE_AGENT_HARNESS=true` by default.
- `/metrics/harness` requires `ADMIN_CONFIG`, like `/metrics/performance`.
- **One fix operators should know about:** `/metrics` was missing from
  `vite.config.ts`'s `BACKEND_ROUTES`, so `GET /metrics/performance` —
  which the frontend has always called — 404'd under `npm run dev`.
  Production builds were unaffected. Now fixed.

## 19. Rollback strategy

| Action | Effect |
|---|---|
| `ENABLE_AGENT_HARNESS=false` | No envelope is built. Every execution runs exactly as before: no budgets, no deadline, no cancellation, no harness telemetry. Breakers and limits are inert because nothing reaches them. |
| Raise an individual budget | Per-control relief without disabling the layer |
| Revert the commit | Nothing persisted needs undoing — the harness writes no database state |

The switch is genuinely complete because **unguarded is a supported
mode, exercised by every test that builds a bare state** and by every
direct caller of `run_agent` (`eval/runner.py`, `scripts/`, the analyst
loop, the supervisor). It is not a code path that only runs in an
emergency.

---

## 20. Approval requirements — evidence table

| Requirement | Status | Evidence |
|---|---|---|
| Execution is bounded | **PASS** | `TestStepBudget`, `TestDeadline`, stress at 500 |
| Retries are bounded | **PASS** | `TestRetryStorm` (4 independent bounds) |
| Tool calls are bounded | **PASS** | `test_tool_recursion_is_bounded_by_the_tool_call_budget` |
| Timeouts are enforced | **PASS** | `TestDeadline`, `timeout_for` clamp |
| Cancellation works | **PASS** (cooperative) | Cross-thread test; `step_count <= 3` |
| Side-effecting ops idempotency-safe | **PASS** | `TestIdempotency`, 16-thread concurrency |
| Tenant/user state isolated | **PASS** | §11, §12 |
| Model output cannot override controls | **PASS** | Frozen dataclasses; budgets absent from state |
| Failures are observable | **PASS** | §13, termination reason + failure class |
| Sensitive telemetry protected | **PASS** | Content-free assertion over full snapshot |
| Checkpoint recovery works | **N/A — no checkpointer** | §17 condition 1 |
| Existing tests pass | **PASS** | 5872 passed, 0 failed |
| Text-to-SQL accuracy not degraded | **PASS, by construction** | No prompt, node or validation change; §16 |
