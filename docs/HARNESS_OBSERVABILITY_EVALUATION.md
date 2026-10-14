# Agent Harness — Observability, Cost Control and Evaluation

Prompt 04's deliverable. Makes the harness measurable without making
it a data-retention liability.

---

## 1. What was reused

The brief says to use the existing infrastructure wherever possible.
What already existed, and is untouched:

| Existing | Still owns |
|---|---|
| `observability/metrics.py::PerformanceMetrics` | Per-tenant stage timings, p50/p95/p99, cache hit rate |
| `observability/llm_timing_capture.py` | Ollama's own reported load/prompt/eval split and token counts |
| `security/audit_log.py` | Security events, correlation id, tenant stamping |
| `agent/nodes.py::_timed_node` | Per-node `[timing]` log lines and `stage_timings` |
| `model_routing/metrics.py` | Route decisions and escalation budget |
| `governance/telemetry.py` | Policy decision counters |

`agent/harness/telemetry.py` is additive: it records what none of those
could, because none of them had an execution identity to key on.

---

## 2. The safety rule, stated first

**Telemetry carries counts, durations and opaque identifiers. Never
content.**

Not a question, not SQL, not a row, not a tool result, not a document,
not a credential.

`HarnessTelemetry.record_execution` takes
`ExecutionEnvelope.snapshot()` — it does not build its own view. That
is deliberate: there is exactly **one** definition of "safe to retain",
it lives on the envelope, and a second derivation would be a second
chance to widen it.

### Roles are excluded too

Even though they are not secret. A role tuple is *authorization
context*; an execution record carrying it invites a reader to treat
telemetry as an access-control audit trail, which
`security/audit_log.py` already is and this deliberately is not. One
owner per fact (CLAUDE.md §4.8).

`tenant_id` **is** included — every existing audit event already
carries it, and diagnosing an isolation problem without it is
impossible.

Asserted in `test_an_execution_record_contains_no_question_sql_or_role`
against a list of five representative secrets, over the full serialized
snapshot *and* the recent-executions list.

---

## 3. What is recorded

### Execution

`execution_id` · `request_id` · `correlation_id` · `conversation_id` ·
`tenant_id` · graph + version · provider · model · state ·
current node · step count / max · retry count · model calls · tool
calls · token estimate · cost estimate · elapsed · remaining ·
cancelled · termination reason · failure class

### Per node / per tool / per provider

calls · failures · timeouts · avg · p50 · p95 · p99

### Derived rollups

| Group | Metrics |
|---|---|
| Reliability | success, failure, timeout, cancellation, retry rate |
| Performance | p50 / p95 / p99 end-to-end |
| Efficiency | avg model calls, tool calls, steps, tokens, cost |
| Harness health | budget terminations, deadline terminations, cancellations, duplicates prevented, circuit-breaker states |
| Safety | security event counts, by kind only |

Exposed at **`GET /metrics/harness`**, admin-gated with
`Permission.ADMIN_CONFIG`, the same way `/metrics/performance` is.

**Not tenant-scoped, unlike `/metrics/performance`** — a deliberate
difference. That endpoint reports per-question latency, which is
tenant-attributable operational data. This one reports whether the
*runtime* is healthy: breaker states, budget terminations, node
latency. Those are properties of the process, not of any tenant's
traffic, so there is nothing to scope.

---

## 4. Bounded by construction

An unbounded metrics dict fed by request data is a memory-exhaustion
vector, not merely untidy.

- The execution window is a `deque(maxlen=N)`, default 500.
- Each latency window is likewise capped.
- Aggregate keys are **node names, tool names and provider names** —
  all code constants. Nothing user-supplied becomes a dict key, so a
  caller cannot grow these structures at all.

---

## 5. Recording never fails a request

Every recording path is wrapped and fails open. `guard_node` records
node timing in a `finally` with its own `try/except`; `run_agent`
records the execution inside a `try/except`. A telemetry bug costs one
data point, never the user's answer — the standing posture for every
other observability aid in this codebase.

Timing is recorded **on the way out whatever happened**, so a node that
always fails still appears in the latency breakdown. A node missing
from telemetry precisely when it is broken is the opposite of useful.
`test_a_failing_node_still_appears_in_the_breakdown` pins it.

---

## 6. Cost and token control

`observability/llm_timing_capture.py` already parsed Ollama's own
`prompt_tokens`/`completion_tokens`. The harness adds the missing half:
per-execution accumulation (`token_estimate`, `cost_estimate`) and an
optional budget.

**The token budget is off by default** (`HARNESS_MAX_TOKENS=0`). Those
counts are captured opportunistically from log lines, not guaranteed,
and enforcing a budget on a best-effort number would fail executions
over a measurement gap. The counter runs regardless, so an operator can
observe real usage for a while and then switch enforcement on — which
is the right order for a control that can refuse work.

---

## 7. End-to-end tracing

