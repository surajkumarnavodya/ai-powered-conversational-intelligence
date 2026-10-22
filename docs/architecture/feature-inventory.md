# Feature inventory — status, evidence, priority

**Scope.** This document is the *status* view of the platform's
capabilities: for each capability, what state it is actually in, the file
evidence for that claim, and what should happen next. It deliberately does
**not** restate behaviour, invariants or limitations — those have owners
already, and [`CLAUDE.md`](../../CLAUDE.md) §4.8 ("one owner per fact")
forbids a second one. Every row links to its owning document.

**Why this document exists.** [`CLAUDE.md`](../../CLAUDE.md) §1 warns that
implementation status here is not uniform and that conflating the four states
is "the most common mistake a session makes". That warning existed; the
per-capability inventory backing it did not. This is that inventory.

**Date of assessment:** 2026-10-14.
**Commit assessed:** `b4d74d2` (branch `feat/inference-engineering`).
**Read with:** [`context-map.md`](context-map.md) for topic ownership.
**Rows added after the original assessment carry their own date** in the
Status column, so the 2026-10-14 baseline above stays readable as what it
was. §8's test/lint baseline is re-measured in place and states its own
date.

---

## 1. How to read the status column

The four states are [`CLAUDE.md`](../../CLAUDE.md) §1's, plus three
audit-only states for things that document differently than they behave.

| Status | Meaning |
|---|---|
| **Live** | Wired into a request path and running under current defaults |
| **Flag-gated** | Implemented and wired, runs only when an operator enables it |
| **Built, not wired** | Implemented and tested, no live caller |
| **Absent** | No implementation. Either a disclosed gap or genuinely new ground |
| **Partial** | A real caller exists but the capability is narrower than its name suggests |
| **Blocked** | Cannot be verified in this environment (no credentials/infrastructure) |

**Nothing is marked "verified" on inspection alone.** A capability is
evidenced by a test file or a reproducible command, named in the Evidence
column. Where the only evidence is the implementation itself, the status
says so.

---

## 2. Core SQL pipeline

