# Performance: Measured Latency and What Actually Causes It

Measured 2026-10-22 on the reference machine this app is developed
against. Every number below is from a real run against the live SQL
Server and the live Ollama host — none is estimated.

**Headline: the 3–5 second target is not met for SQL answers on this
hardware, and no application change can make it so.** §2 gives the
arithmetic. §6 says what would.

---

## 1. Method

- `agent.graph.run_agent` driven in-process — the same function `/ask`
  calls — with `AgentState["stage_timings"]` captured per node.
- `ollama.Client.chat` wrapped to record, per call: prompt tokens,
  generated tokens, wall-clock, and `num_ctx`.
- Raw Ollama throughput measured separately at several prompt sizes to
  separate *prompt processing* from *token generation*.
- Prompt composition measured by intercepting the call and returning a
  canned response, so sections could be sized without paying for
  generation.

Reference machine: 16 logical CPUs, 31.7 GB RAM, **NVIDIA T1200 Laptop
GPU (4 GB VRAM)**, `qwen2.5-coder:7b`, `OLLAMA_NUM_CTX=16384`.

---

## 2. The root cause

### 2.1 Almost all latency is LLM inference

"Total sales by product", end to end:

| Stage | Time | Share |
|---|---|---|
| `generate_sql` | **86.17s** | 69.6% |
| `classify_analytical_intent` | **25.05s** | 20.2% |
| `generate_insight` | **10.04s** | 8.1% |
| `retrieve_schema` | 1.53s | 1.2% |
| business context + golden examples | 0.53s | 0.4% |
| `estimate_cost` | 0.19s | 0.2% |
| **`execute_sql`** | **0.05s** | **0.0%** |
| `enforce_display_names`, `validate_sql`, recommendations | 0.16s | 0.1% |
| **Total** | **123.77s** | |

A second run instrumented per call: **99.9% of wall-clock was inside
`ollama.Client.chat`**, 2.55s outside it.

**The database is not the bottleneck. It is 0.05 seconds — 0.04% of the
request.** Neither is schema retrieval, the vector store, validation, the
display-name guard, or chart building.

### 2.2 Why inference is slow: the model does not fit in VRAM

```
GET /api/ps
qwen2.5-coder:7b: total=5.84GB  vram=2.37GB  cpu=3.46GB  →  41% on GPU
```

**59% of the model runs on CPU.** Measured throughput, warm:

| | Rate |
|---|---|
| Prompt processing | **~185 tok/s** |
| Token generation | **~9.6 tok/s** |

So per model call:

```
latency ≈ prompt_tokens / 185  +  generated_tokens / 9.6
```

### 2.3 The prompt is large

`generate_sql`'s prompt measured **6,369 tokens / 26,529 characters**:

| Section | chars | ~tokens |
|---|---|---|
| Schema (7 tables) | 11,873 | **2,968** |
| System prompt | 8,060 | **2,015** |
| Trailing instructions + question | 5,614 | 1,404 |
| Retrieved business context | 910 | 228 |
| Reference SQL examples | 72 | 18 |

Applying §2.2's formula: `6369/185 + 63/9.6 ≈ 34s + 7s ≈ 41s` — the
measured steady-state cost. Observed values ranged 72–86s under
contention and 2,283s once while the machine was saturated, which is why
the formula, not any single run, is the thing to reason from.

### 2.4 Top three bottlenecks, with evidence

1. **The 7B model runs 59% on CPU** (`/api/ps`, §2.2). Sets the ~185/9.6
   tok/s ceiling that every other number derives from.
2. **`generate_sql`'s 6,369-token prompt** (§2.3). ~34s of prompt
   processing before a single token is generated.
3. **Model calls that cannot change the answer.**
   `classify_analytical_intent` cost 12–25s on every question; for a
   question with no complexity signal its result provably changes
   nothing (§3.1).

---

## 3. What was changed

### 3.1 Skip the analytical-intent call when it cannot matter

`classify_analytical_intent_node` ran on **every** question — explicitly
not gated by complexity. Its own docstring states that when
`analytical_intent` is `None`, `plan_query_node`'s gate "reduces to
exactly `agent.complexity`'s signals alone, byte-identical to this
node's pre-Prompt-11 behavior."

So for a question with no complexity signal, the call costs 12–25s to
reach a state the gate would have reached anyway. It is now skipped
there, and still runs for a question carrying any signal — where it
earns its cost.

**Measured:** 3 model calls → **2**, and 7,415 → 6,908 prompt tokens.

| | Before | After |
|---|---|---|
| Model calls | 3 | **2** |
| `classify_analytical_intent` | 12–25s | **0s** |
| Total (same question, same machine) | 123.77s | **80.08s** |

