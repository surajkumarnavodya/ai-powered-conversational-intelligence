# CLAUDE.md — Project Context for Claude Code Sessions

This file orients any session working in this repo. **Read it before making
changes.** It holds the rules that apply to *every* task; the detail for any
one subsystem lives in a reference document this file names.

**Start here for navigation:**
[`docs/architecture/context-map.md`](docs/architecture/context-map.md) — which
document owns which topic, which is authoritative, and what to read before
touching a given area.

---

## 1. Project overview

An **AI-Powered Conversational Intelligence** platform, originally and still
primarily a **Text-to-SQL** agent. A user asks a question in plain English in
a React dashboard (`frontend/`); a LangGraph agent turns it into SQL against a
real, user-configured database (relevant tables retrieved from ChromaDB,
embedded from **live schema introspection** — never a hardcoded sample); the
SQL is validated (AST-based SELECT-only allowlist), executed read-only, and
the result is rendered as a table plus an opt-in chart. The LLM runs locally
via **Ollama** — no network calls for the LLM and no API keys for that part.

Database connectivity is entirely `.env`-driven. **There is no hardcoded
connection string, host, or schema anywhere in the codebase.**

Layered on top, each optional and most off by default: multi-source
orchestration (documents, policies, web, media, attachments), a deterministic
analytics/forecasting/recommendation stack, governed semantic metadata, a
multi-tenant identity system, and a set of admin and review dashboards.

### Implementation status is not uniform — do not assume

This platform was built over ~55 numbered prompts. Capabilities fall into
four genuinely different states, and **conflating them is the most common
mistake a session makes here**:

| State | Meaning | Example |
|---|---|---|
| **Live** | Wired into a request path and running | the 19-node SQL graph, RBAC, result governance |
| **Built, not wired** | Implemented and tested, no live caller | `analytics.root_cause` (needs a second dataset the pipeline never produces), `recommendation.RecommendationProvider` (Protocol only), `agent.translation` (no route, node or setting calls it) |
| **Off by default** | Live only when an operator enables it | web search, media search/generation, result cache, model routing, BI integrations |
| **Disclosed gap** | Deliberately absent, with a reason | no scheduler/background worker, no delivery channel for alerts or briefings, no streaming |

**Never document an assumed capability as implemented, and never remove a
requirement because the feature is not yet implemented.** When unsure, read
the feature's own contract document (§8) rather than inferring from code.

### Two history notes that prevent real mistakes

- The project once shipped a bundled sample **DuckDB** database. It was fully
  removed by explicit decision, with no fallback or demo mode. References to
  DuckDB, `db/schema.sql`, or `scripts/seed_db.py` are stale — treat them as
  wrong, not as a parallel supported mode.
- The project once shipped a **Streamlit** app alongside the React dashboard.
  It was removed once parity was confirmed; `frontend/` is the only UI. Two
  non-UI modules were relocated rather than deleted:
  `ui/session_history.py` → `eval/history.py`, `ui/column_formatting.py` →
  `db/column_formatting.py` (mirrored on the frontend by
  `frontend/src/lib/columnFormatting.ts`). References to `ui/app.py`,
  `ui.session_history`, or `ui.column_formatting` are stale.

---

## 2. Verified technology stack

| Concern | Choice |
|---|---|
| LLM runtime | Ollama, default `llama3.1:8b` (swappable via `.env` — e.g. `sqlcoder`, `duckdb-nsql`). Per-request selection via `AskRequest.model`, validated against `Settings.ollama_allowed_models` |
| Orchestration | LangGraph — explicit state machines, not free-form agents. `agent/graph.py` (the 19-node SQL pipeline) and, when multi-source is on, `agent/orchestrator/graph.py` in front of it |
| Config / validation | Pydantic v2. `config/settings.py::Settings` is a frozen `pydantic_settings.BaseSettings`; secrets are `pydantic.SecretStr` |
| User's database | PostgreSQL, MySQL, SQL Server, or Oracle via SQLAlchemy. One or more named connections (`DB_CONNECTIONS`), auto-routed per question |
| SQL parsing / validation | sqlglot — AST allowlist in the dialect matching the target `DB_TYPE` |
| Schema retrieval | ChromaDB, one collection per configured database, embedded from live introspection |
| Business-context retrieval | Same Chroma client, a second collection (`retrieval/`) — 8 typed chunk kinds, deterministic ids |
| Identity / chat history | Optional (`LOCAL_AUTH_ENABLED`), dedicated PostgreSQL DB (`identity/`) — Argon2id + JWT, SQLAlchemy 2.0 ORM, Alembic. **The only ORM and the only real migrations in this repo** |
| Document / policy RAG | SQL Server 2025+/Azure SQL native `VECTOR` (`rag/store.py`), a dedicated connection. Off by default |
| Web search | Pluggable provider (`search/web_search.py`); Tavily implemented. Off by default |
| UI | React 19 + Vite + TypeScript + Tailwind + Zustand + @tanstack/react-query + i18next (en/es/fr/hi/mr). Charting is Plotly-figure-JSON in, Chart.js out (`agent/result_charting.py` builds it, `frontend/src/lib/chartAdapter.ts` renders it) |
| Streaming | SSE via `api/streaming.py` (`POST /ask/stream`) — per-node progress from `agent/progress.py`, then the same `AskResponse`. Calls `api.main.ask`, never a second handler. Token-level streaming is **not** implemented |
| Outbound HTTP safety | `security/outbound_url.py` — host allowlist checked before DNS, blocklist on every resolved address, redirects re-validated hop by hop |
| Backend tests | pytest, fully mocked — no real DB, no Ollama. Opt-in live tests in `tests/live/` are deselected by default (`-m 'not live'`) |
| Frontend tests | vitest + @testing-library/react; oxlint |
| Python | **3.11 is the target per spec, but this machine only has 3.14**, so `.venv` is built against 3.14. See `docs/architecture/overview.md` for two real 3.14 issues already hit and fixed |

---

## 3. Repository navigation

One line per package. **Full per-package conventions and the reasons behind
each boundary:** [`docs/architecture/repository-map.md`](docs/architecture/repository-map.md).

**Core pipeline**