| Capability | Status | Evidence | Owner doc | Action | Pri |
|---|---|---|---|---|---|
| 18-node LangGraph SQL pipeline | Live | `agent/graph.py` (18 `add_node` calls, verified by count); `tests/test_sql_agent_integration.py::test_existing_nodes_are_all_still_present` | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | keep | — |
| AST SELECT-only validation | Live | `agent/sql_validator.py`; `tests/security/` | [`../../SECURITY.md`](../../SECURITY.md) | keep | — |
| Live schema introspection → ChromaDB | Live | `db/`, `embeddings/`; `scripts/build_embeddings.py` | [`overview.md`](overview.md) | keep | — |
| Business-context retrieval (8 chunk kinds) | Flag-gated | `retrieval/`; `ENABLE_BUSINESS_CONTEXT_RETRIEVAL=true` in `.env.example:276` | [`overview.md`](overview.md) | keep | — |
| Multi-database auto-routing | **Partial** (2026-10-17) | `embeddings/retriever.py::select_database`. Ranks connections by **schema-text similarity only** — it has no notion of whether a table holds rows, and there is no user or operator override (`AskRequest` has no `database` field; `SourcePicker` selects source *types*). Reproduced live routing to an empty database over a populated one — F-012 / [`../RISK_REGISTER.md`](../RISK_REGISTER.md) R-020. **Cannot occur with one configured database** (short-circuits) | [`overview.md`](overview.md) | decide: override, row-count awareness, or document the one-database constraint | **P1** |
| Result governance gate | Live | `governance/`; gate runs in `agent.nodes.execute_sql_node` before `result_rows` is set | [`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md) | keep | — |
| SQL structural description (joins, filter columns, aggregates, grain, ordering) | **Live** (2026-10-17) | `agent/sql_validator.py::describe_sql` reads the AST `validate_sql` already parsed; `api/schemas.py::SqlSummaryOut`; `tests/test_sql_validator.py`; rendered by `SqlIntelligencePanel.tsx` | [`../API.md`](../API.md), [`features-frontend.md`](features-frontend.md) | keep | — |
| Answer translation into a UI locale | **Built, not wired** (2026-10-17) | `agent/translation.py` + `tests/test_translation.py`; **no route, node or setting calls it** — grep for importers returns none outside its own test | [`../../CHANGELOG.md`](../../CHANGELOG.md) (2026-10-17) | decide: wire or park | P2 |

## 3. Multi-source orchestration

| Capability | Status | Evidence | Owner doc | Action | Pri |
|---|---|---|---|---|---|
| Multi-source router (7 sources, parallel fan-out) | Flag-gated | `agent/orchestrator/graph.py`, `nodes.py`, `state.py`; `ENABLE_MULTI_SOURCE_ROUTER=false` (`.env.example:685`) | [`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) | keep | — |
| RBAC filtering of the router's own source choice | Live (when router on) | `agent/orchestrator/nodes.py:472` `SOURCE_PERMISSIONS` | [`../AUTHORIZATION.md`](../AUTHORIZATION.md) | keep | — |
| Permission-denial disclosure to caller | Live | `OrchestratorState.permission_denied_notice` → `AskResponse.permission_denied_notice` | [`../AUTHORIZATION.md`](../AUTHORIZATION.md) | keep | — |
| Document RAG | Flag-gated, **Blocked** | `rag/`; `ENABLE_DOCUMENT_RAG=false` (`.env.example:704`). Needs `RAG_STORE_CONNECTION_STRING` — a SQL Server 2025+/Azure SQL `VECTOR` store | [`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) | keep | — |
| Policy RAG + sensitivity gate | Flag-gated, **Blocked** | `rag/`; `ENABLE_POLICY_RAG=false` (`.env.example:710`) | [`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) | keep | — |
| Web search (Tavily) | Flag-gated | `search/web_search.py`; `ENABLE_WEB_SEARCH=false` (`.env.example:740`); tested by `tests/test_web_search.py` (14 mocked tests) | [`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) | keep | — |
| Media search (local CLIP) | Flag-gated | `media/`; `ENABLE_MEDIA_SEARCH=false` | [`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) | keep | — |
| Media generation | Flag-gated | `media_gen/`; `ENABLE_MEDIA_GENERATION=false` (`.env.example:785`) | [`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) | keep | — |
| Chat attachments (per-request source) | Flag-gated | `attachments/` (21 modules incl. `zip_safety.py`, `pdf_safety.py`, `validation.py`) | [`../FILE_UPLOAD_SECURITY.md`](../FILE_UPLOAD_SECURITY.md) | keep | — |
| **Per-conversation source selection by the user** | **Live** (as of 2026-10-15) | `api/schemas.py::AskRequest.sources`; `agent/orchestrator/nodes.py::_apply_source_selection`; `frontend/src/components/chat/SourcePicker.tsx`; `tests/test_source_selection.py` | [`source-connector-contract.md`](source-connector-contract.md) §8 | keep | — |
| Per-source query decomposition | Absent (disclosed) | [`CLAUDE.md`](../../CLAUDE.md) §13 | [`CLAUDE.md`](../../CLAUDE.md) §13 | defer | P2 |

## 4. Source connectivity — inbound

**The headline finding of this audit.** A keyword sweep over `agent api
config db rag search attachments media bi_integration workflow
frontend/src` returned **zero** files for `sharepoint`, `onedrive`,
`dataverse`, `microsoft.graph`, `graph_api`, `outlook`, `msal`, `teams`,
and `openapi`.

| Source | Status | Evidence | Action | Pri |
|---|---|---|---|---|
| SQL databases (PostgreSQL, MySQL, SQL Server, Oracle) | Live | `db/`, `DB_CONNECTIONS` | keep | — |
| File upload (PDF/DOCX/XLSX/CSV/TXT/JSON/PPTX) | Flag-gated | `attachments/processors/`, `attachments/validation.py` | keep | — |
| Web search | Flag-gated | `search/web_search.py`; `tests/test_web_search.py` | keep | — |
| Text/paste as a declared source type | **Partial** | Pasted text reaches the model as question text; there is no distinct source type with its own size cap, retention choice or attribution | implement | P2 |
| SharePoint | **Absent** | 0 files | implement (**Blocked** on tenant) | P1 |
| OneDrive | **Absent** | 0 files | implement (**Blocked** on tenant) | P1 |
| Dataverse | **Absent** | 0 files | implement (**Blocked** on tenant) | P2 |
| Outlook / email | **Absent** | 0 files | implement (**Blocked** on tenant) | P2 |
| Microsoft Teams | **Absent** | 0 files | implement (**Blocked** on tenant) | P2 |
| REST / OpenAPI | **Flag-gated** (2026-10-15), not routable | `rest/`, `security/outbound_url.py`; `ENABLE_REST_CONNECTOR=false`; `tests/test_outbound_url.py` (61), `tests/test_rest_connector.py` (29) | wire into routing when a deployment has endpoints | P2 |
| MCP (real client) | **Flag-gated** (2026-10-15), stdio only | `mcp_client/`; `ENABLE_MCP_CLIENT=false`; `tests/test_mcp_client.py` (62 tests). Separate from `agent/tools/`, which remains the MCP-*shaped internal* registry for the opposite direction | add an HTTP transport; verify against a live server | P2 |
| **Unified inbound connector registry / lifecycle contract** | **Absent** | The 16 files matching `connector` are all **outbound BI** (`bi_integration/`, `api/bi_integration.py`) or unrelated (`agent/followup.py`, `workflow/models.py`). There is no inbound connector type registry | implement | **P0** |

### 4.1 The reuse finding that should shape the connector work

`onboarding/` is already a complete, RBAC-gated, secret-by-reference
connection lifecycle — for databases only. It is the thing a generic
connector contract should **generalize**, not be invented beside.

| Lifecycle stage the pack asks for | Already exists as | Evidence |
|---|---|---|
| create / list / get a connection | `POST`/`GET /onboarding/jobs` | `api/onboarding.py:197,244,260` |
| validate + discover resources | `POST /jobs/{id}/discover` | `api/onboarding.py:302` |
| human review of discovered resources | `GET /jobs/{id}/review-items`, `POST .../decide` | `api/onboarding.py:332,346` |
| publish / enable | `POST /jobs/{id}/publish` | `api/onboarding.py:388` |
| register as a live, routable source | `POST /jobs/{id}/register-for-chat` → `onboarding/env_registration.py` | `api/onboarding.py:441` |
| cancel / retry | `POST /jobs/{id}/cancel`, `/retry` | `api/onboarding.py:522,537` |
| **secret by reference, never stored** | `identity.models.OnboardingJob` — stores `db_type`/`db_host`/`db_port`, **no password column**; the secret is supplied fresh per call | `identity/models.py:882-890` |
| RBAC + ABAC per action | `onboarding/policy.py::authorize_onboarding_action` | `onboarding/policy.py` |
| job state machine | `onboarding/jobs.py` | `onboarding/jobs.py` |

**Implication for Prompt 3 §A:** the connector contract is a refactor of an
existing, tested lifecycle into a source-type-parameterised one — not
greenfield. This materially lowers its risk and changes its ordering.

## 5. Platform foundation (Prompt 2's subject matter)

Prompt 2 proposes building these. They exist.

| Capability Prompt 2 asks for | Status | Evidence | Verdict |
|---|---|---|---|
| Schema-validated, versioned configuration | Live | `config/settings.py` — frozen `pydantic_settings.BaseSettings`, **232** `Field(...)` declarations | already built |
| Feature flags | Live | **54** distinct `enable_*` settings | already built |
| Policy precedence (platform → tenant → user) | Live | `governance/`, `identity/rbac.py`, `api/tenant_admin.py` | already built |
| Typed request/response contracts | Live | `api/schemas.py` (Pydantic v2, `extra="forbid"`) | already built |
| Model gateway / provider adapters | Live (gateway), Flag-gated (routing) | `agent/llm_client.py`; `model_routing/` (9 modules); `ENABLE_MODEL_ROUTING=false` (`.env.example:36`) | already built |
| Tool registry with timeouts/retries/approval | Live | `agent/tools/types.py` (`Tool.timeout_seconds`, `RetryPolicy`, `ToolPermissionError`) | already built |
| Execution budgets / deadlines / cancellation | Live | commit `fc8adf4` "server-owned execution envelope"; `docs/HARNESS_RUNTIME_CONTRACT.md` | already built |
| Observability, correlation IDs, audit | Live | `observability/`; [`../OBSERVABILITY.md`](../OBSERVABILITY.md) | already built |
| Evaluation platform | Live | `eval/`, `eval_platform/` | already built |
| **Streaming** | **Live** (2026-10-15), per-node not per-token | `api/streaming.py` (`POST /ask/stream`), `agent/progress.py`; `tests/test_api_streaming.py`, `tests/test_progress.py`; frontend `lib/askStream.ts` | token-level streaming still absent | 

**Conclusion for Prompt 2: little to implement.** Its only substantial
unmet item is streaming, which is a disclosed gap, not an oversight.

## 6. Governance, identity, admin

| Capability | Status | Evidence | Owner doc |
|---|---|---|---|
| Local identity (Argon2id + JWT), Alembic migrations | Flag-gated | `identity/` (51 modules); `LOCAL_AUTH_ENABLED` | [`../AUTHENTICATION.md`](../AUTHENTICATION.md) |
| Tenant isolation, client-override rejection | Live | `security/tenancy.py::reject_client_tenant_override` | [`../MULTI_TENANCY.md`](../MULTI_TENANCY.md) |
| 18-domain privacy policy, 14 prohibited actions | Flag-gated (3 switches) | `governance/domains.py` | [`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md) |
| Admin dashboards (platform + tenant) | Live | `frontend/src/pages/PlatformAdmin.tsx`, `TenantAdmin.tsx`, `api/platform_admin.py`, `api/tenant_admin.py` | [`features-governance-and-admin.md`](features-governance-and-admin.md) |
| Knowledge-source management UI | Live | `frontend/src/pages/KnowledgeSources.tsx` (documents + policies tabs, upload/delete/download) | [`features-frontend.md`](features-frontend.md) |
| Citations / evidence UI | Live | `frontend/src/components/chat/SourcesUsedPanel.tsx`, `SourceLineagePanel.tsx`, `SourceAnswerCard.tsx` | [`features-frontend.md`](features-frontend.md) |
| Per-conversation source picker | Live | `frontend/src/components/chat/SourcePicker.tsx` (+ `SourcePicker.test.tsx`) | [`features-frontend.md`](features-frontend.md) |
| **Connector configuration UI** (add/edit a connection) | **Absent** | The picker above *selects* among existing sources; nothing yet *configures* one. `KnowledgeSources.tsx` and the two admin pages are the established pattern to follow | — |

