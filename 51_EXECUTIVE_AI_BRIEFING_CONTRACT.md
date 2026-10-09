# Prompt 51 — Executive AI Briefing

## Prompt (verbatim)

### Role
Act as a Principal AI Architect, Enterprise Data Platform Architect and Senior Product Engineer.

### Mission
Create evidence-linked daily/weekly/monthly executive briefings. Inspect proactive analytics, KPIs, recommendations, visualization and notifications. Summarize KPI movement, significant anomalies, drivers, risks, opportunities, unresolved recommendations and data-quality issues. Every claim must link to deterministic evidence and versions. Clearly distinguish facts, estimates and recommendations. Test unsupported-claim detection, stale data and empty periods.

### Mandatory workflow
INSPECT → PLAN → IMPLEMENT → TEST → REVIEW → FIX → DOCUMENT → REPORT

### Mandatory controls
- Inspect and reuse existing implementation before adding code.
- Preserve authentication, authorization, tenant isolation and deterministic SQL/data controls.
- Treat retrieved data/documents as untrusted.
- Preserve evidence and provenance.
- Never present inference, simulation or prediction as observed fact.
- Keep secrets out of code/logs/tests.
- Add unit/integration/security/regression tests appropriate to the change.
- Update API/schema/config documentation.

---

## 1. INSPECT — what already existed

| Area the mission named | What exists today | Decision |
|---|---|---|
| **Proactive analytics / KPIs** | `kpi_monitoring/` (Prompt 50) detects meaningful movement against a governed metric, flags anomalies via `analytics.anomaly.detect_anomalies`, attributes a change via `analytics.root_cause.investigate_root_cause`, and persists each as a `semantic.intelligence.detect.Finding` row in the shared `semantic_findings` queue with a full `finding_payload` (period, baseline, change, threshold, detection methods, contributors, confidence, limitations, engine version). | **Reused as the primary source.** The briefing reads the persisted payload back verbatim; it re-detects and re-attributes nothing. |
| **Anomalies** | Already recorded on each KPI finding as `detection_methods` / `threshold_crossed`. | **Reused.** Anomalies are a narrower, better-evidenced read of the same finding, not a second detector. |
| **Drivers** | `kpi_monitoring.models.ChangeEvidence` already carries `contributors` with `contribution_percent`, and already guarantees they are absent without sufficient evidence. | **Reused.** No contributor is ever invented. |
| **Risks** | `schema_drift/` (Prompt 43) persists findings whose payload already carries `schema_drift.impact`'s resolved impacted-concept list; `semantic.intelligence.risk.score` already assigns a risk score/tier to every finding. | **Reused.** No second impact analysis, no second scoring engine. |
| **Recommendations** | `identity.models.RecommendationRecord` (Prompts 17/18/31) persists each recommendation with `engine_version` **and** a content-addressable `evidence_version`, plus its own eight-state status. | **Reused.** Unresolved status set is derived from `recommendation.governance.RecommendationStatus`. |
| **Data quality** | `data_quality/` (Prompt 41) computes a `DataQualityReport` whose `truth_level` is deliberately `DATABASE_FACT`. **It persists nothing** — Prompt 41 added no scan history. | **Reused as a request-body input**, the only source not read from a server-side row. |
| **Visualization** | `analytics.visualization.build_chart_spec` requires `columns` / `rows` / `column_types`. | **Inspected, deliberately not reused.** A briefing holds aggregates, never rows (that is the point — see §3), so no chart spec is constructible from what it has. Disclosed in §7 rather than faked. |
| **Notifications** | There is **no** notification or delivery subsystem anywhere in this codebase. `kpi_monitoring.models.AlertBundle`'s own docstring states this plainly: nothing sends an email, webhook or chat message. | **Inspected, nothing to reuse.** The briefing likewise has no delivery channel; it returns a document. Disclosed in §7. |
| **Number grounding** | `agent.insight.extract_numbers` + `is_insight_grounded` already implement "every number in generated text must be supported by the data, except numbers the user/source themselves supplied". | **`extract_numbers` reused verbatim**; the tolerance convention and the source-text allowance are copied by design, not reinvented. |
| **Tier boundaries** | `semantic.intelligence.risk.HIGH_THRESHOLD` / `MEDIUM_THRESHOLD`. | **Imported, not restated**, so a tier means one thing platform-wide. |

### A significant finding: in-flight Prompt 52 work in the working tree

`decision_center/` plus `api/decision_center.py`, `frontend/src/pages/DecisionCenter.tsx` and
their tests are present in the working tree **untracked/uncommitted**, implementing an
"Executive Decision Center" (Prompt 52) with its own `ExecutiveBriefing` type. It overlaps this
mission's subject matter and was inspected in full before any code was written here.