Reverted with `SKIP_INTENT_CLASSIFICATION_FOR_SIMPLE_QUESTIONS=false`.

### 3.2 Deterministic source routing already avoids fan-out

This deployment runs `ENABLE_MULTI_SOURCE_ROUTER=true` **and**
`ROUTE_TO_ALL_SOURCES=true` with six sources available (sql, documents,
policy, web, generation, media_search). That setting fans an unselected
question out to sql + documents + policy + web — exactly the
"search everything for every question" pattern to avoid.

Measured on the real orchestrated path:

```
'Total sales by product'
  routed to      : ['sql']
  short_circuited: True
  reasoning      : deterministic source signals were conclusive
                   (threshold=0.70 confident=['sql'] scores=sql=0.90 ...)
```

The deterministic router (`agent/orchestrator/source_signals.py`) resolves
the question before the fan-out applies, so three source legs — each with
its own model call — never run. This was built for routing accuracy; it
is also a latency control, and the measurement is recorded here because
nothing else documented that.

### 3.3 Optional schema ceiling

Schema is the largest prompt section (2,968 tokens, 7 tables) and is
larger than `SCHEMA_TOP_K=4` implies, because relationship expansion adds
tables. `MAX_SCHEMA_TABLES_IN_PROMPT` caps it after retrieval, so the
best-matching tables are kept and the weakest dropped — roughly 2s per
table removed.

**Off by default (`0`).** Dropping a table the question needs turns a
slow correct answer into a fast wrong one; an operator opts in once they
know their schema fits.

---

## 4. Where the target stands, per source

Honest per-source status against "a useful first response in 3–5s":

| Source | Status | Evidence |
|---|---|---|
| **Progress stream** | **MET — 0.025s measured** | Measured directly against a live `/ask/stream` run of "Total sales by product": first event `stage=sanitize_input` at **0.025s**, 19 events over the run, with the pre-generation stages all landing by **2.57s** (`retrieve_schema` 1.91s, `retrieve_golden_examples` 2.22s, `retrieve_business_context` 2.57s, `classify_analytical_intent`/`build_analytical_plan`/`plan_query` 2.57s). `generate_sql` then returned at 103.18s on that run. |
| **Routing decision** | **MET** | Deterministic router resolves without a model call for an unambiguous question |
| **Database execution** | **MET** | 0.05s measured |
| **SQL answer (first useful result)** | **NOT MET — ~80s** | §2.2's formula; 6,369-token prompt at 185 tok/s |
| **Documents / policy RAG** | **not measured here** | The RAG store is not configured on this machine; the same per-call formula applies to its answer step |
| **Web search** | **not measured here** | External provider latency dominates; not exercised |
| **Media generation** | **already async** | Runs through the generation node with an approval gate; `POST /questions` makes the whole question async |

So **every stage before generation finishes inside the 3–5s window**,
which is what makes the progress stream a real answer to "meaningful
progress within 3–5 seconds" rather than a spinner. The answer itself
does not.

**The SQL path is ~16x over target, and the gap is hardware, not code.**
Even reducing the prompt to the 2,015-token system prompt alone — which
would mean sending no schema, making the task impossible — leaves
`2015/185 ≈ 11s`. §6 measures the best configuration available on this
machine at **~30s**, still ~6x over target.

---

## 5. Settings

| Variable | Default | Effect |
|---|---|---|
| `TARGET_FIRST_RESPONSE_SECONDS` | `5.0` | What this document reports against. Advisory — it does not cancel work, because cutting a correct answer off at N seconds trades accuracy for a number. |
| `SKIP_INTENT_CLASSIFICATION_FOR_SIMPLE_QUESTIONS` | `true` | §3.1. `false` restores always-classify. |
| `MAX_SCHEMA_TABLES_IN_PROMPT` | `0` (off) | §3.3. Caps tables rendered into the prompt. |

Existing settings that matter more than any of the above on this
hardware: `OLLAMA_MODEL`, `SCHEMA_TOP_K`, `ENABLE_INTENT_CLASSIFICATION`,
`ENABLE_QUERY_PLANNING`, `ENABLE_ANALYTICAL_PLANNING`,
`ENABLE_RESULT_CACHE` (currently **off** — turning it on makes a repeated
question free).

---

## 6. Infrastructure recommendation

**Use a model that fits in 4 GB of VRAM.** This is the only change that
moves the SQL path toward the target, and it is measured, not assumed:
41% GPU residency is the direct cause of 9.6 tok/s.

**Measured**, same ~2,650-token prompt, same machine:

