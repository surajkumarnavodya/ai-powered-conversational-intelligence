# Intent-based source routing

How a question reaches the right source, what it costs, and what to
configure. Authoritative for routing behaviour; the orchestrator's
node-by-node walkthrough stays in
[`docs/MULTI_SOURCE_GUIDE.md`](MULTI_SOURCE_GUIDE.md).

---

## The problem this fixed

Asking **"Sick leave policy"** took 117 seconds and answered:

> Agent stopped at SQL generation · 32.84s in 5 stages
> This question was not answerable from the connected data, so no query
> was generated.
> **Database**: 0 row(s) returned.

…above the real answer from the policy store.

The router was not the problem. It classified the question as `policy`
correctly. `ROUTE_TO_ALL_SOURCES` — on by default — then added `sql`,
`documents` and `web` anyway, so the SQL pipeline ran for 30.6s, refused,
and the refusal was rendered.

Three things changed:

1. **`ROUTE_TO_ALL_SOURCES` now defaults to `false`**, so a routing
   decision is honoured instead of overridden.
2. **The router returns a structured decision**, not a bare source list.
3. **Failed stages and per-source labels are no longer rendered** to a
   normal caller.

---

## The decision

`prompts/router.md` asks for JSON; `RouterDecision`
(`agent/orchestrator/router_contract.py`) validates it:

| Field | Meaning |
|---|---|
| `primary` | The single best source. `synthesis_node` leads with it. |
| `secondary` | Additional sources, only when genuinely needed. |
| `fallback` | What to try if the primary finds nothing useful. |
| `confidence` | 0–1. **Evidence, not a calibrated probability.** |
| `needs_clarification` / `clarifying_question` | One question, when the ambiguity would change the answer. |
| `source_queries` | The question rewritten per source. |
| `answer_style` | `quick_fact`, `briefing`, `explanation`, `comparison`, `data_table`, `creative`. |
| `reason` | One sentence, for logs. |

The prompt's vocabulary (`sql_database`, `policy_kb`, `user_documents`,
`web_search`, `media_search`, `image_generation`, `video_generation`,
`general_knowledge`, `conversation`) is translated to the graph's own
source names by `ROUTED_SOURCE_NAMES`. `general_knowledge` and
`conversation` map to **no source** — they are real outcomes that run no
retrieval at all.

**A decision is a request, never a grant.** `router_node` still applies
the RBAC filter, the session cost ceiling for paid sources, and the
attachment-forcing rule *after* parsing it (CLAUDE.md §4.3, §5.2).

### Three layers, in order

1. **Deterministic signals** (`source_signals.py`) — zero model calls.
   A question that names its own destination short-circuits here.
2. **The model**, against `prompts/router.md`. Invalid JSON is retried
   **once**; a fenced or prefixed reply is recovered rather than
   discarded.
3. **`keyword_fallback`** — pure regex, no model. Runs when the call
   fails, times out, or returns unusable JSON twice.

---

## Measured cost, and why the fallback matters here

On the reference machine (NVIDIA T1200, 4 GB — see
[`docs/PERFORMANCE.md`](PERFORMANCE.md)), measured 2026-10-22:

| Model | Routing latency | Correct on the brief's cases |
|---|---|---|
| `qwen2.5-coder:7b` (current `OLLAMA_MODEL`) | **31–40s** | yes, on all four sampled |
| `llama3.2:3b` | **22–27s**, one outlier at 1504s | **no** — sent "Top news of India today", "What is a foreign key?" and "Generate an image…" all to `policy_kb` |
| `keyword_fallback` | **<1ms** | yes, on all 12 brief cases |

**`ROUTER_TIMEOUT_SECONDS` defaults to 5.0, so on this hardware the model
call times out and the keyword fallback routes every question.** That is
the intended outcome, not a defect: the fallback is correct on every case
the brief names and costs nothing, while 35s of routing in front of a
question would recreate the latency problem this work set out to remove.