**Note for Prompt 3 §C:** the pack says to add an admin UI "only if the
existing app has an established admin/settings pattern". It does —
`PlatformAdmin.tsx` / `TenantAdmin.tsx` / `KnowledgeSources.tsx`.

## 7. Cross-cutting risks found

Each is a finding of this audit, not a restatement of an existing doc.

| # | Risk | Severity | Evidence | Action |
|---|---|---|---|---|
| R1 | A connector framework built fresh alongside `onboarding/` would be a second owner of connection lifecycle — [`CLAUDE.md`](../../CLAUDE.md) §4.5/§4.8 | **High** (design risk) | §4.1 above | generalize `onboarding/`, do not duplicate it |
| R2 | M365 connectors cannot be end-to-end verified here. Writing them without a tenant risks fabricated scopes, which master instruction #4 forbids | **High** (process risk) | 0 Graph files; no tenant credentials in this environment | config + adapter seam only; mark live verification **blocked** |
| R3 | **[`CLAUDE.md`](../../CLAUDE.md) §13 carries a stale gap entry.** It states "`search/` has no dedicated mocked unit test file ... A real, worthwhile follow-up." That follow-up was **already done** | Medium (doc accuracy) | `tests/test_web_search.py` — 236 lines, 14 tests, `httpx.post` mocked throughout, covering request shape, response parsing, empty/missing results, HTTP error propagation, secret non-leakage and provider dispatch. Its own docstring says it closes that gap | **correct §13.** The gap is closed |
| R4 | ~~Adding user-selected sources to `AskRequest` touches the router's permission filter~~ **Closed 2026-10-15.** The selection is intersected, never unioned; `_apply_source_selection` returns a subset of what configuration already allows and the role check still runs downstream | **High** (security) — **mitigated** | `tests/test_source_selection.py::TestSelectionCannotGrant`; mutating the intersection to a union fails 19 tests | done — keep the mutation evidence in [`source-connector-contract.md`](source-connector-contract.md) §8.2 |
| R5 | ~~No streaming means no mid-run progress signal~~ **Closed 2026-10-15.** `POST /ask/stream` emits per-node progress; the UI shows the running stage in its pending badge | Medium (UX) — **mitigated** | `api/streaming.py`, `agent/progress.py`, `frontend/src/lib/askStream.ts` | done — token-level streaming remains out of scope |

