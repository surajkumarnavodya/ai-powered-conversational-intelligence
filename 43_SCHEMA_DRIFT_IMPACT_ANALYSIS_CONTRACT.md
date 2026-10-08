# 43 — Schema Drift Impact Analysis

Continuous schema-drift detection: compare discovery fingerprints and
detect added/removed/renamed columns, type/nullability changes, key/
relationship changes, and relevant view/object changes. Map impact to
metrics, semantic definitions, golden questions, dashboards, queries, and
recommendations through lineage. Generate actionable reports/alerts; do
not silently rewrite production definitions. Support approval-aware
remediation and metadata rollback. **INSPECT/PLAN/IMPLEMENT/TEST/REVIEW/
FIX/DOCUMENT** — one new top-level package (`schema_drift/`), a new
standalone REST surface (`api/schema_drift.py`, two routes), a new
standalone CLI (`scripts/run_schema_drift_scan.py`), an additive
extension to Prompt 35's existing risk-scoring dict, additive config
surface, and a dedicated cross-cutting regression test proving the
mission's own "never silently rewrite production definitions" rule
structurally. No existing route, node, prompt, model field, or `Settings`
field changed behavior for any pre-existing caller.

## 1. Inspection: what already existed vs. the real gap

A deterministic column-level schema diff did not exist anywhere in this
codebase. The closest prior art was table-level only:
`embeddings.schema_indexer`'s per-database manifest
(`.schema_manifest__<db>.json`) stores a per-*table* fingerprint and
reports `added`/`removed`/`changed` *table names* (`SchemaDiscoveryDiff`,
read back via `get_last_discovery_diff`) — enough to decide which Chroma
chunks to re-embed, nowhere near enough to say *what* changed inside a
changed table (a type change vs. a nullability change vs. a key change
are all just "changed").

Everything the mission's own impact-mapping half needs already existed
and was reused verbatim, not reimplemented:

- `db.schema_introspection.TableSchemaInfo`/`ColumnInfo`/`ForeignKeyInfo`
  — the full column-level introspected shape (type, nullable, PK,
  default, computed/identity, length/precision/scale,
  `unique_constraints`) a real diff needs; `get_schema_fingerprint` for
  the mission's own "compare discovery fingerprints" phrase (the overall
  per-database fingerprint this diff can be triggered from, though the
  actual diff always operates on the full snapshots, not the hash).
- `lineage.graph.build_lineage_graph`/`trace_lineage` (Prompt 42) — the
  mission's own "through lineage" instruction, taken literally: impact
  assessment is `trace_lineage(..., direction="downstream")` over a
  graph built from the *previous* schema snapshot plus a tenant's
  published catalog entries, not a second graph/traversal
  implementation.
- `semantic.intelligence.detect.Finding` + `identity.models
  .SemanticFinding` + `identity.repositories.semantic_intelligence`
  (Prompt 35) — an **already-built, already-tenant-scoped, already-
  versioned (append-only `history`), already-governed** review-queue
  with an accept/dismiss/rollback lifecycle. This is, verbatim, the
  mission's own "approval-aware remediation" shape — confirmed by
  reading `identity/repositories/semantic_intelligence.py` directly
  before writing a single new persistence line. Reusing it rather than
  building a parallel `SchemaDriftRecord`/`SchemaDriftFeedbackEvent`
  pair (the shape this prompt's own first design pass sketched, before
  this inspection step) avoids a second Alembic migration, a second
  repository module, and a second governance-policy module for a
  conceptually identical review-queue problem.
- `identity.repositories.semantic_intelligence.rollback_catalog_entry` +
  `POST /semantic-intelligence/entries/{entry_id}/rollback` (Prompt 35)
  — the mission's own "metadata rollback," already built: creates a new
  `DRAFT` from an older *published* version's content; the published row
  is never touched, and the draft still needs the catalog's normal
  review-and-publish approval. `schema_drift/` never calls this function
  or any other catalog-mutating function itself (proven structurally —
  see §3) — it only ever *reports* which catalog entry a finding
  threatens.
