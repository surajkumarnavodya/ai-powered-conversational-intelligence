# Concurrency, Routing, Guardrails and Grounding

The authoritative document for the asynchronous question queue
(`jobs/`), the deterministic source router
(`agent/orchestrator/source_signals.py`), per-task model pinning
(`model_routing/tasks.py`) and the guardrail façade (`guardrails/`).

Read [`docs/architecture/context-map.md`](architecture/context-map.md)
for how this fits with the other reference documents. Where this document
and a feature's own `NN_*_CONTRACT.md` disagree about that feature, the
contract wins.

---

## 1. What was added, and what was already there

Nearly every capability named in this area existed in some form before
this change. The table separates the two, because conflating them is how
a reader ends up re-implementing something.

| Capability | State before | What changed |
|---|---|---|
| In-flight concurrency limiting | `agent.rate_limit.ConcurrencyLimiter`, atomic, global + per-caller | **Unchanged.** Still bounds `POST /ask`. |
| Per-caller `/ask` cap | `MAX_CONCURRENT_ASK_REQUESTS_PER_CALLER=2`, immediate 429 | **Unchanged.** |
| Execution budgets, cancellation, retry w/ jitter, idempotency, breakers | `agent/harness/` | **Reused.** The queue builds its envelopes and retry policy from it. |
| Multi-source routing | LLM classifier + deterministic `SOURCE_PERMISSIONS` filter | **Extended** with a deterministic pre-pass and confidence scoring. |
| Governed model routing | `model_routing/` — policy, breakers, budgets, fallback chains | **Extended** with per-task baselines and a strategy preference. |
| Input guard, SQL validator, governance, redaction, moderation | `agent/input_guard.py`, `agent/sql_validator.py`, `governance/`, `security/`, `moderation/` | **Composed**, not replaced, behind one configurable façade. |
| **Asynchronous question queue** | *did not exist* | **New** (`jobs/`, `api/jobs.py`). |
| **Job status vocabulary** | *did not exist* | **New** (`jobs.models.JobStatus`). |
| **Grounding / citation gate** | *did not exist* | **New** (`guardrails/grounding.py`). |

---

## 2. Concurrency

### 2.1 Two paths, deliberately different

| | `POST /ask` (synchronous) | `POST /questions` (asynchronous) |
|---|---|---|
| Returns | the answer | a job id, immediately (202) |
| Per-caller cap | `MAX_CONCURRENT_ASK_REQUESTS_PER_CALLER` (2) | `MAX_CONCURRENT_QUESTIONS_PER_USER` (100) |
| Over the cap | immediate 429 | **queued** |
| Cancellation | abort the request | `POST /questions/{id}/cancel` |
| Needs a real user account | no | **yes** (see §2.6) |

`/ask` is unchanged. A caller who wants one answer and will wait for it
should keep using it, and the React composer still does for the first
question of a turn.

The split is not arbitrary. A caller blocking on a response wants a fast
refusal, because a queued wait they cannot see is indistinguishable from
a hang. A caller holding a job id wants the opposite: the question kept
and run when capacity frees up.

### 2.2 Lifecycle

```
                    ┌──────────────────────────► CANCELLED
                    │                      ┌───► TIMEOUT
   submit ──► QUEUED ──► RUNNING ──► COMPLETED
                    │        │      └─────────► FAILED
                    └────────┴─────────────────► BLOCKED_BY_GUARDRAIL
```

Transitions are a **whitelist** (`jobs.models._ALLOWED_JOB_TRANSITIONS`),
the same discipline `agent.harness.lifecycle` follows. A terminal status
has no outgoing edge. `QUEUED → COMPLETED` is illegal: a job must run
before it can complete.

Four of the seven statuses are distinct kinds of "did not succeed", and
keeping them apart is the point:

- **`FAILED`** — something broke.
- **`TIMEOUT`** — exceeded `QUESTION_TIMEOUT_SECONDS` while running, or
  `QUESTION_QUEUE_WAIT_TIMEOUT_SECONDS` while queued.