**A note on R3, because it cuts both ways.** This audit initially recorded
the missing-test gap on [`CLAUDE.md`](../../CLAUDE.md) §13's authority and
on `search/` containing only two modules. Both were wrong: the test file
exists and is thorough. The check that caught it was `ls tests/ | grep
search`, which takes a second.

The same pass also wrongly flagged `env1/` as un-gitignored, on the
strength of `.gitignore` containing `env/` but not `env1/`. `git
check-ignore -v env1/` shows it is ignored by its own nested
`env1/.gitignore` containing `*`. Nothing was committed and there is no
finding.

[`CLAUDE.md`](../../CLAUDE.md) §13's own instruction — "measure before
claiming a finding is or is not yours" — applies to its own contents.
**Treat §13 as dated, not current.**

## 8. Test and lint baseline (actual commands)

**Re-measured again 2026-10-17 (evening) against commit `08f385e`** —
the commit that corrected the `scipy` pin. Every figure below held
exactly, which is the point: the numerical-library downgrade changed
nothing measurable.

```
$ .venv/Scripts/python.exe -m pytest -q
7274 passed, 11 deselected, 1431 warnings in 385.98s (0:06:25)

$ .venv/Scripts/python.exe -m ruff check .
Found 1 error.                      # same I001, agent/multiagent/agents.py

$ .venv/Scripts/python.exe -m black --check .
All done! 1635 files would be left unchanged.

$ .venv/Scripts/python.exe -m mypy .
Found 631 errors in 115 files (checked 820 source files)
                                    # of which exactly 7 outside tests/ -- CLAUDE.md 13's list, unchanged

$ cd frontend && npx vitest run
Test Files  89 passed (89)
     Tests  1161 passed (1161)

$ cd frontend && npx tsc --noEmit -p tsconfig.app.json
                                    # clean, exit 0

$ cd frontend && npm run lint
                                    # exit 0 -- oxlint warnings only
```