| Path | Owns |
|---|---|
| `config/` | Every tunable, sourced from `.env`. Never hardcode a model, path, or connection detail elsewhere |
| `db/` | Engine lifecycle, live schema introspection (the sole source of truth for schema shape), read-only execution, cost estimation, adapters, result cache |
| `embeddings/` | Per-database schema indexing and retrieval; the multi-database auto-router |
| `retrieval/` | Business-context vector retrieval (table/column/relationship/glossary/metric/sql_example/documentation/business_concept chunks) |
| `agent/` | LangGraph nodes (`nodes.py`), graph wiring (`graph.py`), **`sql_validator.py` — the security boundary**, authz, intent, complexity, planning, provenance |
| `agent/orchestrator/` | The multi-source router and subgraphs, one level *above* `agent/` — never the reverse |
| `agent/tools/`, `agent/analyst/`, `agent/multiagent/` | MCP-shaped tool registry (this app's capabilities exposed *to* a client); the AI Data Analyst loop; the fixed-routing specialist supervisor |
| `sources/` | The source-type contract and registry — single owner of each source's routing description, permission and availability. `agent/orchestrator/` derives its tables from here |
| `rest/` | REST/OpenAPI connector, read-only over allowlisted hosts. Off by default; **not routable yet** (no descriptor, no orchestrator node) |
| `mcp_client/` | Client *of* remote MCP servers — the opposite direction to `agent/tools/`, no shared code. Off by default. Administrator-controlled trust only |
| `jobs/` | The asynchronous question queue -- per-user concurrency, queueing, job status, cancellation, graceful shutdown. Sits *below* `api/` and never imports it: the work a job performs is injected as a `JobRunner`, so there is no second pipeline to drift from the governed one |
| `guardrails/` | The configurable façade over the layered safety controls, plus the grounding/citation gate. **Adds a seam, not a second security model** -- `GUARDRAIL_ACTION` governs advisory layers only and can never soften a fail-closed control |
| `api/` | The FastAPI app — the REST surface every UI action goes through, and (once `frontend/dist` exists) the server for the dashboard itself |
| `frontend/` | The React dashboard, the only UI this project ships |

**Analytics and intelligence** — `analytics/` (engine, anomaly, root cause,
causal, forecasting, visualization, scenario, predictive), `recommendation/`,
`semantic/` (catalog, metrics, intelligence), `optimization/`,
`data_quality/`, `lineage/`, `schema_drift/`, `kpi_monitoring/`, `briefing/`,
`decision_center/`, `artifacts/`.

**Governance and platform** — `governance/` (the runtime request/result
policy engine), `governance_center/` (the asset lifecycle — a *different*
concern), `identity/`, `onboarding/`, `eval_platform/`, `bi_integration/`,
`model_routing/`, `workflow/`, `security/`, `observability/`, `moderation/`.

**Optional sources** — `rag/`, `search/`, `media/`, `media_gen/`,
`attachments/`, `voice/`.

**Supporting** — `scripts/` (standalone entry points), `eval/` (the
execution-accuracy benchmark, the security benchmark, the component
benchmark), `tests/` (mirrors package names; `tests/security/` holds
adversarial-scenario regressions).

---

## 4. Core architecture rules

These hold across the platform. Breaking one is a regression even if tests
pass.

1. **The graph is an explicit, bounded state machine.** `agent/graph.py` is
   19 nodes with a conditional retry edge back to `generate_sql` (or to
   `retrieve_schema` for a missing reference), capped by
   `state["max_retries"]`. Never introduce an unbounded loop or a second
   retry budget. Adding or renaming a node means updating
   `tests/test_sql_agent_integration.py::test_existing_nodes_are_all_still_present`
   (an exhaustive equality check) **and**
   `frontend/src/components/sql/QueryLifecycle.tsx`'s stage map.
   **A node added anywhere inside a retry cycle also changes the bound on
   the whole run, and getting that wrong is an HTTP 500, not a slow
   query.** LangGraph's `recursion_limit` is computed by
   `agent.graph.recursion_limit_for` from three structural constants —
   `LONGEST_RETRY_CYCLE_NODES` (13: the `execute_sql → retrieve_schema`
   missing-reference cycle), `PRE_CYCLE_NODES` and
   `POST_EXECUTION_NODES`. This went wrong once, silently:
   `enforce_display_names` was inserted into that cycle, the arithmetic
   was never updated, and at the default `MAX_RETRIES=3` the worst case
   needed 54 node executions against a limit of 50, so a question whose
   every attempt hit a missing column raised `GraphRecursionError` and
   `POST /ask` answered **500** instead of its own "gave up after 4
   attempts" (2026-10-28, `LOP-001`).
   `tests/test_agent_loop_bounds.py::TestTheStructuralBoundMatchesTheGraph`
   now recomputes all three from the compiled graph's edge set and fails
   with the new number, so this cannot drift unnoticed again — and
   `run_agent` catches `GraphRecursionError` regardless, because a
   constant can drift and a catch cannot. Never remove that catch to
   "let the real error surface".
2. **An accuracy aid fails open; a safety control fails closed.** Planning,
   golden examples, business-context retrieval, insight, analytics,
   recommendations, and persistence all degrade to "no extra help" on error
   and are never a reason a question cannot be answered. Validation,
   authorization, tenant resolution, moderation, and the restricted-column
   gate refuse instead.
3. **The LLM may propose; it never decides.** Every routing choice,
   tool call, and generated statement passes a deterministic check the model
   cannot influence. A model's own refusal is never the control.
4. **Provenance is mandatory and never upgraded silently.**
   `agent.provenance.DataTruthLevel` (`DATABASE_FACT` / `AI_INFERENCE` /
   `CONFIRMED_BUSINESS_TRUTH`) travels with every claim. **Never present
   inference, simulation, forecast, or prediction as observed fact.** Only an
   explicit human action (publishing a catalog entry, accepting a finding,
   resolving a recommendation) confirms anything.
5. **Reuse the governed path; never build a second one.** SQL execution is
   `validate_sql` → `enforce_row_limit` → `qualify_table_schema` →
   `execute_readonly_sql` → `govern_rows_for_caller`. Every caller
   (`agent.nodes.execute_sql_node`, `POST /execute`, BI refresh, artifact
   refresh) uses that exact sequence. A new caller reuses it rather than
   reimplementing any part.
6. **Request-scoped state, never process-global mutation.** The selected
   database, model, tenant, and roles are values in the per-request
   `AgentState`. The Ollama client and compiled graphs are process-lifetime
   singletons and must stay stateless. (`tests/conftest.py` clears those
   caches between tests — without it a monkeypatched fake is silently
   ignored.)
7. **Additive, not a rewrite.** Several subsystems are deliberately built and
   tested without a live caller (§1). Do not "finish the wiring" as a side
   effect of an unrelated change, and do not delete them as dead code.
8. **One owner per fact.** Where two surfaces could state the same thing,
   exactly one does. This is load-bearing in the UI especially — before
   adding a badge, panel, or field, check which component already owns it.

---

## 5. Mandatory security and privacy rules

**Never weaken one of these to simplify a change, and never rely on a UI
restriction as a boundary.** Full detail:
[`SECURITY.md`](SECURITY.md), [`docs/AUTHORIZATION.md`](docs/AUTHORIZATION.md),
[`docs/AUTHENTICATION.md`](docs/AUTHENTICATION.md),
[`docs/security/THREAT_MODEL.md`](docs/security/THREAT_MODEL.md),
[`docs/MULTI_TENANCY.md`](docs/MULTI_TENANCY.md).

### 5.1 SQL is untrusted output, always

- `agent/sql_validator.py` parses with sqlglot and accepts only a single
  `SELECT`/`UNION`/`EXCEPT`/`INTERSECT` — an **allowlist of the parsed
  statement type**, never a regex blocklist. It also rejects system
  catalog/data-dictionary references (`information_schema`, `pg_catalog`,
  mssql `sys`, Oracle `ALL_*`/`DBA_*`/`USER_*`/`V$`/`GV$`), nested
  aggregates, and unsafe query hints. **Two further refusals were added
  2026-10-25** by the UAT-readiness Phase 2 attack sweep, both safety
  violations (fail closed, never retried): `row_lock` (`FOR UPDATE`/`FOR
  SHARE` and MSSQL `WITH (TABLOCKX)`-style hints — read-only in their
  projection, blocking in effect) and `unparseable_depth`. The second
  matters beyond its own payload: sqlglot parses *and walks and
  regenerates* by recursive descent, so a 90-byte input made the
  validator raise `RecursionError` — a `RuntimeError`, not a
  `SqlglotError` — instead of returning a refusal. **A gate that crashes
  has not failed closed.** The trigger depth is not a property of the
  input: it moves with the dialect and with the caller's own stack depth,
  so the guard is an exception catch around the whole check sequence, not
  a length or depth cap. Any new sqlglot-parsing security helper needs
  the same guard; `agent.sql_validator.projection_source_columns` carries
  it. See [`docs/uat-readiness/phase-2-security.md`](docs/uat-readiness/phase-2-security.md) §3.1.
- **Validation runs every time SQL is about to be shown or executed,
  including after a user hand-edits it.** An edit is exactly as untrusted as
  an LLM generation. A stored statement (a BI connector's, an artifact's) is
  re-validated on every use — never trusted because it validated once.
- Read-only is **layered, not code-only**: the validator, plus a genuinely
  read-only `DB_USER`. `db.connection.check_write_privileges` is
  startup-enforced. If asked to harden further, push on the database role.
- The row cap is enforced **twice** — `LIMIT`/`FETCH` in the SQL text *and*
  `fetchmany()` at the cursor — so a mistranslated query cannot bypass it.
  The query timeout is enforced at the driver level or by forced connection
  abort.
- The agent's internal retry executions are read-only, validated, capped and
  timed out, but are **never shown to the user**. Nothing is rendered until
  "Confirm and Run", which re-validates and re-executes the current text.

### 5.2 Authorization

- `agent/authz.py` is the base-role permission layer (`viewer`/`user`/
  `analyst`/`admin`); `identity/rbac.py` is the granular layer for identity,
  admin and governed-lifecycle routes. **An unrecognized role grants zero
  permissions.**
- RBAC is wired into every data-touching and expensive route **and into the
  multi-source router itself** — `router_node` filters the LLM's own source
  selection through a permission check before any subgraph runs. A routing
  decision can request access; it can never grant it.
- A UI-level restriction is **never** a boundary. Hiding a nav item or a
  button is UX; the server re-checks every request. `POST
  /navigation/access-denied` audit-logs a refused screen.
- `ENVIRONMENT=production` fails closed at startup when identity is
  unconfigured.

### 5.3 Tenant isolation

- A tenant id is **never read from anything a client sends.** It comes only
  from a `User` row this server loaded, this app's own signature-verified JWT
  `tid` claim, or an IdP-signed claim. `security.tenancy.reject_client_tenant_override`
  **refuses** (400) any request carrying `X-Tenant-Id`/`X-Tenant`/
  `Tenant-Id`/`X-Tenant-Override`, before authentication, in every auth mode.
- **Unknown means deny, never "default".** A missing, soft-deleted, or
  suspended tenant fails closed, and every failure reports one generic
  message — a caller must not be able to tell "no such tenant" from
  "suspended" from "not yours".
  **"Fails closed" is true of one of the two authorization surfaces, and
  measuring which is worth your time before you rely on it (2026-10-28,
  `AUZ-003`).** `resolve_tenant_context` — the only function that reads
  tenant *status* live — has exactly two non-test callers, both on the
  `identity.rbac` path (`require_local_user`, and login/refresh). The
  `agent.authz` path (`api.authz.require_permission`) authorizes from the
  signature-verified JWT alone and touches no database, so it never sees
  a suspension. Measured with an untouched access token, user **and**
  tenant suspended: `/auth/me`, `/conversations` and `/navigation` → 401,
  but `GET /models`, `GET /sources`, `GET /schema/tables` and
  **`POST /ask`** → 200, with the question actually answered. The bound
  is one access-token lifetime (`access_token_expire_minutes`, default
  15, and its `Field(...)` sets **no upper bound**); refresh does refuse,
  so it self-heals. Open and flagged for an owner decision — four options
  weighed in [`docs/uat-readiness/phase-4-authorization.md`](docs/uat-readiness/phase-4-authorization.md)
  §4.2 — and pinned as *current* behaviour by
  `tests/security/test_authorization_matrix.py::TestSuspensionDoesNotReachTheTokenOnlySurface`,
  so the fix cannot land without that test changing.
  A related trap cost this audit one void measurement: `api/main.py`
  serves the frontend from a catch-all `GET /{full_path:path}`, so **any
  nonexistent path returns 200** with `<!doctype html>`. `GET /config`
  and `GET /schema` are not routes. On this app a 200 is never on its own
  evidence that a route authorized you.
- Cross-tenant access to a resource returns the **same 404 a nonexistent
  resource gets** (anti-enumeration), never a distinguishable 403.
  Verified across 11 cross-user attempts on a real conversation and its
  share, 2026-10-28, with the body byte-identical to a random UUID's —
  but **two stores have no tenant to compare at all.** `rag/store.py`
  and `media/store.py` have no `tenant_id` column and not one entry
  point that accepts a tenant, and `api/documents.py`,
  `api/media*.py` and `api/attachments.py` contain the string "tenant"
  **zero** times each. Reproduced at runtime: a `tenant_b` admin
  listed, downloaded and **deleted** a `default`-tenant document
  (200/200/204). That is `AUZ-002`, an open **S1** needing a migration;
  three `xfail(strict=True)` tests in
  `tests/security/test_authorization_matrix.py::TestCrossTenantDocumentAccess`
  hold the line. Do not add a new caller of either store without
  threading a tenant through it.
- Tenant scoping is folded into the query's own `WHERE` clause, never applied
  as a post-hoc filter.

### 5.4 Data classification, restricted fields and result governance

- `governance/` is the centralized engine every request passes through: what
  may be asked (`request_policy`), what may be returned (`result_policy`),
  what may be recommended (`recommendation_safety`).
- **`config/sensitive_columns.yaml` ships as an empty template**, so
  `governance.classification`'s name-pattern inference fills the gap. An
  explicit human classification always wins — including when it *loosens*.
- A caller lacking `Permission.VIEW_RESTRICTED_COLUMNS` sees `[restricted]`.
  **`CREDENTIAL` and `PAYMENT` are withheld from every caller, including an
  admin** — no role unlocks them.
- Protection happens **before the model sees the values**: the result gate
  runs in `execute_sql_node` before `state["result_rows"]` is set, so the
  insight step, recommendation engine, chart builder and response all receive
  protected rows.
- The result gate classifies the **returned column names**, not the SQL text
  — so a `UNION` or `SELECT *` reaches the same protection. **This entry
  also said "an alias", and that was false in the dangerous direction
  until 2026-10-25.** A rename does not keep the name the gate reads:
  `SELECT salary AS region` came back as a column called `region`, which
  classifies as ordinary business data, so the value was returned in the
  clear — 13 of 14 measured rename and wrapper variants defeated the gate,
  `SELECT password_hash AS note` among them, i.e. a `CREDENTIAL` column the
  bullet above says *no* role unlocks. Closed by classifying each output by
  the strictest of its own name and the base columns it was **computed
  from**: `agent.sql_validator.projection_source_columns` resolves those
  (through aliases, wrappers, arithmetic, CTE renames and every compound
  branch) and the two governed callers that hold the executed SQL pass them
  into `govern_result_for_domain`. Both parameters are optional, so a new
  caller that forgets them silently gets the *old, weaker* behaviour —
  pass them. One residual is deliberate and is a standing policy question,
  not a bug: an aggregate over a restricted column is still returned, so
  `MAX(salary) WHERE id = 7` discloses an individual value. See
  [`docs/uat-readiness/phase-2-security.md`](docs/uat-readiness/phase-2-security.md) §4.1–§4.2.
- **Small-group suppression applies to an authorized caller too** — it
  protects the individual in the group, not the column's sensitivity. The
  group label and count stay visible and **suppression never drops a row** (a
  vanished row lets a reader difference a published total against the visible
  rows and recover the hidden value). The per-domain *export ceiling*
  (`DomainPolicy.export_row_limit`) is the one control that does remove rows,
  and it must always be disclosed — `/execute` reports `truncated=True`, and
  the result gate names it in `actions`.
- A **stored** result is re-governed on every read against the *current*
  caller's roles — never trusted because it was governed when captured. Roles
  change. All **three** read-back surfaces do this: `api.artifacts._result_out`,
  `api.shares._governed_result_snapshot` and `api.jobs._regovern_job_result`
  (the asynchronous question queue's stored result — this entry said "both"
  and named only the first two until 2026-10-25). A share is the sharpest
  case — an "anyone with the link" viewer holds no roles at all — so a new
  surface that serves a persisted `result_snapshot` must re-govern it too.

### 5.5 Untrusted content and prompt injection

- Everything retrieved or supplied is **data, never instructions**: web
  search results, ingested PDFs, uploaded attachments, OCR/ASR/caption text,
  media metadata, and database cell values. Each is framed as such in the
  prompt that consumes it.
- Text claiming authority ("this is authorized", "pretend I am admin") and a
  database cell claiming it are both data. **Authorization comes only from
  the caller's roles.**
- Refusal messages are fixed module constants, never built from the question,
  so a refusal cannot be used as an oracle for what matched.
- `security/injection_patterns.py` is a **detection** signal, not the
  boundary — `agent/sql_validator.py` and the RBAC/restricted-column gates
  bound consequences. Attachment scanning is detection-only and never blocks.
- A policy chunk carrying a `sensitivity_category` is **never** summarized
  into an answer — checked before the LLM sees the chunk, fail-closed,
  regardless of caller role. `DocumentRecord.restricted_roles` is a second,
  independent RBAC gate.
- The registry never hot-swaps a live prompt: `agent/llm_client.py` uses
  in-code constants, because making a database row the system prompt would
  turn every write into prompt injection with full pipeline reach.

### 5.6 Secrets, logging and outbound calls

- Secrets are `pydantic.SecretStr`; call sites must use
  `.get_secret_value()`. A leftover `str(secret)` silently sends the literal
  `"**********"`.
- **Never log a connection string, password, or full result rows** — log
  `DB_TYPE`/`DB_NAME`/table names/row counts only. This is a security
  property of this codebase, not a style preference.
- `agent.exceptions.AgentError` carries both full internal detail and a short
  `.safe_message`. Response bodies use `.safe_message`; two global handlers
  in `api/main.py` are the last-resort net. `security.redaction.redact_secrets`
  is applied to any text this app did not construct itself.
- No connection secret is ever persisted — onboarding supplies one per call
  and discards it; `identity.models.OnboardingJob` has no password column.
- Outbound URLs are SSRF-validated (scheme, hostname, resolved address,
  redirects re-validated hop by hop) and **re-validated on every use**, not
  once at creation. Generated media is served through this app under an
  opaque id, never the provider's CDN URL.

### 5.7 Uploads, rate limits and release gates

- File uploads are validated by type, malware-scanned at the raw-byte level
  before any parser runs (fail-closed once enabled), decompression-bomb
  guarded for ZIP-based Office formats, and PDF-preflighted for embedded
  JavaScript/auto-open actions. `moderation/gate.py` is **mandatory** whenever
  media search or document/policy RAG is on — a hard reject on any chunk
  rejects the entire asset, and nothing from a rejected file is stored.
- `/ask` has real admission control (bounded pool + per-caller cap → immediate
  429 before any LLM/DB work). Rate limits are keyed by **real identity**, not
  raw client IP, and `X-Forwarded-For` is read only when an operator sets
  `trusted_proxy_count`.
- CI gates are **blocking** — `ruff`, `black --check`, `bandit`, `pytest`,
  `pip-audit` (with an individually justified ignore list) and
  `detect-secrets` — **except `mypy`, which is report-only**
  (`continue-on-error: true`, and deliberately the last step in the job).
  It was made non-blocking because the `test` job's steps run sequentially
  and a failing `mypy` silently stopped `bandit`/`pytest`/`pip-audit` from
  running at all. So treat a type error as a real defect to fix — CI will
  *report* it and still go green. The standing backlog is in §13.
  **`mypy` is not the only non-blocking step** — `gitleaks`, the Trivy
  container scan and the load-test smoke scenario are too, for their own
  dated reasons. §13's "Four CI steps are non-blocking" bullet is the
  authoritative list; read it before concluding that a green CI run
  proves anything about those four.
- **Do not assume the blocking gates are currently green.** Measured
  2026-10-17 at `08f385e`: `ruff`, `bandit` and `pip-audit` all exit
  non-zero, so three blocking steps fail, while `black` and `pytest`
  pass. Every one of the three is a verified-benign finding awaiting an
  owner decision, not a vulnerability — see §13 and
  [`docs/certification/2026-10-17_release_gate_v2.md`](docs/certification/2026-10-17_release_gate_v2.md)
  §11.6. Never "fix" one by weakening the gate.
  **Re-measured 2026-10-22 after a dependency-remediation pass:**
  `bandit` reported 4 Low / 0 Medium / 0 High across 52,621 lines, run
  exactly as CI runs it. **That was recorded here as "passes" on a false
  premise, and the premise is corrected 2026-10-23:** `bandit` exits **1**
  on any finding at or above its `--severity-level`, whose default is
  `low` — so "only Low findings remain" never meant the gate passed. It
  was red. §13's account was the accurate one. It passes now (exit 0)
  because each deliberate finding carries a justified, correctly-placed
  `# nosec`; verified by stripping them, which makes the gate exit 1 again
  (`docs/security/VERIFICATION_RE_REVIEW.md`).
  `pip-audit` went from 46 findings in 5 packages to **6
  in 2**, both of which are now evidenced as unreachable rather than
  merely benign (see
  [`docs/security/CVE_TRIAGE.md`](docs/security/CVE_TRIAGE.md)'s
  2026-10-22 section). `ruff`'s single `I001` in
  `agent/multiagent/agents.py` is unchanged.
  **`pip-audit` now passes too**: its CI ignore list was rebuilt from 14
  IDs to the 6 this checkout actually reports — ten had gone stale once
  the bumps cleared their advisories, and a stale ignore silently
  re-suppresses a finding if a package is downgraded back into range. The
  command then reports "No known vulnerabilities found, 7 ignored".
  **A third gate was failing and was invisible locally**: `black --check .`
  passed only because the venv had `black 24.10.0` while
  `requirements-dev.txt` pins `26.3.1`. CI installs the pin and would have
  failed on three tracked files. Fixed by formatting two of them and
  `force-exclude`ing `identity/migrations/versions/`, since reformatting
  an applied revision is the hand-edit §12 forbids. So as of 2026-10-22
  `black`, `bandit`, `pytest` and `pip-audit` all pass; the lone `I001`
  is the only blocking finding left.
  **Re-measured 2026-10-25 (UAT-readiness Phase 2), and the picture
  changed in three ways.** Read this paragraph rather than the one above,
  and re-measure again before trusting either.
  *Green:* `ruff check .` exits **0** — the `I001` above is fixed with
  ruff's own import sort, not a `# noqa`, so there is no longer a standing
  blocking lint finding. `black` clean over 1,670 files. `bandit` "No
  issues identified" over 57,270 lines with **0** `# nosec` skipped.
  `pytest` **9,066 passed / 0 failed**. `npm audit --omit=dev` 0
  vulnerabilities.
  *Was red, now green:* `pip-audit` had gone red again on three `pypdf`
  advisories published after 2026-10-22 (CVE-2026-115572/115580/115585).
  Bumped to `pypdf==6.21.2` rather than ignored, because
  `rag/ingestion.py` and `attachments/processors/` point that parser at
  caller-supplied bytes. **The lesson is that this list decays:** a green
  `pip-audit` is a statement about a date, not about the repository.
  *Still red, and it was not previously known to be:* **`detect-secrets`
  is a fifth blocking gate, and it fails.** Neither this section nor §13
  had ever counted it. It is **not** a leaked secret — the full-repo
  triage found no un-baselined secret hash, only ~37 files of new
  `tests/` fixtures and Alembic revision hex plus two i18n label strings
  — the baseline is simply stale, and refreshing it must be done on
  Linux because `detect-secrets` writes OS-native path separators.
  `scripts/check_secrets_baseline.py` now normalises those and prints
  *what* changed grouped by what it means, which it did not before; that
  silence is why the gate's redness went unnoticed. Details and the exact
  command:
  [`docs/uat-readiness/phase-2-security.md`](docs/uat-readiness/phase-2-security.md)
  §6.3–§6.5.

### 5.8 Domain privacy and safety — consult the policy before changing behavior

**Authoritative document:**
[`docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md`](docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md).
**Read it before changing anything that affects what the platform may be
asked, may return, or may recommend in a business domain.**

`governance/domains.py` defines 19 domains plus a `GENERAL` fallback (the
mission's 18, plus `CONSTRUCTION`, which was added later and derived from
its neighbours rather than specified — the enum's own docstring says so), and 14
`ProhibitedAction` values. The rules that must not regress:

- **No domain policy protects personal data less than the baseline.**
  `BASELINE_RESTRICTED_CATEGORIES` (PII, SPI, PHI, compensation, performance,
  credential, payment, banking) is unioned into every domain. Always read
  `DomainPolicy.effective_restricted_categories`, **never
  `restricted_categories` directly** — otherwise detecting a domain could
  *loosen* protection.
- **The `GENERAL` fallback prohibits all 14 actions, not none.** If the
  domain is unknown, no decision about an individual is safe to make or
  recommend.
- Prohibited outputs include individual employment actions, individual
  compensation decisions, individual performance rankings, medical diagnosis
  or prescription, clinical decisions, credit/loan decisions, fraud
  accusations, individualized financial advice, legal conclusions, liability
  determinations, protected-characteristic inference, sensitive personal
  targeting, credential disclosure, and unsupported allegations. **Aggregate,
  organizational and process recommendations are unaffected** — over-blocking
  ordinary analytics is its own failure mode.
- One `ProhibitedAction` list drives both the request gate and the
  recommendation filter, so "what we refuse to be asked" and "what we refuse
  to propose" cannot drift. An unsafe recommendation is **withheld and
  reported, never rewritten** — editing it would leave the evidence attached
  to a claim it no longer supports.
- Telemetry counts decisions, domains and categories — **never content**.
- Each layer has an independent rollback switch:
  `ENABLE_REQUEST_GOVERNANCE`, `ENABLE_RESULT_GOVERNANCE`,
  `ENABLE_RECOMMENDATION_SAFETY`.

No compliance with any named regulatory regime is claimed. These are
technical controls supporting privacy and governance requirements.

---

## 6. Coding and implementation standards

### 6.1 Before writing code

1. **Inspect the existing implementation first.** This repo's single most
   repeated lesson: nearly every "new" requirement turns out to be partly
   built. Grep for the capability, read the owning module's docstring, and
   read the relevant contract document (§8).
2. **Reuse existing components and services.** Extend a function rather than
   writing a parallel one. If two implementations would answer the same
   question, that is a defect regardless of test results.
3. **Do not rewrite working behavior** as a side effect of an unrelated
   change. Preserve public signatures where callers depend on them.
4. **Follow the surrounding conventions** — comment density, naming, module
   layout, error handling, and the fail-open/fail-closed split (§4.2).

### 6.2 Style and typing

- Type hints and docstrings on every public function. This codebase is meant
  to be explainable, not merely working.
- Black for formatting, ruff for linting, mypy for type checking — config in
  `pyproject.toml`. Run `.\tasks.ps1 lint` before considering a change done.
- **No `print()`** except in the standalone CLI scripts meant to be read as
  terminal output (`scripts/test_db_connection.py`,
  `scripts/integration_test.py`). Everything else uses `logging`
  (level from `LOG_LEVEL` in `.env`). Agent nodes log each state transition,
  so the terminal shows the agent's steps live.
- Frontend: TypeScript, Tailwind tokens (never raw colors), i18n keys for
  user-visible text across all five locales, and `sanitizeLabel` on any
  database-derived string before display or re-submission.

### 6.3 Changes and compatibility

- Make **incremental, testable** changes. Prefer an additive field with a
  default over a breaking change to a response model.
- Check backward compatibility: `api/schemas.py` response models are frozen
  and widely consumed; an added field must be optional with a default.
- Use the existing configuration pattern — a new tunable is a `Settings`
  field with a `Field(...)` constraint and a comment explaining the default,
  not a literal in code.
- Never hand-edit an applied Alembic migration; add a new revision. A new
  migration must carry `server_default` for any ORM-filled timestamp column
  (pinned by `tests/test_identity_schema_invariants.py` after a real
  production-only breakage).

### 6.4 Reporting

- **Report assumptions, limitations and unresolved issues explicitly.**
- **Never claim implementation or test success without evidence.** Quote the
  command and its output. If tests fail, say so with the output; if a step
  was skipped, say that.
- When a disclosed limitation still stands after a change, say it still
  stands rather than quietly dropping it.

---

## 7. Testing and validation

```powershell
.\tasks.ps1 test      # pytest
.\tasks.ps1 lint      # ruff check + black --check + mypy
.\tasks.ps1 format    # black + ruff --fix
```

Equivalent `make test` / `make lint` / `make format` targets exist for
WSL/macOS/Linux. Frontend: `npm run test` / `npm run lint` /
`npx tsc --noEmit -p tsconfig.app.json` inside `frontend/`.

- **Every pytest test is fully mocked — no real database, no Ollama.**
  **Enforced since 2026-10-24, not merely stated**:
  `tests/conftest.py::_block_real_ollama_calls` patches
  `ollama.Client.chat`/`embed`/`embeddings` to raise for every test. It
  was added because the rule was being broken silently --
  `tests/test_orchestrator.py::TestRunOrchestrated
  ::test_flag_off_with_attachments_still_runs_the_orchestrator_graph` ran
  the orchestrator graph for real, so `router_node` submitted a genuine
  `call_ollama` to a thread pool it then abandoned
  (`shutdown(wait=False)`), and the request went through on any machine
  with Ollama running. The test passed on the keyword fallback either
  way, and the only trace was an `httpx` log line printed *after*
  pytest's summary. If a test needs an LLM reply, patch the seam the code
  under test uses (`rag.llm.call_ollama`,
  `agent.nodes.generate_sql_from_llm`, ...) — never rely on a timeout or
  a fallback firing.
  `scripts/integration_test.py`, `scripts/run_benchmark.py` and
  `eval/security_benchmark/` are manual and require real infrastructure; CI
  never runs them.
- `tests/conftest.py` strips every env var a `Settings` field could read, so a
  test never silently inherits the developer's real `.env`. It also clears the
  three process-lifetime caches between tests.
  **That sentence was true and insufficient until 2026-10-28.** It strips
  names that *are* `Settings` field names, but `config/settings.py`
  deliberately reads two **open-ended** families straight from
  `os.environ` — `DB_CONNECTIONS` plus a `DB_<NAME>_*` block per entry
  (`_parse_named_connections`), and `MCP_SERVERS` plus `MCP_<NAME>_*`.
  Neither family's members are field names, so none of them was stripped,
  and `load_dotenv()` had already loaded them. Measured with the autouse
  fixture active: **24 `DB_*` vars visible inside a test**, two of them
  `DB_<NAME>_PASSWORD`, and `get_settings().databases` returning this
  machine's **four real SQL Server connections** — so the bullet above
  ("no real database") was false in exactly the way it had already been
  found false for Ollama: silently. The only trace was an `SAWarning`
  about a real server version, printed after pytest's summary.
  Now closed by stripping the prefix families, and pinned by
  `tests/test_settings_env_isolation.py` (7 tests, mutation-verified) so
  it cannot regress unnoticed again. The lesson generalises past this
  one fix: **any new config read that bypasses a `Settings` field also
  bypasses test isolation**, so it needs a matching entry in
  `conftest._DYNAMIC_ENV_PREFIXES`.
- `tests/security/` is for adversarial-scenario regressions that do not fit a
  single module's test file. Check whether a scenario is already covered
  before adding one.
- Add unit, integration, security and regression tests appropriate to the
  change, and **update the tests and documentation that the change
  invalidates**.
- Where a guard is meant to be load-bearing, prove it is not vacuous —
  temporarily breaking it must fail a test. Several guards in this repo are
  documented as mutation-verified for exactly this reason.
- A test that needs real concurrency must use real threads; a sequential test
  passes even with the bug it is meant to catch.

---

## 8. Documentation references

**Read [`docs/architecture/context-map.md`](docs/architecture/context-map.md)
first** — it maps every topic to its owning document, says which is
authoritative, and explains how to resolve a conflict.

| Need | Document |
|---|---|
| Where code lives, and why | [`docs/architecture/repository-map.md`](docs/architecture/repository-map.md) |
| Cross-cutting design decisions and their invariants | [`docs/architecture/overview.md`](docs/architecture/overview.md) |
| Per-node graph walkthrough, diagrams, retry routing table | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Analytics / forecasting / recommendation / lineage features | [`docs/architecture/features-analytics.md`](docs/architecture/features-analytics.md) |
| Identity, tenancy, governed lifecycles, admin surfaces | [`docs/architecture/features-governance-and-admin.md`](docs/architecture/features-governance-and-admin.md) |
| Frontend and chat-workspace features | [`docs/architecture/features-frontend.md`](docs/architecture/features-frontend.md) |
| Security posture, threat model, CVE triage | [`SECURITY.md`](SECURITY.md), [`docs/security/`](docs/security/) |
| Domain privacy and prohibited outputs | [`docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md`](docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md) |
| Authentication / authorization | [`docs/AUTHENTICATION.md`](docs/AUTHENTICATION.md), [`docs/AUTHORIZATION.md`](docs/AUTHORIZATION.md) |
| Multi-tenancy rules and limitations | [`docs/MULTI_TENANCY.md`](docs/MULTI_TENANCY.md) |
| Configuration reference | [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) |
| The `GRANT`s a DBA applies for the read-only database account, per engine | [`docs/DBA_READONLY_ACCOUNTS.md`](docs/DBA_READONLY_ACCOUNTS.md) |
| Entity display names: metadata, SQL repair, join and chart-label rules | [`docs/DISPLAY_NAMES.md`](docs/DISPLAY_NAMES.md) |
| Result-column semantics and chart axis binding (measure vs key) | [`docs/CHART_AXIS_BINDING.md`](docs/CHART_AXIS_BINDING.md) |
| Measured latency, what causes it, and the hardware ceiling | [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) |
| Intent-based source routing, the router contract and its fallback | [`docs/ROUTING.md`](docs/ROUTING.md) |
| How a message is read against the conversation before it: relations, antecedent selection, the standalone rewrite, ambiguity, conversation memory | [`docs/CONVERSATIONAL_CONTEXT.md`](docs/CONVERSATIONAL_CONTEXT.md) |
| Per-user concurrency, the question queue, deterministic source routing, per-task model pins, guardrails, grounding | [`docs/CONCURRENCY.md`](docs/CONCURRENCY.md) |
| Production readiness verdict | [`docs/PRODUCTION_READINESS_RELEASE_GATE.md`](docs/PRODUCTION_READINESS_RELEASE_GATE.md) |
| One feature's full design, tests and limitations | `NN_*_CONTRACT.md` at the repo root — one per numbered prompt; indexed in the context map |

**A feature's own `NN_*_CONTRACT.md` is authoritative for that feature.** The
reference files above carry the invariants and the known limitations; the
contract carries the inspection findings, the design, and the testing
summary.

---

## 9. How to run

See [`README.md`](README.md) for full setup. Short version:

```powershell
.venv\Scripts\Activate.ps1
ollama pull llama3.1:8b
# fill in .env with your real DB connection details first
python scripts\test_db_connection.py
python scripts\build_embeddings.py
python -m scripts.ingest_schema --database-id default   # optional, business-context retrieval
alembic -c identity\alembic.ini upgrade head            # only if LOCAL_AUTH_ENABLED=true
cd frontend; npm install; npm run build; cd ..
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000/` — the API process also serves the built
dashboard. For frontend hot-reload, run `npm run dev` in `frontend/` instead
(Vite proxies API calls to the uvicorn process; **a new backend route prefix
must be added to `vite.config.ts`'s `BACKEND_ROUTES`** — a recurring, easy
miss).

| Task | PowerShell | Make |
|---|---|---|
| Create venv + install deps | `.\tasks.ps1 setup` | `make setup` |
| Verify DB connection | `python scripts\test_db_connection.py` | same |
| Build/refresh embeddings | `python scripts\build_embeddings.py` | same |
| Ingest business context | `python -m scripts.ingest_schema --database-id default` | same |
| Apply identity migrations | `alembic -c identity\alembic.ini upgrade head` | same |
| Run app | `.\tasks.ps1 run` | `make run` |
| Manual real-DB check | `python scripts\integration_test.py` | same |

### Windows / Visual Studio notes

- Opened via **File > Open > Folder**, not a `.sln`-driven project. A minimal
  `.sln` exists only so "Open Solution" also works; it defines no real build
  configurations.
- PowerShell is the primary shell; activation is `.venv\Scripts\Activate.ps1`.
  If script execution is blocked, check `Get-ExecutionPolicy` and set
  `RemoteSigned` for `CurrentUser` — never suggest machine-wide
  `Unrestricted`.
- `DB_TYPE=mssql` needs the Microsoft ODBC Driver for SQL Server installed as
  a **system** package. `ENABLE_MEDIA_SEARCH=true` needs the Tesseract OCR
  binary likewise (OCR fails open without it — reduced accuracy, not a
  blocker). Ollama must be running before the API starts
  (`OLLAMA_HOST` in `.env`, default `http://localhost:11434`).
- `.vs/` is gitignored. Chroma writes local files under
  `embeddings/.chroma/`, regenerated by `scripts/build_embeddings.py`.

---

## 10. Standard task workflow

The workflow every numbered prompt in this repo has followed:

**INSPECT → PLAN → IMPLEMENT → TEST → REVIEW → FIX → DOCUMENT → REPORT**

1. **Inspect** — find what already exists for this capability. Read the
   owning module's docstring and the relevant contract (§8). Record what you
   found and what it means for the design.
2. **Plan** — state which existing code you will reuse, what is genuinely
   new, and which invariants (§4, §5) the change touches.
3. **Implement** — incrementally, reusing the governed paths, following the
   surrounding conventions.
4. **Test** — add the tests the change warrants; run the suites and read the
   output. Attribute any pre-existing failure before claiming it is yours or
   not.
5. **Review** — re-read your own diff against §4 and §5. Check for duplicated
   facts, a weakened control, or a stated limitation that is now false.
6. **Fix** — including anything the tests found that you did not expect.
7. **Document** — update the feature's contract or reference doc, this file
   if a rule changed, and `vite.config.ts`/i18n/tests if the change implies
   them.
8. **Report** — commands run, results, assumptions, limitations, and what you
   deliberately did not do.

---

## 11. Definition of done

- [ ] Existing implementation was inspected and reused where it applied.
- [ ] No duplicate implementation of an existing capability, and no second
      owner for a fact already stated elsewhere.
- [ ] Every rule in §4 and §5 still holds; no security or privacy control was
      weakened, and no UI gate was treated as a boundary.
- [ ] Provenance is correct — nothing inferred is presented as observed fact.
- [ ] `.\tasks.ps1 lint` and `.\tasks.ps1 test` pass, or every failure is
      attributed with evidence.
- [ ] Frontend (if touched): `npm run test`, `npm run lint`, and
      `tsc --noEmit` pass.
- [ ] Tests cover the change, including a security/regression test where a
      control is involved; load-bearing guards are proven non-vacuous.
- [ ] Documentation updated: the feature's contract/reference doc, and this
      file only if a cross-cutting rule changed.
- [ ] Backward compatibility checked for API schemas, settings and
      migrations.
- [ ] Assumptions, limitations and skipped work reported explicitly.

---

## 12. Never touch

Only when a task is specifically about them — never as a side effect:

- **Secrets**: `.env` — real, populated `DB_PASSWORD`,
  `RAG_STORE_CONNECTION_STRING`, `WEB_SEARCH_API_KEY` and JWT keys. Never
  read into a commit, log line, or response. `.env.example` is the template and is safe to edit.
  `.secrets.baseline` is tool-generated; regenerate, never hand-edit.
- **Migrations**: `identity/migrations/` — the only real migrations here.
  Never hand-edit an applied revision; add a new one.
- **Generated/derived** (each rebuilt by its own command): `embeddings/.chroma/`,
  `frontend/dist/`, any `node_modules/`, `.venv/`, `__pycache__/`,
  `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `.vs/`, `media_library/`.
- **Benchmark-pinned prompt text**: `agent.llm_client._INSIGHT_SYSTEM_PROMPT`
  and the generation system prompt. `docs/EVALUATION_CURRENT.md`'s accuracy
  numbers were measured against their exact wording; changing them without
  being able to re-run that benchmark invalidates the only real accuracy
  figures this project has.

---

## 13. Known gaps and follow-ups

Named so a future session does not rediscover them. Each feature's own
contract lists its specific limitations; these are the cross-cutting ones.

- ~~**`search/` has no dedicated mocked unit test file.**~~ **Closed.**
  `tests/test_web_search.py` (14 tests, `httpx.post` mocked throughout) now
  pins the Tavily request shape, response parsing, empty/missing results,
  HTTP error propagation, secret non-leakage and `SUPPORTED_SEARCH_PROVIDERS`
  dispatch. Verified 2026-10-14; the entry had outlived the gap by some time.
  **Treat every item below as dated** — confirm a gap still exists before
  acting on it, the same way §13 asks you to measure before claiming a
  finding is yours.
- **No per-source query decomposition.** Every routed subgraph receives the
  same full question, so a question that is really two unrelated asks can
  retrieve poorly on both sides.
- **`DB_<NAME>_SCHEMA` is one schema per connection**, not "all schemas". A
  database whose tables span many schemas exposes one at a time.
- **The eval harness covers the SQL path only** — it exercises
  `agent.graph.run_agent`, not multi-source or cross-source questions.
- **Follow-up resolution is per-request, not checkpointed.**
  `agent.nodes.classify_followup_node` sees only the
  `conversation_history` the caller resends, capped at
  `MAX_FOLLOWUP_EXCHANGES`; no LangGraph checkpointer exists (none ships for
  SQL Server, so it would need a custom `BaseCheckpointSaver`).
  **Narrowed 2026-10-24.** What that node *does* with the window is no
  longer "check whether it is non-empty and take `[-1]`":
  `agent/context_resolution.py` + `agent/conversation_memory.py` now
  classify the message's relation to the conversation, score every turn
  in the window as a candidate antecedent, carry constraints forward with
  same-kind replacement on a correction, and produce a standalone
  `resolved_question` that reaches schema retrieval, the router, every
  source node and the generation prompt. The *statelessness* above is
  unchanged and is still the real limitation: the window is whatever the
  client resends, so editing or deleting an earlier turn changes context
  only because the client sends a different array, and nothing is cached
  across requests. Authoritative:
  [`docs/CONVERSATIONAL_CONTEXT.md`](docs/CONVERSATIONAL_CONTEXT.md).
  Three things that doc records and a session should not rediscover.
  `ENABLE_CONTEXT_RESOLUTION_LLM` is **off by default** because one
  escalation measured 11.1s on this hardware *and* because defaulting it
  on put a real Ollama call inside the test suite. The router's
  `general_knowledge`/`conversation` routes **do** compose a real answer
  now (`agent/orchestrator/general_knowledge.py`), but only when
  `ENABLE_GENERAL_KNOWLEDGE_ANSWERS=true`; the default still returns the
  fixed message, because that route is chosen *when routing could not
  match a source*, which is sometimes a miss on a real data question.
  And resolution now has a **measured** accuracy figure --
  `eval/context_resolution_benchmark/` (65 hand-labelled cases, no
  infrastructure needed, asserted in CI by
  `tests/test_context_resolution_benchmark.py`), which is the one place
  in this repo where routing-adjacent accuracy is quantified rather than
  only pinned by test. It earned its keep: it found seven real defects
  during development, one of which made a politely-phrased refinement
  ("Can you exclude cancelled orders?") come out as a new topic carrying
  no context at all.
  *Corrected twice. 2026-10-17: this entry cited `langgraph==0.2.62`,
  while the pin read `1.0.10rc1`.* **2026-10-22: both were true at once —
  `requirements.txt` pinned `1.0.10rc1` and the venv actually had
  `0.2.62` installed, three major versions behind its own pin.** The RC is
  now resolved: the pin is **`langgraph==1.0.10`** final, which also fixes
  PYSEC-2026-83 and closes the "a release candidate in a production
  requirements file" concern this entry used to raise. It pulls
  `langchain-core` 0.3.86 → 1.6.13 transitively; both graphs still compile
  and the suite is unchanged. The conclusion is unchanged and re-verified:
  zero `BaseCheckpointSaver`/`checkpointer=` usage anywhere in this repo.
- **Chat history and artifacts cover locally-authenticated users only.** An
  OIDC or unauthenticated caller is answered normally but nothing is
  persisted — there is no `identity.users` row to attach a conversation to.
- **A reloaded turn is not a byte-for-byte reconstruction** — the answer, its
  SQL and a bounded result snapshot are persisted (in
  `identity.models.AiOutput.metadata_json`); charts, retrieved DDL and
  per-source citations are not.
- **No scheduler, no background worker, no delivery channel.** KPI
  monitoring, schema drift and briefings return documents or exit codes; CLI
  scripts under `scripts/` are the cron entry points. Nothing is emailed,
  posted or messaged.
- ~~**No streaming.**~~ **Partly closed 2026-10-15.** `POST /ask/stream`
  (`api/streaming.py`) emits per-node progress over SSE and then the same
  `AskResponse` `POST /ask` returns -- it *calls* that handler rather than
  reimplementing it, so every control applies identically. What is still
  absent is **token-level** streaming: `agent/llm_client.py` makes one
  blocking call per step and the generation/insight prompts are
  benchmark-pinned (§12), so streaming the model's own tokens is a
  separate, evidence-gated change. No WebSocket endpoint exists.
- **Process-local state**: rate limiters, circuit breakers, the performance
  rollup, the result cache, the security-event ring buffer, **the
  asynchronous question queue (`jobs/`)** and the compiled graph/Ollama
  singletons are all per-worker. A multi-worker deployment has one of each
  per worker. The queue is the sharpest case, because its cap is advertised
  to users: N workers admit up to N x `MAX_CONCURRENT_QUESTIONS_PER_USER`,
  and queued/running jobs do not survive a restart. See
  [`docs/CONCURRENCY.md`](docs/CONCURRENCY.md) 2.9.
- **The asynchronous question queue requires a real user account.**
  `POST /questions` returns 403 in `none`/`static_token` auth mode, by
  design: those modes give every caller the same subject, so job ownership
  cannot be enforced, and one shared bucket would let each caller read the
  others' results. Deny-by-default — not a gap to close by relaxing it.
- **Routing-confidence and grounding thresholds are judgement calls, not
  measurements.** `SOURCE_CONFIDENCE_THRESHOLD` (0.70),
  `MIN_SOURCE_CONFIDENCE` (0.70) and
  `guardrails.grounding.MIN_LEXICAL_OVERLAP` (0.15) were chosen to err
  toward deferring to the LLM classifier and toward *not* refusing a
  correct answer. The deterministic router's signal patterns are
  hand-authored, and the eval harness still covers the SQL path only, so
  routing accuracy has no labelled-corpus measurement behind it. Treat
  `AskResponse.source_confidence` as evidence, never as a probability that
  an answer is correct.
- **Environment-blocked verifications** (not oversights — no infrastructure
  exists here): DAST has never run against a live deployment, OIDC has never
  been verified against a live IdP, ClamAV's fail-closed path has never seen
  a real daemon, `docs/EVALUATION_CURRENT.md`'s accuracy numbers predate
  many pipeline changes, and **the question queue's 100-concurrent path has
  only been exercised against a mocked runner** — never against a live
  database and Ollama, which is what `python scripts/integration_test.py`
  is for.
- **Lint/type findings — measure before claiming one is, or is not, yours.**
  Measured 2026-10-17: `mypy .` reported **631 errors across 115 files**
  (**re-measured 2026-10-25: 723 across 129** — the growth is entirely in
  `tests/`, tracking tests added since; the **7 production-code errors
  below are unchanged**), of
  which **616 are in `tests/`** and 8 in `eval/security_benchmark/` (largely
  `AgentState`-vs-`dict` looseness) — a bounded, separate hygiene pass.
  `black --check .` is clean (1,635 files) and `ruff check .` has exactly
  one finding, an import-order `I001` in `agent/multiagent/agents.py`.
  **The "production modules are otherwise clean" claim this entry used to
  make is no longer true** — seven of those errors are in production code,
  so it is named here rather than left for a session to trip over:
  `agent/sql_validator.py:839` (two errors — a `# type: ignore[union-attr]`
  that no longer covers the code actually reported; introduced by `933cfff`,
  2026-10-17, so *not* ancient), `db/schema_introspection.py:376`
  (`25073964`, 2026-10-04), and four SQLAlchemy
  `SQLCoreOperations[...]`-vs-`None` assignments in
  `identity/repositories/semantic_intelligence.py:199,200,242` and
  `identity/repositories/recommendation_governance.py:402`. `mypy` is
  report-only in CI (§5.7), so nothing is blocking them.
  **Re-measured 2026-10-17 at `08f385e`: every figure above still holds
  exactly** — 631 mypy errors in 115 files with the same 7 production
  findings, black clean over 1,635 files, one ruff `I001`. No drift, so a
  new finding is attributable. The one addition: **`bandit` now reports 4
  Low findings, not 3** — `agent/sql_validator.py:842`
  (`B112 try_except_continue`) joined the set, introduced by `933cfff`'s
  `describe_sql`, alongside `agent/harness/retry.py:75` (B311),
  `api/streaming.py:82` (B101) and `security/audit_log.py:255` (B110).
  Still 0 High and 0 Medium, and all four are deliberate and already
  annotated for `ruff` but not for `bandit` — which is why that blocking
  gate fails (§5.7).
- ~~**Three tests fail only in file order, and the cause is cross-file
  state, not the tests.**~~ **Closed 2026-10-24, with the cause
  attributed.** The entry was right that it was cross-file state and
  right that `tests/conftest.py`'s cache clears did not cover it. The
  specific chain, reproduced with two tests
  (`pytest tests/security/test_model_routing_never_weakens_deterministic_controls.py::TestACallerPinnedModelIsNeverOverridden::test_run_agent_honors_an_explicit_selection_over_routing
  tests/test_agent_nodes.py::TestGenerateRecommendationsNode::test_missing_state_fields_default_to_empty_without_crashing`):

  1. That security test runs the **real** compiled graph. Its `graph_run`
     fixture mocks four LLM seams but not `generate_analytical_plan_from_llm`
     or `generate_query_plan_from_llm`, and its question ("top 5 products
     per region with year-over-year growth and total margin ratio") is the
     only one in the file complex enough to trip the planning gate.
  2. So it made **three genuine HTTP calls to a real Ollama server**, and
     `build_analytical_plan` took **14 seconds**.
  3. That latency landed in `observability.metrics.get_default_metrics`'
     process-global rollup, which `tests/conftest.py` was not clearing.
  4. `generate_recommendations_node` reads that rollup and correctly
     emitted "The 'build_analytical_plan' stage is slow (p95 14039ms over
     1 recent request(s))" — so every later `recommendations == []`
     assertion failed, and only when `tests/security/` ran first.

  Fixed in three places, because the cause and the mechanism are
  different: `_block_real_ollama_calls` (§7) stops the slow call, the
  fixture now mocks the two planning seams, and
  `_clear_process_singleton_caches` clears the rollup so a genuinely slow
  stage in any future test cannot leak a recommendation into an unrelated
  one. **Mutation-verified**: removing the rollup clear fails 2 of the 9
  tests in `tests/test_no_real_llm_calls_in_tests.py`, one of them the
  exact `recommendations == []` assertion the five failures made. A full
  run is now **0 failed** (8261 passed, measured 2026-10-24; 8,297 passed
  / 0 failed re-measured 2026-10-25; **9,485 passed / 0 failed
  re-measured 2026-10-28**, 16 skipped, 11 deselected, 5 xfailed, 514s).

- **The local 7B model still refuses some answerable terse questions.**
  Measured 2026-10-22, after the retrieval fix in
  `embeddings.retriever._select_by_relevance` (see
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)'s `retrieve_schema_node`
  entry): of six phrasings of "top selling products", four now answer
  correctly, while **"Worst selling products" and "Most popular
  products" still end in `rejection_reason=off_topic`**. This is no
  longer a retrieval problem -- the generation prompt verifiably contains
  `FactResellerSales` and `SalesAmount` (19,494 chars, one model call),
  and `qwen2.5-coder:7b` returns the literal string `NOT_A_QUERY`
  anyway. Deterministic across repeated runs. **Confirmed to be model
  capacity, not the pipeline: re-running the same two questions with
  `model="llama3.1:8b"` answered both** -- "Worst selling products" in
  316.1s (10 rows) and "Most popular products" in 110.7s (10 rows),
  through the identical graph, schema and prompt. So `OLLAMA_MODEL` is a
  real lever an operator already has; note the latency it costs on this
  hardware (see [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md)), which is
  why it is not simply changed here. The other lever is the generation
  system prompt, and that is benchmark-pinned (§12), so changing it needs
  the benchmark re-run -- a deliberate, separate exercise at ~80s per
  question. **Do not "fix" this
  by retrying the off-topic sentinel**: `agent.nodes.generate_sql_node`
  deliberately never retries it (there is no error to feed back), and
  turning a refusal path into a retry path would both cost a second full
  generation cycle and weaken a control. Note also that SQL *quality* on
  these terse questions is uneven even when they succeed -- one phrasing
  returned a bare product list with no measure column, another a single
  row -- which is the same model-capacity limitation, not a pipeline bug.
  **Re-measured 2026-10-28 (UAT-readiness Phase 5) and this entry is now
  stale in both directions -- read the new numbers, not the ones above.**
  On `llama3.1:8b`, live, against `adventureworks`: **none of the three
  phrasings was refused**, so the false-refusal half of this entry no
  longer reproduces. What replaced it is worse and is why the entry is
  being corrected rather than deleted. "Most popular products" returned
  the ten most **expensive** products (`SELECT TOP 10
  EnglishProductName, StandardCost ... ORDER BY StandardCost DESC`) --
  a confident, plausible-looking answer to a *different question*.
  "Top selling products", the phrasing this entry calls working,
  returned **334 rows** from a `ROW_NUMBER() OVER (PARTITION BY
  d.ProductKey ORDER BY COUNT(d.ProductKey))` that ranks nothing,
  because partitioning by the column being ranked gives every row
  `rn = 1`. "Worst selling products" returned **0 rows** from the same
  degenerate shape -- a *false empty*, where the reader concludes the
  data has no answer. So "uneven quality" understates it: the failure
  mode is answering the wrong question, and one phrasing's failure is
  indistinguishable from "no data".
  **No grounding check can catch this, and that is the point worth
  keeping.** The insight for the expensive-products answer was correct
  about what had been *computed* -- it said "by standard cost", not
  "most popular" -- so provenance held and the gate behaved as designed.
  Deterministic grounding verifies an answer against *the result*; it
  cannot verify the result against the *question*. Only the eval suite
  measures that. All five phrasings are now a standing dataset
  (`eval/uat_golden/sql_accuracy.yaml`, `uat_j15_*`) so the next model
  change re-measures them instead of rediscovering them. Evidence and
  the open decision: `HAL-007` / `DOC-011` in
  [`docs/uat-readiness/FINDINGS_REGISTER.md`](docs/uat-readiness/FINDINGS_REGISTER.md).

