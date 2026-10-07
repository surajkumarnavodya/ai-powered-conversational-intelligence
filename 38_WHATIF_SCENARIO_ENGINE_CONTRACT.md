# 38 — Governed What-If Scenario Engine

Prompt 38 of the ongoing Enterprise AI Analytics & Recommendation
Platform initiative, built on top of Prompt 13's analytics engine
(`analytics.engine.compute_analytics_result`, reused verbatim), Prompt 16's
forecasting engine (`analytics.forecasting.generate_forecast`, reused for
the forecast-scenario path), Prompt 34's multi-agent supervisor (a new
specialist, following the exact `root_cause`/`forecast` precedent), and
Prompt 37's own "explicit, caller-supplied, the planner never creates one"
gate. **INSPECT/PLAN/IMPLEMENT/TEST/REVIEW/FIX/DOCUMENT** — one new
computation module (`analytics/scenario.py`), additive model/
visualization extensions, a new multi-agent specialist, a new, standalone
REST surface (`api/scenarios.py`), additive config surface, and a
dedicated cross-cutting security regression test. No existing route,
node, prompt, or `Settings` field changed behavior for any pre-existing
caller.

## 1. Inspection: what already existed vs. the real gap

Exhaustive grep confirmed: no "what-if"/"scenario"/hypothetical-parameter
concept existed anywhere in this codebase prior to this prompt.
`analytics.forecasting.generate_forecast` projects a historical trend
forward, but offers no way to ask "what if growth were different from
what actually happened." `analytics.root_cause`/`analytics.causal`
compare two *real* periods; nothing compares a real baseline against a
*hypothetical* one. `recommendation.engine` reasons over already-executed
results; nothing lets a caller propose an assumption and see its modeled
effect before (or without) it ever becoming real. The closest existing
building blocks worth reusing, all found by inspection and reused rather
than reimplemented:

- `analytics.engine.compute_analytics_result` — the real per-side
  analytics computation (rankings, outliers, growth) this prompt reuses
  on both the baseline and the scenario-adjusted rows, so a caller gets
  a full, internally consistent picture of each side, not just one
  aggregate delta.
- `analytics.engine.group_and_sum_by_label`'s own `" / "`-joined
  composite-label convention — reused for `ScenarioParameter
  .target_label` matching, so a scenario's row-targeting agrees exactly
  with every other module's own dimension-label convention.
- `recommendation.engine._restricted_column_names`'s pattern — the
  identical table-agnostic, lowercased restricted-column-name set,
  re-derived (not imported across a module boundary) for this engine's
  own authorization check.
- `agent.multiagent.agents.root_cause_handler`'s "explicit comparison,
  planner never creates one" gate — the identical shape, applied to an
  explicit `ScenarioRequest`.
- `api.analyst.py`'s "feature flag -> 404 when off" + `Permission.ASK`
  + `enforce_api_action_rate_limit` route-construction pattern — reused
  verbatim for `api/scenarios.py`'s own two routes.
- `analytics.visualization.build_forecast_chart_spec`'s exact style —
  mirrored for `build_scenario_chart_spec`.

## 2. What was built

### `analytics/scenario.py` (new)

The engine's three public functions:

- **`validate_scenario_parameters`** — the mission's own "typed
  parameters with units, bounds, affected metrics and model versions"
  requirement, made concrete: `analytics.models.ScenarioParameter`
  carries `name`/`affected_metric`/`unit`/`change_type`
  (`ScenarioChangeType`: `PERCENT_CHANGE`/`ABSOLUTE_DELTA`/
  `ABSOLUTE_VALUE`/`MULTIPLIER`)/`scenario_value`/`target_label`/
  `min_value`/`max_value`/`model_version`. Validates every parameter
  independently and reports every violation (never stopping at the
  first): unknown column, malformed bounds, non-finite value, an
  out-of-bounds value (`is_valid=False`), an unrealistic implied change
  (soft `warnings` by default, hard `violations` when `Settings
  .scenario_reject_unrealistic` is on), a restricted-column
  authorization failure, and every missing-data shape (empty baseline,
  a `target_label` absent from it, `target_label` given without
  `dimension_columns`).
