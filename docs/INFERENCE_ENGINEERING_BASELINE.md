# Inference Engineering — Audit and Baseline

Prompt 01's deliverable: a map of the complete inference path, measured
baselines, a reuse inventory, and a prioritized backlog. **No code was
changed to produce this document.**

The master contract's rule governs everything below: *if the repository
already has an equivalent or stronger capability, do not create a
competing implementation.* Most of this prompt pack's 20 objectives are
already met by this codebase. §6 says which, and why nothing was built
for them.

---

## 1. The headline finding, measured

**Every Ollama call in this deployment runs with a 2,048-token context
window, and the system prompt alone is ~2,016 tokens.**

Measured against the running Ollama instance, not inferred:

```
default window probe (40,000-char prompt):  prompt_eval_count = 2050
with explicit num_ctx=16384              :  prompt_eval_count = 8194
```

A direct test of the consequence:

```
chars sent        : 86,000  (~21,500 tokens)
prompt_eval_count : 2,050            <- 90% of the prompt discarded
system prompt     : "Reply with exactly the word BANANA and nothing else."
reply             : "It seems like you provided a large number of schema..."
```

The model did not follow the system instruction **because truncation
removed it**. `num_ctx` is never set anywhere in first-party code
(verified by grep; every match is in a dependency), so Ollama's runtime
default applies. The model's *trained* context length is 131,072 —
that is not the same as the window it is *served* with, and the gap
between those two numbers is where this problem lives.

### Measured prompt sizes

Built with the real `agent.llm_client._build_user_prompt`:

| Scenario | chars | ~tokens |
|---|---|---|
| system prompt alone (mssql) | 8,064 | 2,016 |
| 4 tables × 10 cols | 11,188 | 2,797 |
| 4 tables × 40 cols | 19,348 | 4,837 |
| 4 tables × 120 cols | 41,188 | 10,297 |
| 4 × 40 + 3 golden + 5 context | 23,823 | 5,955 |
| 4 × 120 + 3 golden + 5 context | 45,663 | 11,415 |
| 8 × 120 + 3 golden + 5 context | 78,723 | 19,680 |

`schema_top_k` defaults to 4, so the 4-table rows are the realistic
ones. **Even the narrowest realistic case (2,797 tokens) exceeds the
2,048-token window.**

### Why this has not been visibly failing

`/ask` answered correctly end-to-end during this audit
(`SELECT TOP 1000 COUNT(CustomerKey) FROM DimCustomer`). Truncation
degrades rather than breaks: the schema block sits at the end of the
user message and survives, so the model often still infers the task.
What is lost first is the **start** of the conversation — the system
prompt, which carries the dialect rules and the SELECT-only
instruction.

That is a quality and safety-margin problem, not an outage, which is
exactly why it has gone unnoticed. It is also why it is P0 here.

**The deterministic gates are unaffected.** `agent/sql_validator.py`
parses every statement with sqlglot and rejects anything that is not a
single read-only `SELECT`, and execution is read-only at the database
role. A truncated system prompt cannot produce unsafe SQL that reaches
a database — it can only produce *worse* SQL that gets rejected. The
LLM proposes; it never decides (CLAUDE.md §4.3). This finding is about
accuracy and wasted retries, not a security hole.

---

## 2. Inference path map

Every LLM call, in order of a typical request:

| # | Node | Function in `agent/llm_client.py` | Token cap | Output contract |
|---|---|---|---|---|
| 1 | `classify_analytical_intent` | `generate_analytical_intent_from_llm` | `intent_classification_max_tokens` | **Pydantic** `AnalyticalIntentClassification` |
| 2 | `build_analytical_plan` | `generate_analytical_plan_from_llm` | `analytical_plan_max_tokens` | **Pydantic** `AnalyticalPlan` |
| 3 | `plan_query` | `generate_query_plan_from_llm` | `query_plan_max_tokens` | `list[str]`, shape-checked |
| 4 | `generate_sql` | `generate_sql_from_llm` | `llm_max_tokens` | fenced SQL, then sqlglot AST |
| 5 | `review_sql` | `review_sql_against_plan_from_llm` | `sql_review_max_tokens` | verdict + feedback text |
| 6 | `review_metric_conformance` | `review_sql_against_metrics_from_llm` | `sql_review_max_tokens` | verdict + feedback text |
| 7 | `generate_insight` | `generate_insight_from_llm` | `insight_max_tokens` | text, then groundedness check |

