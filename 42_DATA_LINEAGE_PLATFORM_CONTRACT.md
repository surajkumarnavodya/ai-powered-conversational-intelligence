# 42 — Data Lineage Platform

Prompt 42 of the ongoing Enterprise AI Analytics & Recommendation
Platform initiative: end-to-end lineage from source database objects
through columns, governed metrics, executed SQL, visualizations,
insights, and recommendations. **INSPECT/PLAN/IMPLEMENT/TEST/REVIEW/
FIX/DOCUMENT** — one new top-level package (`lineage/`), a new
standalone REST surface (`api/lineage.py`), a new multi-agent
specialist, a new frontend explorer page, server-driven nav wiring,
additive config surface, and a dedicated cross-cutting regression test
proving the mission's own two named acceptance checks. No existing
route, node, prompt, or `Settings` field changed behavior for any
pre-existing caller.

## 0. A found, disclosed branch-divergence risk (read before extending this further)

**`feat/knowledge-graph-36` (Prompt 36, "Enterprise Knowledge Graph") is
a sibling branch, never merged into this branch's own history, with
materially overlapping scope** — found only *after* this prompt's own
implementation was complete and tested, while preparing to commit (a
cross-branch `git log --all` check, not something an in-tree grep could
have surfaced: `git branch --all --contains <that commit>` shows it
reachable only from `feat/knowledge-graph-36`, which diverged from this
branch's own history at their shared merge-base `791521a` and was never
merged back into either `master` or this branch). Its own commit message
describes a **persisted**, Postgres-backed (`graph_nodes`/`graph_edges`)
generic node/edge graph spanning tenants/databases/schemas/tables/
columns/metrics/dimensions/business terms/policies/questions/findings/
recommendations/evidence, with `derives_from`/`governed_by`/
`supported_by`/`joins_to` edge types, a `GET /graph/lineage/{id}`/
`GET /graph/impact/{id}` bounded-BFS traversal, and a restricted-column-
removed-before-traversal authorization posture — independently arriving
at several of the same shapes this prompt's own `lineage/` package uses
(`GOVERNED_BY`/`SUPPORTED_BY` edge names, BFS trace, pre-traversal
restricted-node removal).

**What is genuinely different, not just duplicated**: that branch's
graph is a *persisted projection* synced from several source systems at
once (schema, catalog, semantic-intelligence findings, recommendations,
policy/document RAG, chat questions) with no SQL-statement-level
column-derivation parsing at all; this prompt's `lineage/` is a
*stateless, deterministic, sqlglot-based* engine whose own distinct
contribution is column-level lineage recovered from one query's or one
governed metric's actual SQL/expression text (never persisted, always
caller-supplied/re-fetched per call), plus the query → visualization →
insight → recommendation presentation tail that branch's own commit
message does not mention at all.

**This was not reconciled in this pass** — merging two independently-
designed, partially-overlapping graph systems is a larger, separate
undertaking than this prompt's own scope, and attempting it without the
project owner's explicit direction on which to keep, merge, or layer
would risk exactly the kind of silent, unreviewed architectural
collision master rule 4 (preserve working behavior) warns against.
Flagged here, in §6's recommended next prompt, and in the final report
handed back for this prompt — not silently worked around.

## 1. Inspection: what already existed vs. the real gap

Exhaustive grep for "lineage"/"knowledge_graph"/"dependency_graph"/"DAG"
**within this branch's own working tree** confirmed exactly one prior
lineage concept reachable from here:
`data_quality/lineage.py` (Prompt 41) — a narrow, **table-level-only**
link from a data-quality finding to a *published* governed concept
declaring that table as a source (`identity.models.SemanticCatalogEntry
.source_tables`), with no column-level precision, no schema/FK layer, no
query/visualization/insight/recommendation layer, no trace/impact
analysis, no versioning/staleness concept, and no persisted or
assembled "graph" at all — confirmed by direct inspection of that
module's own docstring and `identity/models.py` (no `source_columns`
field exists on `SemanticCatalogEntry`).

