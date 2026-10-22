# Repository map — what lives in each package

The per-package folder conventions, moved verbatim out of the root
`CLAUDE.md` so a session loads them only when it needs them. This is the
authoritative description of **where** code lives and **why** each boundary
is drawn; `docs/architecture/overview.md` covers how the pieces behave at
runtime, and each feature's own `NN_*_CONTRACT.md` covers its design.

## Contents

- [Folder conventions](#folder-conventions)

---

## Folder conventions

- `config/` — all tunables (model name, Ollama host, DB connection fields,
  Chroma path, row limit, timeout, max retries) live in `config/settings.py`,
  sourced from `.env`. `DatabaseConnectionConfig.tenant_ids`
  (`DB_<NAME>_TENANT_IDS`) and `Settings.databases_for_tenant` are Prompt
  20's per-tenant connection binding; empty means shared by every tenant,
  which is what every existing `.env` gets. Never hardcode a model name, path, or connection
  detail anywhere else — import from here. `Settings` (a
  `pydantic_settings.BaseSettings` — see "Pydantic-based configuration and
  validation" below) is a passive config bag; it validates *individual*
  malformed values (e.g. `DB_PORT=abc`) at load time but does not require DB
  fields to be present just to import the module — `db/connection.py`
  validates *combinations* (e.g. "DB_TYPE set but DB_HOST missing") at the
  point something actually tries to connect.
- `db/` — `connection.py` owns the SQLAlchemy engine lifecycle: builds the
  connection URL from config (`build_connection_url`), exposes a cached
  `get_engine()`/`get_read_only_engine()`, and `test_connection()` (a
  `SELECT 1` round-trip with best-effort failure classification — auth,
  host-unreachable, db-not-found, driver-missing, timeout, unknown). Every
  one of these accepts either the legacy global `Settings` or one specific
  `Settings.databases` entry (a named connection — see `DbConnectionLike`),
  so the same functions serve both a plain single-database setup and a
  multi-database one; `get_connection(settings, name)` looks one up by
  name. `schema_introspection.py` is the **sole source of truth** for schema
  shape: `introspect_schema(engine, schema)` uses SQLAlchemy's `Inspector`
  to pull real tables/columns/types/FKs and synthesizes a compact
  `CREATE TABLE`-style DDL string per table (for LLM prompt consistency,
  not necessarily valid executable DDL). `get_schema_fingerprint()` hashes
  that output for Chroma cache invalidation. **Prompt 06**
  (`06_DATABASE_DISCOVERY_CONTRACT.md`) extended it: views are discovered
  alongside tables (`TableSchemaInfo.is_view`, rendered as `CREATE VIEW`);
  `ColumnInfo` gained `default`/`is_computed`/`is_identity`/`length`/
  `precision`/`scale`, all sourced from data the `Inspector` already
  returns — no new query; one table/view's own introspection failing no
  longer aborts the whole database (logged and skipped instead). `db/
  row_count_estimate.py` (new) adds an opt-in (`include_row_counts=True`),
  catalog-only approximate row count per table/view (`sys
  .dm_db_partition_stats`/`pg_stat_user_tables`/`information_schema
  .tables.TABLE_ROWS`/`ALL_TABLES.NUM_ROWS` depending on `DB_TYPE`) —
  deliberately never a `SELECT COUNT(*)`, and off by default so every
  existing caller's per-refresh cost is unchanged unless it opts in.
  **Prompt 07** (`07_RELATIONSHIP_INTELLIGENCE_CONTRACT.md`) added
  `TableSchemaInfo.unique_constraints` (from `Inspector
  .get_unique_constraints`, same cost class as PK/FK) and
  `relationship_inference.py` (new) — infers candidate FK-shaped
  relationships sqlglot never declared, from structural signals only by
  default (name convention, type compatibility, target-column
  uniqueness — no query) plus an explicitly opt-in, bounded data-driven
  refinement (`verify_candidates_with_data`, null-fraction + sampled
  value-overlap, never a full scan). See "Relationship & schema
  intelligence" below.
  `execution.py` owns read-only
  SQL execution mechanics (`execute_readonly_sql` — background-thread
  timeout enforcement plus a `fetchmany()` row cap), shared by
  `agent.nodes.execute_sql_node` and `api/main.py`'s `POST /execute`
  ("Confirm and Run") path — a pure database-execution concern with no
  LangGraph dependency, so it lives here rather than in `agent/`.
  `adapter.py` (Prompt 03 of the platform initiative,
  `03_DATABASE_ADAPTER_CONTRACT.md`) consolidates capability facts
  otherwise scattered across three dicts in three files
  (`SUPPORTED_DB_TYPES`, `execution.py`'s `_STATEMENT_TIMEOUT_SQL`,
  `query_cost.py`'s `_STRATEGIES`) into one `DatabaseCapabilities` object,
  plus a `DatabaseAdapter` Protocol and its one real implementation,
  `SqlAlchemyDatabaseAdapter` — a zero-logic wrapper over this folder's
  own existing functions. Not called by `agent/nodes.py` or any route in
  this increment; the acceptance criterion it targets ("a new engine
  needs no AI-layer changes") already held before this module existed,
  per that doc's own honest framing. **Prompt 04** (`04_SQL_SERVER_
  ADAPTER_CONTRACT.md`) found and fixed the one place that zero-logic
  claim wasn't quite true: `execute_readonly` now applies
  `agent.sql_validator.qualify_table_schema` first, exactly like
  `execute_sql_node`/`POST /execute` already do — without it, a
  schema-qualified connection (this project's own real `HrAutomationDb`
  example, see the Python-3.14-gotchas note above) would have silently
  failed every query through this still-unwired adapter. **Prompt 19**
  (`19_QUERY_STORE_PERFORMANCE_CONTRACT.md`) added `query_store.py` — a
  SQL-Server-only, fully optional module reading Query Store's own DMVs
  for high-cost/repeated/regressed query evidence, feeding
  `recommendation/engine.py`'s existing `DATABASE_PERFORMANCE` category.
  See "SQL Server Query Store performance intelligence" below.
- `embeddings/` — `schema_indexer.py`'s `build_index(tables, db_name, ...)`
  takes already-introspected `TableSchemaInfo` objects (not a file, not an
  engine) and embeds them into **that database's own Chroma collection**
  (never a shared one — see `get_collection`'s docstring for why), keyed by
  a hash of the introspected schema so re-embedding only happens when that
  database's schema actually changed. **Prompt 06**
  (`06_DATABASE_DISCOVERY_CONTRACT.md`) made this genuinely incremental: a
  per-database JSON manifest (`.schema_manifest__<db_name>.json`, replacing
  the old bare-hash file — no migration needed, an orphaned old file is
  just never read again) also stores a per-*table* fingerprint, so a
  change to one table triggers a targeted `collection.upsert`/`delete` for
  just that table instead of deleting and rebuilding the entire
  collection. `get_last_discovery_diff(db_name, settings)` reads back
  what changed (added/removed/changed table names) without touching
  `build_index`'s own signature/return type. `refresh_schema_index(engine, db_name,
  settings, force)` is the single introspect → sample → embed pipeline for
  one database; `refresh_all_schema_indexes(settings, force)` runs it for
  every configured database and is what `scripts/build_embeddings.py` and
  `api/main.py`'s startup/`POST /schema/refresh` actually call — that route's
  response now additionally reports `view_count`/`added_tables`/
  `removed_tables`/`changed_tables`/`last_discovered_at` per database (new,
  optional fields; existing fields unchanged). `retriever.py`'s
  `retrieve_relevant_schema(question, db_name, ...)` does top-k similarity
  search over one database's table-level DDL chunks — unchanged in shape
  from before, just explicitly scoped to one database's collection now.
  `retriever.py`'s `select_database(question, settings)` is the
  auto-router: with one configured database it short-circuits immediately
  (no behavior change); with several, it compares each database's own
  best-matching table and picks the winner, before per-table retrieval
  runs. See "Multi-database auto-routing" below. `golden_examples.py` is a
  sibling collection on the same Chroma client/persist dir, one per
  configured database (`f"golden_examples__{db_name}"`) — see "Golden-dataset
  feedback loop" below.
- `retrieval/` — the business-context vector-retrieval layer (table/
  column/relationship/glossary/metric/sql_example/documentation chunks) —
  see "Business-context vector retrieval" below for the full design.
  `models.py` (the typed `Chunk` model + deterministic ID/content-hash
  generation), `chunking.py` (schema + `data/knowledge/*` → chunks),
  `embeddings.py` (provider abstraction: `"local"` reuses `embeddings
  .schema_indexer`'s own embedding function, `"fake"` for tests),
  `vector_store.py` (the `VectorStore` interface + its Chroma
  implementation), `reranker.py` (deterministic similarity + type-priority
  + exact-match + diversity scoring, no external reranker model),
  `retriever.py` (the orchestration `agent.nodes.retrieve_business_context_node`
  calls — fails open on any failure, see that module's own docstring),
  `ingestion.py` (idempotent discover → chunk → hash → embed → upsert,
  driven by `scripts/ingest_schema.py`/`scripts/rebuild_index.py`).
  Deliberately reuses `embeddings.schema_indexer.get_chroma_client`'s
  cached `PersistentClient` rather than a second vector database.
  **Prompt 07** (`07_RELATIONSHIP_INTELLIGENCE_CONTRACT.md`) added
  `chunking.inferred_relationship_chunks_from_schema` — same
  `ChunkType.RELATIONSHIP` a real, declared FK's chunk uses, but every
  inferred one's *text* (never just `extra`, which the LLM never sees)
  opens with "CANDIDATE relationship (NOT a declared foreign key —
  inferred, not confirmed...", unmistakably distinct wherever the two are
  retrieved together. `ingestion.build_schema_chunks` calls it by
  default (`Settings.enable_relationship_inference`, structural-only,
  no query); `run_ingestion` additionally runs the opt-in, data-verified
  pass when it has a live engine (see "Relationship & schema
  intelligence" below). **Prompt 09** (`09_SEMANTIC_CATALOG_CONTRACT.md`)
  added an 8th type, `ChunkType.BUSINESS_CONCEPT` —
  `chunking.business_concept_chunk_from_catalog_entry` renders a
  **published** `semantic.catalog.CatalogEntrySnapshot` into a chunk (a
  draft/reviewed one never reaches this function);
  `ingestion.sync_catalog_entry_to_vector_store` embeds+upserts it (and
  deletes a just-superseded version's own chunk) synchronously from
  `api/semantic_catalog.py`'s publish route, reusing the exact same
  `EmbeddingProvider`/`VectorStore` primitives `run_ingestion` itself
  uses — never a second ingestion pipeline. See "Tenant-aware semantic
  catalog" below. **Prompt 10** (`10_GOVERNED_METRICS_CONTRACT.md`) added
  `retriever.extract_governing_metrics(items)` — filters already-
  retrieved `BUSINESS_CONCEPT` chunks (zero new query) down to
  `METRIC`-typed ones, feeding `AgentState["governing_metrics"]`. See
  "Governed metrics, dimensions & semantic contracts" below.
- `agent/` — LangGraph nodes live in `nodes.py`, one function per node, each
  taking and returning `AgentState` (defined in `state.py`). `graph.py`
  wires them together and compiles the graph. `sql_validator.py` is the
  security boundary — see below. `AgentState["selected_database"]` records
  which configured database a question was auto-routed to (set once by
  `retrieve_schema_node`); the sqlglot dialect used for validation/cost
  estimation is resolved from *that* database's `db_type`
  (`db.connection.get_connection(settings, selected_database)` +
  `get_sqlglot_dialect()`) — never a single hardcoded/global engine.
  `AgentState["selected_model"]` is the identical pattern applied to Ollama
  model selection (`model_registry.py` — configured/allowed vs. installed
  vs. selectable, see "Configurable Ollama model selection" below): resolved
  once by `run_agent()`, read (never re-selected) by every LLM call
  `nodes.py` makes for that question.
  `complexity.py` is the source of the adaptive retry budget and the
  planning/review gate (`detect_complexity_signals`/`compute_max_retries`,
  see "Agentic query planning + plan-conformance self-correction" below) —
  a cheap regex heuristic, no LLM call, computed once by `run_agent()` per
  question. **Prompt 10** (`10_GOVERNED_METRICS_CONTRACT.md`) added
  `review_metric_conformance_node` (between `review_sql` and
  `validate_sql`) — gated on `AgentState["governing_metrics"]` being
  non-empty, **independent** of `complexity_signals`/`query_plan`'s own
  gate (a simple KPI question like "what's our revenue?" has no
  complexity signal at all, but can still have a governing metric to
  enforce). See "Governed metrics, dimensions & semantic contracts"
  below. **Prompt 11** (`11_ANALYTICAL_INTENT_CONTRACT.md`) added
  `intent.py` (new) — the typed `AnalyticalIntentType`/
  `AnalyticalIntentClassification`/`intent_implies_planning` contract —
  and `classify_analytical_intent_node` (between `retrieve_business_context`
  and `plan_query`, runs on every question, unlike the complexity-gated
  nodes above). See "Analytical intent classification" below.
- `agent/orchestrator/` — the multi-source router, one level up from
  `agent/`'s own SQL-only graph, never the other way around: `nodes.py`
  (`router_node`/`classify_sources` — LLM classification only when 2+
  sources are configured, else a zero-cost short-circuit; `sql_subgraph_node`,
  which calls `agent.graph.run_agent` as an opaque function, never
  reimplementing any of it; `document_rag_node`/`policy_rag_node`/
  `web_search_node`; `synthesis_node`, a pure pass-through unless 2+ sources
  actually fired), `state.py` (`OrchestratorState` *extends* `AgentState`,
  never replaces it — see "Multi-source orchestration" below), `graph.py`
  (`run_orchestrated`, the single entry point `api/main.py` calls in place
  of `agent.graph.run_agent` directly).
- `agent/tools/` — a generic, MCP-shaped tool abstraction (`types.py`'s
  `Tool`/`ToolCategory`/`RetryPolicy`/`ToolResult`, `registry.py`'s
  `ToolRegistry`, `definitions.py`'s `build_default_registry()`) wrapping
  the same six real functions `agent/orchestrator/nodes.py` already calls
  (`agent.graph.run_agent`, `rag.graph.run_rag` × 2 collections,
  `search.web_search.web_search`, `media.search.search_media`,
  `agent.orchestrator.nodes.execute_generation`) — see "Tool/MCP
  abstraction" below. **Additive, not a replacement**: the orchestrator
  graph's own hardcoded nodes are unmodified and still the only thing
  `run_orchestrated` actually calls in production today.
- `agent/provenance.py`, `analytics/`, `recommendation/`, `semantic/` —
  typed contracts for the Enterprise AI Analytics & Recommendation
  Platform initiative (`00_MASTER_IMPLEMENTATION_CONTRACT.md`):
  `agent/provenance.py`'s `DataTruthLevel`/`ProvenancedClaim`
  (DATABASE_FACT/AI_INFERENCE/CONFIRMED_BUSINESS_TRUTH); `analytics/`'s
  `AnalyticsProvider` + a real adapter over `agent.insight.ResultSummary`'s
  already-computed, never-rendered `trend`/`outliers`/`stddev`;
  `recommendation/`'s `RecommendationProvider` (contracts only, no
  implementation exists until Prompt 17, see below); `semantic/metrics.py`'s governed
  `MetricDefinition`/`MetricRegistry`, loading the same
  `data/knowledge/metrics.yaml` `retrieval/` already parses for fuzzy
  retrieval, deliberately not a reuse of `retrieval.models.ChunkType
  .METRIC`. Same "additive, not wired into any live route yet" posture as
  `agent/tools/` above — see `02_TARGET_ARCHITECTURE.md` for the full
  design and its explicit "stubbed today, wired later" table. **Prompt 09**
  (`09_SEMANTIC_CATALOG_CONTRACT.md`) closed that table's two named
  metric-specific gaps for good, plus a materially broader scope: `semantic
  /catalog.py`'s `CatalogEntrySnapshot`/`CatalogStatus` (a real, persisted,
  tenant-aware draft→reviewed→published→superseded business-concept
  catalog — entities/metrics/dimensions/domains, not metrics alone) and
  `semantic/catalog_policy.py`'s RBAC+ABAC. See "Tenant-aware semantic
  catalog" below. **Prompt 16** (`16_FORECASTING_CONTRACT.md`) added
  `analytics/forecasting.py` and is the first real producer of
  `recommendation.models.Recommendation` anywhere in this codebase
  (`recommendations_for_forecast`) — `recommendation/`'s own
  `RecommendationProvider` Protocol remains contracts-only, unimplemented;
  this is a standalone function reusing its typed `Recommendation`/
  `RecommendationKind` shape directly, not an implementation of that
  Protocol (its own docstring discloses why — see "Deterministic
  forecasting" below). **Prompt 17**
  (`17_RECOMMENDATION_ENGINE_CONTRACT.md`) added the first real,
  general-purpose implementation: `recommendation/engine.py`'s
  `generate_recommendations` — a Data→Finding→Evidence→Rule→Candidate→
  Validation→Confidence→Recommendation pipeline across nine categories,
  wired live into `agent/graph.py` as `generate_recommendations_node`.
  `RecommendationProvider` itself is still unimplemented (the engine is a
  standalone module, not that Protocol's implementation — its own
  `recommend(summary: ResultSummary)` signature still doesn't fit this
  engine's own, richer multi-source input shape). See "Evidence-first
  recommendation engine" below. **Prompt 18**
  (`18_RECOMMENDATION_GOVERNANCE_CONTRACT.md`) added
  `recommendation/governance.py` (the eight-state feedback lifecycle) and
  `recommendation/governance_policy.py` (RBAC+ABAC) on top — a quality-
  measurement layer over already-produced recommendations, not a change
  to how/whether Prompt 17's engine produces them. See "Recommendation
  governance & feedback" below. **Prompt 19**
  (`19_QUERY_STORE_PERFORMANCE_CONTRACT.md`) added two more
  `DATABASE_PERFORMANCE` rules to `recommendation/engine.py`
  (`QueryStoreHighCostRepeatedQueryRule`/`QueryStoreRegressionRule`),
  reusing the existing category rather than inventing a new one — see
  "SQL Server Query Store performance intelligence" below.
- `rag/` — document/policy agentic RAG, one implementation shared by both
  the "documents" and "policies" collections (parameterized by collection
  name, not two near-duplicate modules): `store.py` (SQL Server native
  `VECTOR` storage, a dedicated connection — see `Settings.
  rag_store_connection_string`'s docstring for why it's never one of
  `DB_CONNECTIONS`), `ingestion.py` (PDF extract via `pypdf` → chunk →
  embed → store, flags likely-scanned pages as OCR-needed rather than
  silently indexing nothing), `embedding.py` (shared embedding function,
  reused by ingestion *and* retrieval so both live in the same vector
  space), `retriever.py` (top-k retrieval + LLM relevance grading + query
  rewrite), `graph.py` (`build_rag_subgraph`/`run_rag` — retrieve → grade →
  rewrite/retry (bounded) → generate-with-citations, or an
  insufficient-information fallback; a "policies" chunk carrying a
  `sensitivity_category` is never summarized into an answer — a fixed,
  reviewed category this app hard-blocks regardless of caller role, layered
  underneath the newer per-document `restricted_roles` RBAC gate — see
  "Authentication, authorization, and the 2026 security hardening passes"
  below).
- `search/` — live web search, `web_search.py`: a provider-name → call-
  function dict (`SUPPORTED_SEARCH_PROVIDERS`), shaped exactly like
  `db.connection.SUPPORTED_DB_TYPES` — swapping `WEB_SEARCH_PROVIDER` is a
  `.env` change, not a code change. Only `tavily` is implemented today.
- `sources/` — the source-type contract and registry. **Single owner of
  each source's routing description, required permission and
  "is it configured" rule**: `agent/orchestrator/nodes.py` derives
  `_SOURCE_DESCRIPTIONS`, `SOURCE_PERMISSIONS` and `get_available_sources`
  from this table rather than keeping three parallel literals that could
  drift. `descriptors.py` declares only sources that actually exist;
  `types.py` also holds a `SourceConnector` lifecycle Protocol with no
  production implementer yet (`mock.py` + the conformance suite are what
  stop it being aspirational), and `registry.py` keeps "configured" and
  "authorized" as separate questions because they have different remedies.
  Design: `docs/architecture/source-connector-contract.md`.
- `rest/` — REST/OpenAPI connector: a fixed set of operator-declared HTTPS
  endpoints, read-only, off by default (`ENABLE_REST_CONNECTOR`). A
  question's text never reaches a URL — endpoints are fixed and filtering
  happens on what comes back. **Not routable yet**: no `sources/descriptors.py`
  entry and no orchestrator node, so `GET /sources` does not advertise it.
  The SSRF controls live in `security/outbound_url.py`, not here.
- `mcp_client/` — a client **of** remote MCP servers, over stdio. The
  opposite direction to `agent/tools/` (which is MCP-*shaped* so this app's
  own capabilities could be exposed *to* a client) — no shared code, and a
  remote tool is never registered into the local `ToolRegistry`. Off by
  default (`ENABLE_MCP_CLIENT`). `trust.py` is the point of the package:
  administrator-controlled trust only, an empty per-server allowlist
  approves nothing, and a server's own metadata cannot widen its access.
  `protocol.py` is pure and I/O-free so the wire format is testable without
  a subprocess; `transport.py` is the only production code here that spawns
  one, which is why it passes no shell and inherits no environment.
- `security/outbound_url.py` — SSRF-safe outbound validation and a bounded
  JSON GET for *new* callers: HTTPS-only, a host allowlist checked **before**
  DNS, every resolved address checked against the private/reserved
  blocklist, and redirects re-validated hop by hop. Two older copies of the
  blocklist exist (`media_gen/download.py`, `bi_integration/outbound_safety.py`)
  for documented reasons; `tests/test_outbound_url.py` asserts all three
  match, so divergence fails CI rather than going unnoticed.
- `agent/progress.py` — per-node progress events for `POST /ask/stream`.
  Emitted from `agent/nodes.py::_timed_node`, which already computes stage,
  attempt, duration and outcome, so no node body knows this exists. With no
  sink bound — every caller except an SSE stream — it is a contextvar read
  and an early return. An event carries metadata only: it reaches the client
  *before* the result gate runs, so it never carries SQL, a row, or an error
  message.
- `attachments/` — chat file attachments (images, PDF, DOCX, XLSX, PPTX,
  TXT, MD, CSV, JSON) given directly to the model as context for the
  question that attached them — see "Chat attachments" below for the full
  design. `models.py` (`Attachment`/`ProcessedAttachment`/`AttachmentError`),
  `validation.py`, `storage.py` (sanitized on-disk storage, id-only paths),
  `store.py` (the bounded, in-memory, owner-scoped `AttachmentStore`),
  `image_processing.py`/`vision.py` (normalize → data URL; local Ollama
  vision description with an OCR fallback), `processors/` (one
  `FileProcessor` per file kind + a registry), `context_builder.py`
  (delimited, truncation-safe prompt context), `pipeline.py` (the
  validate → scan → store → process orchestration `api/attachments.py`
  and `graph.py` both call, plus `register_derived_image` for a resize/
  remove-text output), `state.py`/`graph.py` (the attachment-QA LangGraph
  subgraph, wired into `agent/orchestrator/` as a new `"attachments"`
  source). `ocr_extract.py`/`image_ops.py`/`inpaint.py`/`capabilities.py`
  are the explicit image-action modules (OCR-with-regions, deterministic
  resize, classical-inpainting text removal, and the live capability
  registry, respectively) — see "Chat attachments, image actions, and
  their security hardening" below for the full design.
- `frontend/` — the React + Vite + TypeScript + Tailwind dashboard, the
  only UI this project ships (see the "History note" near the top of this
  file for the Streamlit app it replaced). `src/pages/Chat.tsx` is the main
  chat page; `src/pages/KnowledgeSources.tsx` is PDF upload/management for
  the "documents"/"policies" collections (Vite/react-router's equivalent of
  a second page, mirroring what used to be a separate Streamlit multipage
  script). `src/store/chatStore.ts` (Zustand) owns conversation state —
  `queryHistory` is the array of turns for whichever conversation is
  currently on screen, and `conversations` is every conversation started
  this session (keyed by id), auto-saved as `queryHistory` changes
  (`commitQueryHistory`); switching conversations swaps which one
  `queryHistory` points at. This is in-memory/session-only, same as the
  Streamlit app's own history was — a page reload clears it, by design, not
  as a regression (server-backed history for a locally-authenticated user
  is a separate, additional layer — see "Universal server-side chat
  history" below; `chatStore` is what mirrors that server state into the
  UI, not a replacement for it). `src/components/layout/AppShell.tsx` is
  the full-viewport shell: a persistent left conversation-history rail
  (`Sidebar.tsx`, `lg:`+ viewports) + main workspace, with a compact header
  (product mark, nav, theme toggle, gear icon) above the workspace only.
  Below `lg:`, the header's hamburger (`MobileNav.tsx`) opens the same
  `Sidebar` in a slide-in drawer instead — one history-list implementation,
  not two kept in sync. **2026 UI pass** (see "Frontend UI redesign"
  below): this replaced an earlier single combined right-side
  `HistoryDrawer.tsx` (history + settings behind one gear icon, now
  deleted) — settings are now `SettingsDialog.tsx`, reachable from the
  header gear and the Sidebar's own footer, wrapping the same
  `HistorySettingsSection.tsx` content unmodified. `HistorySettingsSection.tsx`
  holds everything that isn't chat history itself: appearance
  (theme/accent via `src/lib/theme.ts`; the application and code/SQL fonts
  via `src/lib/fonts.ts`, which owns both allowlists and their lazy
  self-hosted loading; language), per-database connection
  status, a manual re-test, a manual schema refresh, a schema browser
  grouped by database, and the "Generate AI insight" toggle.
  `src/components/chat/TurnCard.tsx` renders one
  question+answer turn — schema context and the generated SQL are both
  collapsible (`src/components/ui/expander.tsx`), and technical metadata
  (which database a question was routed to) sits inside a collapsed
  "Query information" panel rather than as a always-visible label. Every
  SQL-specific render (schema context, editable SQL box, Confirm and Run,
  results table) is gated on `"sql"` actually being one of
  `state.sources_used` (or that key being absent entirely, which implies
  the router is off) — a pure web/document/policy answer renders through a
  separate path instead (`SourcesUsedPanel.tsx`), never through the
  SQL-specific one. `POST /execute` (`api/main.py`) re-validates and
  re-executes the *current* SQL text fresh every time "Confirm and Run" is
  clicked — see "SQL is untrusted output, always" below for the nuance
  around the agent's own internal self-correction executions, which are
  never shown to the user. In development, `vite.config.ts`'s
  `BACKEND_ROUTES` proxies `/ask`/`/execute`/`/documents`/`/schema`/
  `/feedback`/`/health`/`/media`/`/generate`/`/voice`/`/search`/`/auth` to
  the API on port 8000 so the browser never needs CORS (`/auth` added
  alongside the local-account feature — see "Local self-hosted accounts"
  below; the chat-history routes, `/conversations`/`/chat`, use the same
  no-prefix same-origin convention and don't need a separate
  `BACKEND_ROUTES` entry since they're plain top-level paths already
  covered by Vite's dev-server default proxying of anything not matched by
  a static asset); in production, `npm run build`'s output
  (`frontend/dist`) is served by that same API process (`api/main.py`'s
  `StaticFiles` mount) at the same paths, so the frontend's own fetch calls
  never need an `/api` prefix or environment-specific base URL.
- `identity/` — this app's own self-hosted user accounts (optional, off by
  default — `Settings.local_auth_enabled`), the *only* ORM (SQLAlchemy 2.0
  declarative) and the *only* real (Alembic) migrations anywhere in this
  codebase. **Prompt 20** added `identity.models.Tenant` (26 tables now)
  plus `users.tenant_id`/`conversations.tenant_id` and
  `identity/repositories/tenants.py` — see "Multi-tenant enterprise
  architecture" below — see `identity/__init__.py`'s own docstring for why this is a
  deliberate exception to the rest of the codebase's "raw SQLAlchemy Core +
  idempotent `ensure_schema()`" convention. `models.py` (26 tables:
  `Tenant` (Prompt 20 -- the isolation boundary every tenant check now
  resolves from),
  accounts/RBAC, sessions/tokens, `Conversation`/`Prompt`/`AiOutput`
  for chat history, **Prompt 08**
  (`08_ONBOARDING_ENGINE_CONTRACT.md`)'s `OnboardingJob`/
  `OnboardingReviewItem`/`OnboardingArtifact` (see "Client-database
  onboarding engine" below), **Prompt 09**
  (`09_SEMANTIC_CATALOG_CONTRACT.md`)'s `SemanticCatalogEntry` — one row
  per **version** of one governed business concept, never mutated in
  place once published (see "Tenant-aware semantic catalog" below)), and
  **Prompt 18** (`18_RECOMMENDATION_GOVERNANCE_CONTRACT.md`)'s
  `RecommendationRecord`/`RecommendationFeedbackEvent` — an immutable
  recommendation snapshot plus its own append-only feedback/audit event
  log (see "Recommendation governance & feedback" below),
  `security.py` (Argon2id hashing, JWT issue/validate,
  opaque refresh tokens), `password_policy.py` + `display_name.py`
  (mandatory-display-name + password-strength validation — see
  `docs/authentication-and-password-policy.md`), `repositories/` (one
  module per aggregate: `users.py`, `sessions.py`, `tokens.py`,
  `signin_events.py`, `history.py` — the chat-history repository, see
  "Universal server-side chat history" below; `onboarding.py` — Prompt 08's
  own plain CRUD for the three tables above, zero authorization logic,
  mirroring `repositories/shares.py`'s own identical split; `semantic_catalog.py`
  — Prompt 09's identical-shape CRUD for `SemanticCatalogEntry`,
  including version/supersession bookkeeping; `recommendation_governance.py`
  — Prompt 18's CRUD + enforced-transition logic for
  `RecommendationRecord`/`RecommendationFeedbackEvent`, plus the one
  read-only quality-metrics aggregate), `rbac.py`
  (granular permission codes bridging into `agent/authz.py`'s own base
  role names — Prompt 08 added `ONBOARDING_MANAGE`/`ONBOARDING_REVIEW`,
  Prompt 09 added `CATALOG_MANAGE`/`CATALOG_REVIEW`, Prompt 18 added
  `RECOMMENDATION_REVIEW`/`RECOMMENDATION_MANAGE`),
  `migrations/` (Alembic, `identity/alembic.ini` — run via `alembic -c
  identity/alembic.ini upgrade head`). Tests against this package use a
  real in-memory SQLite engine (`Base.metadata.create_all`), never a mock
  of the ORM — production always runs against PostgreSQL
  (`AUTH_DATABASE_URL`), a dedicated database, never one of
  `DB_CONNECTIONS`.
- `onboarding/` (Prompt 08, `08_ONBOARDING_ENGINE_CONTRACT.md`) — the
  client-database onboarding engine: profiling, PII detection, semantic
  inference, SME review, and a draft semantic contract for a database the
  platform operator is not the SME of. See "Client-database onboarding
  engine" below for the full design; `api/onboarding.py` is its REST
  surface.
- `semantic/catalog.py`/`catalog_policy.py` (Prompt 09,
  `09_SEMANTIC_CATALOG_CONTRACT.md`) — the tenant-aware, draft→reviewed→
  published→superseded governed semantic catalog connecting technical
  metadata to business meaning (entities/metrics/dimensions/domains).
  See "Tenant-aware semantic catalog" below for the full design;
  `api/semantic_catalog.py` is its REST surface, `identity.models
  .SemanticCatalogEntry`/`identity.repositories.semantic_catalog` its
  persistence.
- `api/` — `main.py`'s FastAPI app is the REST surface every UI action
  goes through, and (once `frontend/dist` exists) also the process that
  serves the React dashboard itself. A `lifespan` context manager warms
  every process-lifetime singleton at startup rather than on whichever
  request happens to arrive first — every configured database's read-only
  engine, the cached Ollama client, and the compiled SQL/orchestrator
  LangGraph graph(s) — and stashes them on `app.state` for
  discoverability, though request handling itself still reaches them
  through the same cached module-level functions `eval/runner.py` uses,
  not through `app.state` (see "Process-lifetime singletons" below). Two
  global exception handlers
  (`@app.exception_handler(AgentError)` /
  `@app.exception_handler(Exception)`) are the last-resort net ensuring no
  response body ever contains a raw internal exception string — every
  known failure path already uses `.safe_message` locally (see "Centralized
  exception handling" below), so in normal operation these only fire for a
  genuinely new/forgotten call site, not the common case. `/ask` accepts an
  optional `session_id` (`AskRequest.session_id`), generating one via
  `uuid4()` when omitted, and always echoes it back in `AskResponse
  .session_id` — a pure correlation token (the multi-source router's own
  per-session cost-ceiling key), unrelated to real conversation storage.
  `AskRequest.conversation_id`/`AskResponse.conversation_id` are the
  *actual* server-side identifier — set only for a locally-authenticated
  caller, persisted via `api/chat_persistence.py::persist_ask_turn` into
  `identity.models.Conversation`/`Prompt`/`AiOutput` (see "Universal
  server-side chat history" below); this is the one place `/ask` is no
  longer fully stateless, though only conditionally, per-caller.
  `api/chat_history.py` (`GET/POST/PATCH/DELETE /conversations`, `GET/POST
  /conversations/{id}/messages`, `GET /chat/search`) is the read/manage
  surface for that same data. `api/onboarding.py` (Prompt 08,
  `08_ONBOARDING_ENGINE_CONTRACT.md`) is the REST surface for the
  client-database onboarding engine — 10 routes under `/onboarding`; see
  "Client-database onboarding engine" below. `api/semantic_catalog.py`
  (Prompt 09, `09_SEMANTIC_CATALOG_CONTRACT.md`) is the REST surface for
  the tenant-aware semantic catalog — 8 routes under `/semantic-catalog`;
  see "Tenant-aware semantic catalog" below. `api/recommendation_governance.py`
  (Prompt 18, `18_RECOMMENDATION_GOVERNANCE_CONTRACT.md`) is the REST
  surface for the governed recommendation lifecycle — 7 routes under
  `/recommendations`, deliberately with no create route (only the live
  `/ask` pipeline creates a record); `api/recommendation_persistence.py`
  is that write path, wired into `POST /ask` right alongside
  `api/chat_persistence.py`. See "Recommendation governance & feedback"
  below.
- `scripts/` — standalone entry points: `test_db_connection.py` (verify
  `.env` before booting anything else — prints pass/fail, DB version, table
  count, or a classified readable error, per configured database),
  `build_embeddings.py` (introspect + embed every configured database, with
  `--force`), `integration_test.py` (manual, requires real database(s), not
  part of the pytest suite — see `tests/` below; also demonstrates routing
  when 2+ databases are configured), `run_benchmark.py` (the Text-to-SQL
  benchmark runner — see `eval/` below; also manual/real-DB-required, not
  part of the pytest suite), `build_user_guide_pdf.py` (rebuilds
  `docs/User_Guide.pdf` from `USER_GUIDE.md` via `reportlab` — the actual
  source of truth for that PDF; re-run after every `USER_GUIDE.md` edit.
  Previously a standalone binary with no checked-in source at all, three
  revisions hand-authored outside the repo — this replaced that with a
  reproducible build, closing a real doc-drift gap
  `docs/PRODUCTION_READINESS_REPORT.md` had flagged).
- `eval/` — the Text-to-SQL benchmark: `schema.py` (dataset + result data
  model), `dataset_loader.py` (loads `eval/benchmark/*.yaml`),
  `evaluators.py` (grades one case — **execution-accuracy first**: gold
  SQL is executed live and the agent's actual result set is compared
  against it, never SQL-text similarity alone), `metrics.py` (reduces
  graded results into the named benchmark metrics), `runner.py` (drives
  `agent.graph.run_agent` for every case), `reporting.py` (markdown report
  + JSON baseline serialization), `regression.py` (compares a run against
  `eval/baselines/latest.json`). `eval/benchmark/*.yaml` holds the actual
  cases, split by difficulty/category; `eval/eval_questions.yaml` and
  `scripts/run_eval.py` are the superseded predecessor, kept unmodified —
  see both files' deprecation notes. Each case's `database:` field is
  currently an inert descriptive label (e.g. `AdventureWorksDW2025`), not a
  `Settings.databases` connection name — the benchmark runner resolves one
  engine/dialect globally, same as before multi-database support existed.
  Routing the benchmark itself per-case is a known, deliberately
  out-of-scope follow-up (see the multi-database auto-routing design note
  below). **Prompt 11** (`11_ANALYTICAL_INTENT_CONTRACT.md`) added
  `BenchmarkCase.expected_intent`/`expect_ambiguity` (optional, hand-
  labeled golden-intent hints), `CaseRunResult.observed_intent`/
  `observed_ambiguity_flags`/`intent_classification_correct`, and
  `evaluators.evaluate_intent_classification`/`metrics.py`'s
  `intent_classification_accuracy` — diagnostic only, exactly like
  `sql_exact_match`, never folded into `overall_pass`/`final_accuracy`.
- `tests/` — pytest, all fully mocked, no real DB or Ollama required.
  `conftest.py`'s autouse `_isolate_settings_from_real_environment` fixture
  strips every env var one of `Settings`'s fields could read before each
  test, so a test building `Settings(**partial_kwargs)` never silently
  inherits this developer's own real `.env` (see "Pydantic-based
  configuration and validation" above for why that isolation is needed at
  all now). Mirrors package names (`test_sql_validator.py`, `test_connection.py`,
  `test_schema_introspection.py`, `test_schema_retriever.py`,
  `test_agent_nodes.py` — including `TestPlanQueryNode`/`TestReviewSqlNode`,
  `TestRetrieveGoldenExamplesNode`, and the nested-aggregate
  `TestValidateSqlRejectsNestedAggregates` cases,
  `test_complexity.py` (the adaptive-retry-budget heuristic, including the
  "top N *by* metric" false-positive regression case),
  `test_llm_client_planning.py` (the plan/review response parsers'
  fail-open contracts, and the golden-examples prompt-block ordering), and
  `test_golden_examples.py` (the golden-dataset store itself — id
  determinism/upsert idempotency, similarity filtering, fail-open on a
  Chroma error) — plus `test_db_router.py` (the multi-database
  auto-router, `embeddings.retriever.select_database`, and
  `retrieve_schema_node`'s retry-reuses-the-same-database contract),
  `test_orchestrator.py` (source availability, LLM classification parsing +
  fallback, single-source short-circuit, multi-source fan-out/synthesis,
  and the `ENABLE_MULTI_SOURCE_ROUTER`-off pass-through guarantee — all
  mocked at the `rag.llm.call_ollama`/`rag.graph.run_rag`/
  `search.web_search.web_search` seams, no real SQL Server/Tavily/Ollama
  needed), and `test_eval_*.py` for the benchmark framework's own logic
  (dataset loading, grading, metrics, regression detection — not a live
  run, which stays manual like `run_benchmark.py` itself). `rag/` and
  `search/` don't yet have their own fully-mocked unit test files (see
  "Known gaps" below) — they were verified against this project's real
  SQL Server/Tavily/Ollama instances instead during development.