- **A fail-closed layer does not run on one live route, and it is the
  input guard** (2026-10-28, `HAL-006`, open and flagged).
  `guardrails/__init__.py` lists input validation
  (`agent.input_guard.check_input`) as fail-closed. It is wired into
  `agent.nodes.sanitize_input_node`, which lives **inside the SQL
  subgraph**, plus `agent.orchestrator.general_knowledge` (reached only
  when `ENABLE_GENERAL_KNOWLEDGE_ANSWERS` is on) and `api.jobs`. The
  orchestrator's `route_after_router` can send a question straight to
  `synthesis` -> END when it matches no source, and on that route no
  guard runs. Measured: five structurally different inputs -- a direct
  prompt injection, an off-topic request, a DELETE request, a DROP TABLE
  request and an unanswerable forecast question -- all returned 200 /
  `succeeded` / `rejection_reason: null` in ~2.4s with one identical
  no-match message, while `check_input` on the injection returns
  `passed=False, reason=injection_detected`.
  **It is not a data-safety bypass**: that route does no retrieval, no
  generation and no execution, and the validator and RBAC gates are
  untouched; the message being byte-identical for every input even
  satisfies §5.5's no-oracle rule perfectly. What is lost is
  **detection and the audit record**, so the UAT script's J12.3
  ("trigger a refused action, then find it in the audit") cannot pass
  there, and the documented `injection_detected` / `off_topic` reasons
  are unreachable through `/ask` for an unmatched question. Pinned as
  *current* behaviour by two `xfail(strict=True)` tests in
  `tests/security/test_input_guard_reaches_every_route.py`, so the fix
  cannot land without those marks coming off.