Building blocks reused, never reimplemented:

- `db.schema_introspection.TableSchemaInfo`/`ForeignKeyInfo` — live-
  introspected tables/columns/declared foreign keys, the `DATABASE_FACT`
  schema layer. `get_schema_fingerprint` is reused verbatim for this
  platform's own versioning/staleness contract.
- `db.relationship_inference.InferredRelationship` — structurally-
  inferred candidate FKs (never declared facts), reused verbatim
  including its own `truth_level` field, for the schema layer's
  optional `AI_INFERENCE` edges.
- `semantic.catalog.CatalogEntrySnapshot`/`status_to_truth_level` — the
  governed catalog's own ORM-independent read shape and its
  draft/reviewed/published/superseded → truth-level mapping, reused
  verbatim (never re-derived) for the catalog layer.
- `agent.provenance.DataTruthLevel` — the shared three-level vocabulary
  every lineage node/edge is tagged with.
- `optimization.validator`/`analytics.predictive`/`recommendation.engine`/
  `data_quality.engine`'s independently re-derived `_restricted_column_names`
  authorization pattern — reused for this platform's own restricted-
  column **node suppression** (see §3).
- `agent.nodes._query_fingerprint`'s SHA-256-prefix convention — reused
  (via a sibling, `lineage.sql_lineage.query_fingerprint`) as the
  deterministic id a query's own lineage node is keyed by.
- `GET /schema/tables` (`api/main.py`) — the exact live-introspection +
  tenant-scoping + anti-enumeration-404 pattern this platform's
  `/lineage/graph`/`/stale-check` routes reuse verbatim.
- `api/data_quality.py`'s own `/lineage` route — the template this
  platform's `/graph`/`/stale-check` routes mirror for "a route that
  reads tenant-scoped catalog data requires a local account."
- `agent/multiagent/`'s `DataQualityRequest`/`OptimizationRequest`
  two-tier-authorization and self-contained-request patterns — mirrored
  exactly for the new `LineageRequest`/`lineage_handler`/`TOOL_LINEAGE`.

**Real gap this prompt closes**: nothing in this codebase before it
could answer "where did this KPI's number actually come from" or "what
breaks if this table/column changes" across the full mission-named
chain, with deterministic parsing preferred over LLM guesses, and no
way to detect that a previously-computed lineage fact had gone stale.

## 2. What was built

### `lineage/` (new top-level package, a peer of `analytics/`/
`optimization/`/`data_quality/`)

- `models.py` — `LineageNodeType` (database/table/column/metric/
  dimension/query/visualization/insight/recommendation),
  `LineageEdgeType` (reads/joins_via/derives_from/governed_by/
  visualizes/explains/supported_by), `LineageNode`/`LineageEdge`
  (immutable, each carrying its own `DataTruthLevel` and, for a
  catalog-derived node, its own `concept_version`), `LineageGraph`
  (nodes/edges + `schema_fingerprint`/`catalog_versions` — the
  versioning contract — + `suppressed_restricted_nodes`/`limitations`),
  `LineageTraceResult`/`LineageStalenessReport`. `LINEAGE_ENGINE_VERSION`.
- `sql_lineage.py` — `extract_column_lineage(sql, dialect)`: a
  deterministic sqlglot parse of one top-level `SELECT`'s own
  `FROM`/`JOIN` tables and output projections, resolving each output
  column to its real source table.column(s) and naming any aggregate/
  transform function involved. Never raises (an unparseable statement
  returns `is_parseable=False`); an unqualified column with more than
  one candidate table in scope is disclosed `is_ambiguous=True`, never
  guessed. A CTE's own name is excluded from `tables_read` and never
  traced into its base tables (disclosed scope boundary, matching
  `data_quality/lineage.py`'s own table-level-only precedent for a
  different boundary).