| Model | GPU residency | Prompt | Generation |
|---|---|---|---|
| `qwen2.5-coder:7b` (current) | 41% (2.37/5.84 GB) | 93.3 tok/s | 8.4 tok/s |
| `llama3.2:3b` | 51% (2.26/4.40 GB) | **188.3 tok/s** | **16.1 tok/s** |

Prompt processing doubles and generation roughly doubles.

### `OLLAMA_NUM_CTX` is itself a VRAM cost

The 3B model *still* only reached 51% residency, because the KV cache at
`num_ctx=16384` is larger than the weights. Lowering it measurably moves
the model onto the GPU:

| `num_ctx` (llama3.2:3b) | GPU residency | Prompt | Generation |
|---|---|---|---|
| 16384 | 51% (2.26/4.40 GB) | — | 14.5 tok/s |
| 8192 | **67%** (2.30/3.41 GB) | 189.8 tok/s | **23.7 tok/s** |
| 4096 | **80%** (2.35/2.94 GB) | 196.0 tok/s | **34.1 tok/s** |

Generation more than doubles from 16384 → 4096. Prompt processing stays
~190 tok/s throughout: it is compute-bound, not memory-bound.

**`num_ctx=4096` cannot hold this app's 6,369-token prompt**, so 8192 is
the usable setting. (A `num_ctx` change also forces a model reload —
keep it uniform across callers.)

### What this adds up to

Applying §2.2's formula to the 6,369-token prompt:

| Configuration | Prompt | Generation | Total |
|---|---|---|---|
| 7B @ 16384 (today) | 68s | 7.5s | **~76s** |
| 3B @ 8192 | 34s | 2.7s | **~36s** |
| 3B @ 8192 + `MAX_SCHEMA_TABLES_IN_PROMPT=4` | 27s | 2.7s | **~30s** |

So the realistic ceiling on this hardware is **~30s**, about 4x better
than today's 123.77s — and still roughly **6x over the 3–5s target**.

Prompt processing is what remains, and it is bounded by compute. Reaching
3–5s would need the prompt down to ~500–900 tokens, which means sending
no schema and makes the task impossible. The only route to the target is
a GPU that holds weights *and* KV cache entirely — at which point prompt
throughput typically rises by an order of magnitude rather than a factor
of two.

Other options, in rough order of value:

1. **Switch to `llama3.2:3b` with `OLLAMA_NUM_CTX=8192`** — measured
   above, ~2x on both axes, no code change. Validate accuracy against
   `docs/EVALUATION_CURRENT.md`'s benchmark before adopting: a 3B model
   is weaker at SQL than a 7B coder model, and this document makes no
   accuracy claim about it.
2. **A GPU with ≥8 GB VRAM**, which would hold a 7B model *and* its KV
   cache entirely and remove the CPU fallback. This is the only option
   that plausibly reaches the 3–5s target.
3. **`ENABLE_RESULT_CACHE=true`** — makes a repeated question free. It is
   off today.
4. **`SCHEMA_TOP_K` tuning** — each table removed is ~2s.
5. **Keep `OLLAMA_NUM_CTX` stable across callers.** A changed `num_ctx`
   forces Ollama to reload the model: the same prompt measured 5.7s at a
   stable context and 13–14s when the context varied between calls.

---

## 7. What was deliberately not done

- **The generation system prompt was not shortened.** It is 2,015 tokens
  and benchmark-pinned (CLAUDE.md §12): `docs/EVALUATION_CURRENT.md`'s
  accuracy numbers were measured against its exact wording. Trading
  unmeasured accuracy for ~11s is not a trade to make silently.
- **No timeout was added that cancels a slow-but-correct answer.** The
  target is a goal to engineer toward, not a deadline to truncate
  results at.
- **No guardrail, validator, authorization, grounding or audit control
  was weakened** (brief §12). `tests/test_latency_optimizations.py`
  asserts the SQL validator takes no settings at all, so no latency knob
  can reach it.

---

## 8. Test coverage

`tests/test_latency_optimizations.py` — 19 tests. Call *counts* and
prompt *composition* are asserted rather than wall-clock, which is
machine-dependent and would be flaky:

- a simple question makes **no** intent model call;
- a complex question (`top 5 per region`, `year over year growth`) still
  classifies;
- both the skip and the feature flag can be turned off;
- signals are derived when absent from state;
- the schema ceiling is off by default and rejects unsafe values;
- the latency budgets reject zero, negative and non-numeric values;
- skipping the call returns only the intent and status, so it cannot
  widen a permission or a retry budget;
- the SQL validator remains unreachable from any latency setting.
