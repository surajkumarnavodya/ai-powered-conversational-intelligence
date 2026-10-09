# PROMPT 48 — ENTERPRISE BI INTEGRATIONS

## Role
Act as a Principal AI Architect, Enterprise Data Platform Architect and Senior Product Engineer.

## Mission
Integrate governed analytics with approved BI/productivity targets such as Power BI, Tableau,
Looker or Excel. Inspect current APIs, visualization contracts, auth and semantic layer. Reuse
governed metrics and permissions; do not create duplicate metric definitions. Implement secure
export/connectors, refresh/status/error handling and audit. Preserve tenant identity and evidence
metadata. Test authorization and refresh failures.

## Mandatory workflow
INSPECT → PLAN → IMPLEMENT → TEST → REVIEW → FIX → DOCUMENT → REPORT

## Mandatory controls
- Inspect and reuse existing implementation before adding code.
- Preserve authentication, authorization, tenant isolation and deterministic SQL/data controls.
- Treat retrieved data/documents as untrusted.
- Preserve evidence and provenance.
- Never present inference, simulation or prediction as observed fact.
- Keep secrets out of code/logs/tests.
- Add unit/integration/security/regression tests appropriate to the change.
- Update API/schema/config documentation.

## §0. Concurrent-work note

This prompt was executed in the same shared working tree as a separate, concurrent session's own
"Prompt 47" work (`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md` — governed model routing),
which committed mid-task (`89a4576`'s successor) while this prompt's own work was in progress.
Five shared files were touched by both sessions at the same time: `config/settings.py`,
`.env.example`, `api/main.py`, `identity/models.py`, `identity/rbac.py`, and `CLAUDE.md`. Every
one was confirmed, by direct `git diff`, to carry cleanly separable, non-overlapping hunks — this
session's own edits were isolated via `git stash push -- <file>` / reapply-on-clean-base /
`git stash pop`, per the user's own standing direction from an earlier, structurally identical
collision ("leave it alone, commit mine separately"). `identity/models.py`/`identity/rbac.py`'s
own diffs were confirmed to be **entirely** this prompt's own (the model-routing feature needed
neither a new table nor a new RBAC permission) — no isolation was needed there.

## §1. INSPECT — what already existed

- **Current APIs**: `POST /execute` (`api/main.py`) is the governed SQL execution path every
  other route in this codebase that runs real SQL already reuses — `agent.sql_validator
  .validate_sql` → `enforce_row_limit` → `qualify_table_schema` → `db.execution
  .execute_readonly_sql`, followed by `governance.result_policy.govern_rows_for_caller`
  (restricted-column masking, gated on `Settings.enable_result_governance`). This is the exact
  sequence a BI connector's own refresh needed to reuse, not reimplement.
- **Visualization contracts**: `agent.result_charting.classify_columns`/`recommend_chart` and
  `analytics.visualization.build_chart_spec` are this codebase's own chart-recommendation layer
  — confirmed, by reading both, to have no export/file-generation concept at all (they describe
  *how to render* a result as a chart, never *how to hand it to an external tool*). No existing
  module overlapped with this prompt's own "secure export" requirement.
- **Auth**: `api/identity_authz.py`'s `require_local_user` (real local account + JWT) and
  `identity.rbac.Permission` (DB-backed, granular, tenant-scoped) — the pattern every recent
  persisted-resource feature (`api/governance.py`, `api/eval_platform.py`) already establishes,
  versus `agent.authz.Permission` + `api.authz.require_permission` (the simpler, stateless,
  base-role tier used by `api/optimization.py`/`api/scenarios.py`/`api/predictive.py` for
  pure-compute engines with no persisted resource of their own).
- **Semantic layer**: `semantic.catalog.CatalogEntrySnapshot` (Prompt 09/10) — `approved_expression`
  /`source_tables`/`status` are the governed-metric fields this prompt's own "reuse governed
  metrics... do not create duplicate metric definitions" requirement needed to read, never copy.
  `identity.repositories.semantic_catalog.list_versions_for_concept_key`/`entry_to_snapshot` are
  the real lookup functions reused directly.