- **`CANCELLED`** — the owner, or graceful shutdown, stopped it.
- **`BLOCKED_BY_GUARDRAIL`** — refused by policy. **Nothing
  malfunctioned**; reporting this as a failure would make a correct
  refusal indistinguishable from an outage, both to the user and to
  whoever alerts on failure rates.

`QuestionJobOut.agent_status` maps all four onto `AskResponse`'s frozen
four-value vocabulary for a client that already renders it.

### 2.3 Admission is one atomic step

Every capacity counter lives behind **one** `threading.RLock` in
`jobs.store.JobStore`. `admit` and `promote_next` are the only methods
that change a counter, and each is a single critical section.

This is not an optimisation; it is the correctness requirement. Split
across a limiter and a store, "is there a free slot", "take it" and
"record that we took it" are three steps with two windows — and under 100
concurrent submissions those windows are not theoretical: two workers
each see one free slot and both take it.

`tests/test_question_queue.py::TestPerUserConcurrencyUnderRealThreads`
proves this with **real threads**. A sequential test of a
check-then-increment passes even when it is racy.

**Fairness.** `promote_next` picks the oldest *startable* queued job, not
simply the oldest. A strict FIFO head-of-line queue would let one user at
their own 100-job ceiling block a second user's first question.

### 2.4 Backpressure

Exceeding the concurrency cap queues. Exceeding the *queue* refuses with
429, because an unbounded queue is a memory leak with a progress bar.

| Bound | Setting | On breach |
|---|---|---|
| Running, per user | `MAX_CONCURRENT_QUESTIONS_PER_USER` | queue |
| Running, process-wide | `MAX_CONCURRENT_QUESTIONS_GLOBAL` | queue |
| Queued, per user | `QUESTION_QUEUE_MAX_DEPTH_PER_USER` | 429 |
| Queued, process-wide | `QUESTION_QUEUE_MAX_DEPTH` | 429 |
| Retained jobs, per user | `QUESTION_MAX_RETAINED_JOBS_PER_USER` | oldest terminal evicted |
| Result retention | `QUESTION_RESULT_TTL_SECONDS` | evicted |

### 2.5 Validation of the limit

`MAX_CONCURRENT_QUESTIONS_PER_USER` is validated **at startup**, and a
bad value prevents boot rather than being clamped. An operator who asked
for 10000 and silently got 500 has a deployment that does not do what
their configuration says, and nothing tells them.

| Value | Outcome |
|---|---|
| non-numeric | refused |
| `0`, negative | refused |
| `> 500` (`MAX_SAFE_CONCURRENT_QUESTIONS_PER_USER`) | refused |
| `MAX_CONCURRENT_QUESTIONS_GLOBAL` below it | refused |
| `QUESTION_QUEUE_MAX_DEPTH_PER_USER` above `QUESTION_QUEUE_MAX_DEPTH` | refused |

The 500 ceiling is a guard against a typo, not a capacity claim: every
RUNNING question holds a worker thread, a pooled database connection and
an in-flight Ollama call for its full duration.

### 2.6 Duplicate submission

Prevented server-side, twice over:

1. **Idempotency.** `jobs.store.build_idempotency_key` hashes the
   operation, the **owner**, the **tenant**, and either the client's
   `idempotency_key` or the question text plus its options. A resubmission
   returns the original job with `duplicate: true`. Owner and tenant are
   hashed *in*, not prefixed, so no crafted client key can collide with
   another user's or another tenant's entry. The question is hashed,
   never stored, so an idempotency table is not a log of every question
   anyone asked.
2. **Client-side**, in `questionQueueStore`: a question already in flight
   is not re-sent. Checked synchronously before any `await`, which is the
   window a double-click exploits.

Either alone is insufficient — the local check cannot survive a process
restart, and the server check alone would still briefly render a second
card.

### 2.7 Timeouts, retries and cancellation

- **Run timeout** is enforced at the **node boundary** via the
  execution envelope's deadline, not with a watchdog thread per job: at
  100 concurrent questions, a thread per job to watch a thread per job is
  the resource exhaustion this subsystem exists to prevent. A single
  monitor thread flips the status to `TIMEOUT` and signals the envelope,
  so the next poll is truthful immediately even though the worker only
  notices at its next node.