One execution is followable across every layer:

```
API            request_id, correlation_id  (api/main.py middleware)
  ↓
harness        execution_id                (ExecutionEnvelope.identity)
  ↓
graph          graph_name, graph_version
  ↓
node           current_node, per-node timing
  ↓
model          provider, model, per-provider latency and failures
  ↓
tool           tool name, latency, timeouts
  ↓
database       existing db/execution timing
  ↓
response       termination_reason, failure_class
```

`correlation_id` is taken from the existing middleware rather than
minted fresh, so a harness record joins to the audit log on a key that
was already there.

---

## 8. Evaluation suite

`tests/test_harness_observability.py` — **28 cases**, including all 12
scenarios Prompt 04 lists:

| # | Scenario | Test |
|---|---|---|
| 1 | Normal query | `test_01_normal_query` |
| 2 | Complex query | `test_02_complex_query_uses_more_budget_but_completes` |
| 3 | Multi-step | `test_03_multi_step_execution_counts_every_distinct_node` |
| 4 | Tool failure | `test_04_tool_failure_is_classified_and_bounded` |
| 5 | Model failure | `test_05_model_failure_trips_the_breaker_after_a_threshold` |
| 6 | Database failure | `test_06_database_failure_is_retryable_but_on_a_tighter_policy` |
| 7 | Timeout | `test_07_timeout_terminates_and_is_recorded` |
| 8 | Cancellation | `test_08_cancellation_terminates_and_is_recorded` |
| 9 | Retry | `test_09_retry_is_counted_against_the_execution` |
| 10 | Recovery | `test_10_recovery_returns_to_running` |
| 11 | Long context | `test_11_long_context_is_truncated_and_disclosed` |
| 12 | Concurrent requests | `test_12_concurrent_executions_do_not_interfere` |

Scenario 12 uses **12 real threads**, because a sequential version
passes even with the bug it is meant to catch — the same reason the
cancellation and idempotency tests use real threads.

---

## 9. Agent-quality metrics

Prompt 04 asks for SQL execution accuracy, result accuracy, analytical
accuracy, clarification rate and failed-execution rate.

**Three of these already have an owner and were not duplicated.**
`eval/` holds the execution-accuracy benchmark, the security benchmark
and the component benchmark, and `docs/EVALUATION_CURRENT.md` is the
authoritative record of accuracy numbers. Re-deriving accuracy from
harness telemetry would create a second owner for a fact that already
has one (CLAUDE.md §4.8), and a worse one: the harness sees whether an
execution *completed*, not whether its answer was *right*.

What the harness contributes honestly:

| Metric | Source |
|---|---|
| Failed-execution rate | `reliability.failure_rate` — **new** |
| Clarification rate | derivable from `AgentState["status"]`, pre-existing |
| Timeout / cancellation rate | **new** |
| SQL / result / analytical accuracy | `eval/` — **unchanged, not duplicated** |

Stated plainly: the harness measures *reliability*, not *accuracy*.
Conflating them would be the exact "never document an assumed
capability as implemented" failure CLAUDE.md §1 warns about.

---

## 10. A pre-existing bug this work found

`frontend/src/lib/api.ts` has always called `GET /metrics/performance`,
but `/metrics` was **missing from `vite.config.ts`'s `BACKEND_ROUTES`**
— so under `npm run dev` that call 404s while the production build
works fine. Exactly the dev-only gap CLAUDE.md §9 flags as a recurring
miss, and the same one `/onboarding` and `/semantic-catalog` each had
before their own prompts found it.

Found because `/metrics/harness` is the second route under that prefix.
Fixed by adding `'/metrics'`.

---

## 11. Acceptance against Prompt 04

| Criterion | Status |
|---|---|
| Every execution traceable | **Yes** — `execution_id` joins API → node → model → tool → DB |
| Metrics expose no sensitive content | **Yes** — asserted over the serialized snapshot |
| Cost/token usage measurable | **Yes** — measured always, enforced optionally |
| Harness failures diagnosable from telemetry | **Yes** — termination reason, failure class, per-node failures |
| Existing application metrics compatible | **Yes** — nothing existing was modified |

---

## 12. Limits

1. **Process-local and in-memory**, resetting on restart. One store per
   worker (CLAUDE.md §13). No metrics backend is wired; the snapshot is
   shaped to be scraped but nothing scrapes it yet.
2. **No distributed tracing.** `correlation_id` and `execution_id` make
   a trace reconstructible from logs; there is no OpenTelemetry
   exporter.
3. **Cost is in ordinal compute units, never money** — the same
   convention `model_routing.models.TIER_COST_UNITS` already uses.
   Every model runs locally.
4. **Token counts are best-effort** (§6).
5. **No dashboard ships.** The endpoint returns the data; rendering it
   is not in scope, and no delivery channel exists for alerts
   (CLAUDE.md §13's disclosed gap).
