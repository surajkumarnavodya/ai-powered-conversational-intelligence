# Inference Engineering — Implementation Report

Final report for the 20-prompt inference-engineering pack. Companion to
`docs/INFERENCE_ENGINEERING_BASELINE.md` (Prompt 01's audit).

**The master contract's hard constraint governed everything here:** *if
the repository already has an equivalent or stronger capability, do not
create a competing implementation.* Eleven of the twenty objectives
were already met. Nothing was built for those, and §4 says so one by
one.

---

## 1. The result, in one table

The defect the audit found, and the fix, both measured against the
**real** `/ask` workload — not a synthetic prompt:

| | Prompt tokens Ollama evaluated | `/ask` latency |
|---|---|---|
| **Before** (`num_ctx` unset → 4096, usable 2048) | **2,050** | 70.1 s |
| **After** (`num_ctx=16384`, usable ~8192) | **5,570** | ~127 s |

The application's real SQL-generation prompt is **5,570 tokens**.
Before this change Ollama evaluated 2,050 of them and silently
discarded the rest — **63% of every prompt, starting from the front,
which is where the system prompt lives**.

A direct demonstration of the consequence, from the audit:

```
system prompt : "Reply with exactly the word BANANA and nothing else."
prompt sent   : ~21,500 tokens
evaluated     : 2,050
reply         : "It seems like you provided a large number of schema..."
```

The model ignored its system instruction because truncation had
removed it.

### The cost, stated plainly

**Latency roughly doubles: 70 s → 127 s.** That is not a regression to
fix — it is the price of the model actually processing the prompt it
was always supposed to receive. Previously the pipeline was fast partly
because it was throwing most of its input away.

Operators who prefer the old latency can set `OLLAMA_NUM_CTX=4096`, and
should understand they are re-enabling truncation when they do.

### Why this never showed up as a failure

Truncation degrades rather than breaks. The schema block sits at the
*end* of the user message and survives, so the model usually still
produced workable SQL. What was lost was the front of the
conversation — the dialect rules and the SELECT-only instruction.

**The deterministic gates were never affected.**
`agent/sql_validator.py` parses every statement with sqlglot and
execution is read-only at the database role. A truncated system prompt
could only produce *worse* SQL that gets rejected, never unsafe SQL
that reaches a database. This was an accuracy and wasted-retry problem,
not a security hole.

---

## 2. What was implemented

Three modules, two settings groups, one measurement script's worth of
evidence.

### Prompt 05 — decoding policy (`agent/llm_client._decoding_options`)

Seven call sites each repeated `{"num_predict": …, "temperature": 0.0}`
and **none set `num_ctx`**. One helper now owns decoding policy:

| Option | Value | Note |
|---|---|---|
| `num_ctx` | `ollama_num_ctx` (16384) | the fix |
| `seed` | `ollama_seed` (0) | makes a regression reproducible, not "usually reproducible" |
| `top_p` | `ollama_top_p` (1.0) | pinned so a future temperature change cannot silently also move the sampling distribution |
| `temperature` | 0.0 | **preserved**, not introduced |
| `num_predict` | per task | an insight is two sentences; SQL is not |

**A measured operational detail worth knowing:** the usable window is
`num_ctx / OLLAMA_NUM_PARALLEL`, a server-side Ollama setting this
application cannot read. Measured here — `num_ctx=8192` admitted 4,098
tokens, `num_ctx=16384` admitted 8,194, exactly half each time, with
`/api/ps` confirming the model loaded at the requested length. The
defaults are sized against the *usable* half.

### Prompt 02 — context budgeting (`agent/context_budget.py`)

Retrieval was capped by **count** (`schema_top_k=4`) and never by
**size**, so the assembled prompt ranged 2,800–19,700 tokens on
realistic inputs.

The budget drops **whole blocks**, lowest priority first, and reports
what it dropped. Two tiers:

- **Never dropped:** schema (no valid SQL without it), governing
  metrics (a *mandatory* governance block — dropping one would let the
  model answer with its own definition of a metric the business has
  already defined), the question, and retry error feedback (a retry
  without its error is just the same failing call).
- **Dropped first:** business context → golden examples → follow-up →
  plan. All four are accuracy aids, and this codebase's standing rule
  is that an accuracy aid degrades to "no extra help" rather than
  failing a request (CLAUDE.md §4.2).

Three deliberate non-behaviours:

1. **No block is ever truncated mid-way.** Half a `CREATE TABLE` reads
   as a complete, smaller schema, and the model will confidently write
   SQL against columns that do not exist.
2. **No block is ever reworded.** `_INSIGHT_SYSTEM_PROMPT` and the
   generation prompt are benchmark-pinned (CLAUDE.md §12).
3. **When the critical sections alone exceed the budget, it says so
   and keeps them.** Dropping a schema to satisfy a number would be
   strictly worse than a long prompt.

**Verified byte-identical** below the budget — a budgeting layer that
quietly reshaped the common-case prompt would invalidate
`docs/EVALUATION_CURRENT.md`'s accuracy numbers, which cannot be
re-measured in this environment.

### Prompts 08 / 14 / 18 — fingerprints (`agent/inference_fingerprint.py`)

The audit's defect went unnoticed for a long time because **nothing
recorded which configuration produced a given answer** — a quality
regression and a configuration change looked identical in the logs.

Three separate hashes (prompt / decoding / retrieval) rather than one,
so a telemetry diff points at the subsystem. Surfaced on the existing
`GET /metrics/harness` rather than a new endpoint. Hashes and numeric
tunables only — asserted to carry no prompt text and no secret.

---

## 3. Files changed

**New:** `agent/context_budget.py`, `agent/inference_fingerprint.py`,
`tests/test_context_budget.py`,
`docs/INFERENCE_ENGINEERING_BASELINE.md`, this file.

**Modified:** `agent/llm_client.py` (decoding helper; `_build_user_prompt`
assembles labelled sections), `config/settings.py` (4 settings),
`api/main.py` (fingerprint on the existing metrics route).

**Untouched, deliberately:** `agent/sql_validator.py`, `agent/graph.py`,
`agent/nodes.py`, `model_routing/`, `eval/`, `governance/`, `identity/`,
`security/`, `embeddings/`, `retrieval/`, `db/`.

---

## 4. All 20 prompts — verdict

### Already satisfied; building anything would have duplicated

| # | Objective | Existing implementation |
|---|---|---|
| 03 | Dynamic model routing | `model_routing/` — policy, circuit breakers, budgets, fallback |
| 04 | Structured outputs | Pydantic `model_validate` on intent and analytical plan; sqlglot on SQL; fail-open parsers |
| 06 | Adaptive retries | `agent/error_classification.py` + `agent/harness/retry.py` — typed classes, backoff, full jitter, four independent bounds |
| 07 | Caching | `db/result_cache.py`, per-database, off by default |
| 09 | SQL guardrails | `agent/sql_validator.py` — AST allowlist, restricted columns, wildcard, system catalogs, unsafe hints |
| 12 | Concurrency / backpressure | `agent/rate_limit.py` + bounded pool + `agent/harness/` budgets and cooperative cancellation |
| 13 | Fallback / degraded modes | Established fail-open/fail-closed split + harness `FailureClass` |
| 14 | Observability | `observability/` + `agent/harness/telemetry.py` + `GET /metrics/harness` — *extended* with the fingerprint |
| 15 | Evaluation | `eval/` — runner, evaluators, regression, baselines, golden dataset |
| 16 | Adversarial testing | `tests/security/` — governance bypass, injection, cross-tenant, harness resilience |
| 19 | Release gate | `docs/PRODUCTION_READINESS_RELEASE_GATE.md`, `docs/HARNESS_PRODUCTION_READINESS.md`, blocking CI |

### Implemented

| # | Objective | What landed |
|---|---|---|
| 01 | Audit and baseline | `docs/INFERENCE_ENGINEERING_BASELINE.md` — measured, not estimated |
| 02 | Context budgeting | `agent/context_budget.py` |
| 05 | Deterministic decoding | `_decoding_options` + `num_ctx`/`seed`/`top_p` |
| 08 | Prompt compilation | Assembly was already deterministic; added the missing version fingerprint |
| 18 | Versioning | `agent/inference_fingerprint.py` |
| 20 | Final hardening | This report; dead code removed during review |

### Assessed and deliberately not implemented

| # | Objective | Why not |
|---|---|---|
| 10 | Semantic consistency | `agent/plan_validator.py` and `agent.insight.is_insight_grounded` already cover plan conformance and insight grounding. A further "SQL vs retrieved schema" check is a real but *small* gap; adding it would mean a second validator alongside sqlglot's, and the contract forbids a competing implementation for a gap this size. **Recorded as a follow-up, not built.** |
| 11 | Latency optimization | The measured win was *removing truncation*, which prompt 02/05 did. Latency went **up**, for the reason in §1. Introducing parallelism across the 18-node graph would change execution ordering the retry routing depends on — out of proportion to the gain. |
| 17 | Cost accounting | `observability/llm_timing_capture.py` already captures per-call tokens and `agent/harness` accumulates `token_estimate`/`cost_estimate` per execution, both surfaced on `/metrics/harness`. Nothing further measured as missing. |

---

## 5. Tests

| Suite | Cases | Result |
|---|---|---|
| `tests/test_context_budget.py` (new) | 35 | passed |
| Full backend suite | **5,903** | passed, 0 failed |
| Frontend (unchanged by this work) | 1,050 | passed |

**Mutation-verified.** Making `CRITICAL` sections evictable and
removing `num_ctx` from the decoding options fails **7 of 35**;
restoring them returns to green. Every mutation script asserts its
pattern matched before writing — a `str.replace` that silently misses
reports "all passed" and fakes the proof.

**Live end-to-end** against real SQL Server and real Ollama, after the
change: `/ask` returns `succeeded` with correct SQL, the dashboard and
both health endpoints return 200.

### One flaky test, and why this change makes it flakier

`tests/test_orchestrator.py::TestRunOrchestrated::
test_flag_off_with_attachments_still_runs_the_orchestrator_graph`
failed once, in a full-suite run executed while the dev server was up
and Ollama was serving requests. It passes standalone and passes in a
full run with Ollama idle.

The cause is pre-existing and worth recording: that test mocks
`run_agent` and `run_attachment_qa` but then runs the **real
orchestrator graph**, whose synthesis node makes a genuine Ollama
call. So it is not "fully mocked" the way `CLAUDE.md` §7 describes
every pytest test as being, and it fails whenever that real call is
slow.

**This change plausibly makes it fail more often**, because a larger
`num_ctx` makes every real Ollama call slower, so a test already near
its timeout moves past it. The right fix is to mock the synthesis call
in that test rather than to lower `num_ctx`; it is left alone here
because it belongs to the orchestrator's own suite, not to this
work.

---

## 6. Security and compatibility

- **No security control was touched.** `sql_validator`, `authz`,
  tenancy, governance and the audit log are unchanged. The budget
  decides *what context the model sees*; it has no authority over what
  may be executed or returned.
- **Governing metrics are `CRITICAL`** precisely so a space-saving
  decision can never silently weaken a governance block.
- **No prompt text is logged.** `BudgetResult.summary()` carries
  section names, counts and sizes; the fingerprint carries hashes.
  Asserted by test.
- **Backward compatible.** `_build_user_prompt` gained an optional
  `settings` parameter that defaults to resolving its own;
  `CONTEXT_BUDGET_MAX_CHARS=0` restores exact pre-budget behaviour; the
  decoding helper preserves the `temperature=0.0` every call site
  already used.

---

## 7. Risks and follow-ups

1. **Latency roughly doubled** (§1). Inherent, not a defect, and
   tunable via `OLLAMA_NUM_CTX`. **This is the one change an operator
   must consciously accept.**
2. **VRAM rises with `num_ctx`.** Measured at ~2.2 GB with
   `num_ctx=8192`; roughly another GB per doubling per concurrent slot
   on an 8B model.
3. **No accuracy claim is made, in either direction.**
   `docs/EVALUATION_CURRENT.md`'s numbers were measured against a
   pipeline that was silently truncating 63% of its prompt. They are
   now, strictly, measurements of a different system. Re-running that
   benchmark needs infrastructure this environment does not have. The
   honest position: the model now receives the prompt it was designed
   to receive, and whether that moves the score is **unmeasured**.
4. **The usable window depends on `OLLAMA_NUM_PARALLEL`**, which this
   application cannot read. The defaults assume the measured factor of
   2; a deployment that pins it to 1 has twice the headroom documented.
5. **Follow-up, genuinely useful, not built:** a check comparing the
   generated SQL's table references against the *retrieved* schema
   (prompt 10). `find_unexpected_table_references` already exists in
   `agent/sql_validator.py` and would be the thing to reuse.
