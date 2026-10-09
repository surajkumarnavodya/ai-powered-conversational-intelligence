# PROMPT 50 — PROACTIVE AI ANALYST

## The prompt, verbatim

**Role:** Principal AI Architect, Enterprise Data Platform Architect and Senior Product Engineer.

**Mission:** Build proactive KPI monitoring. Inspect anomaly detection, semantic metrics, jobs, recommendations and notifications. Allow policies for approved metrics, thresholds, comparison periods and business calendars. Detect meaningful changes, investigate evidence, suppress duplicates and rank findings by impact. Include baseline, affected dimensions, confidence and limitations. Support quiet periods and user preferences. Test alert noise, duplicate suppression and insufficient evidence.

**Mandatory workflow:** INSPECT → PLAN → IMPLEMENT → TEST → REVIEW → FIX → DOCUMENT → REPORT

**Mandatory controls:** Inspect and reuse existing implementation before adding code. / Preserve authentication, authorization, tenant isolation and deterministic SQL/data controls. / Treat retrieved data/documents as untrusted. / Preserve evidence and provenance. / Never present inference, simulation or prediction as observed fact. / Keep secrets out of code/logs/tests. / Add unit/integration/security/regression tests appropriate to the change. / Update API/schema/config documentation.

---

## 1. INSPECT — the five areas the mission names

