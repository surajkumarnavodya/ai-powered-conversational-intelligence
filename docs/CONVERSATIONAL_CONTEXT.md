# Conversational context resolution

Authoritative for: how an incoming message is read against the
conversation before it, what is carried into it, and what is deliberately
not. Owns the decision table, the thresholds, the ambiguity rule, and
this feature's limitations.

For *where* the resolution is used, the per-node walkthrough in
[`ARCHITECTURE.md`](ARCHITECTURE.md) remains authoritative. For the
settings themselves, [`CONFIGURATION.md`](CONFIGURATION.md) is.

---

## 1. The gap this closes

Follow-up handling was a yes/no gate with a hardcoded antecedent.
`agent.followup.classify_followup` decided *whether* a question pointed
outward; `agent.nodes.classify_followup_node` then bound the antecedent
to `conversation_history[-1]` unconditionally.
`agent.input_guard.sanitize_conversation_history`'s own docstring
recorded the consequence in as many words: **"only the single most recent
entry is ever functionally read downstream."**

Three things followed from that, each a real defect rather than a missing
nicety:

1. **The question text was never rewritten.** `state["question"]` stayed
   the raw `"Price?"` all the way into schema retrieval, the router
   prompt and the generation prompt. `retrieve_schema_node` concatenated
   the prior question for the *embedding only*, and nothing else saw any
   context at all. `prompts/router.md` asks for `{question}` under the
   label "(standalone form)" — a contract written before anything
   produced one.
2. **Only the last turn could be the antecedent.** Returning to an
   earlier topic was unreachable by construction, and a reference with
   two plausible referents resolved silently to the most recent one
   instead of asking which was meant.
3. **Nothing tracked constraints.** "I meant ₹60,000" could only ever be
   a new question, never a replacement of a budget stated earlier.

---

## 2. The two modules, and the split between them

| Module | Owns |
|---|---|
| `agent/conversation_memory.py` | **The facts.** What a transcript establishes: entities named, constraints stated and not retracted, a topic label per turn, and a quoted digest of turns outside the resolution window. |
| `agent/context_resolution.py` | **The decision.** How this message relates to those facts, which turn it resolves against, what its standalone form is, and whether to ask instead. |

The split is what makes each testable in isolation: extraction is checked
against a transcript with no classifier in the loop
(`tests/test_conversation_memory.py`), and the decision against a
hand-built snapshot with no regexes in the loop
(`tests/test_context_resolution.py`).

### Memory is extracted, never inferred

