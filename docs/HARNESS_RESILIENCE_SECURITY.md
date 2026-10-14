# Agent Harness — Resilience, Guardrails and Security

Prompt 03's deliverable. Builds on
`docs/HARNESS_MANAGEMENT_BASELINE.md` and
`docs/HARNESS_RUNTIME_CONTRACT.md`.

**What this does not do.** It does not replace SQL validation,
authentication, authorization, RAG security or tenant isolation. Those
remain exactly where they were. The harness *integrates with* them and
bounds the resources an execution may spend reaching them.

---

## 1. Control matrix

| # | Control | Module | Bounds | Reused or new |
|---|---|---|---|---|
| 1 | Retry classification | `harness/failures.py` | What may be retried at all | New |
| 2 | Bounded retry + backoff + jitter | `harness/retry.py` | Attempts, wait, total wait | New |
| 3 | Circuit breaker | `harness/breakers.py` | Calls to a sick provider | **Reuses** `agent/multiagent/policy.py::CircuitBreaker` |
| 4 | Deadline composition | `harness/envelope.py::timeout_for` | Child never exceeds parent | New |
| 5 | Tool governance | `agent/tools/registry.py` | Permission, timeout, attempts | **Pre-existing, unchanged** |
| 6 | Result/context size | `harness/limits.py` | Bytes carried | New |
| 7 | Idempotency | `harness/idempotency.py` | Duplicate work in one execution | New |
| 8 | Resource budgets | `harness/envelope.py` | Steps, calls, tokens, duration | New (Prompt 02) |
| 9 | Admission control | `agent/rate_limit.py` | Concurrent executions | **Pre-existing, unchanged** |
| 10 | SQL safety | `agent/sql_validator.py` | Statement shape | **Pre-existing, unchanged** |
| 11 | Authorization | `agent/authz.py`, `identity/rbac.py` | Who may do what | **Pre-existing, unchanged** |
| 12 | Tenant isolation | `security/tenancy.py` | Cross-tenant access | **Pre-existing, unchanged** |
| 13 | Safe failure | `harness/failures.py::HarnessFailure` | What a caller is told | New |

Five of thirteen are pre-existing and were deliberately not rebuilt.

---

## 2. Retry policy

Four independent bounds, **all** of which must pass:

1. the failure class is retryable,
2. attempts so far `< max_attempts`,
3. the execution's own retry budget is not spent,
4. the next backoff still fits inside the deadline.

(4) is the one most retry helpers miss. Sleeping eight seconds before
an attempt that cannot finish before the request deadline is strictly
worse than failing immediately: it burns a worker, delays the error the
caller is already waiting for, and the retry was never going to land.

### Never retried

Authorization denied · validation rejected · deterministic SQL error ·
cancelled · budget exhausted · deadline exceeded · invalid state ·
**unknown**.

`UNKNOWN` being non-retryable is deliberate and is the direction that
matters: a *new* failure class is non-retryable until someone adds it
on purpose. An unrecognized failure retried three times is a retry
storm; surfaced once, it is a bug report.

Note the distinction the prompt asks for on destructive operations: SQL
here is `SELECT`-only and validated on every use, so there is no
destructive database operation to retry. A *deterministic* SQL error is
still never retried, because the same statement will fail identically.
The graph's own self-correction loop generates a **different**
statement — a different thing, budgeted separately by `max_retries`.

### Backoff

Exponential with a per-wait cap and a separate total-wait cap, because
many short waits add up to the same problem one long wait causes.

**Full jitter** (`random.uniform(0, backoff)`), not `backoff ± 10%`.
Equal-width jitter still leaves a synchronised fleet retrying inside
one narrow window after a provider blip; full jitter spreads across the
whole interval, which is what actually breaks a thundering herd.

Two policies ship. `DATABASE_RETRY_POLICY` is deliberately tighter
(2 attempts, 5s total vs 3 and 30s): a database that is timing out is
usually under load, and piling retries onto a loaded database is how a
slowdown becomes an outage.

---

## 3. Circuit breaker

**Reuses `agent/multiagent/policy.py::CircuitBreaker`** — a correct
closed/open/half-open implementation with cooldown and single-probe
recovery, already under test. A second breaker would be a second place
for the half-open logic to be subtly wrong.

What `harness/breakers.py` adds is a **provider-scoped registry** and
the rule about which failures may trip one.

### Only health signals trip it

Only `MODEL_TIMEOUT` and `MODEL_UNAVAILABLE`. A malformed model answer
is a failure but says nothing about provider health — counting it would
let one awkward question disable a model for every caller. `record_outcome`
is the only path to a breaker, so the rule cannot be bypassed by a call
site forgetting it.

### Why the primary path needed one (baseline H5)