- **The insight gate checks three kinds of contradiction, not one**
  (2026-10-28, `HAL-001`/`HAL-002`, both fixed). It used to check
  numbers only, so `"West was the strongest region."` against a result
  whose computed top label is `East` was stored and shown. It now also
  refuses a superlative attached to a label that is not the computed top
  one, and a direction opposite to a computed two-period trend. Both
  fire **only on a contradiction of something `agent.insight
  .summarize_result` computed in Python from the real rows** -- never on
  a merely unverifiable claim, which stays labelled rather than
  suppressed. A comparative, a causal claim, a date the result does not
  contain, and an entity absent from the result are **deliberate
  residuals**, each pinned by a test that says so.
  Two traps for a new caller. `agent.insight.insight_grounding_failure`
  takes `result_labels` **optionally**, and omitting it silently gets
  the weaker two-check gate -- the same shape §5.4 records for
  `projection_source_columns`. And `_compute_trend` now only produces a
  trend for a label column `analytics.period_labels
  .classify_period_column` recognises as chronological: before that
  gate, a `Region, TotalSales` result produced a -58.3%
  "period-over-period" change between two region names, and because
  `ResultSummary.allowed_percents()` feeds the gate, it *licensed* an
  insight stating a decline that never happened.

- **Four CI steps are non-blocking** by deliberate, dated decision:
  `gitleaks`, `mypy` (§5.7), the Trivy container scan — report-only because
  the action only fails a step when its `exit-code` input is set, which it
  deliberately is not, making that step's `continue-on-error` redundant —
  and the load-test smoke scenario. Every other gate blocks (§5.7).
  Note that `.github/workflows/ci.yml`'s own comment on the mypy step still
  describes the older measurement ("281 ... all in test files"); the figures
  above are the current ones.
  See [`docs/security/CVE_TRIAGE.md`](docs/security/CVE_TRIAGE.md).
  **There are five blocking gates, not four, and this bullet and §5.7
  both missed one until 2026-10-25:** `detect-secrets` (its own job, so
  it runs even when `test` fails) blocks, and it was failing. Neither
  list had ever named it, which is the more useful lesson here — a gate
  nobody enumerates is a gate nobody measures. The blocking set is
  `ruff`, `black`, `bandit`, `pytest`, `pip-audit` **and**
  `detect-secrets`.
  **Nine blocking gates as of 2026-10-29 (Phase 8), not six.** Three
  were added: a **second, blocking `mypy` step** scoped to the 41
  production packages (0 errors in 482 files; the report-only `mypy .`
  stays as it is and still reports 746, 736 of them in `tests/`), the
  frontend's **`vitest`** suite (1,508 tests that CI had never run),
  and **`npm audit --omit=dev`**. The blocking set is now `ruff`,
  `black`, `bandit`, `pytest`, `pip-audit`, `detect-secrets`,
  `mypy` (production), frontend `vitest` and `npm audit`.
  `bandit`'s scope also changed and the old number was misleading: it
  named twelve packages on the command line, so it had **never scanned
  `identity/`** or ~25 other packages — 58,041 of 119,384 lines, i.e.
  "green" meant 48.6% of the code, and the whole tree reported **24**
  findings, six in `identity/security.py`. It is now scoped by
  exclusion from `[tool.bandit]` in `pyproject.toml`, so a new package
  is covered the day it is created. `tests/test_ci_gate_coverage.py`
  fails when any of these falls behind the repository.