Two additions this pass, neither a drift in the figures above:

- **`bandit` reports 4 Low findings, not 3** — `agent/sql_validator.py:842`
  (`B112`) joined the set via `933cfff`. 0 High, 0 Medium.
- **`ruff`'s single `I001` makes a *blocking* CI gate fail.** The finding
  itself is old and recorded as accepted; the consequence was not
  recorded anywhere until now. Filed as F-011 in
  [`../certification/2026-10-17_release_gate_v2.md`](../certification/2026-10-17_release_gate_v2.md)
  §11.3. [`implementation-roadmap.md`](implementation-roadmap.md) Batch 0
  still owns the decision.

**Beyond the suites: the running application was driven end to end for
the first time** on 2026-10-17 — 20 probes covering auth, RBAC on 14
admin routes, the SQL validator against four write/catalog attempts, the
row cap, restricted-column masking on live compensation data, and chat
history persistence. Recorded in that same document, §11.5. It is
*manual technical* verification, **not** UAT, and it is what finally
puts a "verified" mark against the controls this document has only ever
been able to evidence with mocked tests.

---

**Previous re-measurement, 2026-10-17 against commit `a446755`**, Python 3.14.4
(`.venv`). The 2026-10-14 baseline at `b4d74d2` is kept below it for the
delta, since the point of this section is attributability.

