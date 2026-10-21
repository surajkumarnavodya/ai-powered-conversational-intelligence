# 53 — Concurrent Questions, Auto-Routing, Guardrails and Grounding

**Status:** implemented and verified (mocked). See §6 for what is *not*
verified and why.

**Authoritative reference for behaviour and configuration:**
[`docs/CONCURRENCY.md`](docs/CONCURRENCY.md). This contract carries the
inspection findings, the design decisions and the test evidence.

---

## 1. Inspection findings

The brief asked for ten capability areas. Inspection found most already
built — the single most repeated lesson in this repo (CLAUDE.md §6.1).
Recording what was found, because the value of this change is largely
*what it did not rebuild*.

### Already present, reused unchanged

| Asked for | Already existed |
|---|---|
| Atomic, race-safe concurrency limiting | `agent.rate_limit.ConcurrencyLimiter` + `BoundedConcurrencyLimiterCache`, lock-based, global and per-caller |
| Timeout, cancellation, graceful shutdown | `agent/harness/` (`ExecutionEnvelope` deadlines, node-boundary cancellation), `api.main._shutdown_ask_executor` |
| Retry with exponential backoff and jitter | `agent.harness.retry.RetryPolicy` |
| Idempotency | `agent.harness.idempotency` (intra-execution, tenant-scoped hashes) |
| Circuit breakers, health, model fallback | `model_routing/` (policy, breakers, budgets, fallback chains, metrics) |
| Multi-source routing with authorization | `agent/orchestrator/` — LLM classify → deterministic `SOURCE_PERMISSIONS` filter → parallel fan-out → attributed synthesis |
| Source metadata ownership | `sources/descriptors.py` |
| Input validation, injection detection | `agent/input_guard.py`, `security/injection_patterns.py` |
| SQL safety | `agent/sql_validator.py` (AST allowlist, SELECT-only) |
| PII / restricted columns / suppression | `governance/` (request, result, recommendation, classification) |
| Secret redaction | `security/redaction.py` |
| Content moderation | `moderation/gate.py` |
| Tenant isolation | `security/tenancy.py` |
| Admission control on `/ask` | bounded `ThreadPoolExecutor` + two limiters |
| Per-execution budgets | `HARNESS_MAX_*` settings |
| Streaming | `api/streaming.py` (`POST /ask/stream`, per-node SSE) |

### Genuine gaps, which this change closes

1. **No queue.** Per-caller `/ask` cap was 2 with an immediate 429. No
   job status, no cancellation endpoint, no request-boundary idempotency.
   (`docs/SCALE_OUT_PROMPT.md` Phase 4 had named this as out of scope.)
2. **No deterministic source pre-router and no confidence scoring.**
   Routing depended on how well the configured local model followed a
   prompt; `router_node`'s own comments record two models disagreeing on
   the same question.
3. **No per-task model baselines.** Every non-SQL call site read
   `Settings.ollama_model` directly. No `MODEL_ROUTING_STRATEGY`.
4. **No single configurable guardrail surface**, and no vocabulary for
   reporting a refusal distinctly from a failure.
5. **No grounding or citation gate.** `OrchestratorState.citations` was
   plumbed but unused on several paths; nothing checked whether an answer
   was supported by its evidence.
6. **Frontend was single-threaded.** One `pendingQuestion`, one abort
   controller; the composer's button was Stop-only while pending.

---

## 2. Design decisions and their reasons

### 2.1 A separate asynchronous path, not a modified `/ask`

`/ask` is unchanged. A caller blocking on a response wants a fast
refusal; a caller holding a job id wants the question kept. One endpoint
cannot be both, and changing `/ask` would alter a frozen contract every
existing client depends on.

### 2.2 `jobs/` sits below `api/` and never imports it

The work a job performs is injected as a `JobRunner` protocol. `jobs/`
knows nothing about FastAPI, `AskResponse` or `run_orchestrated`;
`api/jobs.py` supplies a runner that calls the *same* entry point and
*same* serializer `/ask` uses. Injection is what makes "no second
pipeline" structural rather than a promise.

### 2.3 One lock owns every counter