- **Retries** use `agent.harness.retry.RetryPolicy` — exponential
  backoff with jitter — bounded by `QUESTION_MAX_RETRIES`. Only failures
  the harness classifies as transient are retried. An authorization
  denial, a validation rejection and a **guardrail refusal** are never
  retried: a refusal is a decision, and repeating it spends capacity to
  be told no again.

  The retry runs **in place on the same worker**, so a retrying job stays
  `RUNNING` and keeps its concurrency slot across the backoff — worth
  knowing when reading `running_global`. Handing the job back to the
  queue would free the slot, but then it would have to win admission
  again behind every other waiter, turning a transient blip into an
  unbounded wait. The backoffs are short (sub-second to a few seconds, a
  30s total ceiling) against a budget measured in minutes, and
  `decide_retry` refuses a backoff that would exceed the execution's
  remaining deadline.
- **Cancellation** flips the status under the store's lock *before*
  signalling the envelope, so a caller polling immediately afterwards
  sees `CANCELLED`. The in-flight execution stops at its next node
  boundary — not instantly. Python cannot pre-empt a thread inside a C
  extension, the same limitation `db.execution` documents; what the
  signal buys is bounding the wasted work to roughly one node rather than
  a whole graph run.
- **Graceful shutdown** stops admitting, cancels every queued job,
  signals every running one, then waits up to
  `QUESTION_SHUTDOWN_GRACE_SECONDS`. A worker still inside a C extension
  at the end of the grace outlives it; the cost is a discarded result,
  never a corrupted one.

### 2.8 Resource exhaustion

The queue's cap is an **admission** gate. Each admitted run is still
bounded by controls that predate it and are untouched:

| Resource | Bound by |
|---|---|
| Database connections | `DB_POOL_SIZE + DB_MAX_OVERFLOW`, plus `agent.rate_limit.get_database_execution_limiter` (per database, sized from that pool) |
| Model calls | `LLM_CALL_RATE_LIMIT_PER_MINUTE`, process-global, checked before every real call including retries |
| Rows / query time | the twice-enforced row cap and the driver-level query timeout |
| Context / result size | `HARNESS_MAX_CONTEXT_CHARS`, `HARNESS_MAX_RESULT_BYTES` |
| Steps / LLM / tool calls | `HARNESS_MAX_STEPS`, `HARNESS_MAX_LLM_CALLS`, `HARNESS_MAX_TOOL_CALLS` |
| Retrieved chunks | `MAX_RETRIEVAL_CHUNKS` |
| Worker threads | `MAX_CONCURRENT_QUESTIONS_GLOBAL` (the pool size) |

So raising the queue's cap does **not** quietly raise any of the above.
`jobs/sizing.py` cross-checks them at startup and warns — never refuses —
when they disagree, because every finding there is correct-but-slower
behaviour rather than a fault.

### 2.9 Known limitations

- **Process-local.** No persistence, no cross-process coordination. A
  multi-worker deployment has one queue, and therefore one set of
  per-user counters, **per worker** — so N workers admit up to N ×
  `MAX_CONCURRENT_QUESTIONS_PER_USER`. This matches every other control
  in this codebase (CLAUDE.md §13: rate limiters, breakers, caches) and
  is the first thing to revisit for a multi-replica deployment.
- **Jobs do not survive a restart.** The store is in memory. A restart
  loses queued and running jobs; their owners see nothing rather than a
  stale answer.
- **Requires a real user account.** `none` and `static_token` auth modes
  provide no distinct per-caller subject, so ownership cannot be
  enforced; `POST /questions` returns **403** rather than putting every
  caller in one shared bucket where each could read the others' results.
- **No push.** The frontend polls (`POLL_INTERVAL_MS`, 1.5s) for the
  jobs that can still change. No WebSocket endpoint exists, and
  `POST /ask/stream` serves the synchronous path.

---

## 3. Source routing

### 3.1 The three layers, in order

1. **Availability** — `sources/descriptors.py` is the single owner of
   each source's availability rule, permission and routing description.
   A feature flag alone with nothing configured behind it does not make a
   source available.