The LLM router is there for deployments whose hardware can meet the
budget, and for the questions regex cannot read. To actually use it,
raise `ROUTER_TIMEOUT_SECONDS` past your measured routing latency — and
measure first.

A smaller model is **not** the lever it appears to be: `llama3.2:3b` was
both slower than hoped and materially worse at the task.

---

## The documents/policy pairing

`documents` and `policies` hold the same *kind* of thing — uploaded text
— split by an upload-time flag, not by subject. Someone asking a question
has no way of knowing which of the two their PDF landed in. So when the
route contains exactly one of them, the other is added deterministically
(`router_node`, before the authorization filter).

**This is not optional, and it was briefly lost.** It was added by
`07dbfd6`, removed without mention by `40c9100`, and nobody noticed
because `ROUTE_TO_ALL_SOURCES` was on at the time and ran both
collections anyway. Defaulting the fan-out off turned its absence into a
live failure, caught end-to-end: "Sick leave policy" routed to `policy`
alone and answered *"I couldn't find any relevant information"* with the
leave-policy PDF sitting unread in `documents`.

It is now pinned by
`test_the_text_collections_are_paired_with_the_fan_out_off`, so a future
refactor cannot drop it silently again.

It applies **before** the permission check, so it can put a source on the
route but never past authorization — a caller without the policy
permission still has it dropped. An explicit source selection is
untouched: narrowing to Documents by hand is an instruction, not a guess.

---

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `ROUTE_TO_ALL_SOURCES` | `false` | `true` restores the old fan-out to every retrieval source. |
| `ROUTER_MODEL` | `""` (→ `OLLAMA_MODEL`) | Which model routes. |
| `ROUTER_MAX_TOKENS` | `400` | Cap on the routing reply. |
| `ROUTER_TIMEOUT_SECONDS` | `5.0` | Budget before falling back to keywords. |
| `SHOW_PIPELINE_DEBUG` | `false` | `true` renders failed/errored stages again. |

### `SHOW_PIPELINE_DEBUG`

Off by default. With it off, `api.main._stage_timings_out` **withholds**
stages whose outcome was `error`, so the dashboard's "Agent stopped at…"
banner — which is derived from exactly those rows — never appears.

Withheld **server-side**, not hidden in the client: a UI restriction is
never a boundary in this codebase. The detail stays in the logs and in
the structured trace regardless; this only decides what is serialized.

---

## Answer composition

`synthesis_node` produces **one merged answer**, not labelled sections.
The `**Database**: …` / `**Policy**: …` prefixes are gone: which sources
contributed is carried by `sources_used`, which the dashboard already
renders as chips, so a prefix per paragraph was a second owner of the
same fact (CLAUDE.md §4.8).

A zero-row SQL result no longer contributes a line at all. A query that
runs cleanly and matches nothing is a *successful* query, and announcing
"0 row(s) returned" above another source's answer describes the pipeline
rather than the question. The count is still in `row_count` for the
result table and the debug view.

When `route_decision["primary_source"]` is set, the primary source's
contribution leads.

---

## Known gaps

- **The LLM composer in the brief's Part 5 is not implemented.** Merging
  is deterministic: primary first, blank-line separated, no labels. An
  LLM merge would add one model call per multi-source answer — 30–80s on
  this hardware — to a change whose purpose was to make answers faster.
  `answer_style` is carried through state and ready for it.
- **`needs_clarification` is parsed and carried but not yet acted on.**
  The orchestrator does not currently short-circuit to ask the question.
- **SQL preflight (`SET PARSEONLY ON`, `sp_describe_first_result_set`) is
  not implemented.** The existing AST validator, the double row cap and
  the query timeout (SECURITY.md) are unchanged and still apply.
- **Per-source `source_queries` are parsed but not yet threaded into each
  subgraph** — every source still receives the full question, which is
  the pre-existing behaviour noted in CLAUDE.md §13.
