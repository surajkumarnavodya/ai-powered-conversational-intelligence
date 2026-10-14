# Analytics and intelligence features — invariants and limitations

One entry per analytics capability: what it does, the invariants that
must not regress, and the known limitations. Each entry names its own
authoritative `NN_*_CONTRACT.md`, which holds the full inspection findings,
design and testing summary.

Moved verbatim out of the root `CLAUDE.md`. **Read the entry for a
capability before changing it** — most of these engines are deliberately
pure, deliberately unwired, or deliberately capped, and the reasons are
recorded here.

## Contents

- [Governed metrics, dimensions & semantic contracts (Prompt 10)](#governed-metrics-dimensions--semantic-contracts-prompt-10)
- [Analytical intent classification (Prompt 11)](#analytical-intent-classification-prompt-11)
- [Analytical planning layer (Prompt 12)](#analytical-planning-layer-prompt-12)
- [Deterministic Analytical Result Engine (Prompt 13)](#deterministic-analytical-result-engine-prompt-13)
- [Anomaly detection & evidence-based root cause (Prompt 14)](#anomaly-detection--evidence-based-root-cause-prompt-14)
- [Visualization & analytical presentation engine (Prompt 15)](#visualization--analytical-presentation-engine-prompt-15)
- [Deterministic forecasting (Prompt 16)](#deterministic-forecasting-prompt-16)
- [Evidence-first recommendation engine (Prompt 17)](#evidence-first-recommendation-engine-prompt-17)
- [Recommendation governance & feedback (Prompt 18)](#recommendation-governance--feedback-prompt-18)
- [SQL Server Query Store performance intelligence (Prompt 19)](#sql-server-query-store-performance-intelligence-prompt-19)
- [Advanced hierarchical root-cause engine (Prompt 37)](#advanced-hierarchical-root-cause-engine-prompt-37)
- [Governed What-If Scenario Engine (Prompt 38)](#governed-what-if-scenario-engine-prompt-38)
- [Governed Optimization Engine (Prompt 39)](#governed-optimization-engine-prompt-39)
- [Governed Predictive Analytics engine (Prompt 40)](#governed-predictive-analytics-engine-prompt-40)
- [Data Quality Command Center (Prompt 41)](#data-quality-command-center-prompt-41)
- [Data Lineage Platform (Prompt 42)](#data-lineage-platform-prompt-42)
- [Schema Drift Impact Analysis (Prompt 43)](#schema-drift-impact-analysis-prompt-43)
- [Proactive KPI monitoring (Prompt 50)](#proactive-kpi-monitoring-prompt-50)
- [Executive AI briefing (Prompt 51)](#executive-ai-briefing-prompt-51)
- [Executive Decision Center (Prompt 52)](#executive-decision-center-prompt-52)

---

## Governed metrics, dimensions & semantic contracts (Prompt 10)
Extends the semantic catalog above with five fields that make a
`METRIC`-type entry *governing*, not merely descriptive —
`approved_expression`/`source_tables`/`filters`/`dimensions`/
`aggregation` (`semantic.catalog.CatalogEntrySnapshot`, `identity.models
.SemanticCatalogEntry`) — and gives it real behavioral teeth: "confirmed
definitions take precedence over LLM-generated definitions," the
acceptance criterion being "equivalent KPI questions consistently use
the governed metric definition."

**Invariants that must not regress:**
- **A governing metric reaches the SQL-generation prompt as a mandatory
  instruction, not an advisory hint.** `retrieval.retriever
  .extract_governing_metrics` filters already-retrieved
  `BUSINESS_CONCEPT` chunks (zero new query) down to `METRIC`-typed ones
  into `AgentState["governing_metrics"]`; `agent.llm_client
  ._build_mandatory_metrics_block` renders them as a "you MUST use
  exactly this approved expression" block — still framed as DATA, never
  instructions, extending `_build_plan_block`'s existing imperative-but-
  injection-safe precedent, never `_build_business_context_block`'s
  purely-advisory one. Deterministic: the same governing metric always
  produces byte-identical guidance regardless of the question's own
  phrasing (verified directly in `tests/test_llm_client_metric
  _conformance.py`) — this is the literal mechanism satisfying the
  acceptance criterion.
- **Enforcement, not just a stronger hint.** New `agent.nodes.review_
  metric_conformance_node`, wired into the graph as `review_sql →
  review_metric_conformance → validate_sql` — a pure pass-through
  (zero LLM call) whenever `governing_metrics` is empty or
  `Settings.enable_metric_conformance_review` is off, mirroring
  `review_sql_node`'s identical skip/retry/give-up shape otherwise
  (same `retry_count`/`max_retries` budget, a new
  `last_error_category="metric_definition_not_used"`). **Deliberately
  not a `agent.sql_validator` safety check** — an LLM-judged accuracy
  aid (two expressions can be equivalent without being AST-identical),
  kept structurally separate from the deterministic, fail-closed
  security validator per master rule 5.
- **Conflict detection is non-blocking.** `identity.repositories
  .semantic_catalog.find_conflicting_published_entries` scans PUBLISHED
  rows in the same `(tenant_id, database_id, concept_type)` scope for a
  *different* `concept_key` sharing a business name/synonym, run at both
  create and publish time, surfaced via `CatalogEntryOut
  .conflicting_entry_ids`/`conflicting_entry_names` and logged via
  `security.audit_log.log_security_event` — never a rejection (this
  codebase's standing "surface ambiguity, never auto-resolve it"
  posture).

**Read [`10_GOVERNED_METRICS_CONTRACT.md`](../../10_GOVERNED_METRICS_CONTRACT.md)**
for the full inspection findings, every new module's design, the 50-test
testing summary, and the security/tenant-isolation/performance review.


## Analytical intent classification (Prompt 11)
`agent/intent.py` (new) classifies what *kind* of analytical question a
question is — `AnalyticalIntentType`'s 13 categories (LOOKUP,
AGGREGATION, TREND, COMPARISON, RANKING, DISTRIBUTION, SEGMENTATION,
FUNNEL, COHORT, FORECAST, ANOMALY, ROOT_CAUSE, RECOMMENDATION) — the
general classifier `02_TARGET_ARCHITECTURE.md` §2 named as missing back
in Prompt 02 (distinct from `agent.followup.classify_followup`'s
*conversational* standalone/followup/ambiguous judgment and
`agent.complexity`'s narrow retry-budget trip-wires, neither of which
this replaces). New `agent.nodes.classify_analytical_intent_node`, wired
`retrieve_business_context → classify_analytical_intent → plan_query`,
runs on **every** question (gated only by `Settings
.enable_intent_classification`, default `True`) — unlike `plan_query_node`'s
own complexity-gated call, since this is meant to be foundational.
Always `AI_INFERENCE` (`agent.provenance.DataTruthLevel`), never
confirmed, never itself a security/authorization control.

**Invariants that must not regress:**
- **Purely advisory to `plan_query_node`'s gate, never to the retry
  budget.** `agent.complexity.compute_max_retries`/`state["complexity_signals"]`
  are computed once in `run_agent()` *before* the graph is even built —
  no node, including this one, can retroactively widen `max_retries`.
  `plan_query_node`'s gate became `enable_query_planning and
  (complexity_signals or intent_implies_planning(analytical_intent))` —
  reading `agent.intent._INTENT_TYPES_IMPLYING_PLANNING` (every intent
  except LOOKUP/AGGREGATION) via that one helper, never re-derived. When
  `analytical_intent` is `None` (disabled, unreachable Ollama, or
  unparseable), this reduces to exactly `complexity_signals` alone —
  byte-identical to this node's pre-Prompt-11 behavior, preserved by
  construction.
- **Never short-circuits the graph.** Structurally distinct from
  `classify_followup_node`'s own *conversational* "ambiguous"
  classification (the one path in this codebase allowed to end the
  graph early at `needs_clarification`) — `classify_analytical_intent`
  has exactly one outgoing edge (a straight `add_edge`, never a
  conditional routing table with an `END` branch), verified directly
  (`tests/test_sql_agent_integration.py
  ::test_classify_analytical_intent_has_no_conditional_routing`). A
  non-empty `ambiguity_flags` only ever widens `plan_query_node`'s gate,
  the same way any other planning-implying intent does.
- **Cannot bypass authorization, by construction.** `Permission.ASK` is
  enforced by a FastAPI dependency before `run_agent`/the graph are even
  built; this node runs deep inside an already-authorized request and
  never reads/writes `caller_roles` or any field `validate_sql_node`'s
  restricted-column gate reads.
- **`metric_candidates` (raw, unconfirmed phrases from the question's own
  text) is never conflated with `AgentState["governing_metrics"]`
  (Prompt 10's already-governed, `CONFIRMED_BUSINESS_TRUTH` set)** — two
  deliberately separate truth-level concepts, per master rules 9/10.
- **Eval harness coverage is diagnostic only** — `eval.evaluators
  .evaluate_intent_classification`/the new `intent_classification_accuracy`
  metric never feed `compute_overall_pass`/`final_accuracy`, exactly
  like `sql_exact_match`'s own long-standing posture.

**Read [`11_ANALYTICAL_INTENT_CONTRACT.md`](../../11_ANALYTICAL_INTENT_CONTRACT.md)**
for the full inspection findings, the ~102-test testing summary, the
structural "never short-circuits/never bypasses authorization" proof,
and why no new live-verified TREND/COMPARISON benchmark case was added
in this pass (a disclosed, environment-specific DB-access limitation,
not an oversight).


## Analytical planning layer (Prompt 12)
`agent/analytical_plan.py` (new) is a typed, resolvable-to-real-schema
`AnalyticalPlan` — metric(s)/dimensions/filters/time range/grain/
comparison/ranking/sort/limit/required database capabilities — sitting
between Prompt 11's intent *classification* and free-text SQL. The real
gap this closes: the pre-existing free-text `query_plan: list[str]`
(`agent.nodes.plan_query_node`) was only ever checked by a *second LLM
call* (`review_sql_node`) — nothing deterministically verified a plan's
own table/column/relationship/authorization/capability claims, the exact
thing `agent.sql_validator.validate_sql` already does for generated SQL
one layer later. New `agent.plan_validator.validate_plan` is that
deterministic re-verification — a pure function (no I/O, no LLM call)
checking metric/dimension existence against the already-retrieved
schema, FK-declared relationship paths between the plan's own tables,
`config.sensitive_columns`-classified restricted columns against caller
permission (reusing `agent.authz.has_role_permission`, the identical
check `validate_sql_node` already applies to generated SQL), time-range
column type feasibility, and target-`DB_TYPE` capability support for a
small, honestly-scoped set of SQL feature tags (window functions, CTEs,
`PERCENTILE_CONT`, `STRING_AGG`, `ROLLUP`, `LAG`/`LEAD`).

New `agent.nodes.build_analytical_plan_node`, wired
`classify_analytical_intent → build_analytical_plan → plan_query` (two
straight edges, no conditional routing), gated by the exact same
trigger `plan_query_node` already used (`Settings.enable_query_planning`
AND (`complexity_signals` or `intent_implies_planning`), now factored
into a shared `_planning_gate` helper so the two nodes can never drift)
plus `Settings.enable_analytical_planning` (default `True`). **This is
the first tier of a three-tier fallback**: (1) a validated structured
plan — only when the LLM call succeeds, the response parses, AND
`validate_plan` finds zero violations; (2) `plan_query_node`'s existing
free-text plan — runs completely unmodified whenever tier 1 leaves
`state["analytical_plan"] = None`, **for any reason**, including a
syntactically-fine-but-deterministically-invalid plan (discarded in
full, never partially trusted — this is what makes "preserve direct
Text-to-SQL fallback" true by construction, not convention); (3) direct
generation, when neither planning node ever fires. On success, the
validated plan is also rendered into `state["query_plan"]`
(`agent.analytical_plan.render_plan_as_steps`) so `review_sql_node`'s
existing plan-conformance LLM check keeps working unmodified against it
— no second review node was added (master rule 3).

**Invariants that must not regress:**
- **An invalid plan is never partially trusted.** `validate_plan`
  returns every violation found (not just the first); the moment
  `is_valid` is `False`, the whole plan is discarded —
  `state["analytical_plan"]` stays `None`, never a plan with "the valid
  parts kept." Verified directly
  (`tests/test_agent_nodes.py::TestBuildAnalyticalPlanNode
  ::test_plan_that_fails_deterministic_validation_falls_back_to_none`
  and `::test_invalid_plan_falls_back_and_plan_query_node_then_runs_normally`,
  the latter proving tier 2 then runs exactly as it would have before
  this prompt existed).
- **Never short-circuits the graph.** Same structural proof pattern as
  Prompt 11's own: `build_analytical_plan` has exactly one outgoing edge
  (a straight `add_edge` to `plan_query`, never a conditional routing
  table with an `END` branch) — verified directly
  (`tests/test_sql_agent_integration.py
  ::test_build_analytical_plan_has_no_conditional_routing`).
- **Does not weaken or duplicate the real SQL-safety gate.**
  `agent.sql_validator.validate_sql`'s safety-violation check and
  `validate_sql_node`'s own restricted-column gate are completely
  unmodified by this prompt and still run on every generated SQL
  statement regardless of whether a plan was ever built, validated, or
  rejected — `validate_plan` is strictly an earlier, additional,
  non-load-bearing-for-security layer (master rule 5: never let an LLM
  bypass a deterministic security control).
- **Relationship-path checking is bounded, not a full graph search.**
  Only `FOREIGN KEY (...) REFERENCES ...` edges parsed out of the
  already-retrieved schema tables' own DDL text
  (`db.schema_introspection.extract_ddl_foreign_key_targets`) are
  considered — never a cross-schema search beyond what's already
  retrieved, and never one of `db/relationship_inference.py`'s own
  lower-confidence inferred candidates. A real join path through a table
  that didn't make the top-k retrieved set is rejected
  (`no_relationship_path`), a disclosed, bounded scope matching this
  codebase's existing "the retrieved schema is generation's own sandbox"
  posture.
- **`plan_query_node`'s own logic is byte-identical to its pre-Prompt-12
  shape** apart from one new early-return (skip, pass-through, when
  `state["analytical_plan"]` already holds a validated plan) — verified
  by the full pre-existing `TestPlanQueryNode` suite passing unchanged.

**Read [`12_ANALYTICAL_PLANNING_CONTRACT.md`](../../12_ANALYTICAL_PLANNING_CONTRACT.md)**
for the full inspection findings, every new module's design, the
61-test testing summary, and the security/tenant-isolation/performance
review.


## Deterministic Analytical Result Engine (Prompt 13)
`analytics/engine.py` (new) is the actual deterministic calculator the
pre-existing `analytics/` typed-findings boundary was always meant to
front: `compute_analytics_result(columns, rows)` classifies a raw SQL
result into one of six `ResultShape`s (`SCALAR`/`TIME_SERIES`/
`CATEGORICAL_AGGREGATE`/`MULTIDIMENSIONAL`/`RAW_TABLE`/`EMPTY`) and
computes the full stat set this requires: row/null/distinct counts,
min/max/mean/median, variance/stddev, percentiles (p25-p99 via
`statistics.quantiles`), a full period-by-period growth series (not just
first-vs-last) with missing-period detection for year/year-month-shaped
labels, a complete ranking with each entry's share-of-total percent
(which doubles as the "distribution" requirement — one computation, not
two), and both z-score and IQR outlier detection. Every finding carries
its own `formula: str` and the whole result is stamped with
`ANALYTICS_ENGINE_VERSION` — the literal "record formula/version
metadata" requirement, and every finding is `DataTruthLevel.DATABASE_FACT`
(`agent.provenance`) by construction, never an LLM's own claim.

New `agent.nodes.compute_analytics_node`, wired
`execute_sql --(succeeded)--> compute_analytics --> generate_insight`
(straight edges only), gated by `Settings.enable_analytics_engine`
(default `True`) — runs on **every** successful execution (unlike
`generate_insight_node` right after it, a pure zero-I/O computation has
no narrative cost to gate on), storing the result on
`AgentState["analytical_result"]` and `AskResponse.analytical_result`.
Fails open on any unexpected error, the same posture every accuracy-aid
node in this graph already takes.

**Invariants that must not regress:**
- **`agent/insight.py` is completely untouched** — `summarize_result()`
  and the live `_INSIGHT_SYSTEM_PROMPT`/`_build_insight_prompt` text stay
  byte-identical (re-running `tests/test_insight.py` unmodified is the
  proof), because that prompt's exact wording is what
  `docs/EVALUATION_CURRENT.md`'s live-LLM benchmark numbers were measured
  against — the identical reasoning that already kept
  `trend`/`outliers`/`stddev` "computed but not wired into the live
  prompt" (see "AI Data Analyst depth" above). `analytics/engine.py`
  computes an independent, null-aware superset directly from `columns`/
  `rows`, never by reusing or extending `summarize_result`'s own code.
- **Null handling differs from `agent.insight.ColumnStat` by design**:
  a column's numeric-ness here is judged from its *non-null* values
  (nulls excluded from aggregates, counted via `null_count`) — unlike
  `agent.insight`'s stricter "every row including nulls must be numeric"
  rule. This deliberately lives only in the new, independent engine, not
  back-ported to the pinned one.
- **The live insight narrative prompt is NOT extended to use this
  engine's output in this pass** — `state["analytical_result"]` is
  computed, tested, and surfaced via `AskResponse` ahead of that
  separate wiring decision, exactly the same deferral this codebase
  already chose once for trend/outlier/stddev.
- **`analytics/models.py`'s pre-existing shape is fully preserved** —
  every new field/enum value is additive with a default, verified by
  re-running `tests/test_analytics_provider.py`/the pre-existing part of
  `tests/test_analytics_models.py` unmodified. The legacy
  `AnalyticsFinding.outlier`/`variance_column` fields (set only by the
  pre-existing `ResultSummaryAnalyticsProvider`) are untouched; the new
  engine uses its own `outlier_detail`/`variance` fields instead.
- **Missing-period detection is bounded** — only year and year-month
  label shapes get gap detection; a full ISO date is recognized as
  period-shaped for classification purposes but deliberately gets no gap
  detection (the real reporting cadence isn't inferable from the label
  alone). Multidimensional ranking is a flattened composite-key
  breakdown, not true N-way pivot/cube analysis.

**Read [`13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md`](../../13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md)**
for the full inspection findings, every new module's design, the
testing summary, and the security/tenant-isolation/performance review.


## Anomaly detection & evidence-based root cause (Prompt 14)
`analytics/anomaly.py` (new) is a general-purpose, configurable anomaly
detector over a **chronological** series — distinct from Prompt 13's own
categorical z-score/IQR outlier check, which looks at one static set of
per-label totals with no time order. `detect_anomalies` scans a whole
series against up to five independent methods: a flat absolute
threshold (off by default — no safe generic unit), point-over-point
percent change, a **rolling** (not global) z-score baseline, a
whole-series IQR fence, and an opt-in seasonality-aware comparison
("where sufficient" — only evaluated when at least one same-lag prior
occurrence exists). A point can be flagged by several methods at once,
each recorded separately with its own `formula`/`threshold_used`,
never collapsed into one undifferentiated boolean. The **only** live
integration: `analytics.engine.compute_analytics_result`'s `TIME_SERIES`
branch feeds this module the exact same `GrowthStat.points` series it
already computed (zero new queries), gated by `Settings
.enable_anomaly_detection` (default `True`), appending one `ANOMALY`
finding per flagged point to the same `AgentState["analytical_result"]`/
`AskResponse.analytical_result` surface Prompt 13 built.

`analytics/root_cause.py` (new) is an evidence-based contribution
engine: given a current period's and a baseline period's raw rows (same
columns, a dimension breakdown column or columns, and a value column),
`investigate_root_cause` runs the prompt's own named flow — anomaly
(magnitude) → baseline → dimension breakdown (reuses `analytics.engine
.group_and_sum_by_label`, promoted from private to public for this
reuse) → contribution → ranking → validation → evidence — as one
function. **The acceptance criterion ("no root cause is stated without
supporting evidence") is enforced structurally**: `RootCauseResult
.contributors`/`.confidence` can never be populated unless at least one
dimension value clears `Settings.root_cause_min_contribution_percent`
(default `10.0`) in the validation step; `has_sufficient_evidence` is
derived from `contributors` being non-empty, never set independently —
verified live against real AdventureWorksDW2025 data (a genuine
category/region breakdown correctly explained a real year-over-year
drop with three validated contributors and a confidence of `0.65`, six
smaller contributors correctly excluded as noise).

**Invariant that must not regress — honest, disclosed scope split**:
anomaly detection is **live-wired** (zero new queries needed — it only
ever needs the one series already in hand); root-cause/contribution
analysis is **not wired into any live LangGraph node** in this pass — it
inherently needs *two* datasets (the anomalous period's breakdown and a
baseline period's), and this application's pipeline generates exactly
one SQL query per question today. `investigate_root_cause` is fully
built, fully tested, and callable directly (by a script, a future route,
or a future pipeline extension) — mirroring the exact "stubbed today,
wired later" precedent `analytics/` itself already established in
Prompt 02, continued by Prompt 13 for the live insight prompt.

**Other invariants that must not regress:**
- **A sustained linear trend can still be flagged by the rolling
  z-score method** — a known, disclosed limitation (no detrending step),
  not a false claim of trend-immunity; the rolling baseline still
  recovers faster from a one-off spike than a global mean would (proven
  directly in `tests/test_analytics_anomaly.py`).
- **A zero-stddev same-lag seasonal history is correctly skipped, never
  divided-by-zero** — identical philosophy to every other zero-stddev
  guard already in `analytics/`.
- **`analytics/models.py`'s pre-existing shape is fully preserved** —
  every new field/enum value is additive with a default; re-running
  `tests/test_analytics_provider.py`/`test_insight.py` unmodified proves
  neither the legacy adapter nor the pinned insight module were touched.

**Read [`14_ANOMALY_ROOT_CAUSE_CONTRACT.md`](../../14_ANOMALY_ROOT_CAUSE_CONTRACT.md)**
for the full inspection findings, every new module's design, the
testing summary, and the security/tenant-isolation/performance review.


## Visualization & analytical presentation engine (Prompt 15)
`analytics/visualization.py` (new) is a deterministic, no-LLM, no-I/O
chart-specification builder: `build_chart_spec(columns, rows,
column_types, analytics_result=None, settings=None) -> ChartSpec | None`
maps a raw SQL result's shape to a full specification (chart type,
fields/roles, inferred aggregation/format, title, sort, a top-N limit,
and always-non-empty accessibility metadata) — closing three gaps the
pre-existing frontend `frontend/src/lib/chartEngine.ts` had no mapping
for at all: single-numeric-column distributions, box-and-whisker
summaries, and geography. **Deliberately additive to, never a
replacement for, `agent.result_charting.classify_columns`/
`recommend_chart`** — those stay completely untouched;
`ExecuteResponse.chart_recommendation`/`column_types` keep powering
`chartEngine.ts`'s own seed-selection exactly as before (see "Optional,
opt-in SQL result charting" above — that section's own "must not
regress" invariant is unmodified by this prompt). `build_chart_spec`
takes `column_types` as an input rather than re-inferring it, so there is
exactly one numeric/date/text classification in this codebase, not two.

**The histogram-vs-box/map resolution**: this app's chosen charting
stack (Chart.js, no plugins) can't render a true box-and-whisker plot or
a map, and "do not introduce duplicate chart libraries" says not to add
one casually. A histogram, though, is just a bar chart of binned counts
— genuinely implemented on both sides (`analytics.visualization`'s own
binning logic for the spec; `chartEngine.ts`'s new `computeHistogramBins`
+ a `histogram` → `chartJsType: 'bar'` branch in `prepareChart`, reusing
the exact same rendering path `bar`/`bar-horizontal`/`bar-stacked`
already use — zero new UI component code). `ChartType.BOX`/`.MAP` are
real, always-computed, detected concepts in the backend's own taxonomy
(`detect_geography_columns` tags geo_region/geo_latitude/geo_longitude
columns unconditionally) but gated behind two new, off-by-default
`Settings` flags (`enable_box_plot_charts`/`enable_map_charts`) — so the
engine never actually *recommends* an unrenderable type today; a
distribution falls back to `HISTOGRAM` and a geography breakdown falls
back to `BAR`. **`'box'`/`'map'` were deliberately NOT added to the
frontend's own `ChartTypeId`** — unlike histogram there's no
achievable-without-a-new-library rendering path for either yet, and a
permanently-disabled frontend type would be dead code, not a feature.

**Invariants that must not regress:**
- **`visualization_spec` (`ExecuteResponse`'s new field, from
  `api/main.py`'s `/execute` handler) is additive and NOT wired into
  `chartEngine.ts`'s own seed-selection in this pass** —
  `chart_recommendation`/`column_types` remain the frontend's actual
  chart-picker seed; `visualization_spec` is parallel API surface a
  future prompt can have the frontend start consuming, the same
  "compute + test + expose via API, defer the deepest UI wiring" posture
  Prompt 13/14's own analytics surfaces already used.
- **A computation failure here can never fail `/execute`'s response** —
  wrapped in the identical try/except-log-and-degrade-to-`None` shape
  `agent.nodes.compute_analytics_node` already established; an accuracy/
  presentation aid is never a reason a successful "Confirm and Run"
  fails.
- **Accessibility text is never empty** — reuses a real, already-
  computed `analytics.engine.AnalyticsResult`'s own grounded
  `RANKING`/`GROWTH`/`COLUMN_SUMMARY` claim text when one is passed in
  (verified live: a real ranking query's spec carried `analytics.engine`'s
  own claim text verbatim inside its `accessibility.alt_text`), falling
  back to a generic, deterministic description otherwise.
- **Title generation never double-prefixes an aggregation word already
  present in a column's own name** (e.g. `"TotalRevenue"` must render as
  `"Total Revenue"`, never `"Total Total Revenue"`) — a real bug found
  during direct smoke-testing before any test was written, fixed by
  `_label_with_aggregation_prefix`'s shared-tokenizer membership check,
  now a locked-in regression test.
- **The frontend histogram validity rule requires no co-present text/date
  column, matching the backend's own identical rule** — a real bug (the
  validity rule initially only checked the numeric-column count, not
  whether a category column was *also* present, contradicting its own
  disclosed reason text) found by the extended test suite itself, not a
  design review, and fixed before merge.

**A genuine, disclosed, pre-existing limitation surfaced by live
testing, not introduced by this prompt**: a SQL Server `decimal` column
comes back from `pyodbc` as Python `decimal.Decimal`, which gives the
result `DataFrame` an `object` dtype — `agent.result_charting
.classify_columns` (untouched by this prompt) classifies that as
`"text"`, not `"numeric"`. Since `build_chart_spec` deliberately reuses
`column_types` rather than re-inferring it, an uncast `decimal`-typed
single-numeric-column result gets no chart spec at all today (`None`),
not a wrong one — identically true of the pre-existing
`chart_recommendation` path, which this prompt's design explicitly keeps
untouched.

**Read [`15_VISUALIZATION_ENGINE_CONTRACT.md`](../../15_VISUALIZATION_ENGINE_CONTRACT.md)**
for the full inspection findings, every new module's design, the
testing summary (30 new backend + frontend test cases, full suites
rerun clean — 3088 backend / 287 frontend, zero regressions), the two
real bugs found and fixed (the title double-prefix, the frontend
histogram validity rule and its `ChartPicker.tsx` icon-map crash), and
the security/tenant-isolation/performance review.


## Deterministic forecasting (Prompt 16)
`analytics/forecasting.py`'s `generate_forecast` extends
`compute_analytics_result`'s `TIME_SERIES` branch one step further: not
just describing observed history, but projecting it forward. Five
deterministic, stdlib-only baseline models (naive, seasonal-naive, moving
average, linear trend, simple exponential smoothing) plus an `AUTO` mode
that backtests every eligible candidate and picks the most accurate — no
numpy/pandas-time-series/statsmodels/sklearn added, matching `analytics/`'s
own established "stdlib only" posture. New `agent.nodes.generate_forecast_node`,
wired `compute_analytics → generate_forecast → generate_recommendations`
(a straight edge, never a conditional one, so it can never short-circuit
the graph; `generate_recommendations` is Prompt 17's own node, inserted
immediately after this one — see "Evidence-first recommendation engine"
below). Only ever attempts a forecast when
`classify_analytical_intent_node` already classified the question as
`AnalyticalIntentType.FORECAST` (Prompt 11's own intent, unused by
anything until now) — no new classification, pure reuse of a judgment
already made earlier in the same run. `AskRequest.forecast_horizon`/
`AskResponse.forecast_result` thread the same validate-before-admission-
control pattern `model` selection already established; `run_orchestrated`
threads the identical parameter through to both the short-circuit and
multi-source paths.

**Invariants that must not regress:** every `"ok"` `ForecastResult` is
always `DataTruthLevel.AI_INFERENCE` and always carries a non-empty
`limitations` tuple — the literal "forecasts are explicitly represented
as estimates with limitations" acceptance criterion, enforced inside the
engine itself, not left to whichever caller renders the result. A
data-insufficient or untrusted-temporal-semantics series (fewer than
`Settings.forecast_min_data_points` points, an unrecognized label
pattern, or a raw-ISO-date-labeled series — forecasting inherits
`detect_missing_periods`'s own "date gets no gap detection" scope
boundary rather than relaxing it) is a normal, typed
`ForecastResult(status="rejected", ...)`, never an exception and never a
reason `/ask` itself fails. `recommendations_for_forecast` (the first
real producer of `recommendation.models.Recommendation` in this
codebase) and `analytics.visualization.build_forecast_chart_spec` are
built and fully tested but **not wired into the live `/ask`/`/execute`
paths or the frontend in this pass** — the same disclosed "compute +
test, defer the deepest wiring" deferral Prompts 14/15 already
established.

**Read [`16_FORECASTING_CONTRACT.md`](../../16_FORECASTING_CONTRACT.md)** for
the full inspection findings, every model's fitting/backtest/interval
design, the testing summary (3132/3132 backend tests passing, up from
3088 before this prompt — including 7 pre-existing orchestrator/`/ask`
tests whose own stand-ins needed updating for `run_agent`'s new
parameter, not a regression in this prompt's own code), and the
disclosed, honest scope boundaries.


## Evidence-first recommendation engine (Prompt 17)
`recommendation/engine.py`'s `generate_recommendations` is the first
general-purpose recommendation engine in this codebase — a deterministic
Data → Finding → Evidence → Rule/Model → Candidate → Evidence Validation
→ Confidence → Recommendation pipeline across nine modular categories
(performance, anomaly, revenue, customer, product, operations,
data_quality, security, database_performance), each rule consuming a
typed fact some other, already-existing module already computed
deterministically (`analytics.engine.compute_analytics_result`,
`analytics.root_cause.investigate_root_cause`, `db.query_cost
.estimate_query_cost`, `observability.metrics.PerformanceMetrics
.snapshot`, `config.sensitive_columns`) — zero new queries, zero new LLM
calls. New `agent.nodes.generate_recommendations_node`, wired
`generate_forecast → generate_recommendations → generate_insight` (a
straight edge, never a conditional one, so it can never short-circuit
the graph) — the graph is now eighteen nodes (see "Self-correcting
retry loop" above).

**Invariants that must not regress:** "no recommendation without
evidence" is enforced in code, not by convention —
`recommendation.engine._finalize_candidate` is the *only* place this
module ever constructs a `Recommendation`, and it refuses to for any
candidate whose `evidence` tuple is empty. This is deliberately a
**pipeline-level** invariant, not a `Recommendation` `model_validator`:
`analytics.forecasting.recommendations_for_forecast`'s six pre-existing
call sites construct `Recommendation`s with no evidence at all and must
keep validating unmodified (master rule 4) — `Recommendation.evidence`
legally defaults to `()`. What the model *does* enforce at the type
level: any `evidence` item present must be `DataTruthLevel.DATABASE_FACT`
or `CONFIRMED_BUSINESS_TRUTH`, never `AI_INFERENCE` — an inference cannot
ground another inference. A candidate also clearing evidence validation
but whose rule-computed confidence falls below
`Settings.recommendation_min_confidence` is still dropped — the literal
"supported vs. unsupported" distinction, separately testable from
evidence presence. **Authorization is re-checked inside the engine
itself**, independent of whatever role the original query ran under
(following `agent/authz.py`'s own stated "no frontend-level restriction
is a security boundary ... the corresponding internal node re-checks
independently" principle): a candidate whose evidence touches a column
`config.sensitive_columns` classifies restricted is suppressed outright
for a viewer lacking `Permission.VIEW_RESTRICTED_COLUMNS` — not
SECURITY-category-specific; any category's candidate referencing a
restricted column is suppressed the same way. In the one place this is
wired live today (the graph node, same request, same caller), this check
is provably redundant with `validate_sql_node`'s own pre-execution gate
(a restricted column could only reach a successful execution because
that caller already had permission) — it exists for every *other*
caller of `recommendation.engine.generate_recommendations` (a future
report job, a future dedicated route, a batch recompute with a different
viewer's roles), where it is not redundant at all.

**Known, disclosed limitation:** the `operations` category (built on
`analytics.root_cause.investigate_root_cause`) never fires from the live
graph node — inherited directly from Prompt 14's own disclosed gap (root
cause needs a second, comparison dataset this single-query pipeline
doesn't produce); it remains fully built, fully tested, and reachable by
calling the engine directly with one in hand. Column-name keyword
heuristics (revenue/customer/product) are a disclosed, bounded approach,
not a business-glossary lookup — a natural, not-yet-taken follow-up is
feeding these from `semantic/catalog.py`'s own governed business
concepts (Prompt 9/10) instead.

**Read [`17_RECOMMENDATION_ENGINE_CONTRACT.md`](../../17_RECOMMENDATION_ENGINE_CONTRACT.md)**
for the full inspection findings, every category rule's design, the
testing summary (3172/3172 backend tests passing, up from 3132 before
this prompt), and the disclosed, honest scope boundaries.


## Recommendation governance & feedback (Prompt 18)
`recommendation/governance.py` (pure, mirrors `semantic.catalog`'s own
typed-vocabulary split) defines the eight-state lifecycle a *persisted*
recommendation instance moves through — `GENERATED` → optionally
`REVIEWED` → a quality verdict (`ACCEPTED`/`PARTIALLY_USEFUL`/
`REJECTED`/`INCORRECT`) → optionally `RESOLVED` (only from `ACCEPTED`/
`PARTIALLY_USEFUL`) — with `EXPIRED` reachable from any non-terminal
state. This gates nothing about whether a recommendation is *shown*
(Prompt 17's own evidence/confidence/authorization checks already
decided that); it exists purely to measure quality after the fact —
"recommendation quality can be measured and improved through controlled
feedback."

`identity.models.RecommendationRecord` (one immutable snapshot of a
Prompt-17 `Recommendation`, tenant-scoped, a mutable `status` column) +
`RecommendationFeedbackEvent` (an append-only audit log — `from_status`/
`to_status`, `actor_user_id` *or* `actor_label`, `reason`, and both
`recommendation_version`/`evidence_version` re-snapshotted onto every
single event, not just the parent record) persist what Prompt 17's
engine produces live: `POST /ask` (`api/recommendation_persistence.py`,
the identical fail-open/locally-authenticated-only contract
`api/chat_persistence.py` already established) writes one record per
`AskResponse.recommendations` entry. `identity/repositories
/recommendation_governance.py` enforces every transition against
`recommendation.governance.VALID_STATUS_TRANSITIONS` (defense in depth
behind the API's own pre-flight check) and is the sole place
`quality_metrics_for_tenant` reads feedback events back — a plain count
rollup, never anything that writes to `config.settings.Settings` or a
`recommendation.engine` rule.

`api/recommendation_governance.py` exposes seven routes under
`/recommendations` (list, get, get-events, feedback, resolve, expire,
metrics), each gated by `recommendation.governance_policy
.authorize_recommendation_action` — two new permissions,
`Permission.RECOMMENDATION_REVIEW` (analyst+: view/feedback/resolve) and
`Permission.RECOMMENDATION_MANAGE` (admin-only: expire), seeding
automatically via the existing idempotent `identity.bootstrap.seed_rbac`.
**There is no create/ingest route** — a record is only ever created by
the already-`Permission.ASK`-gated `/ask` pipeline itself.

**Invariants that must not regress:** no code path anywhere in this
codebase reads a `RecommendationFeedbackEvent` row and writes back to an
engine setting or rule threshold — "feedback must not automatically
alter production rules from a single event" holds structurally, not by
convention, since `quality_metrics_for_tenant` (plain counts) is the
only read path over that table. `resolved`/`expired` are unreachable
through the general `POST .../feedback` route (its own request schema
rejects them, 422, before ever reaching the repository) — each has its
own dedicated, more narrowly-permissioned route instead. Every action
beyond none is ABAC-gated on `security.tenancy.resolve_actor_tenant_id`
matching the record's own `tenant_id`; a cross-tenant or nonexistent
record is denied identically and mapped to the same 404
(anti-enumeration, matching `api/semantic_catalog.py`'s own precedent).

**Live-verified, not just against SQLite:** `alembic -c identity/
alembic.ini upgrade head` was run against the real, configured
PostgreSQL identity database for this pass — which also revealed and
applied three *prior* prompts' migrations that had never actually been
applied to this real database before, alongside this prompt's own new
`recommendation_records`/`recommendation_feedback_events` tables; both
new RBAC permission codes were then confirmed seeded via a direct
`identity.bootstrap.seed_rbac` run against that same database.

**Read [`18_RECOMMENDATION_GOVERNANCE_CONTRACT.md`](../../18_RECOMMENDATION_GOVERNANCE_CONTRACT.md)**
for the full inspection findings, every table/route's design, the
testing summary (3272/3272 backend tests passing, up from 3172 before
this prompt), and the disclosed, honest scope boundaries.


## SQL Server Query Store performance intelligence (Prompt 19)
`db/query_store.py` is a SQL-Server-only, fully optional module — a
no-op for every other `DB_TYPE` regardless of its settings, the same
provider-neutral posture `db/adapter.py::DatabaseCapabilities` and
`db.connection.check_write_privileges`'s own `checked: bool`/fail-open
contract already establish elsewhere in `db/`. `check_query_store_availability`
is the mandatory first call (`db_type != "mssql"`, the feature flag off,
a permission error, or Query Store simply not being enabled on the
target database all resolve to the identical `available=False`, never
an exception); `get_top_queries`/`get_regressions` then read SQL
Server's own Query Store DMVs (`sys.query_store_query`/`_query_text`/
`_plan`/`_runtime_stats`/`_runtime_stats_interval`) for high-cost/
repeated patterns and self-referential regressions (a query's recent
average duration vs. its own baseline), every DMV read going through
the existing `db.execution.execute_readonly_sql` — no new execution
path. A small, lock-protected, in-process TTL cache
(`get_cached_query_store_findings`, default 5-minute refresh) keeps the
live `/ask` pipeline's added cost "usually free," mirroring
`observability.metrics.PerformanceMetrics`'s own posture.

**Invariant that must not regress: literals never leave this module.**
`_mask_literals` parses every query's text with `sqlglot` and replaces
every literal with a placeholder *before* truncating it into a
`normalized_sql_preview` — the only text form `db/query_store.py` ever
constructs or returns; an unparseable query degrades to a fixed generic
placeholder, never a raw-text pass-through. `recommendation/engine.py`'s
two new rules (`QueryStoreHighCostRepeatedQueryRule`/
`QueryStoreRegressionRule`) feed `RecommendationInputs
.query_store_findings` into the **existing**
`RecommendationCategory.DATABASE_PERFORMANCE` — reusing Prompt 17's own
category, never a new one — and `agent.nodes
._query_store_findings_for_state` only calls into `db.query_store` at
all when the request's *selected* database's own `db_type == "mssql"`;
a postgres/mysql/oracle-routed question never reaches this module,
structurally, not just by convention (verified directly in
`tests/test_agent_nodes.py` via an assertion-raising mock). Reading
Query Store's DMVs needs the connected principal to hold the
database-scoped `VIEW DATABASE STATE`/`VIEW DATABASE PERFORMANCE STATE`
permission — an optional grant on top of this project's existing
read-only `DB_USER` role; without it, Query Store insights are simply
`available=False`, and every other feature keeps working unchanged.

**Read [`19_QUERY_STORE_PERFORMANCE_CONTRACT.md`](../../19_QUERY_STORE_PERFORMANCE_CONTRACT.md)**
for the full inspection findings, every DMV query's design, the testing
summary (3308/3308 backend tests passing, up from 3272 before this
prompt), and the disclosed, honest scope boundaries (most notably: no
live SQL Server instance with Query Store enabled was available to
verify the DMV queries against real data in this pass).


## Advanced hierarchical root-cause engine (Prompt 37)

`analytics/causal.py` upgrades Prompt 14's flat, single-level
`analytics.root_cause.investigate_root_cause` into a hierarchical,
evidence-driven investigation — `investigate_root_cause_advanced`
**reuses** that function verbatim as its per-level computation (never
reimplemented) and layers four capabilities on top: (1) per-contributor
statistical significance testing (a stdlib-only Welch-style two-sample
z-test over the raw underlying row values, gated by
`Settings.root_cause_min_sample_size`/`root_cause_significance_alpha`) —
a contributor whose raw dollar move looks large but whose sample is too
small or too noisy is reported as a **distractor**
(`ContributorEvidence.is_distractor`), never promoted to a claim; (2)
hierarchical drill-down (`HierarchicalRootCauseNode.children`) — only a
statistically supported contributor is drilled into at the next
dimension level, over the real subset of rows matching its own label,
bounded by `Settings.root_cause_max_hierarchy_depth`; (3) pairwise
interaction testing (`InteractionEffect`) for exactly two single-column
hierarchy levels, using the textbook two-way, unweighted-means
fixed-effects decomposition (`interaction = cell_contribution -
(row_mean(a) + col_mean(b) - grand_mean)`) over the full observed grid —
exactly `0` for a perfectly additive dataset, non-zero only for a genuine
synergy, gated by `Settings.root_cause_interaction_min_percent`; (4) a
structural correlation-never-causation guarantee —
`AdvancedRootCauseResult.causation_confirmed` is declared
`Literal[False]` (pydantic *and* mypy both reject `True`), `evidence_type`
is the deliberately singleton `CausalEvidenceStrength.CORRELATION_ONLY`,
and a fixed, non-LLM-authored `causation_disclaimer` is always present.

**Wired into the one existing live caller, nowhere new.** Prompt 34's
multi-agent supervisor's `root_cause_handler` gained one new branch: when
a caller's `RootCausePair` names an optional `dimension_hierarchy` (a
new, additive field defaulting to `None`) **and**
`Settings.enable_advanced_root_cause` is on, the handler calls the new
`root_cause_engine_advanced` tool instead of the pre-existing
`root_cause_engine` one; every other `RootCausePair` — including every
one already in this codebase's own tests — takes **exactly** the
pre-Prompt-37 code path, unchanged. Every claim the advanced path builds
carries its own inline correlation-not-causation caveat; a justified
interaction becomes its own claim; a distractor becomes a disclosed open
item, never a claim.

**Invariant that must not regress — still no live, automatic trigger**,
identical to Prompt 14's own disclosed gap: this application's pipeline
generates one SQL query per question, and nothing decides "this question
needs a second, baseline-comparison query" and issues one.
`investigate_root_cause_advanced` (like `investigate_root_cause` before
it) is reachable only through an explicit, caller-supplied
`RootCausePair` — `/analyst` still never populates `root_cause_pairs` at
all. New `retrieval.retriever.extract_governing_dimensions` (Prompt 10's
`extract_governing_metrics`, mirrored for `concept_type == "dimension"`)
is built and tested for a future caller to resolve a `dimension_hierarchy`
from governed catalog dimensions, but likewise has no live caller yet.

**Known, disclosed limitations:** interaction testing is pairwise-only
(a 3+-way interaction is out of scope, matching `RankingStat`'s own
flattened-composite-label precedent) and assumes a reasonably complete
observed `(label_a, label_b)` grid — a sparse/unbalanced grid makes the
unweighted row/column means a cruder approximation of each dimension's
"true" main effect. The significance test is a large-sample z-test
approximation, not an exact Student's t-test.

**Read [`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`](../../37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review — including
`tests/security/test_causal_never_asserts_causation.py`, a dedicated
cross-cutting regression proving (at both the type and text level) that
nothing this engine produces, at any layer, ever asserts a confirmed
cause.


## Governed What-If Scenario Engine (Prompt 38)

`analytics/scenario.py` is this platform's first what-if/scenario
capability — typed assumptions (`analytics.models.ScenarioParameter`:
`affected_metric`, `unit`, `change_type`
(`PERCENT_CHANGE`/`ABSOLUTE_DELTA`/`ABSOLUTE_VALUE`/`MULTIPLIER`),
`scenario_value`, optional `target_label`/`min_value`/`max_value`,
`model_version`) run against a real, already-fetched baseline
(`columns`/`rows`) without ever touching a database. `run_scenario`
reuses `analytics.engine.compute_analytics_result` on both the real
baseline and a brand-new, in-memory, proportionally-scaled copy of the
rows (the original `rows` argument is never mutated) — producing one
`ScenarioMetricComparison` per affected metric. `validate_scenario_parameters`
reports every parameter's own violations (unknown column, malformed/
out-of-bounds value, non-finite value, an unrealistic implied magnitude,
a restricted-column authorization failure, or missing baseline/
target-label data) — **one invalid parameter rejects the entire
scenario**, never a partial run with the bad one silently dropped.
Multiple parameters touching the same metric compose **sequentially, in
the order given** (a disclosed, bounded simplification, not a jointly-
solved system).

**Clearly separates observed facts, assumptions, and modeled estimates —
structurally, not conventionally.** A new, orthogonal
`analytics.models.ScenarioValueKind` enum (`OBSERVED`/`ASSUMPTION`/
`MODELED_ESTIMATE`) is distinct from `agent.provenance.DataTruthLevel`
(which still answers "how sure are we," unchanged); `ScenarioMetricComparison
.baseline_kind`/`.scenario_kind` are fixed `Literal`s pydantic itself
refuses to construct swapped — the identical `Literal[False]`-style
type-level guarantee Prompt 37's `causation_confirmed` already
established, applied to this engine's own observed-vs-estimate axis.

**Wired into the multi-agent supervisor the same way `root_cause` already
is**: a new `AgentName.SCENARIO` specialist, gated on an explicit,
caller-supplied `ScenarioRequest` (`agent/multiagent/supervisor.py`) —
the planner never creates one. Runs over one `sql_data` task's own
already-governed rows, never a second query. `caller_roles` is never
included in the handler's own tool-call payload; the registry injects
the real, authorized roles automatically.

**New, standalone REST surface, `api/scenarios.py`**: `POST
/scenarios/validate` and `POST /scenarios/run`. **Neither route ever
queries a database** — both take the baseline `columns`/`rows` directly
in the request body (the caller's own already-fetched, already-governed
result, e.g. from a prior `POST /execute`), which is the literal,
structural mechanism behind "run scenarios without changing source
data" at the API layer. Gated by `Settings.enable_what_if_scenarios`
(404 when off), `Permission.ASK`, rate limiting, and payload bounds
(`max_result_rows`/`scenario_max_parameters`).

**Invariant that must not regress:** a scenario's `baseline_value` is
always labeled `"observed"` and its `scenario_value` always
`"modeled_estimate"`, enforced by the pydantic type system itself, not a
runtime check a future edit could skip — locked in by
`tests/security/test_scenario_never_presents_estimate_as_fact.py`.

**Known, disclosed limitations:** no live, automatic trigger proposes a
scenario from a natural-language "what if" question yet — both the REST
routes and the multi-agent specialist require an explicit, already-
structured request. No persistence/governance/dashboard exists for a
scenario (unlike Prompt 18's recommendation governance) — a scenario is
a single, stateless computation per call. `analytics.scenario
.recommendations_for_scenario`/`analytics.visualization
.build_scenario_chart_spec` are built and tested but not wired into any
live recommendation/frontend path — the same "compute + test, defer the
deepest wiring" deferral this series has used repeatedly.

**Read [`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`](../../38_WHATIF_SCENARIO_ENGINE_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Governed Optimization Engine (Prompt 39)

`optimization/` (new top-level package, a peer of `analytics/`/
`recommendation/`, not a submodule of either) is this platform's first
decision-optimization capability — pricing, resource allocation,
inventory, workforce, capacity. `optimization.models.OptimizationProblem`
is the mission's own "decision variables, objective, constraints, rules,
source data and version" requirement as one typed, immutable value:
`DecisionVariable` (`CONTINUOUS`/`INTEGER`/`BINARY`, bounds, unit),
`Objective` (`MINIMIZE`/`MAXIMIZE`, linear terms, a constant),
`Constraint` (`<=`/`>=`/`==`, linear terms, an RHS), plus descriptive
`business_rules`/`source_data_description`/`source_columns` fields.
**"Never let an LLM invent constraints or objectives" is structural, not
a runtime filter**: `ObjectiveTerm`/`ConstraintTerm` (at both the engine
and REST layer) are `(variable, coefficient)` numeric pairs referencing
an already-declared variable by name — there is no field anywhere in
this contract a formula/expression string could occupy.

`optimization.engine.solve_optimization_problem` uses
`scipy.optimize.linprog` (HiGHS method) — already a genuine, pinned
direct dependency as of this prompt (was transitive via scikit-learn/
sentence-transformers; no new dependency was added) — as the single
solver entry point for both continuous LP and mixed-integer problems
(via `linprog`'s own `integrality` parameter). **Sensitivity
information (shadow prices/reduced costs) is exposed only when every
decision variable is `CONTINUOUS`** — a mixed-integer problem's duals
are not theoretically valid sensitivity information (a standard result
in optimization theory), so this engine never reports one regardless of
what the solver happens to return for the final LP relaxation. The
sign conventions converting scipy's own internal dual values back into
each constraint's own original shadow price were empirically verified
against six hand-computed textbook/worked examples, not merely derived
on paper (see `optimization.engine._shadow_price_sign`'s own docstring).

**Reject invalid or unauthorized assumptions, never partially trust
one**: `optimization.validator.validate_problem` reports every
violation found (undeclared variable references, malformed bounds, too
many variables/constraints, a restricted-column authorization failure)
— one violation rejects the *entire* problem, the identical "no partial
trust" convention `analytics.scenario.validate_scenario_parameters`
already establishes. `OptimizationStatus.UNAUTHORIZED` (a property of
who is asking) is kept structurally distinct from `.INFEASIBLE` (a
property of the math) and `.INVALID` (a property of the problem's own
structure) — never conflated.

**Require authorization and audit**: a new, dedicated
`agent.authz.Permission.RUN_OPTIMIZATION` (analyst tier+) — deliberately
not folded into `Permission.ASK`, since a solved optimization is a
suggested real-world decision, more consequential than a read-only
question. Every authorization denial is audit-logged
(`security.audit_log.log_security_event`, metadata only — never the
problem's own coefficients/values).

**Wired into the multi-agent supervisor the same way `scenario` already
is**: a new `AgentName.OPTIMIZATION` specialist, gated on an explicit
`OptimizationRequest` — the planner never creates one. Unlike
`ScenarioRequest`, it carries no `data_task`: a problem is always fully
caller-specified. **A real bug this prompt's own test suite found and
fixed**: the new tool was first registered at the `RUN_OPTIMIZATION`
permission tier *inside the multi-agent tool registry itself*, which
made an unauthorized caller get a bare, unexplained "tool not permitted"
denial instead of the engine's own richer, disclosed `UNAUTHORIZED`
status. Fixed by registering the tool at `Permission.ASK` (a pure
formality every caller already clears) and letting `validate_problem`'s
own internal check be the only place `RUN_OPTIMIZATION` is actually
enforced for this path — restoring the identical two-tier pattern
`scenario`/`root_cause` already use.

**New, standalone REST surface, `api/optimization.py`**: `POST
/optimization/validate` and `POST /optimization/solve`. **Neither route
ever queries a database or calls an LLM** — both take a fully-specified
problem directly in the request body. Gated by `Settings
.enable_optimization_engine` (404 when off), `Permission.RUN_OPTIMIZATION`,
rate limiting, and payload bounds (`optimization_max_variables`/
`optimization_max_constraints`).

**Invariant that must not regress:** `OptimizationSolution.truth_level`
is always `DataTruthLevel.AI_INFERENCE`, across every outcome (optimal,
infeasible, invalid, unauthorized) — never promoted to `DATABASE_FACT`/
`CONFIRMED_BUSINESS_TRUTH`. Locked in by
`tests/security/test_optimization_never_presents_recommendation_as_fact.py`.

**Known, disclosed limitations:** `business_rules` is descriptive
provenance only in this pass, not a separately enforced or reusable
constraint-template registry; no automatic integration pulls real
baseline data into a problem's own coefficients (unlike `ScenarioRequest`,
which does read a `data_task`'s rows); no IIS computation for infeasible
problems; no nonlinear/quadratic objective support (LP/MILP only,
matching `scipy.optimize.linprog`'s own scope); no persistence/
governance/dashboard for a solved optimization.

**Read [`39_OPTIMIZATION_ENGINE_CONTRACT.md`](../../39_OPTIMIZATION_ENGINE_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Governed Predictive Analytics engine (Prompt 40)

`analytics/predictive.py` expands `analytics.forecasting` (time-series
projection) into general-purpose, governed regression and
classification — demand, churn, risk. A sibling module within the
existing `analytics/` package (not a new top-level package, unlike
`optimization/`), since this is explicitly "expand forecasting," not a
structurally new problem domain. `train_predictive_model` fits one of a
small, closed, reviewed model catalog
(`analytics.models.PredictiveModelType.LINEAR_REGRESSION`/
`LOGISTIC_REGRESSION`) — never an arbitrary class name a caller (or an
LLM) could inject — over a caller-supplied dataset, never touching a
database or calling an LLM itself.

**Every governance control the mission names, each independently
tested**: data sufficiency (`Settings.predictive_min_training_rows`/
`predictive_min_rows_per_feature`/`predictive_min_rows_per_class`);
leakage checks (`_detect_leakage` — an identical or highly-correlated
feature is rejected by default, `Settings
.predictive_reject_on_leakage`, always disclosed either way);
train/test separation (every metric computed only on a held-out split);
feature validation (numeric-only in this engine version, a disclosed
scope boundary); versioning (`PredictiveModelMetadata`, including a
`data_fingerprint`/`model_fingerprint` pair, `sha256`-hashed the
identical way `db.schema_introspection.get_schema_fingerprint` already
hashes a schema); and authorization (a restricted feature/target column
is rejected unless the caller holds `Permission
.VIEW_RESTRICTED_COLUMNS`, the same independently re-derived pattern
`analytics.scenario`/`optimization.validator` already establish).

**"Test reproducibility" is structural, not incidental**: the train/test
split is a deterministic, hash-based ordering
(`sha256(f"{seed}:{index}")`), never Python's or numpy's own global
random state — the same data and seed always produce byte-identical
fingerprints, coefficients, and metrics, verified directly.

**Uncertainty is exposed per task type**: a regression prediction
carries a residual-based prediction interval (the same z-based
convention `analytics.forecasting.ForecastPoint` already establishes); a
classification prediction carries the full per-class probability
distribution.

**"Store model/data fingerprints, features and metrics" is satisfied via
the typed result object plus an audit-log entry
(`security.audit_log.log_security_event`, `"predictive_model_trained"`,
metadata/fingerprints only, never raw data or coefficients) — a
deliberate, disclosed choice, not a new database table.** This engine
(like every other one in this series) has no persisted model registry in
this pass; every call is self-contained (train and, optionally, predict
happen together, in one deterministic, stateless invocation).

**`analytics.predictive.detect_feature_drift`** — a standalone,
two-dataset comparison (two-sample Kolmogorov-Smirnov test,
`scipy.stats.ks_2samp`, already a direct dependency as of Prompt 39) per
feature, flagged at `Settings.predictive_drift_p_value_threshold`.

**Wired into the multi-agent supervisor the same way `scenario` already
is**: a new `AgentName.PREDICTIVE` specialist, gated on an explicit
`PredictiveRequest` (carries a `data_task`, like `ScenarioRequest`,
unlike the self-contained `OptimizationRequest`) — the planner never
creates one. Registered at `Permission.ASK` at the registry level (the
real, specific authorization check happens inside the engine itself) --
this prompt reused Prompt 39's own found-and-fixed two-tier pattern from
the start, rather than rediscovering the same bug.

**New, standalone REST surface, `api/predictive.py`**: `POST
/predictive/train` and `POST /predictive/drift`. **Neither route ever
queries a database or calls an LLM.** Gated by `Settings
.enable_predictive_analytics` (404 when off), `Permission.ASK`, rate
limiting, and payload bounds.

**Invariant that must not regress:** `PredictiveModelResult.truth_level`
is always `DataTruthLevel.AI_INFERENCE`, across every outcome (trained
regression/classification, insufficient data, invalid, unauthorized,
leakage detected) — never promoted to `DATABASE_FACT`/
`CONFIRMED_BUSINESS_TRUTH`. Locked in by
`tests/security/test_predictive_never_presents_prediction_as_fact.py`.

**Known, disclosed limitations:** no persisted model registry (every
call trains fresh); no categorical feature encoding (numeric features
only); no automatic integration feeding a predictive model's own
coefficients into `analytics.scenario`/`optimization.engine`;
multi-class classification discloses only the first class's coefficient
vector; no nonlinear model support.

**Read [`40_PREDICTIVE_ANALYTICS_CONTRACT.md`](../../40_PREDICTIVE_ANALYTICS_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Data Quality Command Center (Prompt 41)

`data_quality/` (new top-level package, a peer of `analytics/`/
`optimization/`/`recommendation/`) covers completeness, validity,
uniqueness, consistency, referential integrity, freshness, and
distribution stability. `data_quality.engine.assess_data_quality` is a
pure function over already-fetched table data (`TableQualityInput.columns`/
`.rows` per table): it never queries a database and never calls an LLM —
the identical "neither route ever queries a database" posture
`analytics.scenario`/`optimization`/`analytics.predictive` already
establish. **Reused, not reimplemented**: `analytics.engine
.compute_analytics_result` supplies completeness (null counts) and
validity-outlier findings (z-score/IQR); `analytics.predictive
.detect_feature_drift` supplies distribution-stability in full (a
two-sample Kolmogorov-Smirnov test) — this package only re-tags its
typed result.

**The one deliberate departure from every sibling engine in this
series**: `DataQualityFinding.claim.level`/`DataQualityReport.truth_level`
are always `DataTruthLevel.DATABASE_FACT`, never `AI_INFERENCE` — unlike
`analytics.scenario`/`optimization`/`analytics.predictive`/
`analytics.causal` (all `AI_INFERENCE` ceilings), a null rate, a
duplicate key, an orphaned foreign-key value, an out-of-range date, an
unexpected category, and a stale timestamp are not predictions or
inferences about something not yet observed — they are deterministic
measurements of real, already-fetched rows, the identical posture
`analytics.engine.AnalyticsResult` already establishes for its own,
narrower statistics.

**Seven dimensions, each independently gated on a table actually
declaring a relevant check** (a dimension with zero configured checks is
never assessed — `DimensionScore.score` is `None`, not a false `100`):
COMPLETENESS (null-rate threshold, configurable per column), VALIDITY
(`DateColumnRule`/`CategoricalColumnRule`, plus the reused outliers),
UNIQUENESS (`unique_key_sets` — composite-key duplicate detection),
CONSISTENCY (`ConsistencyRule` — a typed `left OP right` cross-column
comparison, never a formula string an LLM could author, the identical
structural guarantee `optimization.models`/`analytics.scenario.models`
already establish), REFERENTIAL_INTEGRITY (`ReferentialCheck` — a child
value absent from the caller-supplied `parent_key_values` is an orphan),
FRESHNESS (`FreshnessRule` — the table's own most recent timestamp
compared against a caller-supplied `as_of`, never the server's wall
clock), and DISTRIBUTION_STABILITY (reuses `detect_feature_drift` against
a caller-supplied reference dataset).

**Authorization**: a finding whose column `config.sensitive_columns`
classifies "restricted" is suppressed outright for a caller lacking
`Permission.VIEW_RESTRICTED_COLUMNS` — never partially redacted, the
identical posture `recommendation.engine`'s own restricted-column gate
already establishes. Unlike `optimization.validator`/`analytics.predictive`
(which reject the *whole* problem/model for touching a restricted
column), a data-quality scan never rejects the whole request — only the
specific restricted finding is withheld, disclosed via
`DataQualityReport.insufficient_checks` and audit-logged.

**Lineage** (`data_quality/lineage.py`) connects a finding to affected
governed metrics/dashboards by *table* name only, never by column —
`identity.models.SemanticCatalogEntry.source_tables` is a real,
persisted field; the catalog schema has no `source_columns` field at
all (confirmed by inspection), so column-level lineage would imply a
precision this codebase's own catalog schema cannot back. A finding is
never presented as proof a governed metric IS wrong — only that the
metric *potentially* reads a table with an open finding (exposure, not
a verdict). Only a `published` catalog entry is ever linked (a `draft`/
`reviewed` one is silently excluded, defense in depth). `build_lineage_report`
is a pure function over a lightweight per-table severity-count summary
(`TableFindingsSummary`), not the full report — a caller never needs to
re-transmit raw finding detail already returned by a prior scan.

**New, standalone REST surface, `api/data_quality.py`**: `POST
/data-quality/scan` (never touches a database or calls an LLM — the
dataset(s) are taken directly in the request body, `Permission.ASK`) and
`POST /data-quality/lineage` (the one route in this feature that *does*
touch a database — it reads the tenant's own published semantic-catalog
entries via `identity.repositories.semantic_catalog.list_entries`,
gated by `require_local_user`). Both gated by `Settings
.enable_data_quality_engine` (404 when off), rate limiting, and payload
bounds (`data_quality_max_tables`/`data_quality_max_rows_per_table`).

**Multi-agent wiring**: a new `AgentName.DATA_QUALITY` specialist, gated
on an explicit, caller-supplied `DataQualityRequest` (carries a
`data_task`, like `ScenarioRequest`/`PredictiveRequest`) — the planner
never creates one. Scoped to the single-table checks (completeness,
validity, uniqueness, consistency) only — referential-integrity,
freshness, and distribution-stability each need a second dataset this
specialist does not have, the identical disclosed scope boundary
`RootCausePair`'s own plain `investigate_root_cause` path has for the
live graph. Its `AgentSpec.max_truth_level` is `DataTruthLevel
.DATABASE_FACT` — the only engine specialist in this series whose
ceiling is not `AI_INFERENCE`.

**Invariant that must not regress:** every `DataQualityFinding`/
`DataQualityReport.truth_level` stays `DataTruthLevel.DATABASE_FACT`,
and `data_quality.lineage` never asserts a governed metric IS broken —
only that it is potentially affected. Locked in by
`tests/security/test_data_quality_findings_stay_observed_fact.py`.

**Known, disclosed limitations:** lineage is table-level only (no
column-level catalog field exists to back a finer claim); the
multi-agent specialist covers only the four single-dataset dimensions;
no persisted scan history/dashboard exists (every call is self-contained,
the same "engine first, persistence/governance later, if ever" posture
Prompts 17/18/39/40 already established); no automatic trigger proposes
a scan from a natural-language question — both REST routes require an
explicit, already-structured request.

**Read [`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`](../../41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Data Lineage Platform (Prompt 42)

`lineage/` (new top-level package, a peer of `analytics/`/
`optimization/`/`data_quality/`) is end-to-end lineage from source
database objects through columns, governed metrics, executed SQL, to
visualizations/insights/recommendations. Deterministic parsing is
preferred over LLM guesses throughout: `lineage/sql_lineage.py`'s
`extract_column_lineage` is a sqlglot-based parse of one top-level
`SELECT` (no recursion into a `FROM`-nested subquery; a CTE's own name
is excluded from `tables_read` and never traced into its base tables —
disclosed scope boundaries, never a silent wrong guess), reused by
`lineage/catalog_lineage.py` to recover **column-level** lineage for a
governed metric's own `approved_expression` wherever it declares exactly
one source table and the expression parses cleanly — a real improvement
over Prompt 41's `data_quality/lineage.py`, which stayed table-level
only because the catalog schema itself has no `source_columns` field
(still the fallback here when column-level resolution isn't possible).
`lineage/schema_lineage.py` builds the `DATABASE_FACT` schema layer
(declared FKs) plus optional `AI_INFERENCE` candidate-relationship edges
(`db.relationship_inference.InferredRelationship`, reused verbatim,
including its own `truth_level`); `lineage/presentation_lineage.py`
connects an already-produced query/visualization/insight/recommendation
tail (never recomputing or calling an LLM itself — a plain, primitive-
shaped description of an object the caller already has).

**One uniform edge-direction convention makes a single trace function
correct across the whole mission-named chain**: `lineage/graph.py`'s
every edge has `source_id` *depend on* `target_id` (a query `READS` a
table, a metric is `GOVERNED_BY` a column, a visualization `VISUALIZES`
a query). `trace_lineage(graph, node_id, direction, max_depth)` is a
breadth-first, cycle-safe, depth-bounded walk — **upstream** follows a
node's own outgoing edges ("where did this KPI's number come from");
**downstream** follows its own incoming edges ("what breaks if this
table/column changes" — impact analysis). `lineage/staleness.py
.detect_staleness` is a pure comparison (never reads a wall clock or
re-fetches anything itself) between a graph's own recorded
`schema_fingerprint`/`catalog_versions` (the literal versioning
requirement — reuses `db.schema_introspection.get_schema_fingerprint`
verbatim) and caller-supplied current live values.

**New, standalone REST surface, `api/lineage.py`**: `POST /lineage/graph`
(requires a local account, like `data_quality`'s own `/lineage` route —
live-introspects the caller's tenant-scoped database and reads that
tenant's own *published* catalog entries, the identical
`GET /schema/tables`-shaped tenant-scoping + anti-enumeration-404
convention), `POST /lineage/trace` (pure, `Permission.ASK` only — never
touches a database, operates over a graph the caller already has),
`POST /lineage/stale-check` (requires a local account; re-fingerprints
live and compares). Gated by `Settings.enable_lineage_platform` (404
when off), rate limiting, and payload bounds
(`lineage_max_queries_per_request`/`lineage_max_trace_graph_nodes`/
`lineage_max_trace_depth`).

**Authorization is node suppression, not value redaction** — a `COLUMN`
node naming a column `config.sensitive_columns` classifies "restricted"
is removed from the graph entirely (never kept with a placeholder
value) for a caller lacking `Permission.VIEW_RESTRICTED_COLUMNS`, since
a lineage graph can disclose a restricted column's *existence/name*
even without ever disclosing a *value* — `LineageGraph
.suppressed_restricted_nodes` discloses what was removed, never a
silent omission.

**Multi-agent wiring**: a new `AgentName.LINEAGE` specialist, gated on
an explicit, **self-contained** `LineageRequest` (sql + optional
dialect, no `data_task` — `DataSlot` does not carry the SQL text that
produced its rows, confirmed by inspection, so there is no governed-rows
slot a `data_task` could point at here; the identical shape
`OptimizationRequest` already establishes for the same reason). Its
`AgentSpec.max_truth_level` is `DataTruthLevel.DATABASE_FACT` — a
deterministic parse of SQL text is an observed fact about that text's
own structure, the identical reasoning `DATA_QUALITY`'s own spec gives.
`TOOL_LINEAGE` is registered at `Permission.ASK` (the two-tier pattern
`TOOL_DATA_QUALITY`/`TOOL_OPTIMIZATION` already establish, reused from
the start rather than rediscovering the Prompt-39 bug).

**Frontend**: `frontend/src/pages/DataLineage.tsx` is a lean, read-only
explorer (database selector, "Build graph," nodes grouped by type with
`TruthLevelBadge` reused, a trace panel) — deliberately a plain grouped-
list view, not a graph-visualization library, matching this app's own
conservatism against adding a new rendering library for a chart/graph
type. Wired through `security/navigation.py` (a new `data_lineage`
`NavItem`, `agent_any={ASK}` — hiding the tab is UX only; the route's
own `require_local_user` is the real enforcement), `App.tsx`, and all 5
locale files' nav label.

**Invariant that must not regress:** every node/edge's `DataTruthLevel`
stays correct even reached transitively through a multi-hop trace — a
`draft`/`reviewed` catalog entry or an inferred relationship is always
`AI_INFERENCE`, a `published` entry's own edges are
`CONFIRMED_BUSINESS_TRUTH`, and an LLM-narrated insight or a
recommendation is always `AI_INFERENCE` regardless of how many hops
`trace_lineage` took to reach it. Locked in by
`tests/security/test_lineage_kpi_end_to_end_and_tenant_isolation.py`,
which also proves, against a real identity database with real tenants/
users/published rows, that one tenant's published metric never appears
in another tenant's lineage graph for the same shared database
connection.

**Known, disclosed limitations:** column-level lineage is single-
statement/outermost-`SELECT`-only (no subquery recursion, a CTE is
opaque); a composite foreign key is table-level only in this graph;
`/lineage/graph`'s query/presentation layer is entirely caller-supplied
per call — nothing in this platform auto-captures every live `/ask`/
`/execute` call's own lineage into a persisted history yet (the same
"engine first, persistence later, if ever" posture Prompts 38-41 already
established); `/lineage/trace` requires the caller to already hold the
graph from a prior `/graph` call (no server-side persisted graph store
by id).

**Read [`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`](../../42_DATA_LINEAGE_PLATFORM_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Schema Drift Impact Analysis (Prompt 43)

`schema_drift/` (new top-level package) is continuous schema-drift
detection on top of Prompt 42's lineage platform: `schema_drift.diff
.compare_schemas` is a pure, deterministic comparison between two
already-introspected `db.schema_introspection.TableSchemaInfo` snapshots
(the last one captured for a database vs. a fresh live introspection),
reporting every added/removed column, type/nullability change, primary-
key/foreign-key/unique-constraint change, and table/view change (plus a
table-flipping-to-a-view `OBJECT_TYPE_CHANGED` case) as a typed
`SchemaDriftFact`. A bounded, same-table, same-type/nullability,
name-similarity heuristic (`difflib.SequenceMatcher`,
`Settings.schema_drift_rename_similarity_threshold`) additionally offers
a `COLUMN_RENAMED` candidate — additive, never a replacement for the two
underlying `COLUMN_ADDED`/`COLUMN_REMOVED` facts (master rule 10: an
inference never stands in for an observed fact).

**Heavy, deliberate reuse rather than a second governance/persistence
layer — the mission's own first mandatory control ("inspect and reuse
existing implementation before adding code") applied literally:**
`schema_drift.findings.fact_to_finding` wraps each `SchemaDriftFact` into
`semantic.intelligence.detect.Finding` (Prompt 35's own domain-agnostic
review-candidate shape — kind/key/title/detail/subjects/evidence/
confidence/truth_level/risk/payload), new `kind` values prefixed
`schema_drift_` so they're never confused with a catalog-detector
finding sharing the same table. `identity.models.SemanticFinding` +
`identity.repositories.semantic_intelligence` (`upsert_findings`/
`list_findings`/`get_finding`/`decide_finding`/`rollback_finding`) persist
and govern them completely unmodified — the exact same tenant-scoped,
versioned (append-only `history`), accept/dismiss/rollback review-queue
lifecycle Prompt 35 already built, which is already the "approval-aware
remediation" shape this prompt's own mission calls for.
`semantic.intelligence.risk.score`'s `_BASE` dict gained thirteen new,
disclosed entries (one per `DriftChangeKind`, weighted by real
operational severity — a removal/key-change scores comparably to a
catalog conflict; an additive change scores low) so the exact same risk
formula (base + published-dependent bonus + dependent-count bonus +
confidence gap, high/medium/low tiers) prioritizes a drift finding too —
no second scoring engine.

**"Map impact to metrics, semantic definitions, golden questions,
dashboards, queries and recommendations through lineage" is genuine
reuse of Prompt 42's own `lineage.graph.trace_lineage`, never a second
impact-analysis implementation.** `schema_drift.impact.assess_impact`
resolves each finding's own table/column into a `lineage.schema_lineage`
node id and traces **downstream** (what depends on it) over a
`lineage.graph.build_lineage_graph` result built from the *previous*
schema snapshot (so "what used to depend on the thing that just
changed" is answerable even for a since-removed column) — a newly
`ADDED` finding correctly gets zero impact, since nothing could have
depended on a column that didn't exist yet. A `CONFIRMED_BUSINESS_TRUTH`
node reached this way (a *published* governed metric/dimension) bumps
`Finding.published_count`, which `risk.score` already knows how to
weight — impact and risk compose through the same two pre-existing
mechanisms, not a new one.

**"Support approval-aware remediation and metadata rollback" is the
existing `POST /semantic-intelligence/entries/{entry_id}/rollback`
route** (`identity.repositories.semantic_intelligence
.rollback_catalog_entry`, also Prompt 35) — creates a brand-new `DRAFT`
from an older published version's own content; the published row is
never touched, and the draft still needs the catalog's normal
review-and-publish approval before it goes live.
`schema_drift.impact`'s own `payload["impacted"]` tells an operator
*which* catalog entry/concept_key/version a drift finding threatens;
acting on that is always a separate, human-initiated call to that
already-built, already-tested route — `schema_drift/` itself contains no
call site that creates, edits, publishes, or rolls back a catalog entry
(locked in structurally, not just by convention, by
`tests/security/test_schema_drift_never_rewrites_catalog.py`, which
parses every module's own source and asserts no such name is ever
referenced). Reviewing a schema-drift finding (accept/dismiss) or
restoring an earlier version of *it* likewise reuses the existing
`POST /semantic-intelligence/findings/{id}/decision`/`.../rollback`
routes unmodified — `api/schema_drift.py`'s own surface is deliberately
narrow: `POST /schema-drift/scan` (genuinely new behavior) and
`GET /schema-drift/findings`(`/{id}`) (a kind-scoped, read-only
convenience view over the same shared `SemanticFinding` table Prompt
35's own, unscoped `/semantic-intelligence/findings` already exposes).

**Snapshot capture (`schema_drift.snapshot_store`) is the one genuinely
new persistence mechanism** — nothing else in this codebase stores a
full column-level historical schema snapshot (`embeddings
.schema_indexer`'s own manifest only stores a per-table *fingerprint*,
not the column/type/nullability/key detail a real diff needs). A small
per-database JSON file alongside the Chroma persist directory
(`.schema_drift_snapshot__<db_name>.json`), mirroring that manifest's
own naming convention — not a new identity-DB table, since this is an
operational cache of the last-seen shape, not governed content. Fails
open on a missing/corrupt file (treated as "no previous snapshot,"
i.e. a first scan, where every table legitimately reports as
`TABLE_ADDED`/`VIEW_ADDED` — the identical precedent `embeddings
.schema_indexer.SchemaDiscoveryDiff` already establishes for table-level
discovery).

`POST /schema-drift/scan` (requires a local account, the same
requirement `POST /lineage/graph` already has, since both read
tenant-scoped catalog data) live-introspects the caller's own
tenant-scoped database, loads the previous snapshot, diffs, builds the
previous-schema lineage graph from this tenant's own *published* catalog
entries, assesses impact, persists findings (`Settings
.enable_schema_drift_detection`, default on — a pure, deterministic,
zero-cost-until-triggered structural diff, the same "safe to default on"
posture `enable_data_quality_engine`/`enable_lineage_platform` already
establish, unlike the LLM-adjacent `enable_semantic_intelligence`, off by
default), and saves the current snapshot for next time.
`scripts/run_schema_drift_scan.py` is a standalone, no-authentication CLI
for cron/Task Scheduler alerting (exits `2` if any finding is
`risk_tier == "high"`) — the literal "continuous detection"/"generate
actionable alerts" mechanism at the operator level, since this
application has no built-in background scheduler (the same disclosed
"no background worker" limitation `onboarding/`'s own docstring already
names). It shares `schema_drift.snapshot_store`'s same on-disk baseline
with the API route (alternating between the two never desyncs it) but
deliberately skips impact assessment and governed persistence (both need
a real local-account actor/tenant) — `schema_drift.reports
.render_markdown_report` is the one rendering both the CLI's stdout and
`ScanOut.report_markdown` use, so there is exactly one report format,
not an API-only and a CLI-only copy.

**Known, disclosed limitations:** no automatic trigger exists — a scan
only runs when `POST /schema-drift/scan` or the CLI script is actually
called (by an operator, a cron entry, or a future scheduled job this
prompt does not build); impact assessment only considers declared
foreign keys and published catalog entries, never `db
.relationship_inference`'s own lower-confidence candidates (keeps a
scan's own impact claims at `DATABASE_FACT`/`CONFIRMED_BUSINESS_TRUTH`,
never a second inference layered under this one); "golden questions" and
"dashboards" named in the mission are not separately, structurally
traced — a golden question has no lineage node type in Prompt 42's own
chain, and a dashboard is this application's own chart specs (`lineage
.presentation_lineage`'s `VISUALIZATION` nodes), reachable only for a
query the caller explicitly supplies to `/lineage/graph`, which this
prompt's own impact assessment does not do (it builds a schema+catalog-
only graph, not a query-history one) — disclosed rather than silently
narrowed; the column-rename heuristic is a bounded, greedy, same-table
pairing, never a cross-table or cross-type fuzzy match.

**Read [`43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md`](../../43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Proactive KPI monitoring (Prompt 50)

`kpi_monitoring/` (new top-level package, a peer of `analytics/`/
`data_quality/`/`lineage/`/`schema_drift/`/`optimization/`/
`model_routing/`) watches **governed** business metrics for meaningful
movement, investigates what moved, suppresses repeats and noise, and
ranks what is left by impact.

Almost entirely composition of things that already existed. Detection is
`analytics.anomaly.detect_anomalies` (Prompt 14) verbatim; attribution is
`analytics.root_cause.investigate_root_cause` (Prompt 14) verbatim;
persistence, content versioning, duplicate suppression and the
accept/dismiss/rollback lifecycle are Prompt 35's
`identity.models.SemanticFinding` + `identity.repositories
.semantic_intelligence` **unmodified** -- monitoring is the third
consumer of that one review queue (after the catalog detectors and
Prompt 43's schema drift), not a third place to look for open items.
Ranking is `semantic.intelligence.risk.score`, extended additively.

What genuinely did not exist, and is new here: monitoring policies over
approved metrics, business calendars, quiet periods, per-recipient alert
preferences, and the orchestration tying it together.

**Invariants that must not regress:**
- **Only a *published* catalog metric can be monitored.** A policy naming
  a draft metric, a dimension/entity, or an unknown key is refused --
  with three distinguishable codes, because they need different fixes.
  A KPI alert asserts that a governed business number moved, so the
  number has to be a governed business number first (Prompt 09/10's
  approval workflow decides that; monitoring reuses the decision).
- **Monitoring structurally cannot produce a row.** No module in
  `kpi_monitoring/` imports `agent.sql_validator`, `agent.graph`,
  `agent.nodes`, `agent.llm_client`, `db.execution`, `db.connection`,
  `db.schema_introspection`, `sqlalchemy`, `identity.models` or
  `identity.db`; none imports an LLM client; and none contains a
  `SELECT ... FROM` string literal -- all asserted by parsing the real
  source in `tests/security/test_kpi_monitoring_never_asserts_cause.py`.
  Series and breakdowns arrive from the caller, already fetched through
  the governed path. That is what makes "cannot bypass the deterministic
  SQL controls" structural rather than conventional.
- **It never asserts a cause.** A contributor is a contribution to an
  arithmetic total. Seven causal phrasings are asserted absent from the
  finding title, the detail, the report and every user-facing string in
  the package. The **title never names an attribution at all** -- a
  reviewer scanning a list would read one there as established long
  before opening the evidence.
- **No attribution without evidence, enforced by the type.**
  `ChangeEvidence`'s model validator refuses contributors or confidence
  when `has_sufficient_evidence` is `False`, and refuses insufficient
  evidence carrying no limitation. The wrong combination is
  unrepresentable, not merely discouraged.
- **Suppression governs alerting, never evidence.** Every finding is
  persisted and appears in the review queue regardless; suppression
  decides only whether a human is told *now*. Reasons accumulate rather
  than short-circuiting, because "why didn't I get paged" needs the
  whole answer.
- **Duplicate suppression has two independent halves, and both are
  needed.** Storage-level: the stable `finding_key` (policy + metric +
  **period**) plus a content hash, so an unchanged re-run records
  `unchanged` and a restatement versions the *same* row rather than
  adding a second. Alert-level: a finding already open and undecided is
  not re-paged. Keying on policy+metric alone would collapse every
  period into one row and destroy the history.
- **A business calendar does two separate things on purpose**: excludes
  non-business periods from the **baseline** (so a holiday never poisons
  a trailing average and manufactures a finding in the *next* period)
  *and* refuses to alert on one. Doing only one leaves a real hole
  either way.
- **Period labels are opaque strings** -- never parsed as dates, the same
  boundary `analytics.engine.detect_missing_periods` already draws.
- **Tenant isolation**: findings, approval and duplicate suppression are
  all per tenant, proven against a real identity database.
- `semantic.intelligence.risk.score` gained an optional `impact_bonus`
  defaulting to `0`, so every pre-existing caller scores identically.
  Kind alone ranks a catalog conflict correctly but not a KPI, where two
  drops of the same kind differ by orders of magnitude.

**Known, disclosed limitations:** **no delivery channel exists** --
nothing is emailed, posted or messaged; a run returns a typed
`AlertBundle` describing what *would* be sent. No scheduler exists
either; `scripts/run_kpi_monitor.py` is the cron/Task Scheduler entry
point (exit `2` on a high-risk finding), and it cannot persist a governed
record because it has no actor or tenant. **The caller supplies the
series** -- monitoring does not fetch a metric from its own
`approved_expression`, which would mean generating SQL it deliberately
cannot generate. Attribution is single-level (`analytics.causal`'s
hierarchical engine is not wired in -- a policy cannot yet express a
dimension hierarchy). No frontend.

**A latent bug found in the shared storage and reported, not fixed:**
`identity.models.SemanticFinding.kind` is `String(32)`, but Prompt 43
already stores `schema_drift_column_nullability_changed` (39 chars) and
`schema_drift_unique_constraint_changed` (38). SQLite ignores a VARCHAR
length so every test passes; PostgreSQL -- the real identity database --
would reject those writes. Prompt 50's own four kinds all fit and
`kpi_monitoring.findings._assert_kinds_fit` fails at import if a future
edit breaks that, so this feature cannot reintroduce the problem.

**Read [`50_PROACTIVE_AI_ANALYST_CONTRACT.md`](../../50_PROACTIVE_AI_ANALYST_CONTRACT.md)**
for the full inspection findings, every module's design, the 110-test
testing summary, the three real bugs the tests caught, and the
security/tenant-isolation/performance review.


## Executive AI briefing (Prompt 51)

`briefing/` (new top-level package, a peer of `kpi_monitoring/`/
`data_quality/`/`schema_drift/`/`recommendation/`) assembles an
evidence-linked daily, weekly or monthly executive briefing from what
those subsystems already recorded: KPI movement, significant anomalies,
drivers, risks, opportunities, unresolved recommendations and
data-quality issues. **It detects, computes and ranks nothing of its
own** -- KPI movement, anomaly flags and dimension attribution are read
back verbatim from `kpi_monitoring`'s own persisted
`finding_payload` in Prompt 35's shared review queue; downstream impact
comes from `schema_drift.impact`'s already-resolved impacted-concept
list; unresolved recommendations come from `identity.models
.RecommendationRecord` with its own `engine_version`/`evidence_version`
snapshots; ranking reuses the score `semantic.intelligence.risk.score`
already assigned; and number grounding reuses `agent.insight
.extract_numbers` verbatim.

**Two orthogonal classifications, deliberately not merged.**
`agent.provenance.DataTruthLevel` keeps answering *how* something came
to be known; a new `briefing.models.ClaimClass` answers *what kind of
statement a reader is looking at* -- `OBSERVED_FACT` (arithmetic over
governed query results), `ESTIMATE` (this platform's own inference about
those numbers), `RECOMMENDATION` (a suggestion awaiting a decision) --
the same orthogonality `analytics.models.ScenarioValueKind` already
established against the same enum. One record legitimately produces
several claims in different sections, because they are different kinds
of statement about the same evidence: a revenue drop yields an
`OBSERVED_FACT` movement, an `ESTIMATE` anomaly, an `ESTIMATE` risk, and
an `ESTIMATE` driver per contributor.

**Invariants that must not regress:**
- **Every claim links to deterministic evidence and versions, enforced
  twice.** `BriefingItem.evidence` is `Field(min_length=1)` and
  `EvidenceLink` refuses a link recording no version, so a claim with
  nothing behind it is unrepresentable; on top of that,
  `briefing.claims.finalize` is the **only** `BriefingItem` constructor
  call in the package (pinned by a test that parses the real source) and
  `briefing.engine` runs every candidate through
  `find_unsupported_claims` first -- the same two-layer shape
  `recommendation.engine._finalize_candidate` establishes for its own
  evidence rule. Four withholding codes: `no_evidence`,
  `missing_version`, `truth_level_mismatch`, `ungrounded_number`.
- **No briefing claim is ever `CONFIRMED_BUSINESS_TRUTH`**, for any
  claim class -- pydantic refuses it. A briefing reports what other
  governed subsystems recorded; confirming anything stays an explicit
  human action elsewhere (publishing a catalog entry, accepting a
  finding, resolving a recommendation).
- **Trusted source text may be quoted; caller-supplied text must be
  verified.** A KPI/schema-drift finding and a recommendation record are
  rows this server's own engines wrote through authorized paths, so
  their wording goes into `BriefingItem.grounding_context` and is quoted
  as-is. A data-quality finding arrives in the **request body** (nothing
  in this codebase persists a data-quality scan), which makes it
  untrusted input: its text is *not* self-grounding, so every number it
  states must also appear in the `observed` aggregates the same request
  supplies. Found by a failing test, not a design review -- the first
  implementation verified one section's variant of the same finding and
  let the other self-certify.
- **Staleness is judged against a caller-supplied `as_of`, never a clock
  read** (the identical reason `data_quality.models.FreshnessRule.as_of`
  is caller-supplied), with three budgets, one per cadence -- the same
  record is stale in a daily briefing and fresh in a monthly one. **A
  stale item is marked, ranked below fresh items of equal risk, and
  listed in `stale_inputs` -- never dropped**, since hiding a week-old
  finding would turn a freshness problem into an apparent absence of
  problems. A record with no timestamp is disclosed, never assumed
  fresh.
- **An empty briefing is never silently empty.** `SourceCoverage`
  distinguishes three cases per source -- not supplied ("nothing was
  checked"), supplied but quiet ("none produced a claim"), and omitted
  for a stated reason (most importantly: the caller may not read that
  source) -- which is `kpi_monitoring.models.MonitorResult
  .series_skipped`'s distinction applied one level up.
- **Authorization is per section, not all-or-nothing, and adds no new
  permission.** KPI/schema-drift findings answer to
  `semantic.catalog_policy.CatalogAction.REVIEW` (the same gate
  `api/kpi_monitoring.py`/`api/schema_drift.py` apply to the same rows);
  recommendation records answer to `recommendation.governance_policy
  .RecommendationAction.VIEW` (the same gate
  `api/recommendation_governance.py` applies). A caller holding one but
  not the other gets the other source **named as omitted** in
  `coverage`/`limitations`, never silently absent; a caller holding
  neither gets 403. An aggregate must never reveal what the caller could
  not read directly -- and inventing a `BRIEFING_READ` code would have
  created a second, drifting answer to "who may see this finding".
- **A recommendation never ranks `high` and a driver never outranks its
  own movement.** A suggestion capped at 60 and a driver scored by its
  own share (capped by its parent) keep the reading order honest; a
  suggestion outranking an observed collapse would misorder exactly the
  attention a briefing directs.
- **Nothing asserts a cause.** A driver is a contribution to an
  arithmetic total, with the caveat inline on every item (not only in
  the section note) --
  `tests/security/test_briefing_never_presents_estimate_as_fact.py`
  asserts seven causal phrasings appear in neither any claim nor the
  rendered report, and also parses every module's own source to prove no
  module in `briefing/` imports `identity`, `sqlalchemy`,
  `agent.graph`/`nodes`/`llm_client`/`sql_validator`,
  `db.connection`/`execution`/`schema_introspection`, `ollama` or
  `rag.llm`. A briefing has no way to produce a row, which is what keeps
  the deterministic SQL controls unreachable from here.

`api/briefing.py` is the one route (`POST /briefing/generate`, local
account required, behind `Settings.enable_executive_briefing`, default
on -- a pure read of already-authorized records, no monitored-database
query, no LLM, no outbound call, nothing persisted).
`briefing/reports.py` is the single markdown rendering, shared by the
route's own `report_markdown` field.

**Known, disclosed limitations:** no delivery channel exists anywhere in
this codebase (the platform-wide position
`kpi_monitoring.models.AlertBundle` already states) -- the route returns
a document, nothing is emailed or posted; no scheduler, and
deliberately no CLI either (unlike `scripts/run_kpi_monitor.py`), since
every source here is a tenant-scoped read needing a real authenticated
actor; **no visualization** -- `analytics.visualization.build_chart_spec`
needs `columns`/`rows` and a briefing holds only aggregates by design,
so it was inspected and deliberately not reused rather than faked;
nothing is persisted, so there is no briefing history; **`period` is not
a date filter** -- period labels stay opaque strings (the same boundary
`analytics.engine.detect_missing_periods` draws), so the cadence selects
the freshness budget and the heading while the briefing summarizes
currently-open findings and unresolved recommendations; data quality is
caller-supplied, so a caller that never scans gets an honest "nothing
was checked" rather than a clean bill of health; drivers are
single-level (`analytics.causal`'s hierarchical engine is not reached,
because a KPI finding persists a flat contributor list); no frontend.

**Overlap with the in-flight Prompt 52 work, named rather than left to
be rediscovered:** `decision_center/` (plus `api/decision_center.py` and
`frontend/src/pages/DecisionCenter.tsx`) was found **untracked in the
working tree** while inspecting for this prompt, and has its own
`ExecutiveBriefing` type. It was inspected in full first. The two are
genuinely different deliverables -- the Decision Center is a live read
model (ranked headlines across seven capabilities, decision chains, a
health rollup, role personalization) with no cadence, no staleness
comparison, no claim verification and no fact/estimate/recommendation
axis; `briefing/` is a cadenced, verified document. `briefing/` imports
nothing from `decision_center/`. Reconciling the two (most plausibly:
the Decision Center's headlines passing through `briefing.claims`'
evidence gate, and/or one shared source-adapter layer) is the
recommended next step, and should happen before either surface gains a
frontend.

**Live end-to-end verification, and the three real bugs it found.** This
feature was exercised against real infrastructure (real uvicorn, the real
PostgreSQL identity database, four real SQL Server 2025 monitored
databases), with each of the four sources produced through its own real
production path: `POST /kpi-monitoring/scan`, `POST /schema-drift/scan`
twice (the second observing a genuine column removal), `GET
/schema/tables` -> `POST /execute` -> `POST /data-quality/scan`, and
`identity.repositories.recommendation_governance.create_record`. Final
result: 25 claims across all seven sections, 0 withheld, every claim
carrying its evidence reference and versions. Two of the three bugs it
surfaced are **not** in `briefing/` at all:

- **`semantic_findings` could not be written to at all** (pre-existing,
  critical). `identity.models.SemanticFinding` declares `created_at`/
  `updated_at` with `server_default=func.now()`, so the ORM omits both
  from every INSERT; migration `a1f5c9e3b7d2` created them `NOT NULL`
  **without** that default -- the only one of 15 migrations to do so.
  Every insert raised `NotNullViolation`. Because that table is the
  **shared review queue**, semantic intelligence (Prompt 35), schema
  drift (Prompt 43) and KPI monitoring (Prompt 50) were all silently
  unable to persist anything against a real deployment. Invisible to
  every test, because tests build the schema with
  `Base.metadata.create_all` (which reads the models, and is correct) --
  only a migration-built database is affected, which is every real one.
- **`kind` was `String(32)`, too short for schema drift's own kinds**
  (pre-existing; already disclosed as a latent bug by Prompt 50, hit for
  the first time here). `schema_drift.findings` emits
  `schema_drift_column_nullability_changed` (39 chars) and
  `schema_drift_unique_constraint_changed` (38).
- **The evidence gate withheld true data-quality claims over a unit
  convention** (this feature's own code). `data_quality.engine` records
  `null_rate = 1.0` and renders `"... is 100.0% null (1 of 1)."`, so the
  grounding check compared `100.0` against observed values holding only
  `1.0` and suppressed a correct, deterministic measurement -- 12 of 22
  candidates on the first real run. Every `null_spike` finding renders a
  percentage this way, so this would have withheld most data-quality
  claims for any caller.

**Fixes, and the invariants that must not regress:**
- Migration **`m6q9r2s5t8u1`** adds the two server defaults (with a
  defensive backfill) and widens `kind` to `String(64)`; applied and
  verified against the live database. It uses `op.batch_alter_table`
  rather than bare `alter_column` so it is portable -- identical ALTERs
  on PostgreSQL, copy-and-move on SQLite.
- **`tests/test_identity_schema_invariants.py`** (10 cases) guards the
  whole bug *family*, not these two instances: a static AST scan of
  **every** migration refusing any `create_table` that declares an
  ORM-filled timestamp column without a `server_default` (the one
  historical violation sits in an explicit allowlist, with a test that
  the allowlist must shrink if that migration is ever corrected and a
  test that the repair migration still exists), the model-side half of
  the same invariant, and that both producers' restated
  `KIND_MAX_LENGTH` constants equal the real column length with every
  emitted kind fitting. Both halves were mutation-tested -- reintroducing
  each bug fails 3 and 2 cases respectively, so neither passes
  vacuously. **`schema_drift/findings.py` gained the import-time kind
  guard it never had**; `kpi_monitoring/findings.py` already had one,
  which is exactly why the overflow came from the package without it.
  Note the deliberate duplication: neither package may import
  `identity` (both assert that structurally), so each restates the
  limit and the test above is what keeps them honest.
- **Grounding is now unit-aware for percentages only.**
  `briefing.claims._allowed_numbers` returns two pools, mirroring
  `agent.insight.ResultSummary.allowed_values`/`allowed_percents`'s own
  split: a plain number must still match a plain observed value exactly,
  while a percent-typed number may additionally match an observed value
  scaled by 100, since a rate and the percentage rendered from it are
  the same observed fact in different units. One-directional (stored
  fraction -> rendered percent) and confined to percent-typed numbers --
  a disclosed, bounded widening, taken because the alternative
  suppresses true claims and a claim's text here always comes from a
  deterministic engine, never a model. A fabricated percentage (91% from
  a rate of 0.42) is still refused, as is a bare `100` backed only by a
  rate of `1.0`.
- A driver's share renders without a forced `+` (the live run read
  `"+83.3% of the change"` for a metric that *fell*, which reads as a
  rise); a negative share still keeps its minus.

**Run it yourself**: the exercise is a scratchpad driver, not a committed
test -- it needs the real databases and creates/removes real rows.
`tests/test_api_briefing.py` is the committed, mocked equivalent.

**Read [`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`](../../51_EXECUTIVE_AI_BRIEFING_CONTRACT.md)**
for the full inspection findings, every module's design, the 188-test
testing summary, §5a's live end-to-end run and the three real bugs it
found, and the security/tenant-isolation/performance review.


## Executive Decision Center (Prompt 52)

`decision_center/` (new top-level package) is one read model over
everything Prompts 33-50 already record: business health, KPI movement,
anomalies, risks, recommendations, pending decisions, actions and
outcomes -- each headline drillable to its evidence, metric definition,
period, source, confidence and lineage. `GET /decision-center/briefing`
plus `frontend/src/pages/DecisionCenter.tsx`.

**It aggregates; it never computes a business fact.** Every number comes
from a record an earlier capability wrote, carried with that
capability's own truth level. Its own contribution is ordering, grouping
and linking -- itself an `AI_INFERENCE` judgment, labelled as one on
`DecisionChain`.

**Prompts 36 and 51 do not exist** -- "33-51" is 17 real contracts.

**Invariants that must not regress:**
- **It structurally cannot produce data.** No module in
  `decision_center/` imports `agent.sql_validator`, `agent.graph`,
  `agent.nodes`, `agent.llm_client`, `db.execution`, `db.connection`,
  `db.schema_introspection`, `sqlalchemy`, `identity.models` or
  `identity.db`; none imports an LLM client; **none contains a
  `SELECT ... FROM` literal** -- all asserted by parsing the real source.
  The API route performs the tenant-scoped identity reads and hands rows
  in.
- **Personalization reorders and caps; it can do nothing else.**
  `personalize` never constructs a `Headline` or `HealthIndicator` -- it
  re-sequences the ones it was given, and is asserted to return *the
  same objects*. Health is computed from the complete sets **before** any
  per-role cap, so a display limit cannot change a count. **Every section
  is present for every role**, including empty ones: hiding one would let
  a briefing silently omit an open risk.
- **`Provenance` is mandatory on every `Headline`**, so a headline
  without it is unrepresentable. A field the producing capability never
  recorded reads `"not recorded"` rather than being omitted.
- **`LinkType.DECLARED` and `LinkType.CORRELATED` are never merged** --
  a real foreign key versus two records sharing a subject, the same
  discipline `db/relationship_inference.py` applies to declared versus
  inferred foreign keys.
- **An `AI_INFERENCE` headline may not report a confidence of 1.0.**
  Nothing inferred is certain. A stored 1.0 is clamped rather than
  failing the briefing, so one malformed row cannot take the page down.
- **No "healthy/amber/red" verdict is rendered.** `HealthIndicator` is
  always `DATABASE_FACT` and carries `direction` only -- a threshold
  turning a count into a colour is a business judgment nobody has given
  this platform.
- **An absent chain stage must state why** (`ChainLink`'s validator):
  a chain that simply stops is indistinguishable from one nobody looked
  at.
- **One unavailable source degrades rather than failing the briefing** --
  each repository read is wrapped, the exception *type* is logged (never
  its message, which can carry driver text), and the reason is reported
  on `unavailable_sources`.
- **`MetricDefinitionOut` omits `approved_expression`** -- a metric's
  authoritative SQL is read through `/semantic-catalog` by someone with
  the catalog permission, not shown as briefing content.
- **There is no `profile` request parameter**: personalization comes
  from the viewer's own server-resolved capabilities.

**The one real break in the acceptance chain, disclosed rather than
papered over:** `workflows.recommendation_id` is a real foreign key, so
recommendation -> action -> outcome is `DECLARED` end to end, and
signal -> evidence -> explanation is complete inside a single finding
record. But **nothing links a signal to a recommendation** -- the
recommendation engine builds from query results, not from findings. A
signal and a recommendation about the same subject are therefore offered
as `CORRELATED` (same database *and*, when the signal names a governed
metric, that metric appearing in the recommendation's text -- a bare
same-database rule would correlate everything with everything), and a
chain stopping there names the structural reason. Adding a
`source_finding_id` column was considered and rejected: nothing would
populate it, so it would advertise a capability that does not exist.

**Known, disclosed limitations:** only capabilities that persist
tenant-scoped records can appear (KPI monitoring, schema drift, semantic
intelligence, recommendations, governance, workflows, onboarding); the
compute-on-demand engines (forecasting, scenarios, optimization,
predictive, data quality, lineage, root cause) persist nothing and reach
a briefing only through what a persisted record captured -- every
briefing lists them as not contributing, so "nothing to report" stays
distinguishable from "not read". Severity is carried, never recomputed,
so sections rank on each capability's own scale. The screen is read-only
and links to the surfaces that own each lifecycle. Accessibility is
asserted at component level (roles, `aria-expanded`/`aria-controls`,
text-not-colour, `role="alert"`), not by an automated axe audit --
this project has no browser-level E2E harness. Performance is reasoned,
not load-measured (Docker is unavailable here).

**Read [`52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`](../../52_EXECUTIVE_DECISION_CENTER_CONTRACT.md)**
for the full inspection findings, the persisted-versus-computed split
that decided the design, the 98-test testing summary, and the
security/tenant-isolation/accessibility review.