2. **Deterministic scoring** (new) —
   `agent/orchestrator/source_signals.py` scores every available source
   from the question, the attachments and the conversation context. A
   conclusive read routes directly and **skips the LLM call**.
3. **LLM classification** — `classify_sources`, unchanged, for everything
   ambiguous.

Then, always: the **deterministic permission filter**
(`SOURCE_PERMISSIONS`), the session expensive-source ceiling, and the
domain request policy. The model may propose; it never decides.

### 3.2 Why a deterministic pre-pass

Which sources a question reached used to depend on how well the
configured local model followed a prompt, and that varies by model —
`router_node`'s own comments record `llama3.1:8b` and `qwen2.5-coder:7b`
disagreeing on the same question. Scoring removes the model from the
unambiguous cases:

| Question | Routes to | LLM call |
|---|---|---|
| `Extract text from this image` (file attached) | `attachments` | skipped |
| `Show top 10 sales by region` | `sql` | skipped |
| `Search the web for the latest tariff news` | `web` | skipped |
| `Generate an image of a red bicycle` | `generation` | skipped |
| `What does the employee handbook say about leave?` | `documents` | skipped |
| `Compare our Q3 revenue against the policy cap` | — | **classifier** |
| `Tell me something interesting` | — | **classifier** |

### 3.3 Conservatism is the design

Two rules make the scorer defer rather than guess:

- **Threshold.** Nothing below `SOURCE_CONFIDENCE_THRESHOLD` (0.70)
  routes on its own. A wrong skip routes a question with no model to
  catch it; a wrong non-skip costs one LLM call.
- **Ambiguity.** A runner-up within half the leader's score means two
  sources genuinely apply, and a language model is better at that than
  keyword matching. `Compare our Q3 revenue against the policy cap`
  defers for exactly this reason.

**Attachments keep their own rule.** When a file is attached, the
existing, tested `_looks_like_attachment_only_question` pre-check decides
— not signal scoring. That rule already defers to the classifier when the
question *also* names a database keyword, which is what keeps
`summarize this file and compare it with our database records` reaching
both sources.

### 3.4 Confidence is evidence, not probability

`AskResponse.source_confidence` and `QuestionJobOut.source_confidence`
report a score for every source that was **scored**, not only those
routed to — a low score is the answer to "why didn't this search the
documents?", which is absent if only the winners are reported.

The number is the bounded sum of the deterministic signals that matched.
It is **not calibrated against any ground truth** and is always
`DataTruthLevel.AI_INFERENCE`. The UI therefore renders three coarse
bands ("strong / partial / weak match"), never a percentage: showing
"0.83" invites a reader to treat it as an 83% chance the answer is right,
which is a claim nobody is making.

### 3.5 No answer at all

When every authorized source comes up empty, `synthesis_node` returns one
generic "couldn't find anything" message rather than concatenating each
source's own version. A source denied by role is reported separately via
`permission_denied_notice`, because "not permitted" and "not found" have
different causes and different remedies.

---

## 4. Model routing

### 4.1 Two layers

- **`model_routing/tasks.py`** (new) — *which model does this kind of
  work start from*. A static, operator-configured mapping.
- **`model_routing/policy.py`** — *should we escalate or de-escalate from
  that baseline for this request*, given complexity, budget, breaker
  state and recorded evaluation evidence.

The first supplies the baseline the second already takes as input. Two
decisions, not two implementations of one.

### 4.2 Precedence

1. A caller's validated `AskRequest.model` — always wins.
2. The task's own pin (`ROUTER_MODEL`, `SQL_MODEL`, `RAG_ANSWER_MODEL`).
3. `FALLBACK_MODEL`.
4. `OLLAMA_MODEL`.

`VISION_MODEL` and `EMBEDDING_MODEL` resolve to `MEDIA_VISION_MODEL` and
`EMBEDDING_MODEL_NAME`, which already own those choices — one owner per
fact. `EMBEDDING` deliberately ignores steps 1 and 3: falling back from a
missing embedding model to a chat model would produce vectors from the
wrong model and a corrupt index rather than a clean failure.

### 4.3 A pin can never reach an unapproved model