`jobs.store.JobStore` holds a single `RLock`, and `admit`/`promote_next`
are the only methods that change a counter. Split across a limiter and a
store, "is there a slot", "take it" and "record it" are three steps with
two windows — and at 100 concurrent submissions those windows are real.

### 2.4 A third status vocabulary, with explicit mappings

`JobStatus` is neither `AgentState["status"]` (frozen, four values) nor
`harness.ExecutionState` (execution-scoped). A job exists before any
execution does (`QUEUED`) and can end without one running
(`BLOCKED_BY_GUARDRAIL`). `JobStatus.from_execution_state` and
`to_agent_status` are the only places either mapping lives.

### 2.5 `BLOCKED_BY_GUARDRAIL` is not `FAILED`

Nothing malfunctioned. Folding them together would make a correct
refusal indistinguishable from an outage — to the user, and to whoever
alerts on failure rates.

### 2.6 Deterministic routing is conservative by construction

Two rules make it defer rather than guess: a threshold
(`SOURCE_CONFIDENCE_THRESHOLD`), and an ambiguity rule — a runner-up
within half the leader's score means two sources genuinely apply, which a
language model handles better than keyword matching.

**Attachments keep their existing rule.** When a file is attached, the
already-tested `_looks_like_attachment_only_question` decides, not signal
scoring. Discovered by a test failure: letting scoring decide broke
`summarize this file and compare it with our database records`, which
must reach both sources. The narrower, older rule was right.

### 2.7 Confidence is evidence, not probability

The bounded sum of matched deterministic signals, uncalibrated, always
`AI_INFERENCE`. The UI renders three coarse bands, never a percentage:
"0.83" invites reading it as an 83% chance the answer is right.

### 2.8 `GUARDRAIL_ACTION` governs advisory layers only

One function (`guardrails.engine.resolve_action`) enforces the split, and
`FAIL_CLOSED_LAYERS` is its single source of truth. A new layer is
advisory unless explicitly added to that set — the safe direction, since
a layer wrongly treated as advisory is still enforced by its underlying
module, while the reverse would claim an enforcement this package does
not perform.

### 2.9 Grounding checks necessary conditions, and says so

Not a hallucination detector; nothing local and deterministic can know
whether a fluent sentence is true. It checks that evidence exists, that
citations are present and name genuinely retrieved sources, and that the
answer shares vocabulary with its evidence. Failing any means
**unverifiable**, and the response says "could not verify", never "that
is false".

**Evidence not inspectable ≠ evidence absent.** A missing `chunks` key
means the source answered without exposing what from; that is recorded
as unassessed, not refused. An empty list means retrieval matched
nothing, and *does* refuse. Conflating them would fail closed on a
missing integration detail rather than a safety finding — the wrong
posture for an accuracy aid (CLAUDE.md §4.2).

### 2.10 Frontend overflow, not a rewrite

The first question of a turn still takes the synchronous streaming path.
Only a question submitted *while another is in flight* goes to the queue.
The Stop button is retained for the empty-box case.

---

## 3. What was deliberately not done

- **`ENABLED_SOURCES` as a new setting.** Source availability already has
  one owner (`sources/descriptors.py`, derived from each source's feature
  flag plus the config it needs). A second list could disagree with it.
  `.env.example` documents the mapping instead.
- **`MAX_TOOL_CALLS` / `MAX_TOKENS_PER_REQUEST` as new settings.**
  `HARNESS_MAX_TOOL_CALLS` and `HARNESS_MAX_TOKENS` already own those
  budgets.
- **`VISION_MODEL` / `EMBEDDING_MODEL` as new settings.**
  `MEDIA_VISION_MODEL` and `EMBEDDING_MODEL_NAME` already own them;
  `ModelTask` resolves to those.
- **New `RouteTask` members for unwired tasks.** That enum documents
  call sites that genuinely thread a model; adding members without
  wiring would claim a capability that does not exist (CLAUDE.md §1).
- **A distributed queue.** Redis/Celery is a deployment decision, not a
  code change to make unasked. The process-local limitation is stated in
  `docs/CONCURRENCY.md` §2.9 rather than left implicit.
- **Token-level streaming.** Still absent, for the reasons CLAUDE.md §13
  gives (benchmark-pinned prompts, one blocking call per step).