`agent/llm_client.py` called Ollama directly. With Ollama down, every
node waited its full `ollama_request_timeout_seconds` (300s default) on
every request; a handful of concurrent requests then occupied the whole
bounded `/ask` pool doing nothing but waiting. Now: 5 real failures and
the sixth request fails in microseconds.

Thresholds are looser than the supervisor's (5 failures / 30s cooldown
vs 3 / 60s) because this sits on the primary user-facing path, where a
breaker that opens too eagerly turns a blip into a visible outage, and
a local Ollama usually recovers fast.

**Integration:** `_harness_client(settings)` replaced the raw
`_get_ollama_client(...)` at all seven `client.chat` sites. Success is
reported from `_log_ollama_timing`, which runs after every successful
call — so a *new* call site gets the success signal for free. Forgetting
it would leave a breaker that opens and never recovers, which is worse
than no breaker.

`tests/conftest.py` resets breakers between tests. Without that, one
test driving failures leaves the circuit open and the *next* test fails
for no reason of its own — the hardest contamination to diagnose,
because it only appears in a particular test order.

---

## 4. Timeouts

| Layer | Source | Enforcement |
|---|---|---|
| Request | `REQUEST_TIMEOUT_SECONDS` | Caller stops waiting **and** the envelope's deadline |
| Graph | envelope deadline | Checked at every node edge |
| Node | envelope deadline | `guard_node` |
| LLM | `OLLAMA_REQUEST_TIMEOUT_SECONDS`, clamped | `_harness_client` |
| Tool | `tool.timeout_seconds` | `agent/tools/registry.py` (pre-existing) |
| Database | `QUERY_TIMEOUT_SECONDS` | `db/execution.py` — forced connection abort |

**A child timeout never extends the parent deadline.** `timeout_for()`
returns `min(requested, remaining)`, floored just above zero. The floor
matters more than it looks: `0` means *no timeout* to several
libraries, so returning it on an expired deadline would invert the
control completely.

Deadline-derived LLM timeouts are **bucketed to 10 seconds, rounded
down**, before keying the cached-client pool. Without bucketing a
per-request remaining-time value would mint a near-unique client per
call and turn `functools.cache` into a slow leak, defeating the
connection pooling that cache exists for. Rounding down is the safe
direction.

---

## 5. Tool governance

Unchanged and not duplicated. `agent/tools/registry.py::execute`
already enforces, per invocation: permission (fail-closed on an
empty/unrecognized role set, with an audit event), `tool.timeout_seconds`,
`tool.retry_policy.max_attempts`, and an overall deadline.

The harness adds only what was missing: a **per-execution** tool-call
budget (`max_tool_calls`), which bounds recursion *across* tools in a
way a per-tool attempt limit cannot.

---

## 6. Output limits and context amplification

The attack and the accident are the same shape — one payload big enough
to push the real instructions out of the window — so the defence is the
same.

- **Sizing is on the serialized form**, never `len()`. Ten rows of
  one-megabyte cells is not a small result.
- **An unserializable payload is sized by `repr`**, never 0. Returning
  0 for what we understand least would let exactly those payloads past.
- **Truncation is at a whole-row boundary.** Half a row is a malformed
  record downstream code would read as real data.
- **Truncation is always disclosed.** A silently shortened result reads
  as a complete one — the same stance `governance.result_policy`'s
  export ceiling takes.
- The truncation notice is **plain text**, not a sentinel a model might
  treat as an instruction.

Row-fitting uses a binary search over the row count, because
serializing a large result once per row is itself a performance problem
on exactly the inputs this handles.

---

## 7. Prompt and tool injection resistance

The harness's contribution is **structural, not detective**. It adds no
pattern matching — `security/injection_patterns.py` and
`agent/input_guard.py` already do detection, and
`agent/sql_validator.py` plus the RBAC and governance gates bound
consequences.

What the harness guarantees is that **no content can reach a control**:

| Target | Why content cannot reach it |
|---|---|
| `tenant_id`, `user_id`, `caller_roles` | Frozen `ExecutionIdentity`, set at the request boundary |
| Every budget | Frozen `ExecutionBudgets`, **not in graph state** |
| The deadline | Monotonic, set at construction |
| Security policy | Harness makes no authorization decision at all |
| Tool permissions | `agent/authz.py`, unchanged |

`test_a_tool_result_cannot_change_the_tenant` feeds a node a result
containing `tenant_id`, `user_id`, `caller_roles` and an "ignore
privacy rules" instruction, and asserts the envelope is unchanged. The
reason it cannot work is that budgets and identity are not state keys —
there is nothing for a node to return.

---

## 8. Resource exhaustion

