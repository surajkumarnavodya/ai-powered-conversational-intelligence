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
| **Live** | Wired into a request path and running | the 18-node SQL graph, RBAC, result governance |
| **Built, not wired** | Implemented and tested, no live caller | `analytics.root_cause` (needs a second dataset the pipeline never produces), `recommendation.RecommendationProvider` (Protocol only) |
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
| Orchestration | LangGraph — explicit state machines, not free-form agents. `agent/graph.py` (the 18-node SQL pipeline) and, when multi-source is on, `agent/orchestrator/graph.py` in front of it |
| Config / validation | Pydantic v2. `config/settings.py::Settings` is a frozen `pydantic_settings.BaseSettings`; secrets are `pydantic.SecretStr` |
| User's database | PostgreSQL, MySQL, SQL Server, or Oracle via SQLAlchemy. One or more named connections (`DB_CONNECTIONS`), auto-routed per question |
| SQL parsing / validation | sqlglot — AST allowlist in the dialect matching the target `DB_TYPE` |
| Schema retrieval | ChromaDB, one collection per configured database, embedded from live introspection |
| Business-context retrieval | Same Chroma client, a second collection (`retrieval/`) — 8 typed chunk kinds, deterministic ids |
| Identity / chat history | Optional (`LOCAL_AUTH_ENABLED`), dedicated PostgreSQL DB (`identity/`) — Argon2id + JWT, SQLAlchemy 2.0 ORM, Alembic. **The only ORM and the only real migrations in this repo** |
| Document / policy RAG | SQL Server 2025+/Azure SQL native `VECTOR` (`rag/store.py`), a dedicated connection. Off by default |
| Web search | Pluggable provider (`search/web_search.py`); Tavily implemented. Off by default |
| UI | React 19 + Vite + TypeScript + Tailwind + Zustand + @tanstack/react-query + i18next (en/es/fr/hi/mr). Charting is Plotly-figure-JSON in, Chart.js out (`agent/result_charting.py` builds it, `frontend/src/lib/chartAdapter.ts` renders it) |
| Backend tests | pytest, fully mocked — no real DB, no Ollama |
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
| `agent/tools/`, `agent/analyst/`, `agent/multiagent/` | MCP-shaped tool registry; the AI Data Analyst loop; the fixed-routing specialist supervisor |
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
   18 nodes with a conditional retry edge back to `generate_sql` (or to
   `retrieve_schema` for a missing reference), capped by
   `state["max_retries"]`. Never introduce an unbounded loop or a second
   retry budget. Adding or renaming a node means updating
   `tests/test_sql_agent_integration.py::test_existing_nodes_are_all_still_present`
   (an exhaustive equality check) **and**
   `frontend/src/components/sql/QueryLifecycle.tsx`'s stage map.
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
  aggregates, and unsafe query hints.
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
- Cross-tenant access to a resource returns the **same 404 a nonexistent
  resource gets** (anti-enumeration), never a distinguishable 403.
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
  — so an alias, a `UNION`, or `SELECT *` reaches the same protection.
- **Small-group suppression applies to an authorized caller too** — it
  protects the individual in the group, not the column's sensitivity. The
  group label and count stay visible and **no row is ever dropped** (a
  vanished row lets a reader difference a published total against the visible
  rows and recover the hidden value).
- A **stored** result is re-governed on every read against the *current*
  caller's roles — never trusted because it was governed when captured. Roles
  change.

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
- CI gates are **blocking**: `ruff`, `black --check`, `mypy`, `bandit`,
  `pytest`, `pip-audit` (with an individually justified ignore list), and
  `detect-secrets`.

### 5.8 Domain privacy and safety — consult the policy before changing behavior

**Authoritative document:**
[`docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md`](docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md).
**Read it before changing anything that affects what the platform may be
asked, may return, or may recommend in a business domain.**

`governance/domains.py` defines 18 domains plus a `GENERAL` fallback, and 14
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
  `scripts/integration_test.py`, `scripts/run_benchmark.py` and
  `eval/security_benchmark/` are manual and require real infrastructure; CI
  never runs them.
- `tests/conftest.py` strips every env var a `Settings` field could read, so a
  test never silently inherits the developer's real `.env`. It also clears the
  three process-lifetime caches between tests.
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

- **`search/` has no dedicated mocked unit test file.** `tests/test_orchestrator.py`
  mocks it at the boundary only; the Tavily request construction and response
  parsing were verified against a real instance during development, not by a
  regression suite. A real, worthwhile follow-up.
- **No per-source query decomposition.** Every routed subgraph receives the
  same full question, so a question that is really two unrelated asks can
  retrieve poorly on both sides.
- **`DB_<NAME>_SCHEMA` is one schema per connection**, not "all schemas". A
  database whose tables span many schemas exposes one at a time.
- **The eval harness covers the SQL path only** — it exercises
  `agent.graph.run_agent`, not multi-source or cross-source questions.
- **Follow-up resolution is per-request, not checkpointed.**
  `agent/followup.py`'s `classify_followup_node` sees only the
  `conversation_history` the caller resends, capped at
  `MAX_FOLLOWUP_EXCHANGES`; no LangGraph checkpointer exists (none ships for
  SQL Server at the pinned `langgraph==0.2.62` — it would need a custom
  `BaseCheckpointSaver`).
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
- **No streaming.** `POST /ask` is a single request; there is no SSE or
  WebSocket endpoint, so no mid-run progress signal exists.
- **Process-local state**: rate limiters, circuit breakers, the performance
  rollup, the result cache, the security-event ring buffer and the compiled
  graph/Ollama singletons are all per-worker. A multi-worker deployment has
  one of each per worker.
- **Environment-blocked verifications** (not oversights — no infrastructure
  exists here): DAST has never run against a live deployment, OIDC has never
  been verified against a live IdP, ClamAV's fail-closed path has never seen
  a real daemon, and `docs/EVALUATION_CURRENT.md`'s accuracy numbers predate
  many pipeline changes.
- **Pre-existing lint/type findings — do not assume you caused them.**
  `mypy .` reports errors concentrated in `tests/` and
  `eval/security_benchmark/` (`AgentState`-vs-`dict` looseness) — a bounded,
  separate hygiene pass. `black --check .` is clean and `ruff check .` has one
  import-order finding (`agent/multiagent/agents.py`) as of 2026-10-14.
  **Production modules are otherwise clean — keep them that way**, and
  measure before claiming a finding is or is not yours.
- **Two CI scanners are `continue-on-error`** by deliberate, dated decision:
  `gitleaks` and the Trivy container scan. Every other gate blocks (§5.7).
  See [`docs/security/CVE_TRIAGE.md`](docs/security/CVE_TRIAGE.md).
