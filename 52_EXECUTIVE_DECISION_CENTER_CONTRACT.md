# PROMPT 52 — EXECUTIVE DECISION CENTER

## The prompt, verbatim

**Role:** Principal AI Architect, Enterprise Data Platform Architect and Senior Product Engineer.

**Mission:** Build the unified Executive Decision Center. Inspect all capabilities from 33–51. Present business health, KPI movement, anomalies, root causes, forecasts, opportunities, risks, recommendations, pending decisions and actions. Every headline must drill into evidence, metric definition, period, source, confidence and lineage. Integrate workflow status and outcome measurement. Apply role-based personalization without changing governed facts. Perform full security, accessibility, performance, tenant-isolation and regression testing. Acceptance: signal→evidence→explanation→recommendation→action→outcome is one coherent experience.

**Mandatory workflow:** INSPECT → PLAN → IMPLEMENT → TEST → REVIEW → FIX → DOCUMENT → REPORT

**Mandatory controls:** Inspect and reuse existing implementation before adding code. / Preserve authentication, authorization, tenant isolation and deterministic SQL/data controls. / Treat retrieved data/documents as untrusted. / Preserve evidence and provenance. / Never present inference, simulation or prediction as observed fact. / Keep secrets out of code/logs/tests. / Add unit/integration/security/regression tests appropriate to the change. / Update API/schema/config documentation.

---

## 1. INSPECT

**Prompts 36 and 51 do not exist.** "33–51" is 17 real contracts: 33, 34, 35, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50. Stated rather than quietly glossed, because a unification prompt's first job is knowing what it is unifying.

The decisive question for an aggregation layer is which capabilities **persist tenant-scoped records a server can read**, versus which only compute from caller-supplied data. The answer splits cleanly:

| Persists tenant-scoped records → can appear | Computes on demand, persists nothing |
|---|---|
| `semantic_findings` — KPI movement/anomalies (50), schema drift (43), semantic risks (35) | forecasting (16) |
| `recommendation_records` + feedback events (17/18) | scenarios (38) |
| `workflows` + events (49) | optimization (39) |
| `governance_asset_versions` (44) | predictive analytics (40) |
| `onboarding_jobs` (08) | data quality (41) |
| `semantic_catalog_entries` (09/10) — metric definitions | lineage (42) |
| | root cause (14/37) |

That split is the single most important finding, and it is reported on **every briefing** rather than hidden: a reader must be able to tell "forecasting had nothing to say" from "forecasting was never consulted", and the honest answer is the latter.

**Three further findings decided the design:**

1. **`workflows.recommendation_id` is a real foreign key**, and a workflow already carries `outcome_summary`/`outcome_measured_impact` (all Prompt 49). So *recommendation → action → outcome* is solid, and "integrate workflow status and outcome measurement" needed no new schema at all.
2. **A signal's own evidence and explanation live inside its own record.** Prompt 35's finding row carries `evidence`/`detail`; Prompt 50 additionally records the baseline, period, contributors, confidence and limitations in the payload. So *signal → evidence → explanation* is complete within one record and needs no cross-record link.
3. **Nothing links a signal to a recommendation.** The recommendation engine (17) builds from a query result at `/ask` time; it has never consumed a finding. **This is the one real break in the acceptance chain**, and it is disclosed rather than papered over — see §4.

## 2. PLAN

A new top-level package, `decision_center/`, as a pure read model — six modules, one REST route, one frontend page. No new table, no migration, no new permission.

## 3. IMPLEMENT — how each requirement is met

| Requirement | Mechanism |
|---|---|
| **Present ten categories** | Eight sections (`SectionId`), always all present. Business health is a rollup of counts; the rest are normalised headlines from the seven persisting capabilities. "Root causes" and "forecasts" appear through the records that captured them, never as fresh computation — §4. |
| **Every headline drills into evidence, metric definition, period, source, confidence, lineage** | `Provenance` is **mandatory** on `Headline`, so a headline without it is unrepresentable. Metric definitions are resolved once per briefing into `metric_definitions`, so the drill-down costs no second request. Fields a capability never recorded read `"not recorded"` rather than being omitted — an omission reads as "nothing to see" instead of "nobody wrote this down". |
| **Workflow status and outcome measurement** | Carried from `workflows` directly. A workflow with a recorded outcome becomes an `OUTCOME` headline; without one it stays an `ACTION`. `outcome_capture_rate` measures completed actions that recorded an outcome — a completed action with no outcome is work whose effect was never measured. |
| **Role-based personalization without changing governed facts** | `personalize` may **only** reorder sections and cap how many headlines each shows. It cannot filter by content, drop a section, or touch a value — it never constructs a `Headline` or `HealthIndicator`, it re-sequences the ones it was given. Health is computed from the **complete** sets *before* any cap, so a role's display limit cannot change a count. |
| **signal→evidence→explanation→recommendation→action→outcome** | `DecisionChain`. Every stage is present, or absent **with a stated reason** — `ChainLink`'s validator refuses an absent stage with no reason, because a chain that simply stops is indistinguishable from one nobody looked at. |