Every non-blank chat-model pin is checked against
`OLLAMA_ALLOWED_MODELS` at startup, and the app **refuses to boot** on a
pin outside it. An unapproved pin is not a degraded experience — it is a
model the operator never approved receiving this deployment's prompts and
retrieved data, and the first anyone would hear of it is in a provider's
logs. Every offending pin is reported in one message.

### 4.4 Strategy

`MODEL_ROUTING_STRATEGY` ∈ `capability | cost | latency | quality`
orders candidates that `policy._eligible` has **already approved**. It
cannot reach a model outside the allowlist, relax a task's risk level, or
widen a budget: a strategy is a preference, never a permission.

`capability` is the default and is byte-for-byte the pre-existing
ordering, so the setting changes nothing until an operator sets it. Under
`latency`, a model with no measured p95 sorts **last** — unknown is not
fast, and treating absence of evidence as evidence of speed would be
inference presented as fact.

### 4.5 Health, fallback and disclosure

Circuit breakers, per-request budgets and fallback chains are
`model_routing/`'s existing machinery, unchanged. An
`OllamaUnavailableError` advances the breaker and tries the next model in
the chain; any other `AgentError` means the model answered and this
application's deterministic handling took over, so it is re-raised rather
than retried elsewhere.

**Nothing about model configuration is exposed.** Responses carry only
the resolved model *name* that `AskResponse.model` already returned. No
provider name, key, routing rule, raw prompt or system instruction
crosses the API boundary. `tests/test_question_jobs_api.py` asserts this
against the serialized response.

---

## 5. Guardrails

### 5.1 One façade over controls that already enforce

`guardrails/` adds a seam and a vocabulary. Every control it coordinates
was already present and is still the thing enforcing:

| Layer | Enforced by | Posture |
|---|---|---|
| Input validation | `agent.input_guard.check_input` | fail-closed |
| Prompt injection | `security.injection_patterns` | **detection only** |
| Indirect injection | same, over retrieved/supplied content | **detection only** |
| Topic / domain policy | `governance.request_policy` | fail-closed |
| Source / tool permission | `SOURCE_PERMISSIONS`, `agent.authz`, `identity.rbac` | fail-closed |
| SQL safety | `agent.sql_validator` (AST allowlist) | fail-closed |
| Restricted columns, PII in results | `governance.result_policy`, `governance.classification` | fail-closed |
| Secret redaction | `security.redaction` | always |
| Uploaded content | `moderation.gate` | fail-closed once enabled |
| Grounding | `guardrails.grounding` | configurable |

### 5.2 `GUARDRAIL_ACTION` cannot soften a boundary

`GUARDRAIL_ACTION` ∈ `block | warn | redact` governs **only the advisory
layers**. With `GUARDRAILS_ENABLED=false` **and**
`GUARDRAIL_ACTION=warn` both set, every one of these still refuses:

- a non-`SELECT` statement reaching the database;
- a caller reading a source, tool or column their roles do not grant;
- a `CREDENTIAL` or `PAYMENT` column, which is withheld from **every**
  caller including an admin;
- tenant resolution, which fails closed on unknown;
- a sensitivity-categorised policy chunk being summarised;
- small-group suppression.

`guardrails.engine.resolve_action` is the single function that enforces
this split, and `tests/test_guardrails.py::TestGuardrailActionCannotSoftenABoundary`
asserts it **exhaustively over `FAIL_CLOSED_LAYERS`**, so adding a layer
cannot quietly escape the check. That test also drives
`agent.sql_validator` and `governance.domains` directly with every
guardrail setting at its most permissive, and includes a non-vacuity
check proving an advisory layer *does* honour `warn`.

This mirrors `security/injection_patterns.py`'s standing position:
detection is a signal, the validator and the RBAC gates are the boundary.
A configurable knob over a signal is useful; a configurable knob over a
boundary would be a vulnerability with a `.env` setting.

### 5.3 Everything retrieved is data

`guardrails.engine.scan_untrusted_content` is the shared entry point for
web results, PDF text, attachments, OCR/ASR output, captions, media
metadata, tool output and database cell values. One function, so the
framing is identical everywhere and a new source cannot forget it.