All seven share `_harness_client(settings)` (added by the harness
work), which applies the circuit breaker, the deadline clamp and the
per-execution model-call budget.

### Context sources feeding prompt #4

`_build_user_prompt` assembles, in fixed order: schema → follow-up →
analytical plan *or* query plan → golden examples → business context →
governing metrics → error feedback. Each block has its own
`_build_*_block` builder. Assembly is already deterministic.

### Decoding parameters

Every call site sets `temperature: 0.0` and a per-task `num_predict`.
**No `top_p`, no `seed`, no `num_ctx`, no `stop`.**

> **Follow-up, 2026-10-16.** Prompt 05 closed this for the seven calls in
> `agent/llm_client.py` (`decoding_options`), but the audit's path map
> above covers only that module. A second Ollama call site —
> `rag/llm.py::call_ollama`, behind the RAG subgraph, the orchestrator's
> router / web-search / media-search answer steps and the analyst planner
> — still built its own `{"num_predict", "temperature"}` dict and kept the
> gap. It now calls `decoding_options` too, so there is one decoding
> policy rather than two. Re-measured here on `llama3.1:8b`: without
> `num_ctx` the model is served at 4,096 and a long prompt was evaluated
> at **2,050 tokens with the system prompt gone** (reproducing §1's
> finding on the other call site); at `OLLAMA_NUM_CTX=16384` the same
> prompt was evaluated at 12,043 tokens with the system prompt intact.
> Unifying the window also stops Ollama reloading the model between a SQL
> call at 16,384 and a RAG/web call at the default.

---

## 3. Baseline metrics

| Metric | Value | Source |
|---|---|---|
| Served context window | **2,048 tokens** | measured against live Ollama |
| System prompt size | 2,016 tokens | `_system_prompt("mssql")` |
| Typical full prompt | 2,800–11,400 tokens | table in §1 |
| LLM calls per clean run | 5–7 | §2 |
| End-to-end `/ask` latency | 32–101 s | live runs, 4 questions |
| Backend suite | 5,875 passed | this session |
| Harness overhead | 0.0077 ms/step | `scripts/harness_stress_test.py` |

---

## 4. Reuse inventory — what already exists

| Capability | Where | Verdict |
|---|---|---|
| Orchestration state machine | `agent/graph.py`, 18 nodes | **Reuse, do not touch** |
| Schema retrieval + FK expansion | `embeddings/` | **Reuse** |
| Golden examples | `embeddings/golden_examples.py` | **Reuse** |
| Business-context retrieval | `retrieval/` | **Reuse** |
| SQL AST validation | `agent/sql_validator.py` | **Authoritative gate — do not touch** |
| Read-only execution + forced abort | `db/execution.py` | **Reuse** |
| Retry routing | `agent/graph.py` + `error_classification.py` | **Reuse** |
| Model routing, breakers, budgets | `model_routing/` | **Exists, off by default** |
| Result cache | `db/result_cache.py` | **Exists, off by default** |
| Execution control, budgets, cancellation | `agent/harness/` | **Exists (this session)** |
| Harness telemetry | `agent/harness/telemetry.py` | **Exists** |
| Performance metrics | `observability/metrics.py` | **Reuse** |
| Ollama token capture | `observability/llm_timing_capture.py` | **Reuse** |
| Evaluation harness | `eval/` (runner, evaluators, regression, baselines) | **Reuse, extend only** |
| Governance / privacy engine | `governance/` | **Do not touch** |
| Adversarial security tests | `tests/security/` | **Reuse** |

---

## 5. What will NOT be changed, and why

- **`agent/sql_validator.py`** — the authoritative safety gate. Prompt
  09 asks to harden *around* it, not replace it. Untouched.
- **The benchmark-pinned prompt text** — `_INSIGHT_SYSTEM_PROMPT` and
  the generation system prompt. CLAUDE.md §12: the accuracy numbers in
  `docs/EVALUATION_CURRENT.md` were measured against their exact
  wording, and this environment cannot re-run that benchmark. Budgeting
  may decide *whether* a block is included; it will not reword one.