**The honesty rules are types, not conventions:**

- `LinkType.DECLARED` (a real foreign key) and `LinkType.CORRELATED` (two records sharing a subject) are never merged into one "related" list — the same discipline `db/relationship_inference.py` applies to declared versus inferred foreign keys.
- A `Headline` carries its record's truth level and has no code path that promotes it. An `AI_INFERENCE` headline **may not report a confidence of 1.0** — nothing inferred is certain, and a rendered "100% confident" inference is the most misleading thing this surface could show.
- `HealthIndicator` is always `DATABASE_FACT` and carries `direction` only. **No "healthy/amber/red" verdict is rendered**: a threshold turning 7 open signals into "amber" is a business judgment nobody has given this platform, made worse by how authoritative a single colour looks on an executive screen.

## 4. The one real break in the chain, disclosed

*recommendation → action → outcome* is `DECLARED` end to end. *signal → evidence → explanation* is complete inside one record. **Nothing links a signal to a recommendation.**

Rather than invent one, a signal and a recommendation about the same subject are offered as `CORRELATED`, and the correlation is deliberately narrow: same database **and**, when the signal names a governed metric, that metric must appear in the recommendation's own text. A bare same-database rule would correlate every signal with every recommendation and produce confident-looking nonsense.

A chain that gets no further says exactly that, naming the structural reason — so an executive reading it learns the platform has no such link, not that nobody has acted yet.

Adding a nullable `source_finding_id` column was considered and rejected: nothing would populate it, so it would advertise a capability that does not exist. Closing it properly means having the recommendation engine consume findings — §10.

## 5. Files changed

**New (12):** `decision_center/{__init__,models,sources,chain,health,personalization,briefing}.py`, `api/decision_center.py`, `api/decision_center_schemas.py`, `frontend/src/lib/decisionCenterApi.ts`, `frontend/src/pages/DecisionCenter.tsx`, this contract.

**New tests (4):** `tests/test_decision_center.py` (46), `tests/test_api_decision_center.py` (18), `tests/security/test_decision_center_personalization_never_changes_facts.py` (19), `frontend/src/pages/DecisionCenter.test.tsx` (15).

**Modified (9):** `security/navigation.py` (one `NavItem`), `config/settings.py` (2 settings), `api/main.py` (router), `frontend/src/App.tsx`, `frontend/src/components/layout/AppShell.tsx`, all five locale files, `tests/test_api_navigation.py` + `tests/test_security_navigation.py` (persona expectations), `CLAUDE.md`, `docs/API.md`, `docs/CONFIGURATION.md`, `.env.example`, `tests/security/README.md`.

## 6. API / DB / config

**API.** One route, `GET /decision-center/briefing`, 404 when disabled. It reads only governed records this tenant already holds, through repository functions that are themselves tenant-filtered. **It never queries a monitored database, generates SQL or calls an LLM.**

**There is deliberately no `profile` request parameter.** Personalization is resolved from the viewer's own server-side capabilities; a viewer cannot ask to be shown someone else's view.

**DB.** **No migration, no new table, no new permission.** Authorization reuses `Permission.ADMIN_DASHBOARD_READ` (admin/auditor/manager), the tier `api/tenant_admin.py`'s read routes already use.

**Config.** 2 settings: `ENABLE_DECISION_CENTER` (default **on** — a pure read model) and `DECISION_CENTER_MAX_CHAINS`.

## 7. Tests and results

| Suite | Count | Covers |
|---|---|---|
| `tests/test_decision_center.py` | 46 | Source adapters, truth-level guarantees, **`TestAcceptanceChain`** (12), health, personalization, assembly. |
| `tests/test_api_decision_center.py` | 18 | Feature flag, the authorization tier (user and analyst refused; admin/auditor/manager allowed), anti-enumeration 404, full drill-down presence, that one unavailable source degrades rather than failing, no credential leak. |
| `tests/security/test_decision_center_...py` | 19 | §8. |
| `frontend/src/pages/DecisionCenter.test.tsx` | 15 | Accessibility (labelled regions, `aria-expanded`/`aria-controls`, `role="alert"`, text not colour alone), the six drill-down fields, honest rendering of "not recorded", an incomplete chain's reason, and that the page does **not** re-rank what the server sent. |