Content asserting authority — "this is authorized", "pretend I am admin"
— is matched like any other words. The verdict carries **no notion of
granted permission at all**, which is what makes the claim structurally
impossible to honour. Under `redact`, an offending chunk is dropped
entirely rather than partially masked: a chunk carrying an injection
attempt has no trustworthy remainder worth splicing into a prompt.

### 5.4 Abuse screening is deliberately narrow

`guardrails.engine._ABUSE_PATTERNS` is four patterns, not a content
classifier. The real content gate for ingested assets is
`moderation/gate.py`, which is mandatory whenever media search or
document RAG is on. A broad regex list over user questions over-blocks
ordinary analytics — "accounts at risk of default", "employee attrition",
"exposure to credit risk" — and over-blocking is its own failure mode
(CLAUDE.md §5.8). `tests/test_guardrails.py` pins six such questions as
never blocked.

### 5.5 Refusals are not oracles

Every refusal message is a fixed module constant, never built from the
question and never naming which pattern matched. Matched pattern names go
to the log and the audit event, where an investigator can see them and a
caller cannot.

---

## 6. Grounding and hallucination controls

### 6.1 What this is, stated honestly

This is **not** a hallucination detector. Nothing local and
deterministic can know whether a fluent sentence is true. What
`guardrails/grounding.py` checks is a set of **necessary conditions**,
each of which is a real failure when absent:

| Condition | Failure reason |
|---|---|
| Retrieval returned something | `NO_EVIDENCE` |
| The source did not report emptiness | `SOURCE_REPORTED_INSUFFICIENT` |
| Confidence cleared `MIN_SOURCE_CONFIDENCE` | `BELOW_CONFIDENCE_FLOOR` |
| A citation is present, if `REQUIRE_CITATIONS` | `MISSING_CITATIONS` |
| Every citation names a genuinely retrieved source | `FABRICATED_CITATION` |
| The answer shares vocabulary with its evidence | `LOW_EVIDENCE_OVERLAP` |

Failing any of these makes the answer **unverifiable**, which is weaker
and different from "false". The response says "I could not verify", never
"that is wrong".

`FABRICATED_CITATION` is the one hallucination this genuinely catches:
citations are compared against the **retrieved chunks' own filenames**,
not against themselves, so an invented source is detectable. A citation
differing only by case or padding is treated as a formatting artefact,
not a fabrication — raising a serious alert for a whitespace difference
would train people to ignore it.

### 6.2 The overlap check errs toward permissive

`MIN_LEXICAL_OVERLAP` is 0.15, and an answer with fewer than three
distinctive words is never penalised ("Yes.", "Approximately 25.").
Lexical overlap is a crude proxy; it is set to catch the blatant case — an
answer with nothing in common with its own evidence, which is what a
model answering from parametric memory produces — while never penalising
ordinary paraphrase.

### 6.3 Behaviour

- `SOURCE_GROUNDING_REQUIRED=true` (default): an unverifiable answer is
  **replaced** with a cannot-verify response. Replaced, never rewritten:
  editing it would leave the citations attached to a claim they no longer
  support, the same rule `governance.recommendation_safety` follows.
  The source's status becomes `insufficient_information`, which
  `synthesis_node` already treats as "this source found nothing" — so a
  multi-source answer still shows what the other sources did find.
- `SOURCE_GROUNDING_REQUIRED=false`: the answer is returned and
  **labelled**, which is the honest middle ground.
- `ALLOW_GENERAL_KNOWLEDGE=false` (default): an answer with no evidence
  at all does not stand.

**Evidence not inspectable is not the same as evidence absent.** A
source that answers without exposing its chunks is recorded as
*unassessed*, not refused — failing closed on a missing integration
detail rather than on a safety finding is the wrong posture for an
accuracy aid (CLAUDE.md §4.2). An empty chunk list, by contrast, means
retrieval ran and matched nothing, and *does* refuse.

### 6.4 Provenance is never upgraded

A grounded retrieval answer is still `AI_INFERENCE`: it is a model's
rendering of real evidence, a genuinely different claim from a value read
out of a table. Only the SQL path produces `DATABASE_FACT`, and only an
explicit human action produces `CONFIRMED_BUSINESS_TRUTH`.