- **`model_routing/`** — Prompt 03's objective, already implemented and
  stronger than what a fresh pass would produce. No second router.
- **`eval/`** — Prompt 15's objective, already substantial.
- **`governance/`, `identity/`, `security/`** — out of scope and
  explicitly protected by the contract.

---

## 6. Backlog — all 20 prompts, assessed

**Already satisfied; building anything would duplicate.** The contract's
hard constraint applies.

| Prompt | Objective | Existing implementation |
|---|---|---|
| 03 | Dynamic model routing | `model_routing/` — policy, breakers, budgets, fallback |
| 04 | Structured outputs | Pydantic `model_validate` on intent and analytical plan; sqlglot on SQL; fail-open parsers |
| 06 | Adaptive retries | `agent/error_classification.py` + `agent/harness/retry.py` (classes, backoff, jitter, 4 bounds) |
| 07 | Caching | `db/result_cache.py`, per-database, off by default |
| 09 | SQL guardrails | `agent/sql_validator.py` — AST allowlist, restricted columns, wildcard, system catalogs, hints |
| 12 | Concurrency / backpressure | `agent/rate_limit.py` + bounded pool + `agent/harness/` budgets and cancellation |
| 13 | Fallback / degraded modes | Established fail-open/fail-closed split (CLAUDE.md §4.2) + harness `FailureClass` |
| 14 | Observability | `observability/` + `agent/harness/telemetry.py` + `GET /metrics/harness` |
| 15 | Evaluation | `eval/` — runner, evaluators, regression, baselines, golden dataset |
| 16 | Adversarial testing | `tests/security/` — 50+ governance bypass cases, injection, cross-tenant |
| 19 | Release gate | `docs/PRODUCTION_READINESS_RELEASE_GATE.md`, `docs/HARNESS_PRODUCTION_READINESS.md`, blocking CI |

**Genuine, measured gaps.**

| Priority | Prompt | Gap | Evidence |
|---|---|---|---|
| **P0** | **02** | No character/token budget on the assembled prompt. Retrieval is capped by *count*, not *size*, so a wide schema silently overflows the window and the system prompt is what gets dropped. No observability of prompt size. | §1 |
| **P0** | **05** | `num_ctx` is never set, so every call runs at 2,048 tokens against a model trained for 131,072. No `seed`, so decoding is not reproducible even at `temperature=0`. | §1, §2 |
| P2 | 17 | Token/cost counters exist (`llm_timing_capture`, harness `token_estimate`) but nothing aggregates cost per *question* or exposes it. | §2 |
| P2 | 18 | No prompt or inference-config fingerprint, so a prompt change is not attributable in telemetry. | §2 |

**Partially satisfied; small additive deltas only.**

| Prompt | Gap |
|---|---|
| 08 | Assembly is already deterministic; what is missing is a version fingerprint (folded into 18) |
| 10 | `agent/plan_validator.py` and `agent.insight.is_insight_grounded` exist; no check comparing SQL against the retrieved schema's *actual* tables |
| 11 | Latency is profiled (`stage_timings`, `scripts/profile_pipeline.py`); the measured win is fewer truncation-driven retries, i.e. prompt 02 |

---

## 7. Implementation order

1. **05 first, not 02.** Setting `num_ctx` is a two-line change that
   immediately recovers ~8× the usable window. Doing it first means the
   budget in 02 is sized against a real window rather than a broken one.
2. **02** — budget, prioritize, truncate, and report, with safety-
   critical blocks protected from eviction.
3. **18/17** — fingerprints and cost aggregation, if the first two land
   cleanly.

Everything else: no change, per §6.

---

## 8. Risks

- **Raising `num_ctx` raises memory per Ollama request.** 16k context on
  an 8B model costs roughly 1–2 GB of additional KV cache. This must be
  an operator-tunable setting with a conservative default, not a
  hardcoded jump.
- **Prompt-size budgeting changes what the model sees**, which can move
  accuracy in either direction. The benchmark cannot be re-run in this
  environment, so any budget must default to a value that does not
  evict anything in the common case.
- **No accuracy claim will be made** without the benchmark, in either
  direction.