```
$ .venv/Scripts/python.exe -m pytest --collect-only -q
7274/7285 tests collected (11 deselected) in 20.44s

$ .venv/Scripts/python.exe -m pytest -q -p no:randomly
7274 passed, 11 deselected, 1431 warnings in 580.41s (0:09:40)

$ .venv/Scripts/python.exe -m ruff check .
Found 1 error.                      # I001 import order, agent/multiagent/agents.py
[*] 1 fixable with the `--fix` option.

$ .venv/Scripts/python.exe -m black --check .
All done! 1635 files would be left unchanged.

$ .venv/Scripts/python.exe -m mypy .
Found 631 errors in 115 files (checked 820 source files)

$ cd frontend && npx vitest run
Test Files  89 passed (89)
     Tests  1161 passed (1161)
```

The 11 deselected are `tests/live/`, which need real infrastructure and are
excluded by default (`-m 'not live'`). **Previous baseline, 2026-10-14 at
`b4d74d2`:** 5910 collected, 5910 passed in 413.70s; the same single ruff
finding; black clean over 776 files.

Both lint results still match [`CLAUDE.md`](../../CLAUDE.md) §13's
documented baseline exactly (one ruff import-order finding in
`agent/multiagent/agents.py`; black clean). **Neither was introduced by
this audit** — the audit itself changed no Python. The `mypy` figure is
new to this section and is **not** clean: §13 carries the breakdown,
including the seven findings in production modules.

### 8.1 What the suite does and does not cover

**The full suite is green: 7274 passed, 0 failed, 11 deselected** (the
`tests/live/` set, which needs real infrastructure) as re-measured
2026-10-17. That is a genuinely clean baseline, and it means any failure
introduced by later work is unambiguously attributable.

It does **not** mean the platform is verified end to end. The suite is
fully mocked — no real database, no Ollama — per
[`CLAUDE.md`](../../CLAUDE.md) §7. The harnesses that need real
infrastructure were **not run** and cannot run in this environment:
`scripts/integration_test.py`, `scripts/run_benchmark.py`, and
`eval/security_benchmark/`.

`mypy` was **not run** in that pass, since the audit changed no Python.
It has been run since — [`CLAUDE.md`](../../CLAUDE.md) §13 carries the
current measured figures, including the seven findings that are in
production modules rather than tests, which that entry previously claimed
were clean.

**Accessibility is partly verified as of 2026-10-17**, and the bound is
worth stating here because "a11y tested" is exactly the kind of claim this
document exists to qualify: `jsx-a11y` static rules now run in `oxlint`,
and `axe-core` runs against the *rendered* tree in
`frontend/src/components/ui/accessibility.test.tsx` (shared primitives, a
dialog, a chat message — not every page). **Colour contrast is not covered
and cannot be under jsdom**, which has no layout or paint; it needs a real
browser. See [`features-frontend.md`](features-frontend.md).

---

## 9. Deliberate deviations from Prompt 1's requested outputs

Prompt 1 asked for five documents. Three were created
(`feature-inventory.md`, [`data-source-matrix.md`](data-source-matrix.md),
[`implementation-roadmap.md`](implementation-roadmap.md)). Three were
**not**, because each would have become a second owner of a fact already
owned, which [`CLAUDE.md`](../../CLAUDE.md) §4.8 and §11 forbid:

| Requested | Not created because | Existing owner |
|---|---|---|
| `current-state.md` | would restate package layout + runtime flow | [`repository-map.md`](repository-map.md), [`overview.md`](overview.md), [`../ARCHITECTURE.md`](../ARCHITECTURE.md) |
| `target-state.md` | a target architecture with the same 9-boundary mapping already exists | [`../../02_TARGET_ARCHITECTURE.md`](../../02_TARGET_ARCHITECTURE.md) |
| `decisions.md` | [`overview.md`](overview.md) is explicitly "authoritative for the decisions and invariants" per [`context-map.md`](context-map.md) | [`overview.md`](overview.md) |

If a consolidated current-state narrative is wanted anyway, the correct
move under §4.8 is to **extend** the owning document, not add a parallel
one.