- **"Finishing the wiring" of built-not-wired subsystems** (CLAUDE.md
  §4.7). `analytics.root_cause`, `recommendation.RecommendationProvider`
  and `agent.translation` are untouched.

---

## 4. Files

**New:** `jobs/{__init__,models,store,queue,sizing}.py`,
`guardrails/{__init__,config,engine,grounding}.py`,
`agent/orchestrator/source_signals.py`, `model_routing/tasks.py`,
`api/{jobs,jobs_schemas}.py`, `docs/CONCURRENCY.md`,
`frontend/src/lib/questionsApi.ts`,
`frontend/src/store/questionQueueStore.ts`,
`frontend/src/components/sql/QuestionQueuePanel.tsx`, this contract, and
six test files.

**Modified:** `config/settings.py` (settings + 2 validators),
`api/main.py` (router, startup checks, shutdown hook, 2 response fields),
`api/schemas.py` (2 additive optional fields),
`agent/orchestrator/{nodes,state}.py`, `model_routing/policy.py`
(strategy-aware ordering), `rag/graph.py` (`RAG_ANSWER_MODEL`),
`tests/conftest.py` (queue singleton reset), `.env.example`,
`docs/{API,CONFIGURATION}.md`, `docs/architecture/context-map.md`,
`README.md`, `frontend/vite.config.ts` (`/questions` prefix),
`frontend/src/store/chatStore.ts`,
`frontend/src/components/chat/ChatInput.tsx`,
`frontend/src/pages/Chat.tsx`, 5 locale files, 3 existing test files
(test-double signatures).

### Backward compatibility

- `AskResponse.source_confidence` and `.grounding` are **optional with
  `{}` defaults**, per CLAUDE.md §6.3.
- `chatStore.askQuestion` now returns `QueryHistoryEntry | undefined`.
  The one caller that reads the result handles it; fire-and-forget
  callers are unaffected.
- No migration. `jobs/` has no ORM and no persistence.
- Every new setting has a default that preserves existing behaviour,
  except `ENABLE_QUESTION_QUEUE=true`, which only *adds* routes.

---

## 5. Test evidence

| Suite | Result |
|---|---|
| `tests/test_question_queue.py` | 55 passed |
| `tests/test_question_jobs_api.py` | 35 passed |
| `tests/test_guardrails.py` | 43 passed |
| `tests/test_source_signals.py` | 38 passed |
| `tests/test_model_task_routing.py` | 32 passed |
| `tests/test_question_queue_sizing.py` | 16 passed |
| `tests/test_orchestrator.py` (regression) | 151 passed |
| `tests/test_model_routing.py` (regression) | 59 passed |
| `tests/test_indirect_channel_injection.py` (regression) | 11 passed |
| Frontend (`vitest`) | 1295 passed, 94 files |
| `tsc --noEmit`, `npm run lint` | clean |
| `black --check .` | clean, 1654 files |
| `ruff check .` | 1 finding, pre-existing (CLAUDE.md §13) |
| `bandit` on the new packages | 0 Low / 0 Medium / 0 High, 3656 lines |
| `mypy` on the new production modules | **0 errors**; the 7 that remain are the pre-existing backlog (CLAUDE.md 13) |
| Full backend suite | **7516 passed, 0 failed**, 11 deselected (`-m 'not live'`) |
| Real-path end-to-end check (scratch, not in the suite) | 12 submissions through the live `/questions` routes and the real queue singleton: cap held at 5 running, all 12 completed, idempotency collapsed, cross-owner read 404, shutdown hook drained cleanly |

### Load-bearing guards proven non-vacuous

- **100 concurrent questions, one user, real threads** at the real
  default cap — asserts the cap is reached (not just respected), so a
  test that serialised the work would fail rather than pass silently.
- **`GUARDRAIL_ACTION` cannot soften a boundary** — asserted
  exhaustively over `FAIL_CLOSED_LAYERS` under all three actions, plus a
  non-vacuity check that an advisory layer *does* honour `warn`, plus
  direct drives of `agent.sql_validator` and `governance.domains` with
  every guardrail setting at its most permissive.
- **Transition whitelist** — asserts an illegal edge raises.
- **Over-blocking** — six ordinary analytics questions pinned as never
  blocked.