- **`run_scenario`** — "run scenarios without changing source data,"
  literally: never queries a database, never mutates its own `rows`
  argument (verified directly,
  `TestRunsWithoutMutatingBaseline`), builds a brand-new in-memory copy,
  scales each parameter's matching rows proportionally to its own
  implied total (even-split fallback when the current total is exactly
  `0`), and applies multiple parameters **sequentially**, in the order
  given (a disclosed, bounded simplification, not a jointly-solved
  system — `ScenarioResult.limitations` discloses it whenever two
  parameters target the same metric). "Reject invalid or unauthorized
  assumptions," literally: **one** invalid parameter rejects the
  **entire** scenario (`status="rejected"`, `comparisons=()`) — never a
  partial run with the bad one silently dropped (mirrors
  `agent.analytical_plan`'s own "an invalid plan is never partially
  trusted" precedent, proven directly in `TestNoPartialTrust`).
- **`apply_scenario_to_forecast`** — "Inspect ... forecasts": applies
  exactly one parameter uniformly to every point of an already-computed
  `ForecastResult`, disclosed as a simplistic perturbation of the
  fitted model's own point estimates (not a refit).
- **`recommendations_for_scenario`** — "Inspect ... recommendations":
  the identical `analytics.forecasting.recommendations_for_forecast`
  pattern, reusing `recommendation.models.Recommendation` directly,
  **built and tested but not wired into any live
  `generate_recommendations_node` path** — the same disclosed "compute +
  test, defer the deepest wiring" deferral every engine module in this
  package has used at least once.

### Clearly separating observed facts, assumptions, and modeled estimates

The mission's central requirement, met two ways:

1. **A dedicated, orthogonal enum** — `analytics.models
   .ScenarioValueKind` (`OBSERVED`/`ASSUMPTION`/`MODELED_ESTIMATE`),
   deliberately *not* `agent.provenance.DataTruthLevel` (that vocabulary
   answers "how sure are we," not "which part of this number is a real
   measurement vs. a caller's own input vs. this engine's arithmetic").
2. **Structural, not conventional** —
   `ScenarioMetricComparison.baseline_kind`/`.scenario_kind` are fixed
   `Literal[ScenarioValueKind.OBSERVED]`/`Literal
   [ScenarioValueKind.MODELED_ESTIMATE]` — pydantic itself refuses to
   construct one with the two kinds swapped (proven directly,
   `tests/security/test_scenario_never_presents_estimate_as_fact.py
   ::TestTypeLevelGuarantee`). Every claim the live multi-agent handler
   renders says "(observed)"/"(modeled estimate)" explicitly, proven
   through that same handler end to end.

### Baseline/scenario comparison and visualization

`ScenarioResult.comparisons` — one `ScenarioMetricComparison` per
distinct affected metric, computed from the real baseline total vs. the
scenario-adjusted total (reusing the identical
`current_total`/`baseline_total`/zero-denominator-guarded-percent
convention `analytics.root_cause.investigate_root_cause` already
established). `analytics.visualization.build_scenario_chart_spec` (new)
mirrors `build_forecast_chart_spec`'s exact style: a grouped `BAR_STACKED`
chart with distinct `Y`/`SERIES` fields for "Baseline" and "Scenario" —
never one blended bar — with the scenario's hypothetical nature disclosed
in both `notices` and `accessibility`.

### Multi-agent wiring (`agent/multiagent/`)

A new `AgentName.SCENARIO` specialist (`cost_units=1`, `max_truth_level=
AI_INFERENCE`, allowed only `TOOL_SCENARIO`). `scenario_handler` runs
only for an explicit, caller-supplied `ScenarioRequest`
(`agent/multiagent/supervisor.py`, the identical shape `RootCausePair`
already established) naming which `sql_data` task's own already-governed
rows to run against — never a second query, never caller-side raw data.
`caller_roles` is never included in the dict the handler passes to
`ctx.call`; `agent.tools.registry.ToolRegistry.execute` injects the real,
already-authorized roles into the tool's own payload automatically
(proven directly,
`test_a_scenario_cannot_smuggle_identity_through_its_own_tool_call`).

### REST surface (`api/scenarios.py`, new)

Two routes, **neither of which ever queries a database**:
`POST /scenarios/validate` and `POST /scenarios/run`. Both take the
baseline `columns`/`rows` directly in the request body — the caller's
own already-fetched, already-governed result (e.g. from a prior `POST
/execute`). This is the literal, structural mechanism behind "run
scenarios without changing source data" at the API layer: there is no
`sql` field on either request, no database connection, nothing to
validate as a query at all. Gated by `Settings.enable_what_if_scenarios`
(404 when off, mirroring `api/analyst.py`'s own feature-flag pattern),
`Permission.ASK`, `enforce_api_action_rate_limit`, and payload bounds
(`Settings.max_result_rows`/`scenario_max_parameters`) so this pure-
compute endpoint can't become a resource-exhaustion vector just because
it never touches a database. `api/scenario_schemas.py` mirrors
`analytics.models`' own scenario types field-by-field, the identical
"mirror the internal model, don't re-export it" convention
`VisualizationSpecOut` already established.

### Config (`config/settings.py`)

`enable_what_if_scenarios: bool = True`,
`scenario_unrealistic_change_percent_threshold: float = Field(default=
500.0, gt=0.0)`, `scenario_reject_unrealistic: bool = False`,
`scenario_max_parameters: int = Field(default=20, gt=0)` — same
on-by-default-but-escapable, fully-documented posture as every flag in
this series.

## 3. Proof: boundary, unrealistic, and missing-data cases

All directly in `tests/test_analytics_scenario.py`:

- **Boundary**: a value exactly at `min_value`/`max_value` is valid; a
  value a fraction past either is invalid — `TestBoundaryCases`.
- **Unrealistic**: a 100,000% implied change is flagged
  `is_unrealistic=True` with a disclosed `warnings` entry but still runs
  by default; the identical input is a hard rejection once `Settings
  .scenario_reject_unrealistic` is set — `TestUnrealisticCases`.
- **Missing data**: empty baseline rows, an unknown column, and a
  `target_label` absent from the baseline are each their own distinct,
  disclosed violation (never confused with one another) —
  `TestMissingDataCases`.
- **Unauthorized**: a restricted column is rejected for a `"viewer"` and
  allowed for an `"analyst"` (`Permission.VIEW_RESTRICTED_COLUMNS`), and
  an unauthorized parameter rejects the **whole** scenario run, not just
  itself — `TestUnauthorizedCases`.
- **No partial trust**: one invalid parameter among several rejects the
  entire scenario; too many parameters is rejected before any validation
  even runs — `TestNoPartialTrust`.
- **Composition**: two parameters targeting the same metric compose
  sequentially to the hand-computed expected total (`440.0`), disclosed
  in `limitations`; the scenario-adjusted rows flow through the *real*
  `compute_analytics_result`, correctly re-ranking which label is now
  the top contributor — `TestSequentialComposition`.
- **Forecast scenario**: a uniform 10% adjustment to every forecast
  point, a rejected source forecast never adjusted, a non-finite
  parameter rejected — `TestForecastScenario`.
- **Correlation/fact separation**: the structural `Literal` guarantee,
  both directly (`TestCorrelationNeverFact`) and cross-cutting (
  `tests/security/test_scenario_never_presents_estimate_as_fact.py`,
  including through the one live multi-agent handler).

All example numbers in every test file were independently hand-computed
and cross-checked by directly exercising the module (via a scratch
script, not shown in the final diff) before being written into the test
files — not "whatever the code happened to produce."

## 4. Explicitly out of scope

- **`recommendations_for_scenario` has no live caller** — built, tested,
  deferred, the same posture `analytics.forecasting
  .recommendations_for_forecast` already established for its own engine.
- **Sequential, not jointly-solved, composition** — two parameters
  targeting overlapping rows are applied in the order given, not solved
  as a simultaneous system; disclosed in `ScenarioResult.limitations`
  whenever it's actually relevant.
- **`apply_scenario_to_forecast` does not refit the underlying model** —
  a uniform perturbation of the already-fitted point estimates, disclosed
  in `ScenarioForecastResult.limitations`.
- **No scenario persistence, lifecycle, or governance workflow** — unlike
  Prompt 18's recommendation governance, there is no `ScenarioRecord`
  table, no saved-scenario history, and no review/feedback lifecycle in
  this pass. A scenario is a single, stateless, pure computation per
  call — the identical "engine first, governance/persistence later, if
  ever" split this platform's own Prompt-17-then-18 precedent already
  established for recommendations.
- **No frontend page/dashboard** — the mission's own "support ...
  visualization" requirement is met by `build_scenario_chart_spec`
  (computed, tested, exposed via `ScenarioRunResponse.visualization_spec`)
  — not yet consumed by `frontend/src/lib/chartEngine.ts`, the identical
  "computed + tested + exposed via the API, not yet wired into the
  frontend" deferral `build_forecast_chart_spec`/`build_chart_spec`'s own
  `visualization_spec` already established.
- **3+-way interaction between scenario parameters is not modeled** — a
  parameter's own effect is proportional-and-sequential only; no
  cross-parameter synergy detection (that is `analytics.causal`'s own,
  separate, pairwise-dimension concern, not this engine's).

## 5. Testing

6 test files touched (4 new, 2 extended):

- `tests/test_analytics_scenario.py` (new, 31 tests) — typed-parameter
  contract, no-mutation, boundary, unrealistic, missing-data,
  unauthorized, sequential composition (including a real
  `compute_analytics_result` re-ranking check), no-partial-trust,
  no-op scenario, forecast scenario, recommendations, and the
  correlation/fact structural guarantee.
- `tests/test_api_scenarios.py` (new, 12 tests) — feature flag,
  validation response shape, an unrecognized `change_type` as a 422 (not
  a 500), client-supplied-identity rejection, payload bounds, a full run,
  whole-scenario rejection, and the visualization spec's own disclosure
  text.
- `tests/security/test_scenario_never_presents_estimate_as_fact.py`
  (new, 5 tests) — the cross-cutting type-level + text-level guarantee,
  across both `analytics/scenario.py` directly and the one live
  multi-agent handler end to end.
- `tests/test_multiagent_supervisor.py` (extended, +4,
  `TestScenarioAndWorkspace`) — explicit-request-only gating, an invalid
  parameter disclosed as an open item (never a claim), and the
  identity-cannot-be-smuggled-through-the-tool-call guarantee.
- `tests/test_analytics_engine.py`/`tests/test_analytics_root_cause.py`
  — re-run completely unmodified; both pass, proving the Prompt-13/14
  engines this module reuses were not touched.

**Full backend suite: 4079/4079 tests pass, zero regressions**
(`pytest -q`, re-run after every change in this prompt). `ruff check`/
`black --check` clean on every new/changed file. `mypy` clean on every
new/changed production and test file (the 5 remaining errors are
pre-existing, in 3 files this prompt never touched — confirmed via
`git status`). `bandit`: 0 findings across every new/changed production
module (4369 lines scanned).

## 6. Security / tenant-isolation / performance review

- **Security**: `analytics/scenario.py` is pure functions with no I/O
  and no LLM call of its own — neither prompt-injectable nor able to
  reach any new data. The observed-vs-estimate guarantee is enforced at
  the type level (pydantic `Literal`s, mirroring Prompt 37's own
  `causation_confirmed: Literal[False]` precedent), not merely by
  convention — locked in by a dedicated cross-cutting regression test.
  The restricted-column authorization check is re-derived independently
  (not imported from `recommendation.engine`'s own private helper),
  matching `agent/authz.py`'s own stated "no frontend-level restriction
  is a security boundary ... the corresponding internal node re-checks
  independently" principle — every parameter's authorization is checked
  regardless of whether the caller already had permission to fetch the
  baseline rows in the first place (a future caller of this engine with
  a different viewer's roles is not assumed-safe just because *some*
  caller's roles already cleared the data).
- **Tenant isolation**: `api/scenarios.py`'s two routes never touch a
  database and never read `AgentState`/tenant-scoped storage — the
  caller-supplied `columns`/`rows` are exactly what was sent, with no
  tenant-scoped lookup to isolate. The one live multi-agent caller
  (`scenario_handler`) operates on rows the supervisor's own governance
  step (`govern_rows_for_caller`) already redacted before any engine
  sees them — unchanged by this prompt.
- **Performance**: pure in-memory arithmetic over rows already held in
  memory — no new network call, no new database round-trip. Payload
  bounds (`max_result_rows`/`scenario_max_parameters`) cap this
  pure-compute endpoint's own cost regardless of what a caller sends, the
  identical "cap regardless of payload size" philosophy `max_result_rows`
  already applies to the SQL-execution path. Disabling `Settings
  .enable_what_if_scenarios` removes the REST surface entirely (404) with
  zero other behavior change; the multi-agent specialist is already a
  no-op for every turn that supplies no `scenario_requests`.

## 7. Known limitations / remaining risks

- No live, automatic trigger recognizes a "what if" question and
  generates a `ScenarioRequest`/`ScenarioParameter` set automatically —
  both REST routes and the multi-agent specialist require an explicit,
  already-structured request today. Teaching the pipeline to propose
  (never auto-run) a candidate scenario from a natural-language "what if"
  question is a natural, separate follow-up.
- No persistence/governance/dashboard — see §4.
- Sequential (not jointly-solved) composition and a non-refit forecast
  adjustment are both disclosed, bounded simplifications — see §4.
- `recommendations_for_scenario` has no live caller — see §4.

## Recommended next prompt

Teach the agent pipeline to recognize a "what if" question (a natural
extension of Prompt 11's `AnalyticalIntentType` taxonomy, which has no
WHAT_IF/SCENARIO category today) and propose a candidate
`ScenarioParameter` set from it — surfaced for the caller's explicit
confirmation before `run_scenario`/the multi-agent `ScenarioRequest`
ever executes it, preserving this prompt's own "the planner never
creates one" gate rather than weakening it. Alternatively, wire
`recommendations_for_scenario`/`build_scenario_chart_spec` into a live
path and a frontend surface, closing this prompt's own two disclosed
"not yet wired" gaps — the same recurring, deliberate deferral every
engine prompt in this series has made at least once.
