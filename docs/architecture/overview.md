# Architecture overview — cross-cutting design decisions

The design decisions that apply across the whole platform rather than
to one feature: the agent graph and its retry semantics, how schema and
business context are retrieved, how SQL is validated and executed, how
configuration, exceptions, caching and process singletons work, and the
optional subsystems (RAG, web search, media, voice, attachments,
moderation).

Moved verbatim out of the root `CLAUDE.md`. `docs/ARCHITECTURE.md` holds the
per-node walkthrough and the diagrams; this file holds the decisions and the
invariants that must not regress.

## Contents

- [Python 3.14 gotchas already hit (fixed, but worth knowing about)](#python-314-gotchas-already-hit-fixed-but-worth-knowing-about)
- [Self-correcting retry loop (LangGraph)](#self-correcting-retry-loop-langgraph)
- [Agentic query planning + plan-conformance self-correction](#agentic-query-planning--plan-conformance-self-correction)
- [Golden-dataset feedback loop](#golden-dataset-feedback-loop)
- [Nested-aggregate detection (validator + execution backstop)](#nested-aggregate-detection-validator--execution-backstop)
- [Schema scoping (why ChromaDB at all)](#schema-scoping-why-chromadb-at-all)
- [Business-context vector retrieval (`retrieval/`)](#business-context-vector-retrieval-retrieval)
- [Relationship & schema intelligence (inferred candidate FKs)](#relationship--schema-intelligence-inferred-candidate-fks)
- [Multi-database auto-routing](#multi-database-auto-routing)
- [Tool/MCP abstraction (`agent/tools/`)](#toolmcp-abstraction-agenttools)
- [Multi-source orchestration (router + subgraphs)](#multi-source-orchestration-router--subgraphs)
- [Document/policy agentic RAG (`rag/`)](#documentpolicy-agentic-rag-rag)
- [Live web search (`search/`)](#live-web-search-search)
- [Media generation (image/video) (`media_gen/`)](#media-generation-imagevideo-mediagen)
- [Media search (image/video, optional, off by default) (`media/`)](#media-search-imagevideo-optional-off-by-default-media)
- [Content moderation gate (mandatory, not a feature flag) (`moderation/`)](#content-moderation-gate-mandatory-not-a-feature-flag-moderation)
- [Malware scanning (`security/malware_scanner.py`)](#malware-scanning-securitymalwarescannerpy)
- [Voice mode (speech input/output) (`voice/`)](#voice-mode-speech-inputoutput-voice)
- [SQL is untrusted output, always](#sql-is-untrusted-output-always)
- [True read-only enforcement is layered, not just code-level](#true-read-only-enforcement-is-layered-not-just-code-level)
- [Pydantic-based configuration and validation](#pydantic-based-configuration-and-validation)
- [Centralized exception handling (safe vs. internal messages)](#centralized-exception-handling-safe-vs-internal-messages)
- [Process-lifetime singletons (DB engine, Ollama client, compiled graph)](#process-lifetime-singletons-db-engine-ollama-client-compiled-graph)
- [Caching](#caching)
- [Observability — live performance rollup (`observability/`)](#observability--live-performance-rollup-observability)
- [Optional, opt-in SQL result charting (`frontend/src/lib/chartEngine.ts`)](#optional-opt-in-sql-result-charting-frontendsrclibchartenginets)
- [AI Data Analyst depth — trend/variance/outlier detection (`agent/insight.py`)](#ai-data-analyst-depth--trendvarianceoutlier-detection-agentinsightpy)
- [Chat attachments, image actions, and their security hardening (`attachments/`)](#chat-attachments-image-actions-and-their-security-hardening-attachments)
- [Scale-out program: Phase 0 harness + a same-session hardening pass (2026-09-26)](#scale-out-program-phase-0-harness--a-same-session-hardening-pass-2026-09-26)
- [Configurable Ollama model selection for Text-to-SQL (2026-09-27)](#configurable-ollama-model-selection-for-text-to-sql-2026-09-27)
- [Enterprise scalability/security assessment: identity-aware rate limiting, trusted-proxy IP resolution, structured logging, graceful shutdown (2026-09-27)](#enterprise-scalabilitysecurity-assessment-identity-aware-rate-limiting-trusted-proxy-ip-resolution-structured-logging-graceful-shutdown-2026-09-27)
- [AI-guided (generative) image editing (2026-09-27)](#ai-guided-generative-image-editing-2026-09-27)
- [Scale/performance hardening: result cache + per-database execution limiter (Prompt 22)](#scaleperformance-hardening-result-cache--per-database-execution-limiter-prompt-22)

---

## Python 3.14 gotchas already hit (fixed, but worth knowing about)

- **`pandas==2.2.3` segfaults** building a `DataFrame` from any row data
  containing a raw `datetime.datetime` value (a real access violation deep
  in `pandas.core.arrays.datetimes._construct_from_dt64_naive`, reproduces
  in two lines with no app code involved) — this predates real Python 3.14
  wheels. Fixed by pinning `pandas==2.3.3` (see `requirements.txt`'s
  comment). If a future dependency bump reintroduces an old pandas pin,
  this is the symptom to watch for: the API process dies with
  `Segmentation fault`/`Windows fatal exception: access violation` right
  after a query with a date/datetime column succeeds, not a normal Python
  exception.
- **SQLAlchemy cannot express a nullable column as `Mapped[X | None]`**
  on this Python. Declaring one raises at *import* time, before any query
  runs: `sqlalchemy.util.typing.make_union_type` calls
  `Union.__getitem__(types)` with a tuple, and 3.14 rejects that
  (`TypeError: descriptor '__getitem__' requires a 'typing.Union' object
  but received a 'tuple'`). This is why every nullable column in
  `identity/models.py` is declared non-Optional (`username`,
  `display_name`, `password_hash`, the `avatar_*` trio) — a convention
  that looks like sloppiness and is actually load-bearing. The cost is
  that assigning `None` to one trips mypy, which
  `identity/repositories/users.py::clear_profile_picture` annotates at
  the call site rather than mistyping the column. Measured 2026-10-22 by
  trying it; three test files failed at collection with that TypeError.
- **SQL Server non-default-schema execution**: an engine resolves an
  unqualified table name against the *connecting user's own default
  schema*, not `DB_<NAME>_SCHEMA` — a database whose tables all live under
  a non-default schema (this project's own `HrAutomationDb` example:
  tables under `employee`, connection's default schema `dbo`) fails every
  query with "Invalid object name" otherwise. Fixed by
  `agent.sql_validator.qualify_table_schema`, applied only at the
  execution step (not to `state["sql"]` itself, which stays bare-named for
  the schema-anomaly/restricted-column checks and the UI). Unrelated to
  Python 3.14, but discovered in the same session — worth knowing if you
  configure a database with a non-default schema.


## Self-correcting retry loop (LangGraph)
The full graph (`agent/graph.py`) is eighteen nodes (as of Prompt 17,
`17_RECOMMENDATION_ENGINE_CONTRACT.md`'s `generate_recommendations` —
previously seventeen, after Prompt 16's `generate_forecast`; before that,
sixteen, after Prompt 13's `compute_analytics`; before that, fifteen,
after Prompt 12's `build_analytical_plan`; before it, twelve, before
Prompt 10's `review_metric_conformance` and Prompt 11's
`classify_analytical_intent`):
`sanitize_input → classify_followup → retrieve_schema →
retrieve_golden_examples → retrieve_business_context →
classify_analytical_intent → build_analytical_plan → plan_query →
generate_sql → review_sql → review_metric_conformance → validate_sql →
estimate_cost → execute_sql → compute_analytics → generate_forecast →
generate_recommendations → generate_insight`. On a review, validation, cost-estimate, or execution
failure, a conditional edge routes back to `generate_sql` (or, for a
"missing reference" execution error, back to `retrieve_schema`) with the
error message appended to state history, so the LLM sees what went wrong
and can correct itself. Capped at `MAX_RETRIES = 3` (`config/settings.py`)
as a base, widened per-question by `agent/complexity.py::compute_max_retries`
(up to `COMPLEX_QUERY_MAX_RETRY_BONUS` extra attempts for a "harder than
usual" question, stored in `state["max_retries"]` — every retry-vs-give-up
check reads that, never the raw setting). After the budget is exhausted,
the graph ends in a terminal `failed` state rather than looping forever.
It's a small, explicit state machine, not a ReAct-style free-form agent,
specifically so the retry/error-feedback path is inspectable and boundable.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#2-retry--self-correction-semantics)**
for the full per-node walkthrough and the complete failure-category →
retry-or-fail-closed routing table (which failures never retry — e.g.
safety violations, off-topic input, timeouts — and why).


## Agentic query planning + plan-conformance self-correction
`plan_query_node` (between `retrieve_schema` and `generate_sql`) and
`review_sql_node` (between `generate_sql` and `validate_sql`) are a
decompose-then-check pair, gated by the exact same complexity signals that
widen the retry budget above (`state["complexity_signals"]`) and by
`ENABLE_QUERY_PLANNING` (default `true`). For an ordinary question that
matches no signal, both nodes are a pure pass-through — zero LLM calls,
zero added latency. For a question that does match, `plan_query_node`
makes one LLM call producing a short ordered plan; `review_sql_node`
makes a second LLM call checking the generated SQL against that plan,
feeding a `FAIL` back into another `generate_sql` attempt from the same
retry budget above — never a second, unbounded loop. Both nodes fail open
on an unreachable Ollama server or an unparseable response — this feature
is an accuracy aid, never a reason a question can't be answered.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#agentic-query-planning-and-plan-conformance-review)**
for the full reasoning and the four regex signal patterns that trigger it.


## Golden-dataset feedback loop
`retrieve_golden_examples_node` (between `retrieve_schema` and
`plan_query`) looks up human-approved (question, SQL) pairs from a
per-database ChromaDB collection (`embeddings/golden_examples.py`,
`f"golden_examples__{db_name}"` — deliberately modeled on
`embeddings/schema_indexer.py`'s pattern rather than `rag/store.py`'s
SQL-Server-`VECTOR` one, since the core pipeline already depends
unconditionally on a local Chroma client and this reuses the exact same
embedding runtime already resident in the process, no new optional
subsystem). Gated by `ENABLE_GOLDEN_EXAMPLES` (default `true` — safe even
with an empty store, since retrieval on an empty/missing collection just
returns no examples) and `GOLDEN_EXAMPLES_MIN_SIMILARITY` (default 0.75 —
a poor match is withheld entirely rather than injected, since a
misleading example is worse than none). When one or more examples clear
the threshold, they're injected into every `generate_sql` attempt's
prompt (`agent.llm_client._build_golden_examples_block`) as reference-only
few-shot material, on top of the static `_QUERY_PATTERNS_BLOCK` — framed
as DATA, never instructions, the same security posture every other
per-question prompt block already has. Fails open exactly like
`plan_query_node`/`review_sql_node` above: a disabled flag, an
unreachable/empty store, or a lookup error all resolve to "no examples,"
never a reason a question can't be answered.

Examples are added only via the dashboard's explicit thumbs-up feedback
(`frontend/src/components/sql/GoldenFeedbackWidget.tsx`, calling
`POST /feedback/golden-example`, shown once a "Confirm and Run" result is
genuinely confirmed successful) — never automatically. The SQL saved is
the *exact* SQL actually executed, not the agent's original draft — the
user may have edited the SQL box before confirming, and the corrected
version is exactly what's worth remembering. Saved with a deterministic id
(`embeddings.golden_examples._example_id`, a hash of database+question+SQL)
so re-clicking the widget upserts the same document rather than
accumulating duplicates. Every caller (the React dashboard, the REST API
used standalone, `eval/runner.py`) benefits from retrieval automatically,
since they all call the same underlying `agent.graph.run_agent` graph.

**`POST /feedback/message`** (`feedback/store.py`, a separate ChromaDB
collection) is the general-purpose counterpart: a like/dislike (plus an
optional comment) on *any* assistant answer — SQL, document/policy RAG,
web, or media, not only a confirmed-and-executed SQL result
(`frontend/src/components/chat/ResponseFeedbackWidget.tsx`). Purely
additive — a thumbs-up on a confirmed SQL answer still separately calls
`POST /feedback/golden-example` too — and never feeds few-shot retrieval
itself; it's a read-later feedback log, one shared collection (not
per-database), fails open on any storage error exactly like the golden
store above.


## Nested-aggregate detection (validator + execution backstop)
A real, reproduced failure: a local model asked for something like
"year-over-year growth" reliably reaches for `AVG(CASE WHEN ... THEN
SUM(x) ELSE 0 END)` — an aggregate nested inside another aggregate, which
every supported engine (`mssql`/`postgres`/`mysql`/`oracle`) rejects.
`agent/sql_validator.py::_find_nested_aggregate` walks the `sqlglot` AST
and catches this shape statically, before the query ever reaches the
database (`violation_type="nested_aggregate"`, retryable, not a
`SAFETY_VIOLATION_TYPES` failure). `agent/error_classification.py`'s
`ExecutionErrorCategory.AGGREGATE_NESTING` is the execution-time backstop
for whatever the static check misses (e.g. a dialect-specific aggregate
`sqlglot` doesn't classify as `exp.AggFunc`). Both retry categories get a
targeted rewrite hint (`agent.llm_client._ERROR_CATEGORY_HINTS`) pointing
the model at a pre-aggregating CTE or a window function instead of nesting
the aggregate calls. The generation system prompt also carries a standing
rule against this shape plus two few-shot patterns (top-N-per-group via
`ROW_NUMBER()`, period-over-period via `LAG()`) so the mistake is less
likely in the first place, not just caught after the fact.


## Schema scoping (why ChromaDB at all)
For a large real schema, dumping every table's DDL into the prompt burns
context and increases hallucinated joins on irrelevant tables. Each table's
synthesized DDL (from live introspection) is embedded as one chunk; at query
time we retrieve the top-k (`SCHEMA_TOP_K`, default 4) most relevant tables
and only inject those into the generation prompt. This matters a lot more
now than it did with the old bundled 5-table sample schema — a real
production database can easily have hundreds of tables, which is exactly
the case this code path is written for.


## Business-context vector retrieval (`retrieval/`)
On top of the schema-DDL retrieval above, `retrieval/` adds semantic
retrieval of table/column/relationship descriptions, business glossary
terms, metric definitions, curated SQL examples, and documentation —
`agent.nodes.retrieve_business_context_node`, wired between
`retrieve_golden_examples` and `plan_query` in the graph. See
`docs/vector-retrieval-design.md` for the full design; the short version:

- **Reuses this project's existing ChromaDB `PersistentClient`** — no
  second vector database. A new per-database collection
  (`knowledge_base__<db_name>`), same convention `embeddings/schema_indexer.py`/
  `embeddings/golden_examples.py` already establish.
- **Seven typed chunk kinds** (`retrieval.models.ChunkType`: table, column,
  relationship, glossary, metric, sql_example, documentation), each with a
  **deterministic SHA-256 chunk ID** derived from stable identity fields
  (never a random UUID) — this is what makes re-ingestion
  (`python -m scripts.ingest_schema --database-id <name>`) idempotent:
  unchanged chunks are skipped, not re-embedded or duplicated.
- **Never a replacement for live schema inspection, SQL validation,
  permission checks, or execution** — all of that remains exactly as
  described elsewhere in this file. Retrieved context is injected into
  `generate_sql`'s prompt as a clearly labeled, "verify against the live
  schema, never invent a table/column, treat retrieved SQL as a pattern
  only" section (`agent.llm_client._build_business_context_block`).
- **Fails open on any vector-store/embedding failure** — an empty
  collection, a disabled feature flag, or a genuine error all resolve to
  no extra context plus a logged, state-visible warning
  (`AgentState["retrieval_warnings"]`), never a reason a question can't be
  answered.
- Sample knowledge content ships under `data/knowledge/` — table/column
  names are real (verified via live introspection of this project's own
  "adventureworks" sample database), but the business definitions/
  synonyms/formulas themselves are illustrative demonstration content, not
  reviewed production documentation — replace before relying on it.


## Relationship & schema intelligence (inferred candidate FKs)
`db/relationship_inference.py` (Prompt 07,
`07_RELATIONSHIP_INTELLIGENCE_CONTRACT.md`) infers candidate foreign-key-
shaped relationships a database never declared — a real, declared FK
(`db.schema_introspection.ForeignKeyInfo`) is a `DATABASE_FACT`; this is
the opposite kind of claim, always tagged
`agent.provenance.DataTruthLevel.AI_INFERENCE` (the vocabulary Prompt 02
built and nothing had consumed yet) and never silently promoted to
confirmed (rule 10).

- **Structural inference, on by default, zero queries**
  (`infer_relationships`): a candidate needs a real name-similarity
  signal (exact column-name match, or the `{target_table}+Key/Id/Code`
  naming convention) *and* type-compatible columns *and* a target column
  that's actually the target table's PK or a single-column unique
  constraint (`TableSchemaInfo.unique_constraints`, also new this
  prompt) — any one missing is an outright rejection, not a lower score.
  This is what keeps a type-compatible-but-unrelated column (a
  "misleading name") or a mismatched-type column from ever being
  proposed, and what keeps a many-to-many junction table from ever
  producing a *direct* candidate between the two tables it actually
  bridges (only junction→each-side candidates are proposed, both
  correctly `many_to_one`). A column already part of a declared FK is
  never re-proposed — nothing to infer, it's already a fact.
- **Opt-in, bounded data verification** (`verify_candidates_with_data`,
  `Settings.enable_relationship_data_verification`, default `False`):
  refines a candidate's confidence with a small, bounded sample of real
  data (never a full scan — `fetchmany()`-bounded, the same established
  mechanism `db/value_sampling.py` already uses, not a second
  implementation of it) — null fraction of the source column, and what
  fraction of sampled non-NULL source values are actually found in the
  target column. Only runs from `retrieval.ingestion.run_ingestion` when
  it has a live engine (never for a caller that passes pre-built
  `tables` in directly, e.g. every test).
- **Surfaced the same way as everything else in `retrieval/`** — one
  more `ChunkType.RELATIONSHIP` chunk per candidate
  (`retrieval.chunking.inferred_relationship_chunks_from_schema`), but
  its own *text* (not just `extra`, which the LLM never sees) opens with
  "CANDIDATE relationship (NOT a declared foreign key — inferred, not
  confirmed...", unmistakable wherever it's retrieved alongside a real
  relationship chunk.
- **Fed to planning, not just generation** — before this prompt,
  `agent.llm_client.generate_query_plan_from_llm` only ever saw
  `question`+`schema_context`; relationship-type retrieved context
  (real *and* candidate) never reached it at all. `plan_query_node` now
  threads `state["retrieved_context"]` through
  (`_build_plan_relationship_block`), filtered to relationship-type
  entries only — every other business-context chunk type stays
  generation-only.
- **No promotion path exists** — there is no UI/API in this codebase for
  a human to mark a candidate "confirmed." `CONFIRMED_BUSINESS_TRUTH`
  for a relationship stays exactly what it is for everything else in
  this vocabulary: a declared FK, or a future human-review workflow this
  prompt does not invent.


## Multi-database auto-routing
`DB_CONNECTIONS` in `.env` can name more than one database
(`config.settings.DatabaseConnectionConfig`, collected into
`Settings.databases`). Each configured database gets its **own** Chroma
collection (`embeddings.schema_indexer.get_collection`'s `db_name` param) —
never a shared one, because FK-bridge/keyword-match expansion in
`embeddings/retriever.py` only makes sense within one database's own
foreign-key graph, and a shared collection would also risk table-name
collisions between two databases that happen to share a table name.

Routing itself (`embeddings.retriever.select_database`) is a cheap,
separate pre-step: with one configured database it short-circuits
immediately (no Chroma query, no behavior/latency change for a plain
single-database setup — the overwhelmingly common case); with several, it
queries every database's collection for its own single best-matching table
(`n_results=1`) and picks the database that wins. The existing, unmodified
top-k/FK-bridge/keyword-fallback retrieval logic then runs exactly as
before, scoped to that one winning database's collection.

`retrieve_schema_node` calls `select_database` only on the *first* pass
through a question and stores the result in `AgentState["selected_database"]`.
The one retry path that re-enters `retrieve_schema` (`execute_sql`'s
`missing_reference` retry — see the self-correcting retry loop above)
**reuses** that stored value rather than re-routing: a retry must keep
targeting the same database attempt 1 already generated/executed SQL
against. Every downstream dialect/engine resolution
(`validate_sql_node`, `estimate_query_cost_node`, `execute_sql_node`, and
`api/main.py`'s `POST /execute` "Confirm and Run" route) reads
`db.connection.get_connection(settings, state["selected_database"])`
rather than a single global `Settings.db_type`.

Two things deliberately left alone by this design (documented, not
silently ignored): the eval benchmark's per-case `database:` label (see the
`eval/` folder note above) and `config/table_descriptions.yaml`/
`config/sensitive_columns.yaml`, which are keyed by bare table name, not
`(database, table)` — a note/classification for one configured database's
table could in principle also apply to a same-named table in another. Both
are real, narrow limitations worth knowing about if you're extending this
further, not oversights to silently work around.


## Tool/MCP abstraction (`agent/tools/`)
A generic, governed way to describe and invoke this application's six real
capabilities (SQL, document RAG, policy RAG, web search, media search,
media generation) by name — a uniform contract (permission check, timeout,
retry policy, structured audit logging) wrapping the exact same functions
`agent/orchestrator/nodes.py` already calls, no reimplementation.
**Deliberately additive, not a rewrite: nothing in the live request path
calls through this registry yet.** `agent/orchestrator/nodes.py`'s
hardcoded nodes are completely unmodified, and `run_orchestrated` still
calls them directly in production — this is foundation for a future
tool-dispatching planner and a future external MCP client, not scaffolding
that does nothing (it's fully tested on its own).

**Read [`docs/TOOLS.md`](../TOOLS.md)** for the full contract and design,
and [`docs/PLATFORM_TRANSFORMATION_ASSESSMENT.md`](../PLATFORM_TRANSFORMATION_ASSESSMENT.md)
for the roadmap this is the first step of.


## Multi-source orchestration (router + subgraphs)
`ENABLE_MULTI_SOURCE_ROUTER` (default `false`) puts a router
(`agent/orchestrator/`) in front of the SQL pipeline. Router + subgraphs,
not a single tool-calling agent, was a deliberate choice: every source's
safety boundary (the SQL validator, the policy-sensitivity gate, the
"web content is untrusted" framing) stays separately testable rather than
folded into one model's implicit tool-selection reasoning.

**Flag off (the default):** `agent.orchestrator.graph.run_orchestrated`
calls `agent.graph.run_agent` directly and returns its result completely
unwrapped — the orchestrator graph is never even constructed. This is what
makes the byte-for-byte-unchanged guarantee for SQL-only questions
provable, not just claimed. **Flag on:** `router_node` picks one or more of
`sql`/`documents`/`policy`/`web`/`generation`/`media_search` (zero LLM
calls with ≤1 source available; one LLM call to classify with 2+, falling
back to *every* available source, never zero, on an unparseable response)
and fans them out as parallel branches in one LangGraph step before
`synthesis_node` composes the final answer — a pure pass-through when only
one source fired, a labeled per-source section when 2+.

**Known limitation, not yet fixed:** every routed subgraph receives the
same full, un-decomposed question text — a question that's really two
unrelated asks mashed into one sentence can retrieve poorly on both sides
even though each alone would work. Per-source query decomposition would
fix this; out of scope for the initial build.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#4-multi-source-orchestration)**
for the full diagram, router/fan-out/synthesis mechanics, and
`OrchestratorState`'s shape (it *extends* `AgentState`, never replaces
it), and [`docs/MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) for
how to configure and use each source.


## Document/policy agentic RAG (`rag/`)
One implementation (`rag.graph.build_rag_subgraph(collection)`) serves
both "documents" and "policies" — structurally identical, differing only
in `generate_node`'s sensitivity check. A policy chunk tagged
`compensation`/`disciplinary`/`legal` at upload time is **never**
summarized into an answer — checked before the LLM ever sees the chunk,
fail-closed, the same philosophy as `agent/sql_validator.py`'s
`SAFETY_VIOLATION_TYPES`. **2026 Phase 3** added a second, independent
gate, `DocumentRecord.restricted_roles` (real RBAC — see "Authentication,
authorization, and the 2026 security hardening passes" below); a document
can carry either gate, both, or neither. The chat-answer PDF download
button is built strictly from the `citations` list, which is why a
restricted match (`citations == []`) automatically has no download
button — there's no separate access check to remember.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#documentpolicy-agentic-rag-rag)**
for the full retrieve → grade → rewrite → generate flow, the SQL Server
`VECTOR` storage design (including a real `ntext`/vector-cast bug found
and fixed), and the untrusted-content framing.


## Live web search (`search/`)
`search/web_search.py` mirrors `db/connection.py`'s `SUPPORTED_DB_TYPES`
pattern exactly: `SUPPORTED_SEARCH_PROVIDERS` maps a provider name to its
call function, so `WEB_SEARCH_PROVIDER` is a `.env` change, not a code
change. Only `tavily` is implemented today. Results are wrapped in a fixed
`WebResult` shape before they ever reach a prompt and are explicitly framed
as external/live/untrusted data in the answer-generation prompt
(`agent.orchestrator.nodes.web_search_node`) — the answer text itself
always opens with "According to a live web search:", so it's never
presented as if it came from the company's own systems, and the same
"data, not instructions" principle as ingested PDF content applies here
too, since a search result's content is exactly as attacker-influenceable
as a stored database value or an uploaded document.


## Media generation (image/video) (`media_gen/`)
A tested IMA Studio client wired into the orchestrator as a `"generation"`
source. Image generation is confirmed working end-to-end against a real
IMA account; video generation shares the same code path but hasn't been
separately confirmed live. `ENABLE_MEDIA_GENERATION` stays off by default
so a fresh clone never spends real IMA credits unintentionally.

**Invariants that must not regress:** generated media is served through
this app, never the provider's raw CDN URL — `execute_generation`
downloads the bytes once (SSRF-hardened, redirects re-validated hop-by-hop),
caches them under an opaque `media_id`, and neither the answer text nor
the API response (`MediaGenerationResultOut`) ever carries the raw URL.
Generation is gated by a human-in-the-loop approval step before any
provider call (`Settings.require_generation_approval`, default `true`) —
the only orchestrator source that spends real, metered money; both the
confirm endpoint and the approval-disabled path funnel through the same
`execute_generation` function, which re-runs its own safety/rate-limit
checks regardless of entry point. Video clip length is capped by the
provider's own model (currently 4-15s), not a restriction this app
imposes.

**Read [`SECURITY.md`](../../SECURITY.md)'s "Media generation" section** for the
full security rationale (SSRF hardening, content-policy check, rate
limits, cost ceiling) and [`docs/RESPONSIBLE_AI.md`](../RESPONSIBLE_AI.md)
for the content-policy check's disclosed limitations (a keyword heuristic,
not real moderation). **Known gap:** multi-source synthesis doesn't embed
generated media inline — a generation result folded into a multi-source
answer shows only its (link-free) confirmation text.


## Media search (image/video, optional, off by default) (`media/`)
Content-based search over an **untagged** local image/video library (no
filenames, no manual tags), routed as the `"media_search"` orchestrator
source. Gated by `ENABLE_MEDIA_SEARCH` + a real `MEDIA_LIBRARY_PATH`. Off
by default — it pulls in a real, meaningfully larger dependency footprint
(`torch`, `opencv-python`) that shouldn't land on every fresh clone
uninvited.

**Invariants that must not regress:** embeddings are local CLIP
(`sentence-transformers`' `clip-ViT-B-32`), not a hosted API — the one
real exception to this project's otherwise-consistent "no torch"
dependency posture, a deliberate, disclosed tradeoff (not an oversight).
OCR/ASR/captioning text is framed as **untrusted data, never
instructions** in the answer-composition prompt, same as web-search/RAG
content. Video keyframe/thumbnail serving (`GET /media/library/{media_id}`)
is a separate, persistent path from generated-media's own bounded
in-memory cache — a full video clip is never streamed, only a frame +
timestamp.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#11-media-search)**
for the full video pipeline (scene-change keyframing, ASR, OCR,
captioning, dual embeddings) and known gaps, and
[`SECURITY.md`](../../SECURITY.md)'s "Media search" section for the
untrusted-content framing detail. Needs the system Tesseract OCR binary
installed separately — see "Windows / Visual Studio-specific notes" below.


## Content moderation gate (mandatory, not a feature flag) (`moderation/`)

Both content-ingestion pipelines — media search's images/video and
document/policy RAG's PDFs — run every chunk of every file through
`moderation/gate.py::moderate_chunks` before either one's store ever
persists anything. **Mandatory whenever `ENABLE_MEDIA_SEARCH` or
`ENABLE_DOCUMENT_RAG`/`ENABLE_POLICY_RAG` is on** — deliberately no
`ENABLE_CONTENT_MODERATION` toggle exists that could silently disable it
while leaving either pipeline on; missing provider/store config fails
ingestion closed.

**Invariants that must not regress:** a hard-reject on any chunk rejects
the **entire** asset — never a partial ingestion — and nothing from a
rejected file is ever embedded or stored anywhere (a rejected PDF never
gets a `rag.documents` row at all, including its raw bytes). Neither
provider has a dedicated "weapons"/"drugs"/"synthetic media" category —
stated honestly via a text-blocklist proxy for the first two and a
disclosed `"not_checked"` placeholder (never a hard-reject) for deepfake
detection, not silently glossed over. **An asset whose every chunk came
back `"not_checked"` is refused, not passed** — otherwise a provider that
cannot examine a given content type would turn this mandatory gate into a
no-op for a whole pipeline, which is the exact outcome having no
`ENABLE_CONTENT_MODERATION` toggle is meant to prevent.

**Two providers, and the choice is the operator's**
(`MODERATION_PROVIDER`). `azure_content_safety` is the default and the
stronger gate, and is this project's one required exception to its "fully
local" model posture. `ollama_local` (added 2026-10-31) classifies each
chunk with this project's own Ollama runtime instead: no outbound call and
no API key, honestly weaker, and the reason it exists is that "mandatory
gate" plus "Azure is the only implementation" meant a deployment with no
Azure resource could have document/policy RAG enabled and never ingest a
single file. What holds for either: both report severities on the same
0/2/4/6 scale, **neither decides a rejection** (`moderation/gate.py` owns
the threshold and the asset-level rule), and a provider that cannot be
reached or whose answer cannot be read raises rather than returning
"clean".

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#12-content-moderation-gate)**
for the full per-type chunking strategy (image/video/PDF), the dedupe
store, and the concurrency design, and
[`docs/RISK_REGISTER.md`](../RISK_REGISTER.md)'s R-013/R-014/R-021 for the
disclosed tradeoffs above.


## Malware scanning (`security/malware_scanner.py`)
Scans a file's **raw bytes**, before any parser touches them — a separate
concern from the content-moderation gate above, which only runs after
extraction. Wired into both `rag/ingestion.py::ingest_pdf` and
`media/ingest.py::ingest_file`, right after the dedupe-by-hash check.
Pluggable (`Settings.malware_scan_provider`, `"disabled"`/`"clamav"`),
spoken to directly over `clamd`'s `INSTREAM` protocol.

**Invariant: deliberately off by default, but genuinely fail-closed once
enabled** — an infected result, an unreachable/timed-out daemon, and a
malformed response are all treated as a rejection; "unknown" is never
silently treated as "clean." `"disabled"` is still audit-logged per
upload, not silently skipped.

**Known limitation:** fail-closed logic is unit-tested against a mocked
`clamd` socket only — never exercised against a real daemon in this
project's history. See
[`docs/security/FINAL_PRODUCTION_GATE.md`](../security/FINAL_PRODUCTION_GATE.md)'s
Malware Scanning row and [`docs/RISK_REGISTER.md`](../RISK_REGISTER.md)'s
R-016.


## Voice mode (speech input/output) (`voice/`)
Optional, **on by default** (`ENABLE_VOICE_MODE=true`) — unlike media
generation, this feature spends no money and makes no external network
call once its one-time local model downloads are done. Transcription via
`faster-whisper`, synthesis via Piper — both fully local inference (no
`torch`).

**Invariants that must not regress:** a transcribed question is **never**
treated specially — `POST /voice/transcribe`'s result is submitted
through the exact same `POST /ask` path a typed question uses, so
`agent.input_guard.check_input` applies unconditionally, with no separate
code path for voice input to bypass. Nothing is auto-submitted from a
voice turn — the existing Send button is the only confirmation step — and
`originatedFromVoice` (set only when the textarea still holds a voice
transcript at send time, never by typing) is a structural guarantee, not
a runtime check, that a typed question can never trigger
`POST /voice/synthesize`.

**Disclosed, deliberate exception to "fully local":** live word-by-word
captions while listening use the browser's built-in `SpeechRecognition`
Web Speech API, which (in Chromium) sends microphone audio to the browser
vendor's own cloud speech service. The caption is **never** what gets
submitted — the local Whisper result remains the sole authoritative
transcript, discarded and replaced the moment it returns.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#13-voice-mode)** for
the full UI state machine, autoplay warm-up mechanics, schema-aware
transcription vocabulary hinting, and upload size/duration security
limits.


## SQL is untrusted output, always
The LLM's SQL is never trusted at face value. `agent/sql_validator.py`
parses it with `sqlglot` (in the dialect matching `DB_TYPE`) and rejects
anything that isn't a single `SELECT`/`UNION`/`EXCEPT`/`INTERSECT` statement
(explicit allowlist of the parsed statement type, not a regex blocklist).
**2026 Phase 3:** also rejects a reference to a system catalog/
data-dictionary object — `information_schema`, `pg_catalog`, mysql's
internal schemas, mssql `sys`, Oracle `ALL_*`/`DBA_*`/`USER_*`/`V$`/`GV$`
(`_find_system_catalog_reference`, new `system_catalog_access` violation
type in `SAFETY_VIOLATION_TYPES`) — a syntactically ordinary `SELECT` that
passed every prior check but could reveal internal schema structure or
other users'/roles' grants even under a genuinely read-only DB role, since
DB-role read-only-ness bounds writes, not what a SELECT can read. Uses
curated suffix matching for Oracle (`all_tables`, `dba_users`, ...), not a
blind `user_`/`dba_`/`all_` prefix match, specifically to avoid
false-positiving on an ordinary business table like `user_accounts`.
Execution happens on a read-only-by-convention SQLAlchemy engine
(`db.connection.get_read_only_engine()`), with a row cap (`MAX_RESULT_ROWS`,
enforced *both* via `LIMIT` in the SQL text and independently via
`fetchmany()` at the cursor level, so a malformed/mistranslated query can't
bypass it just by lacking a working `LIMIT`) and a query timeout enforced at
the driver level where a cheap session-level `SET` exists (Postgres, MySQL)
and via forced connection-abort otherwise (SQL Server, Oracle — see
`db/execution.py::_execute_with_timeout`). This validation step runs
**every** time SQL is about to be displayed or executed for the user,
including after the user hand-edits the SQL box in the UI — an edit is
exactly as untrusted as an LLM generation.

One nuance worth knowing if you're reading `api/main.py`/the frontend: the
LangGraph agent's own internal retry loop *does* execute candidate SQL automatically
(that's how it detects and self-corrects runtime errors like an unknown
column) — those internal executions are safe (read-only, validated,
row-capped, timed out) but are never shown to the user. Nothing is rendered
until the user clicks **Confirm and Run**, and that button always
re-validates and re-executes the *current* SQL text fresh, rather than
trusting whatever the agent's last internal attempt produced.

**Prompt 05** (`05_SQL_SERVER_DIALECT_VALIDATION_CONTRACT.md`) verified
every T-SQL construct (TOP, OFFSET/FETCH, CTEs, window functions,
DATEADD/DATEDIFF/DATEPART/DATENAME, EOMONTH, STRING_AGG, TRY_CAST/
TRY_CONVERT, PERCENTILE_CONT WITHIN GROUP, PIVOT/UNPIVOT, bracket
identifiers) against the real validator and found it already handles
all of them — the allowlist is on parsed *shape*, never on which
keywords/functions are used. It found and fixed two real bugs instead:
`qualify_table_schema`/`find_unexpected_table_references`/
`references_multiple_tables`/`find_restricted_column_references` all
mistook a CTE *reference* for a real table (sqlglot represents both as
an identical `exp.Table` node) — closed by the shared
`_cte_reference_table_ids` helper; and `enforce_row_limit` didn't
recognize an existing `FETCH NEXT n ROWS ONLY` as an existing cap
(only checked `exp.Limit`, not `exp.Fetch`), silently widening a small
explicit fetch count to `max_rows` on every call.


## True read-only enforcement is layered, not just code-level
`get_read_only_engine()` does not itself strip write privileges — there's no
generic, cross-database way to do that purely at the SQLAlchemy layer. The
real guarantee is two layers: (1) the SQL validator, described above, and
(2) the `.env` `DB_USER` should be a genuinely read-only database
role/account (documented in README's Security section, not silently
assumed). If you're asked to "harden" this further, that's the layer to
push on — a DB-level read-only user, not more code-level checks, since the
validator is already an AST-based allowlist rather than a blocklist.
`db.connection.check_write_privileges` is a best-effort detector for layer
(2) being misconfigured, and — since 2026 Phase 3 — is startup-enforced,
not just a manual CLI check; see "Authentication, authorization, and the
2026 security hardening passes" above.


## Pydantic-based configuration and validation
`config.settings.Settings` is a `pydantic_settings.BaseSettings` (not a
plain `@dataclass`, as it was originally) — field types, `Field(gt=0)`
constraints, and `Literal` types (`log_redaction_level`) get automatic
env-var coercion and validation essentially for free, instead of the
hand-written `_env_int`/`_validate_security_settings()` loop this replaced.
A `Settings.__init__` override still guarantees the codebase's
long-standing `ConfigurationError` contract: it catches
`pydantic.ValidationError` (raised by Pydantic's own type coercion, e.g.
`MAX_RETRIES=abc`) and translates it, while two `model_validator`s
(cross-field cost-threshold ordering; filling in a default `databases`
entry) raise `ConfigurationError` directly — verified to propagate
unwrapped through Pydantic's validation machinery, since Pydantic only
intercepts `ValueError`/`TypeError`/`AssertionError` to build its own
`ValidationError`. `DatabaseConnectionConfig` is a plain (non-Settings)
`BaseModel`, `frozen=True` like `Settings` itself.

**Because `Settings` now reads `os.environ` for any field a caller doesn't
explicitly pass** (the entire point of `BaseSettings` — unlike the old
dataclass, which just used its class-level default), constructing
`Settings(**partial_kwargs)` directly is no longer isolated from this
machine's real `.env` the way it used to be. `tests/conftest.py`'s
autouse `_isolate_settings_from_real_environment` fixture strips every
env var one of `Settings`'s fields could read before each test — without
it, a test's `Settings(enable_document_rag=True)` would silently inherit
this project's own `ENABLE_POLICY_RAG=true`/`ENABLE_WEB_SEARCH=true` etc.
from the real `.env` for every field it didn't explicitly override. Tests
that build a `Settings` copy with a few fields changed
(`tests/test_connection.py::_settings` is the canonical example) rebuild
via `Settings(**{**base.__dict__, **overrides})` rather than
`BaseModel.model_copy(update=...)`, because `model_copy` skips validation
entirely — several tests (`test_settings_validation.py` in particular)
depend on a bad override still raising `ConfigurationError`.

Secrets (`db_password`, `db_connection_string`, `api_auth_token`,
`rag_store_connection_string`, `web_search_api_key`) are `pydantic.SecretStr`
(see `security/secrets.py`'s docstring) rather than the project's old
hand-rolled `str` subclass, which only masked `repr()`/`%r` and stayed
fully transparent everywhere else (`str()`, f-strings, even `.upper()`).
Pydantic's version masks `str()`/`repr()`/`%r`-formatting alike and isn't
a `str` subclass at all — every call site that needs the real value
(`db/connection.py`'s URL builder, `api/auth.py`'s bearer-token check,
`rag/store.py`, `search/web_search.py`, `security/redaction.py`) must call
`.get_secret_value()` explicitly now; a leftover `str(secret)` would
silently send/compare/search for the literal string `"**********"`
instead.

Several other hand-rolled `@dataclass`es that sit at a real validation
boundary were converted to `BaseModel` (`ConfigDict(frozen=True)`, same
shape as before) the same way: `agent/sql_validator.py`'s
`ValidationResult` (gained a `model_validator` enforcing its
is_valid/error/violation_type invariant), `db/query_cost.py`'s
`CostEstimate`, `agent/insight.py`'s `ColumnStat`/`ResultSummary`,
`db/connection.py`'s `ConnectionTestResult`,
`config/sensitive_columns.py`'s `ColumnClassification`, and
`config/table_descriptions.py`'s `TableDescription`. Purely-internal
plumbing dataclasses with no external validation boundary (`DbTypeInfo`/
`WritePrivilegeCheckResult` in `db/connection.py`, `db/query_cost.py`'s
`_RawPlanInfo`, `db/schema_introspection.py`'s introspection types, the
`eval/` benchmark's own dataclasses, and others) were deliberately left
alone — converting everything would have added validation overhead with
no real boundary to protect. `api/schemas.py`'s request/response models
(already Pydantic) were hardened alongside this: `extra="forbid"` +
`str_strip_whitespace=True` on request models (`AskRequest`,
`ConversationExchangeIn`), `frozen=True` on every response model.
`AskRequest.question` deliberately does NOT duplicate
`Settings.max_question_length` as a schema-level `Field(max_length=...)`
— that cap is config-driven and already enforced downstream by
`agent.input_guard.check_input`, and a static schema-level number would
either hardcode the wrong value or drift from it.


## Centralized exception handling (safe vs. internal messages)
Every `agent.exceptions.AgentError` (and its subclasses —
`OllamaUnavailableError`, `MalformedLLMOutputError`, `SchemaRetrievalError`,
`SqlExecutionTimeoutError`, `OffTopicQuestionError`) now carries two
messages, not one: `str(exc)` stays the full internal detail (a raw
driver/Chroma/Ollama error — genuinely useful for logs and for feeding
back into the retry loop's own self-correction prompt), and `.safe_message`
is a short, non-technical sentence with no driver internals, hostnames, or
connection-string-shaped text, each subclass defaulting to something
sensible. This exists because those two things used to be the same string
everywhere — `agent/nodes.py` and `api/main.py` both embedded `str(exc)`
directly into fields the client sees (`AskResponse.error_history`/
`failure_explanation`), which is exactly how a raw DB error could reach a
response.

Two complementary fixes ride along with this:
- `security.redaction.redact_secrets` (previously applied only to
  `db/connection.py`'s connection-test failures) is now also applied to the
  raw driver text in `agent.nodes.execute_sql_node`'s
  `(SQLAlchemyError, TimeoutError)` handling and
  `embeddings.retriever.retrieve_relevant_schema`'s generic-exception
  wrapping — both are genuine "text this app did not construct itself"
  call sites (see that module's docstring) that weren't covered before.
  Redaction happens once, before the text is used anywhere (logs, the
  retry-feedback prompt, and the user-facing fields alike) — a redacted
  password doesn't change what a syntax/missing-column error says, so
  retry self-correction quality is unaffected.
- `api/main.py` registers two global handlers:
  `@app.exception_handler(AgentError)` (uses `.safe_message`) and
  `@app.exception_handler(Exception)` (a generic "something went wrong"
  body) — both log the full detail server-side, tagged with the request's
  correlation ID, and guarantee no response body ever contains raw
  exception text, even for a failure mode nothing already handles locally.
  `/ask`'s own local `except AgentError` (previously
  `except SchemaRetrievalError`, widened to the whole hierarchy so a future
  source's exception is covered the same way without another special case)
  is the common path in practice — it keeps returning the existing
  200-with-a-`"failed"`-body shape, just built from `.safe_message` now;
  the global handlers are the defense-in-depth net behind it. `/health`'s
  own raw `f"Unreachable: {exc}"` strings (that endpoint has no
  `Depends(verify_api_key)` — it's meant to be reachable by an
  orchestrator with no API key, so a leak there is a *public*, not just
  internal, exposure) are now redacted the same way.


## Process-lifetime singletons (DB engine, Ollama client, compiled graph)
Three things are now built once per process instead of once per call,
using the same `functools.cache`/`lru_cache` pattern
`config.settings.get_settings()` and `db.connection._cached_engine`
already established:
- `agent.llm_client._get_ollama_client(host, timeout)` (public wrapper:
  `get_ollama_client(settings)`) — every `generate_*_from_llm` call site
  used to build a fresh `ollama.Client` (and therefore a fresh underlying
  `httpx.Client` connection pool) on every single call, including every
  retry. Reused now, keyed by `(host, timeout)`.
- `agent.graph.build_graph()` and `agent.orchestrator.graph
  .build_orchestrator_graph()` — previously rebuilt (re-wiring all ten SQL
  nodes, or the orchestrator's six) on *every* `run_agent()`/
  `run_orchestrated()` call. A compiled LangGraph graph is stateless (all
  per-question state lives in the `initial_state` dict passed to
  `.invoke()`), and graph *shape* never depends on `Settings` (`plan_query`/
  `review_sql` are pass-throughs when planning is off, not conditionally
  omitted nodes — see "Agentic query planning" above), so caching is safe
  and this was the single largest avoidable per-question cost of the three.
- `db.connection._cached_engine` — already a singleton before this (see
  its own docstring); unchanged.

`api/main.py`'s `lifespan` context manager (see the `api/` folder note
above) exists purely to move each of these singletons' one-time build cost
from whichever request arrives first to process startup — none of them
were *incorrect* without it, since the cached functions build lazily on
first use either way. **Test isolation note:** because these caches persist
for the life of the Python process, `tests/conftest.py`'s
`_clear_process_singleton_caches` autouse fixture clears all three before
every test — without it, a test that monkeypatches `ollama.Client` (or a
node function referenced by a compiled graph) to a fake would silently see
no effect whenever an earlier test already populated that cache slot,
since the cached function body simply wouldn't re-run.


## Caching
- Chroma embeddings: SHA-256 fingerprint of the *introspected* schema
  (`db.schema_introspection.get_schema_fingerprint`), not a file hash
  anymore since there's no file. Stored alongside the Chroma persist dir;
  `schema_indexer.build_index()` skips re-embedding if the fingerprint
  matches. The UI's "Refresh Schema" button re-introspects and calls
  `build_index()` normally (not forced) — the skip-if-unchanged behavior is
  what makes clicking it cheap when nothing's actually changed.
- Frontend: `@tanstack/react-query` (`src/hooks/queries.ts`) for
  `getHealth`/`getSchemaTables`, invalidated explicitly by the "Refresh
  Schema" button's mutation; `chatStore.ts`'s `nlQuestionCache` (a plain
  `Map`, keyed by `nlCacheKey(question, priorQuestions, enableInsight)`)
  for repeated identical NL questions within a session, to skip redundant
  `/ask` round-trips.
- Process-lifetime singletons (DB engine, Ollama client, compiled LangGraph
  graph) — see "Process-lifetime singletons" above.


## Observability — live performance rollup (`observability/`)
`agent.nodes._timed_node` logs a per-node timing line and appends a
`StageTiming` entry to `AgentState["stage_timings"]`.
`observability/metrics.py`'s `PerformanceMetrics` (a process-wide
singleton) aggregates every completed run's timings and status into a
live snapshot, read back via `GET /metrics/performance` (admin-only).

**Invariant that must not regress: deliberately single-process,
in-memory, resets on restart** — a multi-worker deployment would have one
independent rollup per worker (the identical limitation
`agent/rate_limit.py`'s own sliding-window limiters disclose); real
cross-process metrics would need Prometheus/OpenTelemetry, not attempted
here.

**Read [`docs/OBSERVABILITY.md`](../OBSERVABILITY.md)** for the full
correlation-ID/logging/health-check picture, and
[`docs/PERFORMANCE_BASELINE.md`](../PERFORMANCE_BASELINE.md)/[`docs/PERFORMANCE_RESULTS.md`](../PERFORMANCE_RESULTS.md)
for why no further backend "optimization" is justified today without a
model/hardware/prompt-size tradeoff (LLM inference is 93.8–98% of
wall-clock time).


## Optional, opt-in SQL result charting (`frontend/src/lib/chartEngine.ts`)
A confirmed SQL result never auto-renders a chart — an unobtrusive
"Visualize" button is the only way one appears. Fully client-side
(`plotly` was removed as a backend dependency).

**Invariant that must not regress: the backend's own
`chart_recommendation` is only ever a seed for the *initial* choice —
every chart type's actual enabled/disabled state is recomputed from the
real returned columns/rows on the frontend, every time.** "mixed"
(bar+line) never adds a second y-axis — two measures of different scale
get two charts, never a dual axis, per the loaded data-viz skill's
non-negotiable. A chart config is session-only and does not survive a
page reload or a reloaded-from-server past conversation turn (same
disclosed limitation as "Universal server-side chat history" above).

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#14-sql-result-charting)**
for the full engine design (12 chart types as of Prompt 15's histogram
addition below, the NL follow-up chart-switch detector, the jsdom/
Chart.js test-infrastructure fix) and current test coverage.


## AI Data Analyst depth — trend/variance/outlier detection (`agent/insight.py`)
`ResultSummary` gained three deterministic fields —
`ColumnStat.stddev`/`coefficient_of_variation`, `ResultSummary.trend`, and
`ResultSummary.outliers` — computed the same "Python does the arithmetic,
the LLM only narrates already-computed truths" way the pre-existing
`top_label`/`top_share_percent` fields work, and validated by the same
grounding gate (`is_insight_grounded`).

**Invariant: fully tested but NOT yet wired into the live prompt — do not
treat as user-facing.** `agent.llm_client._build_insight_prompt` is
unchanged, so nothing in the live `generate_insight_node` path surfaces a
trend/variance/outlier claim yet, deliberately deferred: this prompt's
exact wording is what `docs/EVALUATION_CURRENT.md`'s live-LLM benchmark
numbers were measured against, and changing it without being able to
re-run that benchmark would violate this project's "never compromise
correctness" standard.

See [`docs/DEEP_FEATURE_PERFORMANCE_ASSESSMENT.md`](../DEEP_FEATURE_PERFORMANCE_ASSESSMENT.md)'s
P1 roadmap for where this sits relative to the rest of the AI Data
Analyst work.


## Chat attachments, image actions, and their security hardening (`attachments/`)
Chat file attachments (images, PDF, DOCX, XLSX, PPTX, TXT, MD, CSV, JSON)
are given directly to the model as context for the question that attached
them, via `POST /attachments/upload` → the `"attachments"` orchestrator
source. Deliberately **not** `rag/ingestion.py`'s pipeline: a chat
attachment is ephemeral, per-conversation, per-caller content, not a
permanent shared knowledge-base entry — storage is a bounded, in-memory,
process-lifetime registry (`attachments/store.py`), never a database row.
Explicit image actions (OCR/text-extraction, deterministic resize, and
OpenCV-based text removal — never a generative model, never a solid-color
rectangle) are separately callable, not just implicit side effects of
asking a question about an image.

**Invariants that must not regress:**
- An attached image is **never silently discarded** — if no vision model
  is configured (or a call fails), it falls back to OCR instead of being
  dropped, and the result records `vision_unavailable=True` so the UI
  shows an honest degraded-mode notice.
- `router_node` **forces** `"attachments"` into the route whenever the
  caller attached a file, regardless of what the LLM classifier picks.
  A deterministic pre-check (`_looks_like_attachment_only_question`)
  additionally keeps an attachment-only question (e.g. "extract the text
  from this image") from also reaching SQL generation just because a
  database happens to be configured — a real routing bug found and fixed,
  not a hypothetical.
- Resize and remove-text actions always produce a **brand-new**
  attachment (`register_derived_image`) — the original is never mutated
  in place.
- Every successfully-processed attachment's extracted text is scanned for
  injection patterns (detection-only, never block) and, if
  `Settings.malware_scan_provider` is enabled, malware-scanned before any
  parser touches the raw bytes. `"disabled"` (the default) cannot
  silently ship to `ENVIRONMENT=production` alongside chat attachments,
  document RAG, policy RAG, or media search — a startup `model_validator`
  refuses to start otherwise.
- DOCX/XLSX/PPTX (plain ZIP archives) get a decompression-bomb guard
  (`attachments/zip_safety.py`, metadata-only, no actual decompression)
  and PDFs get a catalog-level dangerous-content preflight
  (`attachments/pdf_safety.py` — embedded JavaScript, auto-open actions)
  before parsing — both closed gaps found by a direct security-review
  audit, not assumed-covered by the malware scanner above. The PDF
  preflight is catalog-level only, not a full page/annotation walk — a
  disclosed scope boundary, not a silent gap.
- Every processor call runs on a bounded thread pool with a hard timeout
  (`Settings.attachment_processing_timeout_seconds`) — a pathological
  file can't tie up a request thread indefinitely.

**Known, disclosed limitations:** an attachment is global to the caller,
not scoped to a configured database. PPTX speaker notes aren't extracted
(only slide title + body text). A legacy binary Office format
(`.doc`/`.xls`/`.ppt`) is rejected with a message naming the modern
extension needed instead. OCR/text-removal capability flags report
whether the `pytesseract` *Python package* is importable, not whether the
Tesseract *system binary* is installed — a missing binary degrades one
request to an empty result with a warning rather than flipping the
capability flag off. No live ClamAV daemon has ever exercised the
malware-scanner's fail-closed path in this project's history — only a
mocked socket.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#15-chat-attachments-image-actions-and-security-hardening)**
for the full pipeline (per-file-kind processors, the 7-node LangGraph
subgraph, the capability-registry pattern, and the routing-fix/zip-PDF-
safety/injection-detection hardening pass in full detail), and
[`SECURITY.md`](../../SECURITY.md)'s "Chat attachments — security controls"
section for the user-facing security summary.


## Scale-out program: Phase 0 harness + a same-session hardening pass (2026-09-26)
`docs/SCALE_OUT_PROMPT.md` is an 11-phase program (committed verbatim)
taking this app from single-instance toward horizontally scalable. Phase
0 (done): a load-test harness (`eval/load/`) plus a measured baseline
(`docs/SCALE_BASELINE.md`) — actually running it found and fixed a real,
previously-invisible bug (`db/connection.py::get_engine` was passing a
password-masked connection string to `create_engine`, silently breaking
every discrete-field connection with a real password).

**Invariants that must not regress:** `/ask` concurrency is bounded
(`ThreadPoolExecutor` sized to `Settings.max_concurrent_ask_requests`,
with a per-caller cap) with real admission control — a caller past either
cap gets an immediate 429, before any LLM/DB work starts. Rate/concurrency
limits and attachment ownership are keyed by real identity
(`security.oidc.real_caller_subject`), not raw client IP — includes a
fixed cross-user data-isolation bug where local-auth users were pooled
into one shared "no owner" bucket.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#5-scale-out-program)**
for the full per-change writeup, and
[`docs/SCALE_BASELINE.md`](../SCALE_BASELINE.md)'s "Honest capacity
statement" for what's now suitable for multi-instance deployment vs. what
still isn't (rate limiting, the Chroma schema index, and the compiled-
graph/Ollama-client singletons are all still process-local; no true
cross-thread cancellation or distributed coordination yet). Streaming is
no longer on that list: `POST /ask/stream` (`api/streaming.py`) emits
per-node progress over SSE and then the same `AskResponse` `POST /ask`
returns — it *calls* that handler rather than reimplementing it, so every
control applies identically. Token-level streaming of the model's own
output is still absent.


## Configurable Ollama model selection for Text-to-SQL (2026-09-27)
A caller may pick which locally-installed Ollama model answers a given
question (`POST /ask`'s optional `model` field) instead of always using
`Settings.ollama_model`. Three deliberately separate concepts:
configured/allowed (`Settings.ollama_allowed_models`, server-validated),
this app's own curated display metadata (cosmetic only), and installed
locally (a live Ollama lookup, queried only by `GET /models`, never by
`/ask` itself).

**Invariants that must not regress:** model selection is **request-scoped,
never global** (`AgentState["selected_model"]`, resolved once, read-only
downstream — the identical `selected_database` pattern) — verified with a
real-threading regression test that no process-global mutable model
variable exists. `POST /ask`'s `model` field is validated *before* any
admission-control slot is acquired or LLM/DB work starts; an
unrecognized/disallowed value is an `HTTP 400`, not a graceful "failed"
run. This feature only ever threads `model` through the four SQL-pipeline
LLM calls — the embedding model, vision model, voice transcription/
synthesis, and RAG/web-search LLM calls are untouched.

**Remaining limitations:** model availability is not live-reloaded
mid-process (a `.env` change needs a restart). No per-role model
restriction exists — every role that can reach `/ask` can select any
configured/installed model. Insight/planning/review always use the exact
same model chosen for generation.

**Read [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#6-configurable-ollama-model-selection)**
for the full three-concept design, the `GET /models`/frontend
`ModelSelector.tsx` wiring, and a real `pydantic-settings` env-decoding
bug found and fixed while building this.


## Enterprise scalability/security assessment: identity-aware rate limiting, trusted-proxy IP resolution, structured logging, graceful shutdown (2026-09-27)
A dedicated architecture/scalability/security assessment (synthesizing,
not re-deriving, the Scale-out program above plus `docs/THREAT_MODEL.md`)
found and fixed six further, in-place gaps without introducing any new
infrastructure: opt-in trusted-proxy IP resolution
(`security/client_ip.py`, `Settings.trusted_proxy_count`), identity-aware
rate limiting extended to every remaining rate-limited route, structured
JSON logging, a liveness/readiness split (`GET /live` alongside
`GET /health`), graceful shutdown of the `/ask` thread pool, and a startup
warning when `MAX_CONCURRENT_ASK_REQUESTS` exceeds a replica's own
DB-pool capacity.

**Invariant that must not regress:** at `Settings.trusted_proxy_count`'s
default of `0`, client-IP resolution never reads `X-Forwarded-For` —
identical to pre-assessment behavior, locked in by
`tests/security/test_rate_limit_header_spoofing.py`. Only an operator who
explicitly sets the trusted-hop count opts into trusting that header.

**Read [`docs/ENTERPRISE_SCALABILITY_SECURITY_ASSESSMENT.md`](../ENTERPRISE_SCALABILITY_SECURITY_ASSESSMENT.md)**
(also summarized in
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#7-enterprise-scalabilitysecurity-assessment))
for the full report, current-state/target-state diagrams, and the honest
capacity statement — no million-user or specific-concurrent-user claim is
made anywhere in this pass's documentation.


## AI-guided (generative) image editing (2026-09-27)
Closes a real, previously-disclosed gap: the Edit-image modal's
"AI-guided editing" panel was real UI wired to a deliberate, permanent
stub. Provider: IMA Studio's `image_to_image` task category, chosen after
live verification — none of its 5 available models expose a native
mask/inpainting parameter, a real provider constraint, not worked around
silently.

**Invariants that must not regress:** IMA's lack of a mask channel is
handled by converting this app's own canonical mask into a translucent
red overlay for that one adapter only (`ImaImageEditProvider`) — disclosed
to the user via `ImageEditResult.warnings`, never presented as
pixel-exact masking. A completed edit is always stored as a **brand-new**
attachment (`register_derived_image`) — the original is never mutated.
Generated bytes are decoded/verified via Pillow before being stored —
untrusted output, exactly like "SQL is untrusted output" applied to
pixels. **No SQL-path leakage, structurally guaranteed**:
`attachments/ai_edit.py` imports neither `agent.graph` nor `agent.nodes`
and is reached only through its own dedicated REST route, never through
`/ask`. Only the 4 genuinely generative presets reach the paid endpoint —
blur and table-extraction route to the existing free/analysis endpoints
instead, never the AI-edit one.

**Known, disclosed limitations:** IMA's visual-overlay mask is a
best-effort convention — the model can still edit outside the highlighted
region. A live call against IMA's real infrastructure confirmed the
upload/request/error-propagation path but failed at generation itself
with IMA's own "insufficient points" business error (the configured
account has no credits) — **a successful live generation has not been
confirmed**, only the request path up to that point.

**Read [`docs/image-editing-architecture.md`](../image-editing-architecture.md)**'s
"AI-guided editing: the real implementation" section (also summarized in
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#8-ai-guided-generative-image-editing))
for the full design and every other disclosed limitation.


## Scale/performance hardening: result cache + per-database execution limiter (Prompt 22)
Two additions, both aimed at behavior under saturation rather than at the
common case:

- **`agent.rate_limit.get_database_execution_limiter`** — a per-configured-
  database in-flight execution cap, sized from that database's own
  `db_pool_size + db_max_overflow + database_concurrency_limit_overhead`.
  Distinct from the `/ask`-level limiters, which protect the API tier as a
  whole; this protects one customer database's connection pool. Checked by
  `agent.nodes.execute_sql_node` and `POST /execute` (both run real,
  row-returning execution), never by `db.query_cost`'s plan-only round
  trip. **Below saturation it never trips**, so it restricts nothing that
  currently works; at saturation it turns a blocking `QueuePool` checkout
  wait (up to SQLAlchemy's 30s `pool_timeout`) into an immediate, clearly
  worded rejection. A rejection is **never retried** —
  `last_error_category="database_busy"`, same reasoning as the TIMEOUT
  category: retrying into an already-saturated database spends retry budget
  on something a retry can't fix.
- **`db/result_cache.py`** — an opt-in (`ENABLE_RESULT_CACHE`, default
  **off**), short-TTL, bounded (TTL+LRU) cache for `POST /execute`'s
  `(columns, rows)`, keyed by `(tenant_id, database_name, sql)` and surfaced
  via an `X-Cache: HIT`/`MISS` response header. Off by default because
  caching trades data freshness, which is an operator decision, not a
  safe-by-default accuracy aid. **Never caches SQL referencing a
  `config.sensitive_columns`-classified "restricted" column**, regardless of
  the flag or the executing caller's own permission — the cache has no
  per-caller partitioning, so anything sensitivity-classified is excluded
  entirely rather than reasoning about who may see a hit. Never applied to
  the agent's own internal self-correction retries (those aren't shown to
  the user, and a retry changing the SQL text is the whole point of a
  retry). Exact-text match only, not semantic — the narrow, safe slice of
  `docs/SCALE_OUT_PROMPT.md` Phase 4's larger deferred idea.

Both feed three new **cumulative** (not rolling-window) counters on
`observability.metrics.MetricsSnapshot` — `result_cache_hits`/
`result_cache_misses`/`database_concurrency_rejections` — reusing that
module's existing singleton/lock rather than standing up a second metrics
object. Cumulative deliberately: a cache's value proposition is its hit
*rate* over the process's life, which a bounded window of the most recent
`max_requests` would understate for a long-running, low-traffic deployment.