**SQL answers are not assessed here.** Their grounding is established far
more strongly — the rows came from the database through a validated
`SELECT`, which `DataTruthLevel.DATABASE_FACT` already records. Running a
lexical check over a numeric result would be noise at best and would
refuse correct answers at worst.

---

## 7. Security and authorization

| Control | Where |
|---|---|
| Authentication on every route | `api.auth.verify_api_key`, via `require_permission` |
| `Permission.ASK` for submission | the same permission `POST /ask` requires |
| Owner-scoped reads | `JobStore.get`/`list_for_owner`/`cancel` take an `owner_key` |
| IDOR → **404, not 403** | another user's job id is indistinguishable from a nonexistent one |
| Tenant resolved server-side | `security.tenancy.resolve_tenant_id_for_identity` |
| Client tenant header refused | `reject_client_tenant_override`, before authentication, every auth mode |
| Result re-governed on read | `api.jobs._regovern_job_result`, against the **reader's current** roles |
| No secrets in job records | no connection string, password or prompt; questions are retained but never logged |
| Model validated before a slot is spent | `validate_model_selection`, a 400 rather than a wasted job |

**Result re-governance is the sharpest of these.** A job's result was
governed once, for the roles the submitter held *at submission*. Roles
change. `GET /questions/{id}` re-runs result governance against whoever
is reading now — the same control `api.artifacts._result_out` and
`api.shares._governed_result_snapshot` already apply. Two of the
protections re-applied are not the submitter's to waive even for their
own job: `CREDENTIAL`/`PAYMENT` are withheld from everyone, and
small-group suppression protects the individual in the group.

---

## 8. Frontend

- The **first** question of a turn still takes the synchronous
  `/ask/stream` path, with its live per-node progress — unchanged.
- A question submitted **while another is in flight** is routed to the
  queue by `chatStore.askQuestion` instead of overwriting the single
  pending slot. This is what unblocks the composer.
- The send button is **Stop** only while a question is pending *and* the
  box is empty; with text typed it sends. Stop remains a real abort of
  the outstanding streaming request.
- `QuestionQueuePanel` renders status, source badges, a coarse confidence
  band, the model name and timings. It renders **no answers**: `TurnCard`
  already owns the result table, chart, insight and citations, and a
  second renderer would be a second owner of each.
- Nothing is persisted. A refresh rehydrates from the server, which
  re-governs every result for the caller's current roles — whereas a
  `localStorage` copy would be a stale snapshot governed for whoever the
  user was last time.
- `clearHistory` (logout, or switching user in the same tab) resets the
  queue store, so one user's questions never reach the next session.
- A deployment without the feature (queue off, or an auth mode that
  cannot attribute a job) shows **no asynchronous UI at all** — not an
  error about a feature it does not have.

---

## 9. Operational monitoring

`GET /questions/stats` returns counts only — never content, never
another user's load:

| Field | Watch for |
|---|---|
| `queued_global` | sustained growth = under-provisioned |
| `running_global` vs `max_concurrent_global` | pinned at the cap = saturated |
| `queued_for_caller` | one user queueing heavily |

Alongside the existing `GET /metrics/harness` and
`GET /metrics/performance`, and `GET /health` for the database, Chroma
and Ollama.

Alert on the **statuses**, not a single failure rate:
`BLOCKED_BY_GUARDRAIL` rising is a policy or abuse signal; `TIMEOUT`
rising is a capacity signal; `FAILED` rising is an outage signal. Folding
them together is exactly what the separate statuses exist to prevent.

### Scaling guidance

1. Raise `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` and
   `LLM_CALL_RATE_LIMIT_PER_MINUTE` *before* raising the queue's caps.
   Startup warns when they disagree.
2. Past roughly 256 concurrent, prefer **more replicas** over a higher
   global cap — the cap is a thread count. Remember each replica keeps
   its own queue and its own per-user counters (§2.9).
3. Ollama is usually the real bottleneck. `ROUTER_MODEL` pointed at a
   small model is the cheapest win: it takes the classification call off
   the critical path, and deterministic routing removes it entirely for
   unambiguous questions.