- **An aggregate over a restricted column is still disclosed, and that is
  a standing policy question rather than a bug.** `SEC-006` (§5.4) closed
  the rename bypass by classifying each output column by the base columns
  it was computed from; an **aggregated** projection deliberately
  contributes none, because masking `AVG(salary) AS avg_pay` would reject
  ordinary analytics and §5.8 names over-blocking as its own failure mode.
  The residual is the narrow case: `SELECT MAX(salary) FROM employees
  WHERE id = 7` returns one individual's salary. Deciding when an
  aggregate is too narrow to count as one is what small-group suppression
  already exists to decide, so extending it to this shape is a product
  decision, not a defect fix. Pinned as *current* behaviour by
  `tests/security/test_masking_survives_renaming.py::TestTheAggregateExclusionIsDeliberate`
  — so when the decision is made, that test is the one to change, and its
  failure will say so. Do not "fix" it silently in either direction.
- **The `detect-secrets` baseline is stale, so that blocking gate is red**
  (2026-10-25). No leaked secret: the triage found no un-baselined secret
  hash, only new `tests/` fixtures, Alembic revision hex and two i18n
  label strings. It cannot be refreshed from a Windows checkout --
  `detect-secrets` writes OS-native path separators, so a locally
  regenerated baseline has backslash keys CI's forward-slash scan can
  never produce, and hand-normalising them would be the hand-edit §12
  forbids for a tool-generated file. Run on Linux:
  `detect-secrets scan $(git ls-files) --baseline .secrets.baseline`, then
  `detect-secrets audit .secrets.baseline`. Full triage:
  [`docs/uat-readiness/phase-2-security.md`](docs/uat-readiness/phase-2-security.md)
  §6.4.