- **Stopword list** — pinned as non-vacuous, or every answer would pass
  the overlap check.

### Two real bugs found by these tests

1. **Routing regression.** Signal scoring short-circuited genuinely
   mixed attachment-plus-database questions, dropping the SQL leg. Two
   existing orchestrator tests caught it; fixed by scoping deterministic
   routing to non-attachment questions and adding the ambiguity rule.
2. **Infinite render loop.** `useQuestionQueueStore(selectJobs)` built a
   new array per call, and zustand compares selector results by
   reference. Caught by `Chat.test.tsx` ("Maximum update depth
   exceeded"); fixed by subscribing to the two stored slices and joining
   with `useMemo`.

### Deliberate behaviour change

`ChatInput` previously showed Stop even with text typed while a question
was pending — the composer was single-threaded by design. It now sends,
and the question is queued. `tests/.../ChatInput.test.tsx` was updated to
pin both halves (Stop with an empty box, Send with text). While fixing
it, the original assertion `queryByRole('button', { name: /^send$/i })`
was found to be **vacuous**: i18n renders raw keys in that environment,
so the accessible name is `chat.send` and the anchored pattern could
never match. Corrected to `/send/i`.

---

## 6. Residual risks and unverified behaviour

### Not verified, with the reason

- **No real-infrastructure run.** Every test is mocked — no Ollama, no
  database, no Chroma, per CLAUDE.md §7. The concurrency test exercises
  the queue's own machinery with an injected runner; it does **not**
  prove 100 concurrent *real* questions against a live database and
  Ollama, which needs infrastructure this environment does not have.
  `python scripts/integration_test.py` is the manual path.
- **Multi-worker behaviour unverified.** Process-local by design.
- **Grounding thresholds unmeasured.** `MIN_LEXICAL_OVERLAP = 0.15` and
  `MIN_SOURCE_CONFIDENCE = 0.70` are judgement calls, not measured
  values. Overlap is a crude proxy chosen to err toward permissive.
- **Signal patterns are hand-authored**, not learned or benchmarked.
  Routing accuracy is asserted against the brief's named examples plus a
  table of hand-picked cases, not a labelled corpus. The eval harness
  covers the SQL path only (CLAUDE.md §13).
- **`MODEL_ROUTING_STRATEGY` beyond `capability` is untested against a
  live Ollama**, and `latency` depends on p95 data a fresh deployment
  does not yet have (a model with no measurement sorts last).

### Residual risks

1. **Per-worker caps.** N workers admit up to N × the per-user cap. The
   highest-priority follow-up for a multi-replica deployment.
2. **Jobs lost on restart.** Owners see nothing rather than a stale
   answer, but the work is gone.
3. **Thread-per-running-job.** The global cap *is* a thread count;
   `jobs/sizing.py` warns above 256.
4. **Polling cost.** 1.5s per live job. At 100 live jobs that is ~67
   requests/second from one browser. Bounded (only non-terminal jobs are
   fetched) but not free.
5. **Abuse screening is four regexes.** Intentionally narrow; not a
   content classifier. `moderation/gate.py` remains the real gate for
   ingested assets.
6. **`require_citations` can refuse a correct answer** whose source
   produced no citation. Set `REQUIRE_CITATIONS=false` where a source
   does not cite.

### Pre-existing state, unchanged by this work

`ruff` still reports its one `I001` in `agent/multiagent/agents.py`, and
`mypy` remains report-only with its documented backlog (CLAUDE.md §5.7,
§13). Neither was "fixed" here: both are awaiting an owner decision, and
quietly resolving one would alter a documented state outside this
change's scope.

---

## 7. Configuration required to use this

Nothing, for the defaults. To use the asynchronous path:

1. **A real user account** — `LOCAL_AUTH_ENABLED=true` (and
   `alembic -c identity\alembic.ini upgrade head`) or OIDC. Without one,
   `POST /questions` returns 403 by design.
2. Optionally raise `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` and
   `LLM_CALL_RATE_LIMIT_PER_MINUTE` to match the concurrency you want;
   startup warns when they disagree.
3. For `npm run dev`, `/questions` is already added to
   `vite.config.ts`'s `BACKEND_ROUTES`.