| Area | What was found | Consequence |
|---|---|---|
| **Anomaly detection** | `analytics/anomaly.py::detect_anomalies` (Prompt 14) — five independent methods (flat threshold, point-over-point percent, rolling z-score, whole-series IQR, opt-in seasonal), pure, taking the exact `(period, value)` series shape `analytics.engine.GrowthStat.points` produces. Already reports *which* method fired per point and why a method was skipped. | **Reused verbatim.** Not reimplemented, not wrapped — called with the series and read for the latest point. |
| **Semantic metrics** | `semantic/catalog.py` + `identity.models.SemanticCatalogEntry`: a real draft → reviewed → published approval workflow, with `approved_expression`/`dimensions`/`source_tables` on a published `METRIC` entry (Prompts 09/10). | **This is what "approved metrics" means.** A policy may only target a *published* metric entry in the caller's own tenant. Monitoring reuses that approval decision instead of taking its own view. |
| **Jobs** | No background worker, no scheduler, nothing. Only standalone CLI scripts driven by cron/Task Scheduler (`scripts/run_schema_drift_scan.py` is the established precedent, disclosed in `onboarding/`'s own docstring as a deliberate absence). | A CLI entry point (`scripts/run_kpi_monitor.py`) with an alerting exit code, exactly mirroring that precedent. No scheduler was invented. |
| **Recommendations** | `recommendation/engine.py` produces evidence-grounded recommendations from already-computed facts; `recommendation/governance.py` governs their lifecycle. **But its inputs are one query result**, not a monitored series over time. | Not reused as the finding store — a KPI alert is a review item, not a recommendation. The *evidence-first* principle was reused directly. |
| **Notifications** | **Nothing exists.** No email, no webhook, no chat, no notification table — confirmed by grep across `api/`, `identity/`, `security/`. | A typed `AlertBundle` describing what *would* be sent, and an explicit statement in the package docstring, the model docstring, the API schema and the rendered report that **no delivery channel exists**. Inventing one would have meant handling provider credentials. |

**Two further things found that decided the design:**

- **`identity.repositories.semantic_intelligence.upsert_findings`** (Prompt 35) already *is* duplicate suppression: `(tenant_id, database_id, finding_key)` identity plus a content hash, so an unchanged re-run records `unchanged`, a changed one versions the row into an append-only `history` and reopens it, and the review queue (`list_findings`) is already risk-ordered with a stable tie-break. Prompt 43 (schema drift) is already its second consumer. Monitoring became the third rather than a third place to look for open items.
- **`semantic.intelligence.risk.score`** already ranks findings by a transparent sum of named parts, and already carries `reasons` explaining the placement.

**A latent bug found while inspecting that storage** — reported, not fixed: `identity.models.SemanticFinding.kind` is `String(32)`, but Prompt 43 stores `schema_drift_column_nullability_changed` (39 chars) and `schema_drift_unique_constraint_changed` (38). SQLite ignores a `VARCHAR` length, so every test passes; PostgreSQL — the real identity database — would reject the write. This prompt keeps all four of its own kinds well inside 32 and asserts that at import (`kpi_monitoring.findings._assert_kinds_fit`), so it cannot reintroduce the same problem. See §9.

## 2. PLAN

A new top-level package `kpi_monitoring/`, a peer of `analytics/`, `data_quality/`, `lineage/`, `schema_drift/`, `optimization/`, `model_routing/`. Nine modules, each owning one step of the mission's own named flow, plus a REST surface, a CLI and the documentation.

## 3. IMPLEMENT — how each mission requirement is met

| Requirement | Mechanism |
|---|---|
| **Policies for approved metrics** | `kpi_monitoring/policy.py`. A policy naming anything but a *published* `METRIC` entry in the caller's tenant is refused, with **three distinguishable codes** — `metric_not_found`, `metric_not_published`, `metric_not_a_metric` — because they need different fixes. One bad policy never rejects the run; the rest proceed. |
| **Thresholds** | `MonitoringPolicy.min_change_percent` (the percentage gate) plus optional hard `lower_threshold`/`upper_threshold`. A threshold breach fires **regardless of move size** — an operator who set a floor meant it. A model validator refuses a lower ≥ upper pair, which could never fire. |
| **Comparison periods** | `ComparisonPeriod`: previous period, same period last year (`seasonal_lag`), trailing average (`trailing_window`). Every baseline carries a human-readable `baseline_label` ("mean of the 4 periods before 2026-06"), so a reader never guesses what the comparison was. Too little history is a *reported skip*, not silence. |
| **Business calendars** | `kpi_monitoring/calendar.py`, doing two separate things on purpose: excluding non-business periods from the **baseline** (so a holiday never poisons a trailing average) and refusing to **alert** on one. Doing only one of the two leaves a real hole either way. No date parsing — a period label is opaque, the same boundary `analytics.engine.detect_missing_periods` already draws. |
| **Detect meaningful changes** | `kpi_monitoring/detect.py` runs the policy's comparison rule *and* `detect_anomalies`, records which fired, and reports a point flagged by both **once**. Only the latest business period is ever reported — scanning history would bury what just happened and re-raise old news every run. |
| **Investigate evidence** | `kpi_monitoring/evidence.py` delegates to `analytics.root_cause.investigate_root_cause` verbatim and maps its result. |
| **Suppress duplicates** | Two independent halves. **Storage-level**: the stable `finding_key` (policy + metric + period) plus the content hash, so an unchanged re-run raises nothing and a restatement versions the *same* row. **Alert-level**: a finding whose key is already open and undecided is not re-paged. |
| **Rank by impact** | `semantic.intelligence.risk.score`, extended additively with four `kpi_*` base scores and an optional, already-capped `impact_bonus` term scaled from the move's own percentage. Kind alone is right for a catalog conflict and wrong for a KPI, where two drops of the same kind differ by orders of magnitude. |
| **Baseline, dimensions, confidence, limitations** | `ChangeEvidence` carries all four, and its **model validator makes the wrong combination unrepresentable**: contributors or confidence without `has_sufficient_evidence`, or insufficient evidence with no stated limitation, both raise. |
| **Quiet periods & user preferences** | `kpi_monitoring/suppression.py`. Quiet periods are run-scoped; preferences (`min_risk_tier`, `muted_metric_keys`, `max_alerts_per_run`) are per recipient, applied at alert time — so one user's mute never destroys another's evidence. |

**Suppression governs alerting, never evidence.** Every finding is persisted and appears in the review queue regardless; what suppression decides is whether a human is *told* now. Reasons accumulate rather than short-circuiting, because "why didn't I get paged" needs the whole answer.

**Three kinds of claim are kept apart** (master rule 10): an observed number is arithmetic over supplied rows; the judgment that it is *meaningful* is `AI_INFERENCE`; an attribution is a further inference that exists only with real supporting evidence. The finding **title never names an attribution at all** — a reviewer scanning a list would read a cause there as established long before opening the evidence.

## 4. Files changed

**New (14):** `kpi_monitoring/{__init__,models,calendar,policy,detect,evidence,findings,suppression,engine,reports}.py`, `api/kpi_monitoring.py`, `api/kpi_monitoring_schemas.py`, `scripts/run_kpi_monitor.py`, this contract.

**New tests (3):** `tests/test_kpi_monitoring.py` (68), `tests/test_api_kpi_monitoring.py` (20), `tests/security/test_kpi_monitoring_never_asserts_cause.py` (22).

**Modified (5):** `semantic/intelligence/risk.py` (additive: 4 kinds + an optional `impact_bonus` defaulting to 0), `config/settings.py` (5 settings), `api/main.py` (router registration), `.env.example`, `CLAUDE.md`, `docs/API.md`, `docs/CONFIGURATION.md`, `tests/security/README.md`.

## 5. API / DB / config

**API.** Two routes: `POST /kpi-monitoring/scan` and `GET /kpi-monitoring/findings`, both 404 when `ENABLE_KPI_MONITORING=false`. **Neither queries a monitored database and neither calls an LLM** — the series arrive in the request body, already fetched through the governed path.

Deciding a KPI finding (accept/dismiss/rollback) is deliberately **not** re-implemented: those are `/semantic-intelligence/findings/...`'s existing routes over the same rows. A second lifecycle would be a second thing to keep in step — the same "link, don't duplicate" call `api/schema_drift.py` already made.

**DB.** **No migration, no new table, no new permission.** Findings go into the existing `semantic_findings` table; authorization reuses `CatalogAction.REVIEW`.

**Config.** 5 new settings, all defaulted: `ENABLE_KPI_MONITORING` (default **on** — a pure, deterministic, zero-cost-until-triggered computation, matching `enable_data_quality_engine`/`enable_lineage_platform`/`enable_schema_drift_detection`), `KPI_MONITOR_MIN_CHANGE_PERCENT`, and three payload/work caps.

## 6. Tests and results

| Suite | Count | Covers |
|---|---|---|
| `tests/test_kpi_monitoring.py` | 68 | Calendars, the three comparison periods, detection, ranking, finding shape, the engine, the report — plus the three classes the mission named by name: **`TestAlertNoise`** (7), **`TestDuplicateSuppression`** (7), **`TestInsufficientEvidence`** (8). |
| `tests/test_api_kpi_monitoring.py` | 20 | Feature flag, authorization tiers, anti-enumeration 404, persistence into the shared queue, `persist=false`, re-run behaviour, quiet periods, and that no response carries a credential. |
| `tests/security/test_kpi_monitoring_never_asserts_cause.py` | 22 | §7. |

**110 passing across the three.** `ruff`/`black` clean; `mypy` reports **zero errors** in `kpi_monitoring/` and the two new `api/` modules (the 3 it emits when checking them are pre-existing in `identity/repositories/semantic_intelligence.py`, untouched here).

**Three real bugs my own tests caught**, each fixed rather than worked around:

1. **`caller_roles` was wrong in the route.** `user.roles` yields `UserRole` association rows, not role names — so the restricted-dimension authorization check would have crashed on every scan. Now `get_user_roles(session, user.id)`, the shared helper `api/bi_integration.py` and `api/lineage.py` already use. A route-level test, not a unit test, was the only thing that could find this.
2. **A `float | None` reaching `Finding.confidence`.** `ChangeEvidence` guarantees no confidence without evidence but not the converse — `analytics.root_cause` may report contributors and still leave confidence unset. Keyed off the value itself.
3. **Two docstring promises the code did not keep** (found in review, before tests): `policy.py` promised three distinguishable refusal codes while collapsing them into one, and the engine's truncation branch had an unreachable, convoluted list comprehension.

## 7. Security and tenant review

**Monitoring structurally cannot produce data.** Asserted by parsing the real source: no module in `kpi_monitoring/` imports `agent.sql_validator`, `agent.graph`, `agent.nodes`, `agent.llm_client`, `db.execution`, `db.connection`, `db.schema_introspection`, `sqlalchemy`, `identity.models` or `identity.db`; none imports an LLM client; and **no module contains a SQL literal** (a `SELECT ... FROM` string anywhere would mean something intends to query). It reports on rows its caller supplies, which is why the deterministic SQL controls cannot be bypassed from here — there is no path to a row.

**Never asserts a cause.** Seven causal phrasings (`caused by`, `due to`, `driven by`, …) are asserted absent from the finding title, the finding detail, the rendered report, and every user-facing string literal in the package (docstrings excluded deliberately — they discuss causation precisely to explain why the code refuses to assert it). A sufficient-evidence finding always carries the explicit "contributions to an arithmetic total, not a demonstrated cause" caveat.

**Approved metrics only**, with a restricted-dimension gate: a policy breaking a metric down by a `config/sensitive_columns.yaml`-restricted column is refused unless the caller holds `VIEW_RESTRICTED_COLUMNS`, and the refusal is audit-logged with codes and identifiers only. Attribution puts that column's *values* into a finding, which is the same exposure the classification exists to prevent, reached by a different route.

**Tenant isolation**, over a real identity database: one tenant's findings never appear in another's queue; one tenant's published metric never makes another tenant's policy runnable; the same movement in two tenants is two independent rows; and duplicate suppression is per-tenant, so tenant B's first sighting is not suppressed because tenant A saw it.

**Secrets.** A refusal never echoes a metric's `approved_expression`; `ApprovedMetric` is a deliberately narrow projection that does not carry it, making that leak impossible rather than merely avoided. The finding key is an opaque 64-char hash, not a readable join of business identifiers.

**Untrusted input.** Period labels, dimension labels and row values are compared and summed, never parsed as dates, evaluated, or placed in a prompt. The CLI reads its input file as plain data and refuses any metric not asserted in its own `catalog_metrics`.

## 8. Performance and cost

Zero LLM calls, zero queries against a monitored database, zero network calls. One run is arithmetic over supplied series plus one `detect_anomalies` pass per policy; the API route makes exactly two tenant-scoped identity reads (published catalog metrics, open finding keys). Work is bounded by three caps; findings are ranked *before* truncation, so a capped run keeps the most impactful rather than whichever policies were configured first.

## 9. Limitations, disclosed rather than discovered later

1. **No delivery channel exists.** Nothing is sent. A run produces an `AlertBundle` describing what *would* be sent. Building a transport means provider credentials and a secret-handling decision that belongs to its own prompt.
2. **No scheduler.** A scan runs when the route or the CLI is called. This application has had no background worker since `onboarding/` disclosed the same absence; cron/Task Scheduler driving `scripts/run_kpi_monitor.py` is the mechanism, as it is for schema drift.
3. **The caller supplies the data.** Monitoring does not fetch a metric's series from its `approved_expression` — that would mean generating and running SQL, which this package deliberately cannot do. The natural next step is a thin, governed fetcher outside this package.
4. **The CLI cannot persist or govern.** It has no actor or tenant, so it prints findings rather than filing them, and takes `catalog_metrics` from its input file rather than the governed catalog. The API route is the stronger of the two paths.
5. **Period labels are opaque strings.** Business calendars and quiet periods are explicit label sets, not date rules. Inferring a holiday would mean inferring a locale, a fiscal year and a trading convention this application cannot know.
6. **`SemanticFinding.kind` is `String(32)` and two Prompt 43 kinds already exceed it** (39 and 38 characters). SQLite ignores the length, so the test suite is green and PostgreSQL would reject those writes. This prompt's own kinds all fit and are asserted at import; fixing Prompt 43's requires a column-widening migration and belongs to whoever owns that feature.
7. **No frontend.** The two routes and the CLI are operator-facing.
8. **Attribution is single-level.** `analytics.causal.investigate_root_cause_advanced` (hierarchical, with significance testing) exists and is not wired in here — it needs a dimension hierarchy a policy does not yet express.

## 10. Recommended next prompt

**Give monitoring its own governed fetcher, and close the storage bug.** Two independent pieces:

1. A thin, governed resolver that turns a published metric's `approved_expression` plus a policy's comparison period into a series and its dimension breakdowns, through `agent.sql_validator` → `db.execution` → `governance.result_policy` exactly as `POST /execute` does — closing limitation 3 and making a scheduled scan genuinely autonomous. It belongs *outside* `kpi_monitoring/`, so this package keeps its "cannot produce a row" property.
2. A migration widening `semantic_findings.kind`, closing limitation 6 before a PostgreSQL deployment hits it.