- **The UAT-readiness audit is in progress and writes its own findings to
  disk.** `docs/uat-readiness/FINDINGS_REGISTER.md` is the live register;
  `phase-0-recon.md`, `phase-1-architecture.md`,
  `phase-2-security.md`, `phase-3-authentication.md`,
  `phase-4-authorization.md`, `phase-5-hallucination.md`,
  `phase-6-loops.md`, `phase-7-context.md`,
  `phase-8-best-practices.md` and `phase-9-ui.md` hold the
  method and evidence for what has been done. **Phases 0-9 are
  complete; phases 10-12 are not started.**
  Before re-investigating anything security- or architecture-shaped,
  check the register: it records what was measured, on what date, and
  which items are deliberately flagged for an owner decision rather than
  open defects.
  Phases 3-4 (2026-10-28) fixed five findings — a login timing oracle
  (`AUN-001`), a lockout that outlived a password reset (`AUN-002`), a
  suspended account keeping access on the identity surface (`AUN-003`),
  a defeatable password-policy check (`AUN-004`) and the test-isolation
  leak in §7 (`BP-003`) — and left four flagged for an owner: `AUZ-002`
  (**S1**, cross-tenant document read *and delete*), `AUZ-003` (**S2**,
  §5.3), `AUZ-004` (`platform_admin` grants nothing on the
  `agent.authz` surface, because it is not in
  `agent.authz.ROLE_PERMISSIONS` — fail-closed, but it reads as a broken
  admin account) and `AUZ-005` (any `user` can write a golden example
  that steers their whole tenant; cannot escalate privilege, since the
  validator and result gate still apply, so the exposure is to answer
  *accuracy*).
  Phase 5 (2026-10-28) fixed three S2 grounding defects -- `HAL-003`
  (an INNER join in the display-name repair that halved a measured
  total), `HAL-001` (a fabricated period-over-period trend that the
  insight gate then licensed) and `HAL-002` (the gate checking numbers
  only) -- and flagged four: `HAL-004` (**S2**, the web-search path has
  no grounding gate at all, so an invented URL and publication date
  reach the reader while the Sources panel disagrees), `HAL-006`
  (**S2**, the input guard above), `HAL-007` (**S2**, terse questions
  answered wrongly -- model capacity, see the entry above) and
  `OPS-008` (**S2**, `.env` names an `OLLAMA_MODEL` that is not
  installed, so every question on the default fails; the app starts
  anyway although `GET /models` already reports `installed: false`).
  It also delivered the UAT golden question set (`eval/uat_golden/`,
  57 questions, 11 journeys, both dialects, gated by
  `tests/test_uat_golden_set.py`) -- a **behaviour and structure** gate,
  not an accuracy gate, until a human authors `expected_sql` for every
  case marked `GOLD SQL REQUIRED`.
  Phase 6 (2026-10-28) fixed seven findings and flagged one. **Two were
  S2 and both were the same shape — a *designed* terminal condition
  escaping `run_agent` as an unhandled exception, so `POST /ask`
  answered HTTP 500 instead of the refusal the pipeline had already
  composed.** `LOP-001` is the recursion-limit drift §4.1 now records.
  `LOP-004` is `ProviderUnavailable`: the Ollama circuit breaker opens,
  `harness_ollama_client` raises it a layer below the
  `OllamaUnavailableError` that `generate_sql_node` handles, and
  **nothing in the repository caught it** — so the breaker working
  exactly as designed produced a 500 at the moment graceful degradation
  mattered most. Both graphs now convert it to a terminal `failed`
  carrying the breaker's own safe message. The generalisable lesson:
  when adding a control that raises to stop a run, check that something
  on the path turns it into a response.
  The other five: `LOP-002` (no no-progress detection — a stub model
  repeating one statement spent 4 generation calls and 4 database
  executions on it; `generate_sql_node` now ends the run when an
  attempt reproduces the previous query, compared on
  `agent.sql_validator.canonical_sql_form` so a reformat does not count
  as progress. **Do not "fix" an unexpectedly short retry run by
  removing it** — check `attempt_history[-1]["outcome"] ==
  "no_progress"` first), `LOP-003` (all ten `agent/orchestrator/`
  nodes were registered without `guard_node`, so every non-SQL route
  had no step budget, no deadline check and no cancellation
  checkpoint), `LOP-005` (the dashboard's Stop button aborted the fetch
  and the server ran to completion; `/ask/stream` now cancels the
  execution on disconnect, via a new envelope *observer* in
  `agent/harness/context.py`), `LOP-006`
  (`agent.harness.resolve_timeout` had **zero** non-test callers, so
  the database query was the one operation not clamped to the request
  deadline) and `LOP-007` (retry error context was unbounded, and the
  retry block is `SectionPriority.CRITICAL`, which `context_budget`
  never drops *and never truncates* — so one verbose driver error could
  push the prompt over and make Ollama truncate from the start, where
  the system prompt lives).
  Flagged: `LOP-008` (**S3**) — a driver error reaching the retry
  prompt and `AskResponse.error_history` can contain row values, since
  `redact_secrets` redacts connection secrets and not data. Not a
  cross-privilege disclosure, but a path by which a value the *result*
  gate would have masked appears in an error message. Fixing it is a
  product decision: the error text is what makes self-correction work.
  **One standing rule came out of `LOP-004`: only
  `agent/llm_client.py` may construct an `ollama.Client`.** Anything
  else escapes the circuit breaker, the per-execution model-call budget
  and the deadline clamp all at once — which is what
  `rag.llm.call_ollama` was doing for the router, both RAG subgraphs,
  web search, media search, the general-knowledge answer and the
  analyst planner. It now calls `harness_ollama_client`, and
  `tests/test_llm_client_usage_rollup.py` asserts the one-module rule
  over the whole application.
  Two positive results worth not re-deriving: **all 213 operations deny
  an anonymous caller** (401, never 403, with an exact public allowlist
  of 16), and **no IDOR** across two users on conversations, messages,
  shares or sessions. Mass assignment is blocked everywhere by
  `extra="forbid"`. The authorization matrix is derived from the live
  app rather than a list, because this codebase authorizes in **three**
  structurally different places — a dependency reading the JWT
  (`agent.authz`), a dependency re-reading the database
  (`identity.rbac`), and a call *inside the handler*
  (`api.governance._authorize` and its siblings). Any audit technique
  that sees only one of the three reaches a wrong conclusion in both
  directions: a dependency scan called 117 routes ungated when they are
  not, and a minimal-body probe returns 422 before an in-handler check
  ever runs.

  Phase 9 (2026-10-30) fixed nine findings and flagged six, driving
  real Chrome over the live app for seven role accounts. **Three of its
  lessons are standing traps, and all three are about measurement rather
  than code.**
  *First:* **a UI audit can rate-limit itself, and the wreckage reads as
  a finding.** `POST /auth/refresh` shares
  `login_rate_limit_per_minute` (default 10/min, keyed by client IP) and
  one full page load spends one refresh, so a driver that navigates with
  a page load per screen (84 of them) trips the limit on itself. Before
  `UI-002` that *silently signed the driver out*, so cells recorded the
  sign-in page as though it were the screen under test -- three of that
  run's screenshots are byte-identical to the sign-in capture -- read as data,
  that grid claimed a `viewer` could open Platform Admin. Navigate
  client-side (`history.pushState`) and spend one refresh per role. A
  screenshot of a screen you did not reach is still a screenshot.
  *Second:* **jsdom does not reliably stay silent about what it cannot
  see.** `color-contrast` is blind there and was already disabled.
  `target-size` is also blind (every element measures 0x0). But
  `label-content-name-mismatch` reports a **false pass** -- the exact
  markup Chrome flagged lands in axe's `passes` list, because the rule
  needs rendered text -- so a jsdom test asking axe about it reads as
  evidence of the opposite of the truth. All three are disabled in
  `frontend/src/test/axe.ts` with the measurement behind each.
  *Third, and the most reusable:* **before recording something as
  "browser-only", check whether it is actually computable.**
  `src/test/axe.ts` and `src/index.contrast.test.ts` both recorded
  colour contrast as unreachable without a browser, and on that basis
  the whole class of `bg-[var(--token)]/15 text-[var(--token)]` status
  badges went unchecked -- with a comment asserting "the tint is too
  faint to shift the result", which is false in the direction that hides
  a failure. `src/test/contrast.ts` already composited, so those
  pairings were arithmetic all along: computing them reproduces Chrome's
  ratios **to the second decimal** (`#15803d` on `#dcece2`: Chrome 4.09,
  computed 4.09). The 30 sub-AA declared-token pairings are now pinned
  in `frontend/src/index.contrast.baseline.json` as a **no-growth**
  baseline -- a new failure fails, and a since-fixed pairing fails too,
  so it cannot decay into an allowlist.
  Two more worth not re-deriving. **`UI-004`: the documented per-node
  streaming progress had never reached a browser.** `api/streaming.py`
  binds the progress sink to a `ContextVar` on its own thread, but
  `api.main.ask` hands the graph to a second `ThreadPoolExecutor`, and
  `submit` gives its worker a *fresh, empty* context -- so
  `emit_progress` read `None` every time. The existing test could not
  catch it because it replaced `api.main.ask` wholesale, which removes
  the thread boundary that *was* the bug. **When a control depends on a
  `ContextVar`, a test that mocks away the thread hop tests nothing.**
  And the fix exposed `UI-010`: `emit_progress` fires *after* a node
  returns, so the badge names the node that **finished**, not the one
  running -- measured as 97 seconds reading "plan query" (0.05 ms) while
  `generate_sql` (96,807 ms) ran. Two docstrings claimed otherwise and
  are corrected; the protocol change is flagged.
  Phase 9 also found `UI-012`, a real masking miss:
  `governance/classification.py`'s compensation pattern carried
  `annual[-_]?income` and not `yearly[-_]?income`, so
  `DimCustomer.YearlyIncome` came back as `90000.0` to a caller without
  `VIEW_RESTRICTED_COLUMNS` in the same row as four masked columns.
  Fixed narrowly, with a companion test asserting `net_income` and
  `operating_income` are **not** masked -- so a later widening to bare
  `income` fails loudly instead of quietly over-blocking §5.8's
  ordinary analytics.

  Phase 7 (2026-10-29) fixed five findings and flagged two, and
  **corrected a measurement every context budget in this repo is sized
  against.** `config/settings.py` said the usable window is
  `num_ctx / OLLAMA_NUM_PARALLEL` and to budget against half. Measured
  on `llama3.1:8b` at `num_ctx=16384`, `/api/ps` confirming the served
  length: a ~22,065-token prompt was processed **whole** at 15,442 real
  tokens, and a ~50,065-token one came back at **8,194 with the system
  prompt gone** (the probe put a codeword in the system prompt; the
  model answered "There is no codeword"). So **the shape is a cliff, not
  a taper** — a prompt is processed whole up to roughly the full
  `num_ctx` and past that is cut to `num_ctx/2`, losing the *start*.
  "Exactly half" is the post-truncation floor, not the admission limit.
  `context_budget_max_chars` (32,000) and `web_search_context_max_chars`
  (18,000) are therefore sized against half the real window and leave
  ~50% of context unused — conservative in the safe direction, and
  **deliberately not changed**, because raising them changes which
  blocks reach the model and moves `docs/EVALUATION_CURRENT.md`'s
  numbers. Also measured, and the reason the detector below uses a loose
  ratio rather than trusting `CHARS_PER_TOKEN = 4`: real text here runs
  **3.8–5.7 characters per token**, dense DDL to prose.
  **The generalisable lesson is the one `CTX-006` fixes.** This codebase
  already understood this failure precisely — `agent/context_budget.py`
  and `rag/llm.py` both document it in their docstrings — and had no way
  to tell whether either defence was working: `prompt_eval_count` was
  logged on every call and never compared to what was *sent*. So every
  Phase 7 finding had to be measured by hand, and any could have been
  live for months silently. `agent.llm_client.report_prompt_truncation`
  now warns on every one of the eleven Ollama call sites, each with its
  own `label`. **Only `agent/llm_client.py`'s seven `client.chat` calls
  and `rag.llm.call_ollama` may reach Ollama** (the standing rule
  `LOP-004` established), which is what makes that coverage complete.
  Three fixes were the same shape as `LOP-003` — a correct control
  covering only some of the places it should, with nothing measuring the
  gap. `CTX-002`: the context budget covered `generate_sql` but not
  `plan_query` or `build_analytical_plan`, which embed the *same*
  `schema_context`; both now assemble through one `_assemble_budgeted`
  helper, so a future schema-bearing prompt is budgeted by construction.
  `CTX-001`: the prompt registry's drift suite pinned "exactly the live
  prompt set", but that set was a hand-maintained tuple in
  `governance_center/inventory.py` and nothing compared it to the
  repository — **eleven live prompts were outside it**, including all
  three read from `prompts/*.md` at runtime (retunable with no code
  change at all) and two assembled *inside a function*, where no
  module-level inventory can see them. The inventory now holds 25, and
  `tests/security/test_prompt_registry_covers_every_live_prompt.py`
  scans the source tree with `ast` to close the other direction.
  **Do not build a system prompt inside a function** — a test now fails
  if you do. `CTX-005`: the attachment OCR fallback was appended *after*
  `max_attachment_text_chars` was applied, so the ceiling was advisory
  on the one path whose content is a user-uploaded file.
  Flagged for an owner: `CTX-003` (**neither routing prompt states any
  safety invariant** — `prompts/router.md` and the fallback classifier
  both receive the question and the transcript and carry no
  untrusted-data framing, which falsifies `governance_center/safety.py`'s
  own docstring claim that every such prompt carries it; bounded by
  `router_node`'s RBAC filter, and not fixed because editing a live
  routing prompt changes routing behaviour that nothing measures) and
  `CTX-007` (**schema retrieval is punctuation-sensitive** — "top
  selling products" retrieves 5 tables, "top selling products."
  retrieves 8 and "...?" 6. Case and whitespace do not matter because
  `_extract_keywords` already normalises the lexical path; it is the
  *embedding* query that is raw, and `_select_by_relevance` sizes its
  result from the gap between consecutive scores, so a small vector
  shift changes the *count*. **Do not "just add a `.strip()`"**: the
  trailing-period variant retrieves `FactInternetSales`, arguably the
  more correct table, and the entry above records that this exact
  question is answered badly today — so normalising toward the base
  behaviour may make retrieval worse. Decide with
  `scripts/run_benchmark`, not from the code. Pinned by three
  `xfail(strict=True)` tests).
  One deliverable is explicitly **UNVERIFIED, not passed**: retrieval
  recall/precision has no labelled fixture set, and
  `docs/retrieval-evaluation.md` says so in its own opening.

  Phase 8 (2026-10-29) fixed eight findings and flagged three, and
  **two of its lessons are standing traps worth not rediscovering.**
  *First:* **`assert` is not a control.** `python -O` and
  `PYTHONOPTIMIZE=1` delete every `assert` from the bytecode, and three
  boundaries were relying on one — the four JWT key checks in
  `identity/security.py` (where removal turns a configuration failure
  into an opaque **HTTP 500 on every login**, because the next line
  calls `.get_secret_value()` on `None`),
  `mcp_client/trust.py::assert_no_remote_tool_is_registered` (the trust
  boundary keeping a remote MCP server's tools out of the local
  registry) and `mcp_client/protocol.py`'s JSON-RPC newline-framing
  check. All three raise now. **Narrowing-only asserts are deliberately
  kept**, with a `# nosec B101` naming the real guarantee: the
  distinction is whether removing the statement removes a control, not
  whether the statement is an `assert`. Tested in a subprocess, since
  `-O` is interpreter start-up state, with a test that proves asserts
  really are stripped first so the rest cannot pass vacuously.
  *Second, and this one has already cost one broken application:*
  **`identity/models.py`'s module docstring is right and `mypy` is
  wrong there, on purpose.** Do **not** close the four SQLAlchemy
  `SQLCoreOperations[...]`-vs-`None` errors in
  `identity/repositories/` by widening a column to `Mapped[X | None]`
  or `Mapped[Optional[X]]`. SQLAlchemy's declarative resolution crashes
  on a union annotation under this project's Python 3.14, and the
  docstring states that mypy seeing those attributes as non-Optional is
  the accepted cost of the workaround. A session did it anyway and
  `import api.main` raised `TypeError: descriptor '__getitem__'
  requires a 'typing.Union' object but received a 'tuple'` — the whole
  application stopped importing. They are closed with line-scoped
  `# type: ignore[assignment]` citing that docstring (`BP-015`).
  Also measured and not to be re-derived. **Coverage had never been
  measured in this project** — no coverage tooling was declared
  anywhere and CI has no coverage step. `pytest-cov==7.0.0` is now in
  `requirements-dev.txt`, and over the whole mocked suite:
  `governance/` 98.7%, `identity/` 97.0%, `security/` 96.0%, `api/`
  94.5%, `guardrails/` 93.3% (**95.4%** across the five), plus
  `agent/sql_validator.py` **94%** and `agent/input_guard.py` 98%. Every
  security-critical package clears 90%; **13 individual files do not**,
  worst `identity/db.py` 69.6%, `api/jobs.py` 69.9%,
  `identity/email.py` 71.4%, `api/chat_persistence.py` 76.1%. **No
  coverage gate was added to CI on purpose** — picking a threshold is a
  product decision, not an auditor's ratchet.
  **Six packages were imported directly by production code and never
  declared**, each present only through another package's dependency
  tree: `requests` (which is what `security/outbound_url.py`, the SSRF
  validator, makes its requests with), `httpx`, `av`,
  `huggingface_hub`, `numpy` and `starlette` (imported by
  `security/client_ip.py`, on the rate-limiting path). Same shape as the
  `pyyaml` line already in `requirements.txt`.
  `tests/test_declared_dependencies.py` derives this from the source
  tree with `ast`, which is how it found four of the six that
  inspection had missed — **a new direct import of a third-party
  package must be declared, or that test fails.**
  Flagged for an owner: **`BP-012` (S2) — `pymupdf` is AGPL-3.0**
  (`License: Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial
  License`), imported by `attachments/processors/pdf_processor.py` and
  `rag/ingestion.py`, and `enable_chat_attachments` defaults to
  **`True`**, so it is not behind an off-by-default flag;
  `piper-tts` is GPL-3.0-or-later on the voice path. `LICENSE` here is
  MIT and both ship in the single Docker image. AGPL's network clause
  is the problem: serving software that uses AGPL code obliges you to
  offer the whole work's source under AGPL, and this is a network-served
  application. `pypdf` (BSD-3-Clause) is already a declared dependency
  used elsewhere in `attachments/`, so a replacement path plausibly
  exists. A legal decision, not an engineering one. Also flagged:
  `BP-010` (`ai_generator_client.py` — 593 lines, zero importers, zero
  tests, and importing it would bypass `security/outbound_url.py`,
  bypass `Settings` for credentials, and call `logging.basicConfig()` at
  import time, hijacking the root logger; pinned unwired by a guard
  test) and `BP-013` (`LICENSE` still reads `Copyright (c) 2026 <Your
  Name>`).
  Verified clean rather than assumed, so a later phase need not recheck:
  zero bare `except:` in production and no swallowed errors across 135
  broad handlers (all log with `exc_info=True` or re-raise); **zero**
  blocking calls and zero synchronous `run_agent`/`session.*` calls
  lexically inside an `async def`; 0.85% of 6,713 tests carry no
  assertion and all of those test code that raises on violation; and
  repo hygiene clean, with the only absolute paths in tracked code being
  two `/home/app/.cache` container paths.