Everything in a `MemorySnapshot` is pattern-extracted from text the
caller already sent. That is not a cost compromise — it is what makes the
result safe to carry forward. A model-written summary can invent a
constraint nobody stated, and a fabricated "budget: 60,000" silently
narrowing a later answer is worse than no memory at all. So the digest of
older turns is a **quotation**, shortened, never a paraphrase (CLAUDE.md
§4.4, and §6's "never invent missing historical information").

The one thing extraction does claim is relevance, and it can be wrong
about that. Everything the snapshot produces is therefore
`AI_INFERENCE`-grade and is an accuracy aid that fails open everywhere it
is consumed (CLAUDE.md §4.2).

---

## 3. Relations

`ContextRelation` has seven values. The first two existed before, as
`"standalone"` and `"followup"`; the rest were previously collapsed into
one of those or into `"ambiguous"`, which is why a correction and a
refinement both used to behave like a brand-new question.

| Relation | Means | Example |
|---|---|---|
| `new_topic` | Self-contained. Nothing is carried. | "What is the price of crude oil?" |
| `followup` | Cannot be understood without a prior turn. | "Price?", "What is its price?", "What if costs rise 15%?" |
| `refinement` | Acts on a previous *answer*. | "Make it more professional.", "Expand day two.", "Exclude cancelled orders." |
| `correction` | Replaces something the person said earlier. | "I meant ₹60,000." |
| `topic_return` | Points at something older than the last turn. | "Back to that CORS issue from earlier" |
| `ambiguous` | Points outward, at two or more plausible turns. | "What is its price?" after two phones were named |
| `missing_context` | Points outward with nothing to point at. | "Now break that down by month" as the first message |

`legacy_classification()` maps all seven onto the three values
`AskResponse.followup_classification` has always carried. That field's
`Literal` is a frozen, widely-consumed contract and the frontend switches
on it, so the richer relation travels in its own additive field
(`AskResponse.context_resolution`) rather than widening the old one
(CLAUDE.md §6.3).

---

## 4. The decision, in order

Each step below short-circuits. Evaluated in `resolve_context`.

1. **Explicit reset** (`"forget that"`, `"new topic:"`, `"never mind"`) —
   `new_topic`, the reset clause stripped out so what reaches retrieval
   is the actual request. Outranks every other signal in the message,
   including a pronoun inside the part being dropped.
2. **No eligible history** — an outward-pointing message with no subject
   of its own is `missing_context`; one with no subject and no outward
   signal is `ambiguous`; anything else is `new_topic`.
3. **No question at all** — no subject of its own *and* no attribute of
   anything else (`"why"`, `"more"`) is `ambiguous`, whether or not there
   is history. There is nothing here to resolve, so supplying a subject
   would mean inventing the question. See §5.
4. **Not pointing outward** — `new_topic`, nothing carried. This is the
   "crude oil after the Pixel 10a" case, and it turns on the question
   having its own subject, never on it being long or well-formed.
5. **Competing referents** — two or more candidate turns the question
   gives no reason to choose between is `ambiguous`, with a clarifying
   question naming them. See §6.
6. **Otherwise** — `correction` > `topic_return` > `refinement` >
   `followup`, in that precedence, against the best-scoring antecedent.

### What counts as "pointing outward"

Any of: a referring word including possessives (`its`, `that`, `them`);
an anaphoric discourse marker (`instead`, `again`, `another`); a scenario
marker (`what if`, `suppose`, `assuming`); a topic-return phrase; a
refinement verb; a correction marker; or **no subject of its own plus an
attribute word**.

That last one is the general rule, and it is what replaces a keyword
list. Strip referring words, function words and attribute words from the
message; what remains is the message's own subject:

```
"Price?"                            -> []             depends on context
"Show an example."                   -> []             depends on context
"What is its price?"                 -> []             depends on context
"What is the price of crude oil?"    -> [crude, oil]   stands alone
"Explain database normalization."    -> [database, normalization]
```

So shortness is not the signal — "depends on context to mean anything"
is. A long message can be a follow-up and a short one a new topic, and
both directions are pinned by test.

---

## 5. Attribute vs. contentless

`"Price?"` and `"why"` are both subject-less, and they are **not** the
same case.

- `"Price?"` names an *attribute*. There is a question here; it only
  needs its subject supplied. Resolvable.
- `"why"` names nothing, not even an attribute. There is no question to
  rewrite, so resolving it would mean inventing one. Ask instead.

`agent.followup` could not draw this distinction, and
`tests/test_agent_nodes.py::TestClassifyFollowupNode::test_bare_fragment_needs_clarification_even_with_history`
has pinned the `"why"` half of it since before this feature existed. That
test is why `_has_attribute_word` exists.

---

## 6. Antecedent selection and ambiguity

Every turn in the window is scored on three components:

| Component | Weight | What it is |
|---|---|---|
| lexical | 0.40 | Fraction of the question's own content words present in the turn |
| entity | 0.35 | Fraction of the turn's entities the question names |
| recency | 0.25 | How recent the turn is |

Recency is deliberately the weakest. Taking the most recent turn
unconditionally is the behaviour this feature replaces, so recency breaks
ties rather than deciding. For an explicit topic-return it is *inverted*,
because "going back to the first one" is by construction not about the
latest turn.

**`evidence = max(lexical, entity)` is the load-bearing quantity**, not
the combined score. Lexical and entity overlap are evidence *from the
question*; recency is a prior about conversations in general. A leader
with `evidence == 0.0` was chosen by recency alone, which is exactly when
it is worth either asking the user or asking the model.

Two candidates are **competing** when both hold:

- their `evidence` is within `_AMBIGUITY_TIE_MARGIN` (0.15) — so two
  candidates with *no* evidence are tied at zero, which is the ambiguous
  case;
- their entity sets are **disjoint and non-empty** — because two turns
  about the same subject are not competing referents however they score.

That second condition is what keeps an ordinary refinement chain ("top
products", "now by month", "now for 2013") out of the ambiguity path:
every turn in it shares entities, so there is only one referent to pick.

> **Why evidence and not score.** Comparing on the combined score could
> never fire. With recency weighted at 0.25 and spread across the window,
> two candidates backed by nothing still differed by 0.25 in total score —
> more than any tie margin — so the check was dead for the exact case it
> exists for (two products named, then "What is its price?"). Found by
> test.

Three thresholds here are **judgement calls, not measurements**:
`_AMBIGUITY_TIE_MARGIN` (0.15), the three component weights, and
`CONTEXT_RESOLUTION_MIN_CONFIDENCE` (0.60). They lean toward answering
rather than asking, because an unnecessary clarifying question is a
visible annoyance while a silently wrong referent is a wrong answer. This
is the same class of threshold as `SOURCE_CONFIDENCE_THRESHOLD` and
`guardrails.grounding.MIN_LEXICAL_OVERLAP`, with the same caveat:
**`ContextResolution.confidence` is evidence, never a probability that
the answer is correct.**

---

## 7. The rewrite

Two mechanisms, in order:

1. **Pronoun substitution**, when the antecedent names exactly one entity
   worth substituting. `"What is its price in the US?"` becomes
   `"What is Google Pixel 10a's price in the US?"` — genuinely natural
   standalone text, which is what makes it useful to a web search and an
   embedding alike. A table name is never substituted: it is a database
   fact, not a subject the person named.
2. **An explicit context clause**, when there is no pronoun to substitute
   or no entity to substitute it with. `"Price?"` becomes
   `"Price (regarding: Tell me about the Google Pixel 10a)?"`. Less
   elegant, but it is a *quotation* of the prior turn rather than a guess
   at what was meant.

Constraints still in force are appended once, as a clause, so a scenario
question carries the figures it varies.

**The original is never replaced.** `ResolvedContext` holds
`original_question` and `resolved_question` side by side;
`state["question"]` is untouched, and the transcript, the chat bubble and
the audit log all read it. `agent.nodes.retrieval_question` is the single
owner of "which form does downstream work use", and returns the raw
question whenever nothing was resolved — so a call site reads it
unconditionally and never needs to know whether resolution ran.

### Where the resolved question goes

| Consumer | Why |
|---|---|
| `retrieve_schema_node` | Embedding a terse follow-up matches no table |
| `retrieve_golden_examples_node`, `retrieve_business_context_node` | Same, both are similarity searches |
| `classify_analytical_intent_node`, `plan_query_node`, `build_analytical_plan_node` | All read the question text |
| `generate_sql_node` | The fix this feature exists for |
| `generate_insight_node` | Reads the question text |
| `router_node` → `classify_sources_structured` | Routing from raw text sent "Price?" to general knowledge |
| `web_search_node`, `document_rag_node`, `policy_rag_node`, `media_search_node`, `generation_node`, `attachment_node` | Each is a retrieval against the question |
| `model_routing`'s `assess_request` | A terse follow-up assessed on raw text looks trivial |

---

## 8. Two tiers, and when the second runs

**Tier 1 is deterministic** and handles the overwhelming majority. No LLM
call, so an ordinary follow-up is exactly as fast as it was.

**Tier 2 is one small `ModelTask.ROUTER` call**, and runs only when tier
1's confidence is below `CONTEXT_RESOLUTION_MIN_CONFIDENCE` — including
on the ambiguous path, which is the single case a model is most likely to
settle correctly from the transcript.

**It is off by default** (`ENABLE_CONTEXT_RESOLUTION_LLM=false`), for two
measured reasons:

1. **11.1s**, measured 2026-10-24 against the running Ollama server: one
   escalation on a two-candidate reference, on the user's latency path,
   before any schema retrieval or generation had started. That is the
   same ceiling [`PERFORMANCE.md`](PERFORMANCE.md) records for every
   local model call on this hardware, and it is why every other
   extra-model-call feature here is opt-in too.
2. It would put a network call inside the test suite. CLAUDE.md §7
   requires every pytest test to be fully mocked — no real DB, no Ollama
   — and this setting is read by a node many tests exercise directly.
   Defaulting it on made those tests reach the real server; that is how
   the measurement above was taken.
   `tests/test_context_resolution_integration.py::TestRollbackSwitch::test_no_resolution_path_calls_ollama_by_default`
   is the non-vacuity guard against the default drifting back.

### The model proposes; it never decides

Everything tier 2 returns is re-validated against the real window before
it is used, and anything that does not check out falls back to tier 1's
value rather than failing the resolution (CLAUDE.md §4.3):

| The model returns | What happens |
|---|---|
| An unknown `relation` name | Tier 1's relation is kept |
| An `antecedent_turn` outside the window | Tier 1's antecedent is kept |
| A `standalone_question` that drops the question's own content words | Discarded — this is what stops a small local model *answering* in the rewrite field, which it does occasionally |
| Unparseable output, or none | Tier 1's whole result, logged |
| An exception of any kind | Tier 1's whole result, logged |

The prompt lives at `prompts/context_resolution.md`, beside
`prompts/router.md` and for the same reason: it is this app's own file on
disk, so an operator can tune it, and nothing a user or a retrieved
document can write reaches it (CLAUDE.md §5.5).

---

## 9. Security

### A rewrite widens the ask, so it is re-governed

`classify_followup_node` runs the resolved question back through
`governance.request_policy.evaluate_request` whenever it differs from the
typed text. A follow-up whose *resolved* meaning is a prohibited
individual-level question — "and their salary?" against a turn that named
one employee — is refused on that meaning, through the same rejection
path `sanitize_input_node` uses.

**This check fails closed. The rewrite itself fails open.** The two
directions are not in tension: a rewrite that cannot be produced leaves
the previous behaviour, and one that is produced is judged on its merits
(CLAUDE.md §4.2 / §5.4). It is skipped entirely when nothing was
rewritten, so the common path pays for no second policy evaluation.

### History is untrusted data

Prior turns are normalized and injection-scanned by
`sanitize_conversation_history` before resolution sees them — including
the new `answer` field, which gets the identical treatment for the
identical reason (finding LLM-01). Nothing in either module treats their
text as instructions: `render_memory_block` and the tier-2 prompt both
frame the transcript as a record of what was typed, the same framing
`_build_business_context_block` uses.

### A clarification is not an oracle

Clarifying questions are built only from quoted topic labels, and the
fallback is a fixed module constant. A clarification never names the
signal that matched, so it cannot be used to probe the classifier
(CLAUDE.md §5.5).

### Isolation

`resolve_context` is pure with respect to the application: it reads no
settings, opens no connection and touches no module state. The window it
sees is the one its caller passed, which comes from
`AskRequest.conversation_history` for that request alone. There is no
store or cache it could reach another user's conversation through, and
`require_owned_conversation` already refuses a borrowed
`conversation_id` before any history is read.

Purity is the mechanism, so the test that matters is the one that would
fail if a later change gave up on it.
`tests/test_context_resolution_lifecycle.py::TestConcurrentConversationsDoNotCross`
resolves the **identical** message ("Price?") in four conversations at
once, from real threads released together by a `threading.Barrier`, and
asserts each result names its own subject and no other's. The message
text being identical is the point: only the window differs, so a
memoization keyed on the message alone — the obvious way to lose this
property — returns one conversation's answer to all four.

**Mutation-verified.** Adding a process-global
`dict[str, ResolvedContext]` keyed on `question` in front of
`resolve_context` fails 5 of that file's 13 tests: the concurrency case
and all four editing cases. Reverting restores 13 passed.

---

## 9a. Measured cost

Deterministic resolution only (tier 2 off, which is the default).
Measured 2026-10-24 on this machine, 400 iterations per case, median and
p95:

| Case | median | p95 |
|---|---|---|
| First turn, no history | 0.029 ms | 0.062 ms |
| New topic, 1 prior turn | 0.102 ms | 0.128 ms |
| Follow-up, 1 prior turn | 0.093 ms | 0.121 ms |
| Follow-up, 6-turn window | 0.371 ms | 0.753 ms |
| Follow-up, 20 turns sent (window capped at 6) | 0.391 ms | 0.437 ms |

Sub-millisecond throughout, and flat in how much history the client
sends — the last two rows differ by 0.02 ms because
`CONTEXT_RESOLUTION_RECENT_TURNS` bounds the scoring, not
`MAX_CONVERSATION_HISTORY_TURNS`. Against the 10–300 s a single local
model call costs on this hardware ([`PERFORMANCE.md`](PERFORMANCE.md))
this is not a measurable addition to request latency.

**Prompt cost.** The memory block for a 20-turn conversation renders to
627 characters (~156 tokens), bounded by the entity, constraint and
digest caps in `agent/conversation_memory.py`. It enters the generation
prompt at `SectionPriority.FOLLOWUP`, so `agent.context_budget` drops it
before any schema table when the prompt is tight. Tier 2, when an
operator enables it, adds one `ROUTER_MODEL` call capped at
`CONTEXT_RESOLUTION_MAX_TOKENS` (256) — see §8 for the 11.1 s
measurement.

These are wall-clock measurements of the resolver, not a measurement of
whether its answers are *right*; §11's last bullet is explicit that no
labelled-corpus accuracy figure exists.

---

## 10. Settings

| Setting | Default | Effect |
|---|---|---|
| `ENABLE_CONTEXT_RESOLUTION` | `true` | The rollback switch. `false` restores the previous behaviour exactly — the regex-only classifier, the antecedent bound to `conversation_history[-1]`, and no rewrite, so `resolved_question` stays `None` and every reader falls back to the raw question. Exercised by test, not merely present. |
| `CONTEXT_RESOLUTION_RECENT_TURNS` | `6` | How many recent **successful** turns are eligible to be the antecedent. Turns older than this are quoted into the digest, not dropped. |
| `ENABLE_CONTEXT_RESOLUTION_LLM` | `false` | Tier 2. See §8. |
| `CONTEXT_RESOLUTION_MIN_CONFIDENCE` | `0.60` | Below this, tier 2 is consulted if enabled. |
| `CONTEXT_RESOLUTION_MAX_TOKENS` | `256` | Tier 2's generation cap. One JSON object. |

`MAX_CONVERSATION_HISTORY_TURNS` (20) is unchanged and is a different
bound: the resource cap on the whole caller-supplied array, not the
resolution window.

---

## 11. Measured accuracy

`eval/context_resolution_benchmark/` scores the resolver against a
hand-labelled corpus of 65 cases, and unlike `eval/benchmark/` it needs
**no infrastructure** — `resolve_context` is a pure function of (message,
history), so the whole corpus runs in milliseconds. That is why
`tests/test_context_resolution_benchmark.py` asserts on it in CI, and why
routing/resolution finally has a measured figure where CLAUDE.md §13
recorded none.

```
python -m scripts.run_context_resolution_benchmark
python -m scripts.run_context_resolution_benchmark --json
```

Measured 2026-10-24, deterministic tier only (tier 2 is off by default,
so this is what users actually get):

| Metric | Value |
|---|---|
| cases | 65 |
| exact match (relation + antecedent + required/forbidden substrings) | 100% |
| macro F1 over the seven relations | 1.000 |
| antecedent accuracy | 100% |
| entity resolution | 100% |
| **context leakage** | **0%** (target 0) |
| new-topic accuracy | 100% |
| clarification accuracy | 100% |
| latency median / p95 | 0.09–0.15 / 0.19–0.37 ms |

**Read that honestly.** The corpus was authored, it surfaced seven real
defects, those were fixed, and it now scores 100%. That is what a corpus
does — it is not evidence of perfection on unseen phrasings. The value is
in what it found and in the two metrics whose target is absolute (zero
leakage, 100% clarification). The CI floors are deliberately set *below*
the measured values: a floor pinned at the current score turns every
unrelated improvement into a failure here.

The accuracy rows reproduce exactly across runs; the latency row does
not, which is why it is given as a range. Three runs on this hardware
measured 0.094/0.191, 0.134/0.254 and (with a full `pytest` run
competing for the CPU) 0.147/0.368 ms. At that scale the number is
dominated by machine load, and the only claim it supports is the one
that matters: resolution is microseconds against a pipeline whose other
stages are seconds, so it is not a latency consideration at all.

Two further caveats the table cannot carry. The per-relation support is
very uneven — 24 cases for `followup` against 2 for `correction` and 3
each for `ambiguous` and `missing_context` — so a macro F1 of 1.000 rests
on two correct answers in the thinnest class, and one new phrasing could
move it a long way. And the corpus was iterated against while the
resolver was being fixed, which is exactly the loop that makes a score
stop measuring generalization.

The defects it found, recorded because each is a class of mistake rather
than a one-off:

1. `"Can I see a sample?"` read as having its own subject, because "see"
   survived the stoplist. A verb is never a question's subject.
2. `"How do I add that index?"` classified as a refinement, because "add"
   was an unconditional refinement verb. An interrogative framing turns a
   modifying verb back into an ordinary question.
3. `"Sort it ascending instead."` produced `"Sort Top 10 ascending
   instead."` — "Top 10" had been extracted as an entity.
4. `"Summarize the FY25 hiring plan."` treated as refining the *previous*
   document, which leaked it into an unrelated request. Generative verbs
   only refine when they name no object of their own.
5. `"What is its price?"` on a first turn read as a new topic rather than
   a dangling reference, because the no-history branch used
   `agent.followup`'s narrower notion of "has a subject".
6. `"Show only the top 3 of those."` after six turns on three subjects
   reported ambiguous. A refinement's referent is the previous answer, so
   recency is the answer there, not a tiebreak.
7. `"Can you exclude cancelled orders?"` came out **`new_topic`,
   carrying nothing** — the most damaging direction there is. The
   interrogative guard that correctly stops `"How do I add that index?"`
   from reading as a refinement also fired on polite modals, and an
   imperative dressed in politeness lost both its outward signal and its
   context. Narrowed to wh-words only; five cases now pin both sides.
   Mutation-verified: widening it back drops exact match to 95.4%.

---

## 12. Limitations

Each of these is a real boundary, not a to-do.

- **General-knowledge answering is off by default.**
  `agent/orchestrator/general_knowledge.py` answers the router's
  `general_knowledge`/`conversation` routes from the model's own
  knowledge, so a writing, planning, coding or explanation follow-up now
  has something behind it — but only when an operator sets
  `ENABLE_GENERAL_KNOWLEDGE_ANSWERS=true`. The default returns the same
  fixed message as before, for the reason that module's docstring gives:
  the router picks `general_knowledge` *because it could not match a
  source*, which is sometimes a routing miss on a real data question, and
  answering that from model weights would replace a helpful routing
  failure with a confident unsourced claim about the business.
- **A general-knowledge answer is not grounded, and cannot be.**
  `guardrails.grounding.assess_grounding` scores an answer against the
  evidence that produced it; this route has no evidence by construction,
  so running the gate would refuse every answer. The fixed
  `MODEL_KNOWLEDGE_DISCLAIMER` in the answer text, the
  `answer_truth_level: "ai_inference"` field, and the prompt's own "never
  invent specifics" rule carry that weight instead. They are weaker than
  a grounding check. Treat such an answer as the model's opinion.
- **Per-source rewrites reach retrieval sources only.**
  `RouterDecision.source_queries` is now validated and threaded
  (`agent.orchestrator.nodes.source_query`) for the document and policy
  collections, web search, media search and generation. **`sql` is
  excluded on purpose**: the SQL path's text is `resolved_question`, which
  `classify_followup_node` re-checks against the request-governance
  policy, and accepting a second ungoverned rewrite there would mean the
  text that was governed is not the text that is used.
- **Non-English support is detection, not generation.** The tokenizer is
  Unicode-aware, so a question in any script is seen (before this, every
  Devanagari character was dropped and a Hindi question was *refused* as
  "not enough on its own"). Referring and attribute words are listed for
  the five shipped locales, so a non-English follow-up is detected. But
  pronoun *substitution* needs grammar, so a non-English follow-up
  resolves through the quotation clause — correct, and it reads less
  well. The word lists are short and high-confidence; a missed word
  leaves the question treated as independent, which is the safe
  direction.
- **Entity extraction is pattern-based, not NER.** Proper-noun runs,
  quoted strings, structurally distinctive tokens and recorded table
  names. A lowercase multi-word entity ("crude oil") is not extracted —
  deliberately the safe direction, since a missed entity falls back to
  lexical overlap while a spurious one would compete for referents.
- **Resolution is per-request, and the window is normally the client's.**
  `CONVERSATION_HISTORY_SOURCE=server`
  (`api/conversation_context.py`) makes this server's own chat store
  authoritative for a locally-authenticated caller, so an edit or delete
  actually changes the next answer's context. It is **off by default**
  because it costs the chat UI's per-turn "remove from context" control,
  which works precisely because the client is what sends the history.
  Either way there is still **no LangGraph checkpointer** (none ships for
  SQL Server — CLAUDE.md §13), so nothing is cached across requests under
  a context version and no graph state survives a restart.
- **The answer text carried per turn is a single string**, chosen by
  `frontend/src/lib/history.ts::conversationAnswerText` (insight, then
  synthesized answer, then a per-source answer). A turn whose only output
  was a result table carries no answer text, so a refinement of *that*
  has the SQL and tables to work from but no prose.
- **Tier 2's accuracy is unmeasured.** The benchmark scores the
  deterministic tier only, because a tier-2 figure would depend on which
  local model happened to be installed and so would not be reproducible.