- `schema_lineage.py` — `build_schema_lineage(tables, database_id,
  inferred=())`: one `DATABASE`/`TABLE`/`COLUMN` node each, one
  `JOINS_VIA` edge per declared single-column FK (`DATABASE_FACT`) plus
  one per caller-supplied inferred candidate (`AI_INFERENCE`,
  `is_inferred=True`). A composite FK is disclosed as a table-level
  relationship only (column-level edges are single-column only).
- `catalog_lineage.py` — `build_catalog_lineage(entries)`: one
  `METRIC`/`DIMENSION` node per catalog entry of that concept type
  (`ENTITY`/`DOMAIN` entries are skipped — no lineage node type exists
  for them in this chain), `GOVERNED_BY` edges at
  `status_to_truth_level(entry.status)`. **A real improvement over
  `data_quality/lineage.py`'s table-level-only scope**: a `METRIC`
  entry's own `approved_expression` is deterministically parsed (reusing
  `sql_lineage`'s own column-extraction logic on a bare `SELECT
  <expression>`) to recover column-level lineage whenever the entry
  declares exactly one source table and the expression parses cleanly;
  it falls back to the honest, table-level-only edge otherwise — never a
  silent wrong guess.
- `presentation_lineage.py` — `build_query_node_and_reads` (one `QUERY`
  node + `READS` edges to every real table/column the deterministic
  parse found, `DATABASE_FACT`), `build_visualization_lineage`
  (`VISUALIZATION` → `VISUALIZES` → `QUERY`, `DATABASE_FACT` — a chart
  spec is deterministically computed, no LLM), `build_insight_lineage`
  (`INSIGHT` → `EXPLAINS` → `QUERY`, always `AI_INFERENCE`, carrying its
  own `grounded_in` fact labels), `build_recommendation_lineage`
  (`RECOMMENDATION` → `SUPPORTED_BY` → `QUERY`, always `AI_INFERENCE`).
  Every function here takes plain, primitive-shaped input, never the
  real `ChartSpec`/`Recommendation` types — this module describes an
  already-produced object, never recomputes or calls an LLM itself.
- `graph.py` — `build_lineage_graph(...)` assembles every layer above
  into one `LineageGraph`, applying the restricted-column suppression
  (§3) last. `trace_lineage(graph, start_node_id, direction, max_depth)`
  is a breadth-first, cycle-safe, depth-bounded walk — **upstream**
  follows a node's own outgoing edges (what it depends on); **downstream**
  follows its own incoming edges (what depends on it — impact analysis).
  This uniform `source depends on target` convention holds across every
  layer, which is what makes a single trace function correct for the
  whole mission-named chain at once.
- `staleness.py` — `detect_staleness(graph, current_schema_fingerprint,
  current_catalog_versions)`: a pure comparison (never reads a wall
  clock or re-fetches anything itself, mirroring `data_quality.models
  .FreshnessRule`'s own "caller supplies both sides" discipline) across
  three independent kinds of staleness — schema changed, a linked
  catalog concept moved to a different version, a linked catalog concept
  no longer exists.

### `api/lineage.py` + `api/lineage_schemas.py`

Three routes, all gated by `Settings.enable_lineage_platform` (404 when
off) and rate limiting:

- `POST /lineage/graph` — **requires a local account**
  (`require_local_user`, the identical requirement `data_quality`'s own
  `/lineage` route has). Live-introspects the caller's own tenant-scoped
  database (`db.schema_introspection.introspect_schema`, the same
  metadata-only call `GET /schema/tables` already makes) and reads that
  tenant's own *published* catalog entries, assembling the full
  schema+catalog graph; optional caller-supplied `queries` (SQL text +
  optional visualization/insight/recommendation descriptors) append the
  query/presentation layer — never a second database fetch for that
  part.
- `POST /lineage/trace` — pure (`Permission.ASK` only); never touches a
  database or calls an LLM. Operates purely over a graph the caller
  already has from a prior `/graph` call.
- `POST /lineage/stale-check` — requires a local account; re-fingerprints
  the live schema and re-fetches current catalog versions, compared
  against caller-supplied previously-recorded values.

A `database_id` outside `Settings.databases_for_tenant(tenant_id)` is a
404 — the identical anti-enumeration convention `GET /schema/tables`
already establishes. Request models forbid unknown fields (`caller_roles`/
`tenant_id` can never be client-supplied).

### Multi-agent wiring (`agent/multiagent/`)

New `AgentName.LINEAGE`, `TOOL_LINEAGE` (registered at `Permission.ASK`,
the identical two-tier pattern `TOOL_DATA_QUALITY`/`TOOL_OPTIMIZATION`
already establish and this prompt reused from the start rather than
rediscovering the Prompt-39 bug), `lineage_handler`, and a new, **self-
contained** `LineageRequest` dataclass (sql + optional dialect, no
`data_task` — `DataSlot` does not carry the SQL text that produced its
rows, confirmed by direct inspection, so there is no governed-rows slot
a `data_task` could point at here; the identical shape
`OptimizationRequest` already establishes for the same reason). Its
`AgentSpec.max_truth_level` is `DataTruthLevel.DATABASE_FACT` — the
identical reasoning `DATA_QUALITY`'s own spec gives: a deterministic
parse of SQL text is an observed fact about that text's structure.
Gated on an explicit caller-supplied request; the planner never creates
one.

### Frontend (`frontend/src/pages/DataLineage.tsx`)

A lean, read-only explorer — database selector, "Build graph" (calls
`/lineage/graph`), nodes grouped by type with `TruthLevelBadge` (reused,
not duplicated), and a trace panel (select a node, pick upstream/
downstream, calls `/lineage/trace`). Deliberately a plain grouped-list
view, not a graph-visualization library — consistent with this app's
own stated conservatism against adding a new rendering library for a
chart/graph type. Wired through `security/navigation.py` (new
`data_lineage` `NavItem`, `agent_any={ASK}` — hiding a tab is UX only,
the real enforcement is the route's own `require_local_user`), `App.tsx`,
`AppShell.tsx`'s screen-presentation map, and all 5 locale files' nav
label (the same "translate the minimum bar" precedent `MediaSearch.tsx`
established).

### Config (`config/settings.py`)

`enable_lineage_platform` (default `True`), `lineage_max_trace_depth`,
`lineage_max_queries_per_request`, `lineage_max_trace_graph_nodes` — the
identical "cap regardless of what a caller sends" philosophy
`data_quality_max_tables`/`predictive_max_features` already establish.

## 3. Authorization, tenant isolation, and provenance

**Authorization is node suppression, not value redaction.** A `COLUMN`
node naming a column `config.sensitive_columns` classifies "restricted"
is removed from the graph entirely (never kept with a placeholder value)
for a caller lacking `Permission.VIEW_RESTRICTED_COLUMNS` — a lineage
graph can disclose a restricted column's *existence/name* even without
ever disclosing a *value*, so this platform treats that the same way
`optimization.validator`/`analytics.predictive` treat a restricted
column reference: withheld outright. `LineageGraph
.suppressed_restricted_nodes` discloses what was removed, never a
silent omission.

**Tenant isolation mirrors `data_quality`'s own `/lineage` route
exactly** — `security.tenancy.resolve_actor_tenant_id` (never a client-
supplied value), `Settings.databases_for_tenant` filtering which
database names a tenant may even name, and
`identity.repositories.semantic_catalog.list_entries(session, tenant_id,
...)` scoping every catalog read. Proven directly, against a real
identity database with real tenants/users/published rows (not a mock),
in `tests/security/test_lineage_kpi_end_to_end_and_tenant_isolation.py`.

**Provenance**: every node/edge carries its own `DataTruthLevel` —
`DATABASE_FACT` for a declared FK, a deterministic SQL/expression parse,
or a computed chart spec; `CONFIRMED_BUSINESS_TRUTH` only for a
*published* catalog entry's own edges; `AI_INFERENCE` for an inferred
candidate relationship, a draft/reviewed catalog entry, an LLM-narrated
insight, or a recommendation — never silently promoted, even when
reached transitively through a multi-hop trace (verified directly: every
downstream insight/recommendation node reached by `trace_lineage` stays
tagged `ai_inference`).

## 4. Testing

- `tests/test_lineage_engine.py` (26 cases) — SQL/schema/catalog/
  presentation-layer unit coverage, graph assembly, end-to-end trace
  (including the mission's own KPI-to-source-column and query-to-
  presentation-chain shapes), cycle-safety, restricted-column
  suppression, and all four staleness scenarios.
- `tests/test_api_lineage.py` (16 cases) — feature flag, request
  validation (`extra="forbid"` rejects a client-supplied `caller_roles`/
  `tenant_id`), payload bounds, unknown-database 404, and the local-
  account requirement on `/graph`/`/stale-check`.
- `tests/security/test_lineage_kpi_end_to_end_and_tenant_isolation.py`
  (5 cases) — the mission's own two named acceptance checks, against
  the real HTTP API with a real identity database.
- `tests/test_multiagent_supervisor.py::TestLineageAndWorkspace`
  (4 cases) — explicit-request-only gating, never-attempted-without-
  request, unparseable-SQL-as-open-item, and the identity-cannot-be-
  smuggled guarantee every other engine specialist already proves.
- `tests/test_security_navigation.py`/`tests/test_api_navigation.py` —
  updated for the new `data_lineage` screen (an exhaustive per-persona
  equality check, not a subset-containment one, so a future added/
  removed screen must touch these too, the same discipline Prompt 24
  established for the LangGraph node list).

Full suites re-run clean: **4236/4236 backend tests passing** (the
pre-existing count, confirmed unchanged before this prompt's own new
test files were added) **+ 51 new backend tests**; **561/561 frontend
tests passing**, zero regressions; `npx tsc --noEmit` clean.

## 5. Known, disclosed limitations

- Column-level lineage is single-statement, outermost-`SELECT`-only — a
  subquery nested in `FROM` is not recursed into, and a CTE's own base
  tables are never traced through (the CTE is treated as an opaque
  name). A governed metric's own column-level resolution additionally
  requires exactly one declared source table.
- A composite (multi-column) foreign key is table-level only in this
  graph (column-level `JOINS_VIA` edges are single-column only).
- `/lineage/graph`'s query/presentation layer is entirely caller-
  supplied (SQL text + descriptors) — this platform does not itself
  intercept or auto-record every live `/ask`/`/execute` call's own SQL
  into a persisted lineage history; each `/graph` call is self-
  contained, the same "engine first, persistence/history later, if
  ever" posture Prompts 38-41 already established for their own engines.
- `/lineage/trace` requires the caller to have the graph already in
  hand (from a prior `/graph` call) — there is no server-side persisted
  graph store a trace can be run against by id alone.
- The frontend explorer is a plain grouped-list view, not a visual
  node-link diagram — a disclosed, deliberate scope choice (see §2),
  not an oversight.
- No live SQL Server/PostgreSQL/MySQL/Oracle instance was available in
  this environment to verify `introspect_schema`/`get_read_only_engine`
  against a real database for this feature specifically — both are the
  exact, already-live-verified functions `GET /schema/tables` already
  uses in production, reused verbatim rather than reimplemented.

## 6. Recommended next prompt

**First, and most urgently: reconcile `feat/knowledge-graph-36` with
this work** (see §0) — decide, with the project owner, whether the two
graph systems merge into one, or `lineage/`'s own SQL-statement-level
column parsing and presentation-tail layering become an additive
extension synced *into* that branch's persisted `graph_nodes`/
`graph_edges` store instead of staying a separate, stateless package.
Doing this before either system gets its own further frontend/API
investment avoids compounding the divergence.

Separately: a persisted lineage history (auto-capturing each `/ask`/
`/execute` call's own query/visualization/insight/recommendation
lineage into a queryable store, closing the "self-contained per call"
limitation above — likely the same store `feat/knowledge-graph-36`
already built, once reconciled) and/or a real graph-visualization
frontend, once a design-system decision is made about which (if any)
graph-layout library this app is willing to adopt.