- `semantic.catalog_policy.authorize_catalog_action` (`CatalogAction
  .REVIEW`) — the identical tenant-scoped ABAC check `api
  /semantic_intelligence.py`'s own `/run`/`/findings` routes already
  make, reused unmodified for `/schema-drift/scan`/`/findings` rather
  than a new policy module.

The one genuinely missing piece, confirmed by grep (no existing module
persists a full column-level historical schema snapshot): a place to
keep "the last schema shape we compared against" between scans. Built as
`schema_drift.snapshot_store`, mirroring `embeddings.schema_indexer`'s
own per-database JSON-file-next-to-the-Chroma-dir convention rather than
a new identity-DB table (this is an operational cache, not governed
content — the governed artifact a scan produces, the findings
themselves, already has a home in Prompt 35's own table).

## 2. What was built

- **`schema_drift/diff.py`** — `compare_schemas(previous, current,
  rename_similarity_threshold)`, a pure function (no I/O, no LLM call)
  returning a deterministic, sorted list of `SchemaDriftFact`s across a
  closed `DriftChangeKind` vocabulary: `TABLE_ADDED`/`TABLE_REMOVED`,
  `VIEW_ADDED`/`VIEW_REMOVED`, `OBJECT_TYPE_CHANGED` (a table flipping to
  a view or back), `COLUMN_ADDED`/`COLUMN_REMOVED`, `COLUMN_RENAMED`
  (the one inferred kind), `COLUMN_TYPE_CHANGED`,
  `COLUMN_NULLABILITY_CHANGED`, `PRIMARY_KEY_CHANGED`,
  `FOREIGN_KEY_ADDED`/`FOREIGN_KEY_REMOVED`,
  `UNIQUE_CONSTRAINT_CHANGED`. The rename heuristic
  (`_infer_renames`) is bounded and greedy: only a same-table,
  same-type, same-nullability removed+added pair clearing
  `difflib.SequenceMatcher` similarity is even offered, no column is
  used in two pairs, and the underlying `COLUMN_ADDED`/`COLUMN_REMOVED`
  facts are always reported too — never suppressed in the rename's
  favor (master rule 10).
- **`schema_drift/findings.py`** — `fact_to_finding`/`facts_to_findings`
  wrap each fact into `semantic.intelligence.detect.Finding`, `kind`
  prefixed `schema_drift_` (`is_schema_drift_kind` is the one place that
  prefix is checked, so `api/schema_drift.py` never hardcodes it a
  second time). `truth_level` is `DataTruthLevel.DATABASE_FACT` for
  every kind except `COLUMN_RENAMED` (`AI_INFERENCE`) — locked in by
  `tests/security/test_schema_drift_never_rewrites_catalog.py`, which
  iterates every `DriftChangeKind` value, not just a couple of examples.
- **`schema_drift/impact.py`** — `assess_impact`/`assess_finding_impact`
  resolve a finding's own table/column into a `lineage.schema_lineage`
  node id and call `lineage.graph.trace_lineage(...,
  direction="downstream")` over a caller-supplied graph. Sets
  `Finding.dependents` (count) and `Finding.published_count` (how many
  impacted nodes are `CONFIRMED_BUSINESS_TRUTH`, i.e. a *published*
  governed metric/dimension) plus a `payload["impacted"]` list (node
  id/type/label/truth_level/concept_key/version/depth) — never mutates a
  finding in place (it's frozen); returns an updated copy via
  `dataclasses.replace`.
- **`semantic/intelligence/risk.py`** (edited, additive) — thirteen new
  `_BASE` dict entries, one per `DriftChangeKind`, so the *same* risk
  formula (base-by-kind + 15/published dependent (capped at 2) + 5/
  dependent (capped at 5) + an inference-confidence gap, high ≥70/medium
  ≥40 tiers) scores a drift finding exactly like a catalog-detector one.
  Weighted by real operational severity: `table_removed` 60,
  `view_removed`/`column_removed`/`primary_key_changed` 45,
  `foreign_key_removed` 40, `column_renamed` 35 (the inferred one, scored
  as seriously as a real removal because an undetected rename silently
  breaks exactly as much as a real one),`object_type_changed`/
  `column_type_changed` 30, `column_nullability_changed`/
  `unique_constraint_changed` 20, `foreign_key_added` 10,
  `table_added`/`view_added`/`column_added` 5.
- **`schema_drift/snapshot_store.py`** — `load_snapshot`/`save_snapshot`,
  a per-database JSON file (`.schema_drift_snapshot__<db_name>.json`)
  alongside the Chroma persist directory, full round-trip of every
  `TableSchemaInfo`/`ColumnInfo`/`ForeignKeyInfo` field. Fails open
  (`None`) on a missing or corrupt file.
- **`schema_drift/engine.py`** — `scan_for_drift` orchestrates
  diff → findings → impact (skippable, `previous_lineage_graph=None`) →
  `semantic.intelligence.risk.prioritize` in one call, returning a typed
  `ScanResult`.
- **`schema_drift/reports.py`** — `render_markdown_report`, grouped by
  risk tier (high first), each line disclosing its own truth level
  inline ("DATABASE FACT" vs. "AI INFERENCE (unconfirmed)") and its own
  impacted-dependent count. The single rendering both `ScanOut
  .report_markdown` and the CLI script's stdout use.
- **`api/schema_drift.py`** + **`api/schema_drift_schemas.py`** —
  `POST /schema-drift/scan` (requires a local account, the same
  requirement `POST /lineage/graph` already has; live-introspects the
  caller's own tenant-scoped database, diffs against the stored
  snapshot, builds the previous-schema lineage graph from this tenant's
  own *published* catalog entries, assesses impact, persists via the
  reused `upsert_findings`, saves the new snapshot, returns a
  `ScanOut` carrying `report_markdown` + the top findings) and
  `GET /schema-drift/findings`(`/{id}`) (a kind-scoped, read-only view
  over the same shared `SemanticFinding` table, reusing `list_findings`/
  `get_finding` unmodified). `FindingOut` is imported directly from
  `api.semantic_intelligence_schemas` — one response shape for "one
  governed finding," not a near-duplicate. **Deliberately no
  decision/rollback routes here** — see §3.
- **`config/settings.py`** — `enable_schema_drift_detection` (default
  `True` — a pure, deterministic, zero-cost-until-triggered diff, the
  same "safe to default on" posture `enable_data_quality_engine`/
  `enable_lineage_platform` already establish),
  `schema_drift_max_findings_per_scan` (default 200),
  `schema_drift_rename_similarity_threshold` (default 0.6),
  `schema_drift_impact_max_trace_depth` (default 6).
- **`scripts/run_schema_drift_scan.py`** — a standalone, no-
  authentication CLI for cron/Task Scheduler alerting, looping every
  configured (or `--database`-selected) database, printing
  `render_markdown_report`'s own output, and exiting `2` if any finding
  is `risk_tier == "high"`. Shares the same on-disk snapshot baseline
  the API route reads/writes (alternating between the two never desyncs
  it); deliberately skips impact assessment and governed persistence
  (both need a real local-account actor/tenant this script doesn't have)
  — disclosed in its own module docstring, not silently narrowed.
- **`frontend/vite.config.ts`** — `/schema-drift` added to
  `BACKEND_ROUTES` up front (the recurring dev-proxy gap every new
  backend prefix since Prompt 26 has needed fixed, closed before it
  could be rediscovered the hard way).

**No frontend page was built** — following the established precedent of
Prompts 38–41 (scenario/optimization/predictive/data_quality: engine +
REST, UI deferred), not Prompt 42's own exception. Disclosed in §5, not
silently omitted.

## 3. Authorization, tenant isolation, and "never silently rewrite"

- **RBAC/ABAC**: every route calls `semantic.catalog_policy
  .authorize_catalog_action` with `CatalogAction.REVIEW` — `analyst`/
  `admin` only; a cross-tenant resource resolves to the identical 404 a
  nonexistent one gets (anti-enumeration, the same posture every
  catalog-adjacent route in this codebase already establishes). No new
  permission was added — this reuses `Permission.CATALOG_REVIEW`/
  `CATALOG_MANAGE` directly, since triaging a drift finding is the same
  kind of domain-judgment review work Prompt 35 already gated this way.
- **Tenant isolation**: `/scan` resolves `database_id` through
  `Settings.databases_for_tenant(tenant_id)` (the same anti-enumeration
  convention `GET /schema/tables`/`POST /lineage/graph` already use) and
  reads only this tenant's own *published* catalog rows
  (`identity.repositories.semantic_catalog.list_entries(..., status=
  "published")`) to build the impact graph — a finding's impact can
  never leak across tenants sharing one physical database connection.
  Proven directly: `tests/test_api_schema_drift.py
  ::TestScan::test_a_scan_in_another_tenant_never_sees_this_tenants_findings`.
- **"Never silently rewrite production definitions," proven
  structurally, not just by convention**:
  `tests/security/test_schema_drift_never_rewrites_catalog.py` parses
  every `schema_drift/` module's own AST (`ast.walk`, collecting every
  bare `Name` reference) and asserts none of them ever references
  `create_entry`/`update_draft_entry`/`publish_entry`/
  `review_catalog_entry`/`request_catalog_entry_changes`/
  `rollback_catalog_entry` — the full set of catalog-mutating functions
  in `identity.repositories.semantic_catalog`/`semantic_intelligence`.
  It additionally asserts `api/schema_drift.py`'s own `from
  identity.repositories.semantic_catalog import ...` line names only
  `entry_to_snapshot`/`list_entries` (both read-only). This is a
  stronger guarantee than a runtime permission check, which a future
  edit could accidentally route around — there is structurally no call
  site to route around.
- **Rate limiting**: `/scan` calls `enforce_api_action_rate_limit(...,
  "schema_drift_scan", ...)` — the same reasoning `POST /schema/refresh`/
  `POST /lineage/graph` already rate-limit for (real, live introspection
  work).
- **Provenance**: every finding's `truth_level` is set at construction
  (`fact_to_finding`), never derived later from a mutable flag a future
  edit could flip independently — `DATABASE_FACT` for every deterministic
  structural fact, `AI_INFERENCE` only for the rename heuristic, and
  `CONFIRMED_BUSINESS_TRUTH` is never reachable from this package at all
  (proven directly, iterating every `DriftChangeKind`).

## 4. Testing

- `tests/test_schema_drift_diff.py` (20 cases) — every `DriftChangeKind`,
  the rename heuristic's own bounds (type mismatch rejected, dissimilar
  names rejected, a column never used twice, the underlying add/remove
  facts always still present), determinism (identical snapshots diff to
  nothing, repeated calls produce byte-identical, sorted output), and the
  "first scan has nothing to diff against" contract.
- `tests/test_schema_drift_engine.py` (11 cases) — `findings.py`'s
  truth-level/key-determinism/cross-database-no-collision contracts;
  `impact.py` against a **real** `lineage.graph.build_lineage_graph`
  result (not mocked) for a column-level impact, a zero-impact addition,
  and a table-level impact (forcing `lineage.catalog_lineage`'s own
  multi-source-table fallback); `engine.py`'s full diff→findings→impact
  →risk orchestration, including a first-scan-with-no-graph path;
  `reports.py`'s empty-report and risk-tier-grouping contracts.
- `tests/test_schema_drift_snapshot_store.py` (5 cases) — real file I/O
  against a `tmp_path`-backed `chroma_persist_dir`: full round-trip of
  every field, no-previous-snapshot, cross-database isolation, a corrupt
  file failing open, and overwrite-on-resave.
- `tests/test_api_schema_drift.py` (11 cases) — the real-auth,
  in-memory-identity-database pattern `tests/test_api_semantic
  _intelligence.py` already establishes (so the real ABAC check runs,
  never mocked), combined with `tests/test_api_lineage.py`'s own
  "mock live DB introspection at the module boundary" pattern: feature
  flag off → 404, a viewer without `CATALOG_REVIEW` → 403, an unknown
  database → 404, an unknown request field rejected (422), no local
  account → 401/403, a genuine first scan reporting every table as
  added, a **second** scan (via a monkeypatched `introspect_schema`
  returning a column-removed shape) detecting the removal as
  `DATABASE_FACT`, cross-tenant finding isolation, kind-scoped listing,
  and a finding-detail round trip.
- `tests/security/test_schema_drift_never_rewrites_catalog.py` (6 cases)
  — the structural, AST-based proof described in §3, plus the
  truth-level-by-construction proof over every `DriftChangeKind`.

All 53 new tests pass; the full pre-existing backend suite was re-run
after this change and remains green (confirmed via a full, unfiltered
`pytest` run in this same session, exit code 0) — this prompt's own
additive edits (`config/settings.py`, `api/main.py`,
`semantic/intelligence/risk.py`, `frontend/vite.config.ts`) introduced no
regression. `ruff check`/`black --check`/`mypy` were run against every
new/edited production file directly: zero new findings (the four
pre-existing `mypy` errors surfaced while checking this package's own
import graph are in `db/schema_introspection.py`/`identity/repositories
/semantic_intelligence.py`/`identity/repositories
/recommendation_governance.py`, none of which this prompt touched —
confirmed via `git diff --stat` on each, empty).

## 5. Known, disclosed limitations

- **No automatic trigger exists.** A scan only runs when
  `POST /schema-drift/scan` or `scripts/run_schema_drift_scan.py` is
  actually invoked (by an operator, a cron entry, or a future scheduled
  job) — this application has no built-in background scheduler anywhere
  (the same disclosed limitation `onboarding/`'s own module docstring
  already names for a different feature). "Continuous" means "cheap and
  safe to run on a schedule," not "always running."
- **Impact assessment covers declared foreign keys and published catalog
  entries only** — `db.relationship_inference`'s own lower-confidence
  candidate relationships are deliberately not included, keeping a
  scan's own impact claims at `DATABASE_FACT`/`CONFIRMED_BUSINESS_TRUTH`
  only, never a second inference layered under the drift finding itself.
- **"Golden questions" and "dashboards" are not separately, structurally
  traced.** A golden question has no lineage node type in Prompt 42's
  own closed `LineageNodeType` vocabulary at all; a "dashboard" in this
  codebase is a chart spec (`lineage.presentation_lineage`'s
  `VISUALIZATION` node), reachable only for a query the caller
  explicitly supplies to `lineage.graph.build_lineage_graph`'s own
  `queries` parameter — which this prompt's impact assessment does not
  do (it builds a schema+catalog-only graph from the previous snapshot,
  never a query-history one, since nothing in this codebase persists a
  queryable history of past executed queries' own lineage). A future
  prompt wiring live `/ask`/`/execute` lineage capture into a persisted
  store (already named as a Prompt-42 limitation) would close this.
- **No frontend page** — `api/schema_drift.py`'s two routes have no
  React UI in this pass, following Prompts 38–41's own precedent rather
  than Prompt 42's exception. An operator reviews findings via
  `GET /schema-drift/findings` directly, or via `scripts
  /run_schema_drift_scan.py`'s own markdown output.
- **The rename heuristic is bounded and same-table-only** — a column
  moved to a *different* table (a real, if rarer, kind of "rename") is
  reported as an independent `COLUMN_REMOVED` + `COLUMN_ADDED` pair with
  no rename candidate offered at all, disclosed rather than guessed at
  across tables.
- **No live database was available to verify against a real SQL
  Server/Postgres/MySQL/Oracle instance in this pass** — every test uses
  hand-built `TableSchemaInfo` fixtures or a mocked `introspect_schema`,
  the same environment-blocked constraint every other prompt in this
  series since Prompt 19 has disclosed (no live Ollama/DB instance exists
  in this sandboxed session).

## 6. Recommended next prompt

A scheduled-job runner (a real, persisted job/cron concept this codebase
has never built — `onboarding/`'s own "no background worker" limitation,
named again here, is the same underlying gap) would turn
`scripts/run_schema_drift_scan.py`'s manual/cron-only posture into a
first-class, UI-visible "last scanned at / next scan in" feature, and
would be the natural place to also auto-trigger
`POST /schema-drift/scan` right after `POST /schema/refresh`/
`refresh_all_schema_indexes` succeeds (today two independent, deliberately
decoupled actions). A persisted query-lineage history (named as a known
gap in both Prompt 42's and this prompt's own limitations) would let
impact assessment reach real "dashboards"/"golden questions" rather than
only schema+catalog nodes.