| Resource | Bound | Default |
|---|---|---|
| Graph steps | `harness_max_steps` | 120 |
| Tool calls | `harness_max_tool_calls` | 30 |
| Model calls | `harness_max_llm_calls` | 40 |
| Retries | `max_retries` | 3 |
| Output size | `harness_max_result_bytes` | 8 MB |
| Context size | `harness_max_context_chars` | 400 000 |
| Concurrent executions | `max_concurrent_ask_requests` | 50 (pre-existing) |
| Duration | `request_timeout_seconds` | 600 |

---

## 9. Idempotency

Scoped to one execution. **Not a distributed lock**, and no
cross-process claim is made — one store per worker, like every other
process-local control here (CLAUDE.md §13). It stops the duplicate-work
case that actually happens: a retry loop inside a single request.

Two properties matter more than the caching:

- **Keys are tenant-scoped by construction.** `tenant_id` is hashed in,
  not prefixed, so no crafted `operation` or `payload` can collide with
  another tenant's entry. Two tenants running byte-identical SQL sharing
  a cache entry would be the cross-tenant leak this platform is built to
  prevent, arriving through a performance optimisation.
- **Keys are hashes and nothing else.** SQL text and arguments are
  hashed, never stored. A debug dump of the store must not become a log
  of every query the platform ran (CLAUDE.md §5.6).

`begin()`/`abandon()` guard the *concurrent* duplicate, not just the
sequential one — two threads reaching the same operation at once would
both miss a plain cache and both run. Verified with 16 real threads.

---

## 10. Safe failure

`HarnessFailure` follows `agent.exceptions.AgentError`'s established
split: a short fixed `safe_message` for the caller, and `detail`
carrying `execution_id`, node, step, budgets and elapsed time for
protected logs only. Tests assert the node name and the execution id do
**not** appear in the safe message, and that a `ProviderUnavailable`
message leaks neither host nor port.

---

## 11. Threat and failure model

| Threat | Mitigation | Residual |
|---|---|---|
| Retry storm | 4 independent bounds, full jitter | Per-worker |
| Timeout storm | Composed deadlines + breaker | Cooperative cancellation (§13) |
| Tool recursion | Per-execution tool budget | — |
| Malicious tool output | Size limits, plain-text notice, content never reaches a control | Detection is elsewhere by design |
| Malicious retrieved content | Same | Same |
| Tenant manipulation | Frozen identity; budgets not in state | — |
| User manipulation | Same | — |
| Oversized result | Byte-measured truncation, disclosed | — |
| Infinite graph loop | Step budget + `recursion_limit` | — |
| Provider outage | Circuit breaker, fast-fail | Per-worker breaker |
| Duplicate side effects | Idempotency store; SQL is `SELECT`-only | Not distributed |

---

## 12. Tests

`tests/security/test_harness_resilience.py` — **47 cases**, covering
every adversarial scenario Prompt 03 names.

**Mutation-verified.** Removing the retryability check, the
provider-health filter, and the result-size gate fails **10 of 47**;
restoring them returns to green. Two tests use **real threads**
(concurrent `begin`, cross-thread cancellation) because a sequential
version passes even with the bug it is meant to catch.

Combined with `tests/test_harness_runtime.py` (62): **109 harness
tests**.

---

## 13. Remaining risks

1. **Cancellation is cooperative.** Python cannot pre-empt a thread
   inside a C extension. A cancelled execution stops at its next node
   boundary — wasted work bounded to roughly one node, not zero.
   `db/execution.py` does better for SQL by force-closing the
   connection. **Residual: low**, and it is a strict improvement on the
   previous behaviour of running to completion.
2. **Everything is process-local.** Breakers, idempotency stores,
   limiters. A multi-worker deployment has one set per worker — the same
   property CLAUDE.md §13 already discloses for the other six.
   **Residual: medium** at multi-worker scale.
3. **No checkpointing or resume** (baseline H7, CLAUDE.md §13). No
   `BaseCheckpointSaver` ships for SQL Server at the pinned
   `langgraph==0.2.62`. A crashed execution is lost.
   **Residual: medium**, pre-existing and previously disclosed.
4. **Token budgets are best-effort and off by default.** Ollama's counts
   are captured opportunistically from log lines. The counter runs
   regardless, so an operator can observe before enforcing.
   **Residual: low.**
5. **The retry helper is not yet applied at every call site.**
   `decide_retry` is implemented, bounded and tested, and the circuit
   breaker and deadline clamp *are* wired into the live LLM path. The
   graph's own self-correction loop still uses its pre-existing
   `max_retries` counter rather than this policy. Replacing it was out
   of scope for a control layer and would change SQL-retry behaviour
   that `tests/test_sql_agent_integration.py` pins. **Residual: low** —
   that loop is already bounded; it simply does not yet get backoff and
   jitter.
6. **No live multi-tenant penetration test** of the harness.
   **Residual: low** — tenant isolation is separately tested, and the
   harness introduces no new tenant-scoped storage beyond the
   idempotency key, which is hash-scoped.