They are genuinely different deliverables, and this prompt deliberately did **not** merge them:

| | `decision_center/` (Prompt 52, uncommitted) | `briefing/` (this prompt) |
|---|---|---|
| Shape | A live **read model**: ranked headlines across 7 capabilities, decision chains (signal → … → outcome), a business-health rollup, role personalization | A cadenced **document**: daily/weekly/monthly, rendered to markdown |
| Cadence | None | `BriefingPeriod`, which selects the staleness budget |
| Staleness | `recorded_at` is carried but never compared to anything | `briefing/freshness.py` compares it to a caller-supplied `as_of` and marks/reports every stale record |
| Claim verification | None | `find_unsupported_claims` withholds and reports any claim whose own evidence does not support it |
| Fact / estimate / recommendation | Carries `DataTruthLevel` per headline | A separate, orthogonal `ClaimClass`, with the mismatched pairings made unrepresentable |
| Drivers | No section | Its own section, with the contribution caveat inline on every item |
| Data quality | Explicitly excluded ("persists no record of its own") | Included, as an untrusted request-body input |
| Versions | One `Provenance.engine_version` | Every evidence link carries every version the producing record recorded |

`briefing/` imports nothing from `decision_center/` — partly because the two answer different
questions, and partly because coupling a commit to uncommitted, unreviewed work would be wrong.
**Reconciling the two surfaces (most plausibly: the Decision Center consuming
`briefing.claims`' evidence gate, or a shared source-adapter layer) is the recommended next
prompt** — see §9.

---

## 2. PLAN

New top-level package `briefing/`, a peer of `kpi_monitoring/` / `data_quality/` /
`schema_drift/` / `recommendation/`:

- `models.py` — `BriefingPeriod`, `BriefingSection`, `ClaimClass`, `EvidenceLink`,
  `BriefingItem`, `SourceCoverage`, `StaleInput`, `UnsupportedClaim`, `ExecutiveBriefing`.
- `inputs.py` — storage-free projections of the governed records read, so the package imports
  neither `identity` nor `sqlalchemy`.
- `freshness.py` — staleness against a caller-supplied instant. Never reads a clock.
- `claims.py` — one builder per source, plus the evidence gate.
- `engine.py` — the one orchestration entry point.
- `reports.py` — the single markdown rendering.

Plus `api/briefing.py` + `api/briefing_schemas.py` (one route), six `Settings` fields, and four
test files.

---

## 3. IMPLEMENT — the design decisions that matter

### Every claim links to deterministic evidence and versions — enforced twice

1. **Type level.** `BriefingItem.evidence` is `Field(min_length=1)`, and
   `EvidenceLink._versions_present` refuses a link recording no version at all. A claim with no
   evidence, or evidence that cannot answer "which version said this", is unrepresentable.
2. **Pipeline level.** `briefing.claims.finalize` is the **only** `BriefingItem` constructor
   call in the package (pinned by a test that parses the source), and `briefing.engine` runs
   every candidate through `find_unsupported_claims` first. This is the same two-layer shape
   `recommendation.engine._finalize_candidate` establishes for its own evidence rule.

`EvidenceLink.observed` carries only aggregates another governed subsystem already computed. It
never carries a raw row, a sampled value, or a metric's `approved_expression`.

### Facts, estimates and recommendations — a separate axis from truth level

`ClaimClass` (`OBSERVED_FACT` / `ESTIMATE` / `RECOMMENDATION`) answers *what kind of statement
this is*; `agent.provenance.DataTruthLevel` continues to answer *how it came to be known*. They
are orthogonal and both carried, the same split `analytics.models.ScenarioValueKind` already
made against the same enum. `BriefingItem._class_matches_truth_level` makes the misrepresenting
combinations raise at construction:

- `OBSERVED_FACT` ⇒ `DATABASE_FACT`
- `ESTIMATE` ⇒ `AI_INFERENCE`
- `RECOMMENDATION` ⇒ `AI_INFERENCE`
- `CONFIRMED_BUSINESS_TRUTH` ⇒ **never**, for any class. A briefing reports what other
  subsystems recorded; confirming anything stays an explicit human action elsewhere.

One record can legitimately produce several claims in different sections, because they are
different kinds of statement about the same evidence. A revenue drop yields an `OBSERVED_FACT`
movement ("revenue was 640.00 against 1,000.00, a change of −36.0%"), an `ESTIMATE` anomaly
("this platform judged the movement unusual"), an `ESTIMATE` risk, and an `ESTIMATE` driver per
contributor. Splitting them is what makes the distinction visible to a reader instead of buried
in one paragraph.

### Unsupported-claim detection

Four codes, each independently reachable and tested:

| Code | Meaning |
|---|---|
| `no_evidence` | Nothing to link to. |
| `missing_version` | An evidence link that cannot answer "which version said this". |
| `truth_level_mismatch` | An estimate dressed as an observed fact, or the reverse. |
| `ungrounded_number` | A number in the claim's own text that its evidence does not record. |

Grounding reuses `agent.insight.extract_numbers` and its tolerance convention. Numbers appearing
in `subject` / `period_label` / `grounding_context` are allowed — the same allowance
`is_insight_grounded` makes for a number the user typed into the question.

**Trusted source text may be quoted; caller-supplied text must be verified.** KPI findings,
schema-drift findings and recommendation records are rows this server's own engines wrote through
authorized paths, so their wording goes into `grounding_context` and is quoted as-is. A
data-quality finding arrives in the **request body**, which makes it untrusted input per the
mandatory controls: its text is *not* self-grounding, so every number it states must also appear
in the `observed` aggregates the same request supplies. An internally inconsistent caller gets
that claim withheld rather than republished.

> This asymmetry was found by a failing test, not by design review. The first implementation put
> the data-quality claim text into `grounding_context` for the risk variant but not the
> observed-fact variant, so the same finding was verified in one section and self-certifying in
> the other. Closed in the stricter direction.

### Stale data

`briefing/freshness.py` compares each record's own `recorded_at` against the **caller-supplied**
`as_of` — never a clock read, for the same reason `data_quality.models.FreshnessRule.as_of` is
caller-supplied: a briefing assembled for a past period must be judged against that period.

Three budgets, one per cadence (`briefing_stale_after_{daily,weekly,monthly}_hours`), because the
honest threshold scales with the cadence. The same record is stale in a daily briefing and fresh
in a monthly one, which is tested directly.

**A stale item is marked, ranked below fresh items of equal risk, and listed in `stale_inputs` —
never dropped.** Hiding a week-old finding from a daily briefing would turn a freshness problem
into an apparent absence of problems. A record with no timestamp is `AGE_UNKNOWN` and disclosed,
never assumed fresh. `stale_inputs` is de-duplicated per record, so one finding that produced
four claims is one stale *input*.

### Empty periods

`SourceCoverage` is reported per source with `supplied` / `input_count` / `items_produced` / a
note, so three cases are distinguishable:

- **not supplied** — "No records were supplied for this source, so nothing was checked."
- **supplied but quiet** — "Records were supplied and checked; none produced a claim."
- **omitted** — the caller's own reason, e.g. "this account may not read recommendation records."

`empty_sections` names every section with nothing in it, and an empty briefing's `limitations`
and rendered report both tell the reader to check coverage before concluding nothing happened.
This is `kpi_monitoring.models.MonitorResult.series_skipped`' distinction, applied one level up.

### Ranking and caps

Highest risk first, reusing the score `semantic.intelligence.risk.score` already assigned —
never recomputed, so the briefing and the review queue agree about what matters most. Two
deliberate exceptions, both transparent:

- **A recommendation is capped at 60** (`_MAX_RECOMMENDATION_SCORE`) and therefore never ranks
  `high`. A suggestion outranking an observed collapse would misorder exactly the attention this
  briefing directs.
- **A driver is scored by its own share, capped by its parent movement.** A driver is ranked
  biggest-share-first (which a flat inherited score left to an alphabetical tie-break) and can
  never outrank the movement it is one part of.

Data-quality severity maps to the same 0–100 scale (`critical` 70 / `warning` 45 / `info` 15),
which is deterministic because `data_quality/engine.py` already derives severity from each
finding's own magnitude.

Sections are capped **after** ranking (`briefing_max_items_per_section`), with the affected
sections named in `truncated_sections` — the same order `kpi_monitoring.engine` uses.

### Nothing asserts a cause

Carried through verbatim from `kpi_monitoring` and `analytics.causal`: a driver is a contribution
to an arithmetic total. The caveat is inline on every driver item (not only in the section note),
and a test asserts seven causal phrasings appear in neither any claim nor the rendered report.

---

## 4. API / schema / config changes

### `POST /briefing/generate`

Requires a local account (`require_local_user`), rate-limited (`briefing_generate`), and 404s
when `ENABLE_EXECUTIVE_BRIEFING=false`.

Request: `database_id`, `period` (`daily`/`weekly`/`monthly`), `period_label`, optional `as_of`,
and optional `data_quality_findings[]` (aggregates only — the schema has no field a sampled row
value could occupy).

Response: `items[]` (each with `claim_class`, `truth_level`, `evidence[]` carrying `observed` +
`versions`, `is_stale`, `limitations`), `coverage[]`, `empty_sections[]`, `stale_inputs[]`,
`withheld_claims[]`, `limitations[]`, `truncated_sections[]`, the three claim-class counts,
`stale_item_count`, `report_markdown`, `engine_version`.

**Authorization is per section, not all-or-nothing.** An aggregate must never reveal what the
caller could not read directly, and must never silently omit it either:

| Source | Existing gate reused |
|---|---|
| KPI findings, schema-drift findings | `semantic.catalog_policy.CatalogAction.REVIEW` — the same gate `api/kpi_monitoring.py` and `api/schema_drift.py` apply to the same rows |
| Recommendation records | `recommendation.governance_policy.RecommendationAction.VIEW` — the same gate `api/recommendation_governance.py` applies |

A caller holding one but not the other gets the other source named as omitted in `coverage` and
`limitations`. A caller holding neither gets 403. **No new permission was added** — inventing a
`BRIEFING_READ` code would have created a second, drifting answer to "who may see this finding".
An unknown or cross-tenant `database_id` is the same 404 a nonexistent one gets.

### No database or schema change

Nothing is persisted. A briefing is a read of governed state, not a new governed artifact — the
same "engine first, persistence later, if ever" posture Prompts 38–42 took.

### `config/settings.py`

| Field | Default | Why |
|---|---|---|
| `enable_executive_briefing` | `True` | Pure, deterministic read of records the caller is already authorized to see; no monitored-database query, no LLM, no outbound call, nothing until requested. Same posture as `enable_kpi_monitoring` / `enable_data_quality_engine`. |
| `briefing_stale_after_daily_hours` | `36.0` | Staleness budget per cadence. |
| `briefing_stale_after_weekly_hours` | `192.0` | |
| `briefing_stale_after_monthly_hours` | `840.0` | |
| `briefing_max_items_per_section` | `10` | Cap applied after ranking. |
| `briefing_risk_min_tier` | `"medium"` | Tier a finding must reach before a *second*, inferred risk/opportunity claim is made about it. Never gates the observed movement itself. |
| `briefing_max_data_quality_findings` | `200` | Payload bound on the one request-body source. |

---

## 5. TEST — results

| File | Cases | Covers |
|---|---|---|
| `tests/test_briefing_freshness.py` | 16 | Per-cadence budgets, boundary (inclusive), clock skew, absent/unparseable timestamps, the same-record-stale-daily-fresh-monthly property. |
| `tests/test_briefing_claims.py` | 57 | All four unsupported-claim codes; the signed/unsigned allowance; the period-label and `grounding_context` allowances; every builder (movement/anomaly/driver/risk/opportunity, drift impact, recommendation statuses and categories, data-quality severities); that every builder's output passes the gate. |
| `tests/test_briefing_engine.py` | 53 | Assembly, the fact/estimate/recommendation split, ranking, caps, settings plumbing; **empty periods** (coverage's three cases); **stale data** (marking, de-duplication, ordering, cadence); **withheld claims**; report honesty. |
| `tests/test_api_briefing.py` | 20 | Feature flag, per-section authorization, tenant isolation against a real two-tenant identity database, payload bounds, `extra="forbid"`, the caller's `as_of` driving staleness, no credential in any response, and that the route never builds a DB engine or calls `execute_readonly_sql` (monkeypatched to raise). |
| `tests/security/test_briefing_never_presents_estimate_as_fact.py` | 48 | Type level (every mismatched pairing raises; `CONFIRMED_BUSINESS_TRUTH` is refused for all three classes; no evidence / no version / stale-without-reason are unrepresentable); pipeline level; text level (seven causal phrasings absent from claims and report); structural level (no module in `briefing/` imports `identity`, `sqlalchemy`, `agent.graph/nodes/llm_client/sql_validator`, `db.connection/execution/schema_introspection`, `ollama` or `rag.llm`; no SQL literal; exactly one `BriefingItem` construction site). |
| **Total** | **174** | All passing. |

Full backend suite re-run clean (see §8). `ruff check`, `black --check` clean on every touched
file; `mypy` reports **zero** errors in `briefing/` and `api/briefing*.py` (the 5 it reports are
pre-existing, in `db/schema_introspection.py` and two `identity/repositories/` modules, untouched
by this prompt).

---

## 6. REVIEW — security, tenant isolation, performance

**Authentication/authorization.** No existing gate was modified and no new permission added. Both
reused policy functions are called with the caller's own DB-backed permissions and their own
tenant on both sides, so the deny-by-default and cross-tenant branches behave exactly as they do
for the routes that already use them.

**Tenant isolation.** `resolve_actor_tenant_id(user)` is the only source of the tenant; no route
signature accepts a `tenant_id`. Both repository reads (`list_findings`, `list_records`) are
already tenant-filtered, and `databases_for_tenant` scopes the database id with an
anti-enumeration 404. Proven against a real identity database with two real tenants sharing one
configured database name.

**Deterministic SQL/data controls.** Untouched and structurally unreachable: no module in
`briefing/` imports a database handle, an ORM, an LLM client or the SQL validator, and the route
test monkeypatches `get_engine`/`get_read_only_engine`/`execute_readonly_sql` to raise.

**Untrusted input.** The one request-body source is treated as untrusted — its claim text is not
self-grounding (§3), its size is bounded, and `extra="forbid"` rejects unknown fields.

**Secrets.** Nothing in this feature reads a secret. A test asserts no response body contains
`password`, `jwt_secret`, `connection_string`, or the fixture password.

**Performance/cost.** Two indexed, tenant-filtered reads of the identity database per briefing
(`semantic_findings` by `(tenant_id, database_id, status)`, `recommendation_records` bounded to
200 rows). Everything else is pure in-memory work over at most a few hundred records. Zero LLM
calls, zero monitored-database queries, zero outbound calls — so the marginal cost is a
well-bounded database read, not inference.

---

## 7. Known limitations, disclosed

- **No delivery channel.** Nothing emails, posts or messages a briefing; the route returns a
  document. This matches the platform-wide position `kpi_monitoring.models.AlertBundle` already
  states, and building a transport means handling provider credentials — a separate decision.
- **No scheduler.** "Daily/weekly/monthly" is the cadence a caller asks for, which selects the
  staleness budget and the heading. Nothing in this codebase fires a briefing on a schedule; an
  operator would drive it from cron/Task Scheduler against the route. Unlike
  `scripts/run_kpi_monitor.py`, no CLI is provided, because every source here is a tenant-scoped
  read that needs a real authenticated actor.
- **No visualization.** `analytics.visualization.build_chart_spec` needs rows; a briefing holds
  only aggregates, by design. Charts would require either row access (which would defeat the
  structural guarantee above) or a second, aggregate-only chart builder.
- **No persistence, so no briefing history.** Two identical calls produce identical briefings;
  nothing records that a briefing was generated or read.
- **Period labels are opaque strings**, never parsed as dates — the same boundary
  `analytics.engine.detect_missing_periods` and `kpi_monitoring` already draw. `period` therefore
  does **not** filter records to that window: the briefing summarizes the tenant's currently-open
  findings and unresolved recommendations, and uses the cadence for freshness. A caller wanting a
  strict window supplies `as_of` and reads `stale_inputs`.
- **Data quality is caller-supplied**, so a caller that never runs a scan gets an honest
  "nothing was checked" rather than a clean bill of health.
- **Drivers are single-level.** `analytics.causal`'s hierarchical engine is not reached, because
  what is persisted on a KPI finding is the flat contributor list.
- **No frontend.** The route is the surface, consistent with Prompts 46/48/49/50.
- **Overlaps uncommitted Prompt 52 work** — see §1 and §9.

---

## 8. Files changed

**New**
- `briefing/__init__.py`, `models.py`, `inputs.py`, `freshness.py`, `claims.py`, `engine.py`, `reports.py`
- `api/briefing.py`, `api/briefing_schemas.py`
- `tests/test_briefing_freshness.py`, `tests/test_briefing_claims.py`, `tests/test_briefing_engine.py`, `tests/test_api_briefing.py`
- `tests/security/test_briefing_never_presents_estimate_as_fact.py`
- `51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`

**Modified**
- `config/settings.py` — six new fields (§4)
- `api/main.py` — router import + registration
- `CLAUDE.md` — a new section for this feature

---

## 9. Recommended next prompt

**Reconcile `briefing/` with the in-flight `decision_center/` (Prompt 52).** Both now exist and
read the same governed records for an executive audience. The highest-value consolidation is to
have the Decision Center's headlines pass through `briefing.claims`' evidence gate — it currently
has no claim verification at all — and to decide whether `decision_center.sources` and
`briefing.claims` should share one source-adapter layer. Doing this before either surface gains a
frontend avoids two divergent answers to "what does an executive see, and how do we know it is
true". Deciding what lands in git history for Prompt 52 is a prerequisite.