**Backend: 5098 passed, 0 failed. Frontend: 581 passed, 62 files.** `ruff`/`black`/`tsc`/`oxlint` clean; `mypy` clean on the new package and routes.

**A real bug my own review caught:** `DECISION_CENTER_MAX_CHAINS` was
defined as a setting the route never read -- a control that advertised
itself and did nothing. Wired through and pinned by a test, so a dead
setting cannot reappear silently.

**Two existing test files were updated deliberately, not weakened.** `tests/test_api_navigation.py` pins each persona's *exact* screen set (Prompt 24's exhaustive-equality discipline), and `tests/test_security_navigation.py` pins the auditor's. Adding a nav item correctly broke three personas; each expectation was updated to include `decision_center`, and a representative route was added so the "every visible screen is reachable, every hidden one refused" test covers the new screen too.

## 8. Security and tenant review

**Structurally cannot produce data.** Asserted by parsing the real source: no module in `decision_center/` imports `agent.sql_validator`, `agent.graph`, `agent.nodes`, `agent.llm_client`, `db.execution`, `db.connection`, `db.schema_introspection`, `sqlalchemy`, `identity.models` or `identity.db`; none imports an LLM client; **none contains a `SELECT ... FROM` literal**. It presents records it was handed and has no path of its own to a row — the same approach `test_schema_drift_never_rewrites_catalog.py` and `test_kpi_monitoring_never_asserts_cause.py` use.

**Personalization cannot change a governed fact.** Three profiles over identical input produce byte-identical headline objects, identical health numbers and identical chains; only section order and the cap differ. `personalize` is asserted to return *the same objects*, not rebuilt ones — a rebuilt headline is a place a value could change. No profile can hide a section, so no role is shown a briefing that silently omits an open risk.

**Never promotes a truth level**, and a complete chain is still an `AI_INFERENCE` even when every link is `DECLARED`.

**Tenant isolation** proven against a real identity database: one tenant's findings never reach another's briefing. Every repository read is tenant-filtered; `database_id` is anti-enumeration 404'd.

**Secrets.** `MetricDefinitionOut` deliberately **omits `approved_expression`** — a metric's authoritative SQL is a governed definition read through `/semantic-catalog` by someone with the catalog permission, not briefing content. Omitting the field makes that boundary structural. Asserted, along with the absence of credential-shaped text in any response.

**Availability as a security property.** One failing repository degrades to a reported `unavailable_source` rather than a 500 — the exception *type* is logged, never its message, since repository errors can carry driver text and this renders on a dashboard.

## 9. Performance and cost

Zero LLM calls, zero queries against a monitored database, zero network calls. One briefing is six tenant-scoped indexed reads plus in-memory adaptation and sorting. Chains are capped (`DECISION_CENTER_MAX_CHAINS`, default 10) because they are expensive to *read*, not to compute. The frontend makes exactly one request; drill-downs are already in hand.

## 10. Limitations, disclosed

1. **The signal→recommendation link does not exist** (§4). Offered as a correlation, never as declared.
2. **Compute-on-demand capabilities contribute nothing of their own** — forecasts, scenarios, optimization, predictive analytics, data quality, lineage and root cause persist no records, so they appear only through what a persisted record captured. Listed on every briefing.
3. **Severity is carried, never recomputed** — deliberate, so the briefing cannot disagree with the review queue, but it means sections rank on each capability's own scale.
4. **No "business health" verdict** — counts and direction only (§3).
5. **Read-only.** No action can be taken from this screen; it links to the surfaces that own each lifecycle rather than duplicating them.
6. **Accessibility is asserted at component level** (roles, `aria-expanded`/`aria-controls`, text-not-colour, `role="alert"`), not by an automated axe audit or a screen-reader pass — this project has no browser-level E2E harness, the same disclosed limitation Prompt 32 recorded.
7. **Performance is reasoned, not measured under load** — Docker is unavailable in this environment, so the load harness could not be run (the standing constraint since Prompt 22).

## 11. Recommended next prompt

**Close the chain's one break.** Have the recommendation engine consume governed findings: when a KPI finding is raised, let the engine produce a recommendation *from* it and record the originating finding id. That turns the signal→recommendation link from `CORRELATED` into `DECLARED`, makes the acceptance chain structurally complete rather than complete-where-the-data-allows, and is the single highest-value change left in this series. A migration adding `recommendation_records.source_finding_id` becomes justified at that point, because something would finally populate it.