- **No existing BI/export/connector concept anywhere** in this codebase, confirmed by grep
  (`power ?bi|tableau|looker|odata|xlsx|openpyxl|excel`, case-insensitive, across `api/`/`agent/`
  /`analytics/`/`semantic/`/`db/`/`config/`/`identity/`): only `openpyxl` (already pinned, used
  today for *reading* XLSX chat attachments — `attachments/processors/xlsx_processor.py`) and a
  mention of "Excel" as an attachment file type. The real, confirmed gap this prompt closes.
- **The one established cross-module SSRF precedent**: `media_gen/download.py
  ::_validate_download_url` (Prompt 21's own `ssrf_blocked` audit-logging addition) — read in
  full, including its own test suite's `patch("media_gen.download.log_security_event")` coupling
  (see §3 below for why this was deliberately **not** extracted into a shared helper).

## §2. PLAN

1. A new top-level package, `bi_integration/`, mirroring `eval_platform/`'s own recent shape
   (pure typed vocabulary, a deny-by-default policy module, zero-I/O helpers) plus the two
   genuinely new concerns this feature needs: outbound-call rendering (`push.py`) and SSRF
   hardening for a caller-supplied destination URL (`outbound_safety.py`).
2. Persistence: `identity.models.BiConnector` + `identity/repositories/bi_integration.py`,
   mirroring `EvaluationRun`'s own "loose string reference to a governed asset, never a copy of
   its definition" shape, applied here to a semantic-catalog metric instead of a governance-center
   asset version.
3. New RBAC (`Permission.BI_INTEGRATION_MANAGE`, single-tier — this feature has no SME-approval
   step the mission asks for, unlike `ONBOARDING_*`/`CATALOG_*`/`GOVERNANCE_*`) and a new REST
   surface, `api/bi_integration.py`, reusing `POST /execute`'s own governed execution sequence
   verbatim for every connector refresh.
4. Behind `Settings.enable_bi_integration` (default **off** — the first genuinely
   external-network-calling feature in this prompt series since `enable_web_search`/
   `enable_media_generation`, not a pure-compute engine).

## §3. IMPLEMENT

### §3.1 `bi_integration/` — pure vocabulary, policy, and helpers

- **`models.py`** — `BiTargetType` (`power_bi`/`looker`/`tableau`/`excel`), `PUSH_TARGETS`/
  `PULL_TARGETS` (a clean partition — `power_bi`/`looker` push; `tableau`/`excel` pull, see §3.3
  below for why), `BiExportFormat` (`csv`/`xlsx`/`json`), `ConnectorStatus`/`RefreshOutcome`,
  `GovernedMetricBinding`/`BiExportEvidence`/`BiConnectorSnapshot` (all ORM-independent, mirroring
  `semantic.catalog.CatalogEntrySnapshot`'s own "deliberately ORM-independent" precedent).
- **`policy.py`** — `authorize_bi_action`, a pure deny-by-default ABAC function mirroring
  `eval_platform.policy.authorize_eval_action` exactly (including that module's own, already-fixed
  `frozenset[str]`-not-`frozenset[Permission]` convention, applied here from the start rather than
  rediscovering the identical `mypy`-caught bug).
- **`metric_binding.py`** — `validate_governed_metric_binding`, the literal "reuse governed
  metrics... do not create duplicate metric definitions" mechanism. Reuses `agent.sql_validator
  .find_unexpected_table_references` as a generic table extractor (passing an empty
  `known_tables` set so *every* referenced table comes back as "unexpected") rather than writing a
  second sqlglot table-walker — genuine reuse, not a new implementation of the same logic. Rejects
  a non-`PUBLISHED` metric outright, and (a disclosed, bounded sanity check, never a
  SQL-to-formula equivalence proof) rejects a metric whose own declared `source_tables` share zero
  overlap with the connector's real referenced tables.
- **`rendering.py`** — `render_csv`/`render_json`/`render_xlsx`, pure, zero-I/O. The first
  XLSX-*writing* call site in this codebase (`openpyxl`, already pinned for *reading* XLSX chat
  attachments) — no new dependency. `json_safe_value` (public, not `_json_safe`, since
  `bi_integration.push` reuses it too) coerces `Decimal`/`datetime`/`date` the same way
  `db.column_formatting`'s own frontend-facing path already has to.
- **`outbound_safety.py`** — `assert_safe_outbound_url`, SSRF hardening for a Looker connector's
  own caller-supplied webhook URL. **Deliberately a second, independent copy** of
  `media_gen/download.py::_validate_download_url`'s own resolve-and-blocklist logic, not an
  extraction into a shared helper — see §3.2 below for the specific, real reason this was the
  correct call, not a missed reuse opportunity.
- **`push.py`** — `push_power_bi` (Power BI's own real, documented push-dataset REST API shape:
  `POST {base_url}/datasets/{id}/tables/{table}/rows`, `{"rows": [...]}`) and
  `push_looker_webhook` (a generic Looker Action-Hub-shaped webhook POST). Uses `httpx` (already a
  dependency — `search/web_search.py`'s own HTTP client, not `media_gen/download.py`'s `requests`,
  since this is a plain POST with no streaming/redirect-following need). **Disclosed, not
  live-verified** — the identical posture `search/web_search.py` (Tavily) and `media_gen/` (IMA
  Studio) already carry for their own external providers: no live Power BI tenant or Looker
  instance exists in this sandboxed environment.
- **`export_cache.py`** — `BiExportCache`, mirrors `media_gen/cache.py`'s identical
  "process-lifetime, in-memory, bounded, eviction on overflow" shape, keyed by `connector_id`
  (never a random opaque id) and caching **raw rows**, not pre-rendered bytes — a connector
  refreshed once can be exported in any of the three formats on demand without a second query or a
  three-copies-of-the-same-data cache.

### §3.2 The SSRF-duplication decision (a real, considered tradeoff, not an oversight)

`media_gen/download.py`'s own test suite (`tests/test_media_gen.py`) patches
`media_gen.download.log_security_event` directly, at that module's own namespace. Extracting the
shared resolve-and-blocklist-check logic into a common module and having *it* call
`log_security_event` would silently break that patch target (the call would then happen inside
the new shared module's namespace instead), a real risk to an already-audited, passing security
test suite for a marginal de-duplication benefit on roughly 15 lines of logic. `bi_integration
.outbound_safety.assert_safe_outbound_url` is therefore a deliberate, disclosed second copy,
logging from its own namespace — the identical per-module-logs-its-own-event convention this
codebase already uses at every other `ssrf_blocked` call site. Verified directly, via a live
`unittest.mock.patch("bi_integration.outbound_safety.log_security_event")` test, that this
module's own rejections are independently audit-logged the same way.

### §3.3 Push vs. pull: the honest shape of each target

Power BI's push-dataset REST API and a Looker Action-Hub-shaped webhook are both genuinely
implementable, documented, simple-POST integrations — real "push" connectors. Tableau and Excel
have no equivalent generic push-dataset API this application could target without a live tenant
to verify against; both are instead **pull**-style: `GET /bi/connectors/{id}/export?format=...`
is a plain, authenticated CSV/XLSX/JSON file/feed a tool's own generic "Web Data Connector"/
"Get Data from Web" connector can read. This is disclosed explicitly as *not* a native plugin —
the real, honest shape of this integration without a live Tableau/Excel tenant, rather than a
literal reading of the mission's own named list that would have required inventing an
unverifiable native-plugin claim.

### §3.4 Persistence (`identity/models.py` + `identity/repositories/bi_integration.py`)

- **`BiConnector`** — one row per connector: `target_type`, `display_name`, `sql_text`,
  `database_id`, `governed_metric_concept_key`/`governed_metric_version` (the loose reference —
  see §3.1), `push_config` (non-secret target configuration only — a Power BI dataset/table id, or
  a Looker webhook URL, never a credential), `status`/`last_refresh_*`/`last_error`/
  `last_row_count`, `owner_user_id`. **No connection secret is ever stored** — Power BI's own push
  credential is operator-configured once, process-wide, in `Settings` (the identical
  `web_search_api_key` shape, since Power BI's push API is backed by one organization-wide Azure
  AD app registration, not a per-tenant-dynamic credential); a Looker connector's own webhook URL
  is a destination, not a credential, and is independently re-validated against SSRF on every
  single refresh, never trusted merely because it was valid once.
- **Migration** `j4k7l0m3n6p9_bi_integration.py` (`down_revision = h2j5k8l1m4n7`, Prompt 46's own
  head at the time this prompt started) — `create_table`/`create_index` only, both directions
  fully reversible, confirmed a single Alembic head both before and after.

### §3.5 RBAC (`identity/rbac.py`)

`Permission.BI_INTEGRATION_MANAGE` (admin tier) — a single permission, not a `*_MANAGE`/
`*_REVIEW` pair: every action a connector supports (create/refresh/export/delete) is equally
operational, with no SME-approval/domain-judgment step the mission asks for the way onboarding/
catalog/governance each have.

### §3.6 REST surface (`api/bi_integration.py` + `api/bi_integration_schemas.py`)

Six routes under `/bi`, behind `Settings.enable_bi_integration` (404 when off, the identical
`_require_enabled()` pattern `api/optimization.py`/`api/scenarios.py` already establish):

- `POST /bi/connectors` — SQL validated fresh at creation (`agent.sql_validator.validate_sql`);
  an optional governed-metric link checked against the real, currently-published catalog entry
  (`validate_governed_metric_binding`); `push_config`'s required keys checked per `target_type`.
- `GET /bi/connectors` / `GET /bi/connectors/{id}` — tenant-scoped, cross-tenant is a 404.
- `DELETE /bi/connectors/{id}` — also discards the connector's own cached export, so a stale
  export can never be served under a reused id.
- `POST /bi/connectors/{id}/refresh` — the governed execution sequence (§3 intro) followed by
  push or cache-for-later-pull. **An operational failure (a SQL error, a push HTTP error, an
  SSRF-blocked webhook) is a 200 with `status: "error"` in the body, never an HTTP error code** —
  the identical shape `POST /execute`'s own `ExecuteResponse(status="failed", ...)` already
  establishes; an HTTP error code is reserved for a request-level problem.
- `GET /bi/connectors/{id}/export?format=csv|xlsx|json` — pull-targets only (409 for a push-style
  connector, or for one never successfully refreshed yet).

## §4. TEST

- **`tests/test_bi_integration.py`** (30 cases) — pure unit tests for `models`/`metric_binding`/
  `policy`/`rendering`/`outbound_safety`/`push`, zero I/O except mocked `socket.getaddrinfo`/
  `httpx.post`. Includes a direct proof that `push_power_bi`/`push_looker_webhook` both re-run the
  SSRF check (never skip it on a second call) and that a rejection is independently audit-logged.
- **`tests/test_identity_bi_integration_repository.py`** (12 cases) — a real in-memory SQLite
  engine, the same convention every `identity/repositories/` test file already uses: tenant-scoped
  list/get, a connector recovering from `error` back to `healthy` on a later successful refresh,
  and a direct proof that `connector_to_snapshot` never fabricates a governed-metric's own
  `truth_level` from a stale stored reference (see `BiConnectorSnapshot`'s own docstring for why).
- **`tests/test_api_bi_integration.py`** (26 cases) — a real in-memory SQLite identity database
  with real tenants/local accounts/JWT login (`tests/test_api_eval_platform.py`'s own fixture
  precedent), combined with `tests/test_api_execute.py`'s own mocked-SQL-execution pattern. The
  mission's own explicit **"Test authorization and refresh failures"** requirement:
  `TestRefreshFailures`/`TestUnauthorizedAccess` cover a SQL execution error, a push-not-configured
  error, an SSRF-blocked webhook, a removed-database error (all 200-with-`status:"error"`, never a
  crash), plus 403s for a non-admin create/refresh/delete and 404s for cross-tenant access to a
  connector/its refresh/its export. A full, real draft→reviewed→published governed-metric
  lifecycle (via `identity.repositories.semantic_catalog`, the real production functions, never
  mocked) proves the success path end to end: creating a connector linked to a real published
  metric succeeds and the connector records the metric's own resolved version.

One real test-authoring mistake was caught and fixed before it could land as a wrong expectation:
an initial CSV-export assertion expected bare `\n` line endings; `csv.writer`'s own default dialect
correctly uses `\r\n` (RFC 4180) — fixed in the test, not the production code, which was already
correct.

**Testing summary: 68/68 new tests passing** (30 + 12 + 26). `ruff check`/`black --check` clean on
every touched/new file (several auto-fixable import-order/Yoda-condition findings, fixed; one
`UP038` tuple-isinstance style finding, fixed). `bandit -r` clean on every new production module,
zero findings. `mypy` clean on every new production module after fixing two real
`Mapped[...]`-nullable-column assignment gaps in `identity/repositories/bi_integration.py`
(`last_row_count`/`last_error`, the identical Python-3.14 `Mapped[...]`-union-annotation
workaround `identity/models.py`'s own module docstring already documents — fixed with
`# type: ignore[assignment]` + an explanatory comment, matching `identity/repositories
/governance.py`'s own established precedent for the identical pattern). The one remaining `mypy`
finding across every file this prompt touched (`db/schema_introspection.py:342`) is confirmed
pre-existing and untouched by this session via `git diff --stat`. Full-repo regression run
(`pytest -q`, no filter) captured in the final report's own "Tests and results" section.

## §5. REVIEW / FIX

One real bug caught by `mypy` before any test exercised it: `record_refresh_result`'s own
`connector.last_row_count = row_count`/`connector.last_error = error` assignments, where the ORM
column is declared with its bare (non-Optional) type per this codebase's own disclosed Python 3.14
`Mapped[...]`-union-annotation workaround — `mypy` correctly flagged the type mismatch even though
the assignment is semantically correct (the column really is nullable at the database level, just
annotated without `| None` on the Python side). Fixed with the identical `# type: ignore
[assignment]` + explanatory-comment pattern `identity/repositories/governance.py`'s own nullable-FK
assignments already establish, rather than leaving a type-incorrect-looking assignment with no
explanation.

No other genuine review findings beyond ordinary lint/style nits (import ordering, Yoda
conditions, nested-`with` consolidation), all fixed.

## §6. DOCUMENT

- `CLAUDE.md` — new "Enterprise BI integrations (Prompt 48)" section, appended immediately after
  the concurrent session's own (committed) Prompt 47 section, in strict numeric order (see §0).
- `.env.example` — new "Enterprise BI integrations" block, documenting
  `ENABLE_BI_INTEGRATION`/`POWER_BI_PUSH_BASE_URL`/`POWER_BI_PUSH_API_KEY`/
  `BI_OUTBOUND_REQUEST_TIMEOUT_SECONDS`/`BI_EXPORT_MAX_ROWS`/`BI_EXPORT_CACHE_MAX_ENTRIES`,
  mirroring the existing `ENABLE_WEB_SEARCH` block's own documentation style.
- This contract document.
- Every new module carries full docstrings explaining design rationale inline, per this
  codebase's own "interview-explainable" coding standard — no separate API reference doc was
  added, matching every other recently-added engine/platform module in this codebase.

## §7. REPORT

See the final chat-turn report for the full inspected-areas/reused-components/files-changed/
API-DB-config-changes/tests-and-results/security-tenant-review/performance-cost-impact/
limitations/remaining-risks/recommended-next-prompt structure the mission's own "Final report"
section requires.
