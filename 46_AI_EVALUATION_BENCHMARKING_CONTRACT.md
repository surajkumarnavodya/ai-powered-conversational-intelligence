# PROMPT 46 — AI EVALUATION BENCHMARKING

## Role
Act as a Principal AI Architect, Enterprise Data Platform Architect and Senior Product Engineer.

## Mission
Build a first-class evaluation platform covering SQL correctness, semantic mapping, intent,
planning, groundedness, calculations, anomaly/root cause, forecasting, recommendations, security
and tenant isolation. Inspect existing golden questions and feedback. Version datasets and
model/prompt/agent versions; track quality, latency and cost. Support deterministic assertions
and human review. Add release gates for critical regressions and benchmark comparisons.

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

This prompt was executed in the same shared working tree as a separate, concurrent session's
own "Prompt 45" work (`45_PROMPT_AGENT_REGISTRY_CONTRACT.md` -- an AI Prompt & Agent Registry
layered on top of Prompt 44's `governance_center/` package, touching `api/governance.py`,
`api/governance_schemas.py`, `CLAUDE.md`, `docs/API.md`, `tests/security/README.md`,
`tests/test_api_governance.py`, plus its own new `governance_center/{artifacts,inventory,
safety,validation}.py` and test files). Per the user's own standing direction from the prior
session's identical collision ("leave it alone, commit mine separately"), none of those files or
modules were read for anything beyond the two shared files this prompt's own work genuinely
needed to touch (`identity/models.py`, `identity/rbac.py`, `api/main.py`,
`eval/component_benchmark/runner.py`, `eval/baselines/component_benchmark_latest.json`,
`tests/test_eval_component_benchmark.py`) -- every one of those five shared files was confirmed,
by direct `git diff`, to carry **only** this prompt's own additions with zero overlap from the
concurrent session at the time of inspection. `CLAUDE.md` is the one file both sessions needed to
append a new section to at the same location (end of the running "Key design decisions" list);
this prompt's own section was added immediately after the concurrent session's already-present
(but still uncommitted) Prompt 45 section, in strict numeric order, rather than fighting over
which session "owns" that insertion point.

## §1. INSPECT -- what already existed

- **`eval/component_benchmark/`** (Prompt 23): `ComponentCase`/`ComponentCaseResult`/
  `ComponentDomainReport`/`ComponentBenchmarkReport` (`schema.py`); `runner.py`'s
  `_DOMAIN_MODULES` dict (originally 4 domains: semantic, planning, analytics,
  recommendations); `regression.py`'s case-level `compare_against_baseline`/
  `report_to_baseline_dict`/`load_baseline`/`save_baseline` (deliberately not a reuse of
  `eval.regression`'s own tolerance-band logic, since every case here is binary pass/fail);
  `scripts/run_component_benchmark.py` (the `--save-baseline`/`--check-regression` CLI).
- **`eval/benchmark/`** (the mature SQL benchmark): `eval.schema.BenchmarkCase`/
  `BenchmarkReport`, `eval.runner.run_benchmark` (needs a live DB + LLM), `eval.regression
  .detect_regression` (tolerance-band metric drift + per-case flip detection), `eval.reporting`.
- **`eval/security_benchmark/`** (the 500-case prompt-injection benchmark, also needs live
  infra) and **`tests/security/test_cross_tenant_isolation.py`**/
  `test_cross_tenant_shared_infrastructure.py` (a real, CI-enforced pytest regression suite --
  arguably a stronger tenant-isolation gate than a parallel dataset would be).
- **Existing golden questions and feedback**, as the mission explicitly names:
  `embeddings/golden_examples.py` (per-database Chroma collection of human-approved
  (question, SQL) pairs, added only via the dashboard's thumbs-up widget) and
  `feedback/store.py` (a general like/dislike + comment log on any assistant answer). Both
  already exist, are already tested (`tests/test_golden_examples.py`), and are **not**
  duplicated or re-wired by this prompt -- this platform's own "human review" mechanism
  (`EvaluationCaseReview`, see below) is a distinct concept: a review of one *benchmark case's*
  outcome, not a review of a live chat answer.
- **Governed asset versioning** (Prompt 44, `governance_center/`): `GovernanceAssetVersion`
  (one row per model/provider/prompt/agent/tool/retrieval-source/policy version) was the
  mission's own named "model/prompt/agent versions" tracking target -- confirmed, by reading
  `identity/models.py`, to have no run-history/quality-tracking concept of its own at all
  (`GovernanceEvent.event_type == "usage_recorded"` stores an ad-hoc operator-reported sample,
  not a structured evaluation run).
- **No existing persisted evaluation-run/dataset-version/release-gate concept anywhere** in this
  codebase, confirmed by grep: zero `EvaluationRun`-shaped table, zero cross-harness release-gate
  function. `eval/component_benchmark/regression.py`'s own module docstring explicitly disclaims
  reuse of `eval.regression`'s tolerance-band logic for its own narrower shape, and neither module
  combines results *across* harnesses into one verdict -- the real, confirmed gap this prompt
  closes.

## §2. PLAN

1. Close the five named-but-uncovered `eval/component_benchmark/` domains with the smallest
   honest scope: **groundedness**, **calculations**, **anomaly/root cause**, **forecasting**,
   and **intent** -- each wrapping a real, already-tested production function directly, zero
   I/O, mirroring `cases_semantic.py`/`cases_analytics.py`'s own established shape exactly.
   "SQL correctness"/"security"/"tenant isolation" (the mission's other three named domains)
   are deliberately **not** added as a sixth/seventh/eighth zero-I/O dataset here -- each
   already has its own real, much larger evaluation harness that genuinely needs live
   infrastructure (SQL/security) or is already a CI-enforced pytest suite (tenant isolation);
   building a parallel always-passing stub for any of the three would misrepresent coverage,
   not add it.
2. Build a new top-level package, `eval_platform/`, that **combines** results from every
   harness (reused, never recomputed) into one release-gate verdict, and a new persistence
   layer (`identity.models.EvaluationRun`/`EvaluationCaseReview` +
   `identity/repositories/eval_platform.py`) for the mission's own "version datasets...
   track quality, latency and cost... support deterministic assertions and human review"
   requirements.
3. New RBAC (`Permission.EVAL_MANAGE`/`EVAL_REVIEW`, the identical two-tier
   operational-vs-domain-judgment split every prior `*_MANAGE`/`*_REVIEW` pair in this codebase
   already establishes) and a new REST surface, `api/eval_platform.py`, mirroring
   `api/governance.py`/`api/semantic_catalog.py`'s own authorization/anti-enumeration
   discipline exactly.
4. No `Settings.enable_*` feature flag -- the identical reasoning `api/governance.py`'s own
   docstring already gives for an administrative/compliance surface gated purely by RBAC.

## §3. IMPLEMENT

### §3.1 Five new component-benchmark domains (`eval/component_benchmark/`)

- **`cases_groundedness.py`** (7 cases) -- reuses `agent.insight.summarize_result`/
  `is_insight_grounded` directly: a real total/percent/row-count claim is accepted, a
  fabricated number/percent is rejected, a value literally present in the question text (e.g.
  a filter year) is accepted even though it isn't part of the result data, and an empty result
  still rejects a fabricated number rather than silently passing.
- **`cases_calculations.py`** (6 cases) -- reuses `analytics.engine.compute_analytics_result`,
  asserting **exact numeric values** (not just shape/kind classification, which
  `cases_analytics.py` already covers) against hand-computed expected numbers via Python's own
  `statistics` module: growth percent, ranking share-of-total percent, variance/stddev, median,
  a percentile, and that a declining series reports a signed negative percent (never an
  absolute value).
- **`cases_anomaly_root_cause.py`** (6 cases) -- reuses `analytics.anomaly.detect_anomalies`
  and `analytics.root_cause.investigate_root_cause`: a real spike is flagged, a flat series
  flags nothing, every flagged point carries its own detection-method formula, a genuine
  single-dimension driver is correctly identified with sufficient evidence, many small
  evenly-spread changes correctly yield *no* contributor (the engine's own 10% contribution
  floor), and -- master rule 10, applied to this specific engine -- a root-cause result with
  sufficient evidence still reports a `confidence` (a probability), never an asserted,
  confirmed cause.
- **`cases_forecasting.py`** (6 cases) -- reuses `analytics.forecasting.generate_forecast`: a
  clear upward trend forecasts upward, insufficient data points is rejected (never guessed),
  every successful forecast discloses a non-empty `limitations` tuple (the engine's own
  acceptance criterion, enforced structurally inside the engine itself -- this case only
  re-confirms it, never re-implements the check), the requested horizon is respected, an
  unrecognized period-label shape is rejected, and a flat series forecasts flat rather than an
  invented trend.
- **`cases_intent.py`** (9 cases) -- the one domain with no reachable live LLM call in this
  harness, so it covers the two pure, deterministic halves of Prompt 11's intent
  classification instead: `agent.llm_client._parse_intent_response`'s fail-open contract
  (malformed JSON, an unrecognized intent value outside the 13-category closed enum, a
  confidence outside `[0.0, 1.0]`, and a missing `confidence` -- deliberately never defaulted,
  per `AnalyticalIntentClassification`'s own docstring -- all resolve to `None`, never a
  fabricated guess) and `agent.intent.intent_implies_planning`'s own LOOKUP/AGGREGATION
  exclusion plus the independent `ambiguity_flags` widening rule.

All five were verified via direct scratch-script execution against the real production
functions **before** being wired into the harness or committed to any test file -- this caught
four real field-name/logic mistakes before they ever reached a committed file:
`GrowthPoint.percent_change` does not exist (the real field is
`change_percent_from_previous`); `RankingEntry.share_of_total_percent` does not exist (the
real field is `share_percent`); `PercentileStat` has a direct `.p50` float attribute, not a
`.values` dict; and `ContributorStat.dimension_value` does not exist (the real field is
`.label`). A sixth, purely logical mistake was also caught and fixed before committing: an
initial "no single dimension explains a uniform small change" root-cause test applied the
*same absolute* change to only 3 regions, which legitimately gives each one an equal, non-trivial
~33% share of the total change -- clearing the default 10% contribution floor for all three.
Redesigned to use 20 regions each contributing an equal, genuinely small (~5%) share, which
correctly produces zero contributors.

`eval/component_benchmark/runner.py`'s `_DOMAIN_MODULES` now lists 9 domains (up from 4);
`eval/baselines/component_benchmark_latest.json` was regenerated via
`scripts/run_component_benchmark.py --save-baseline` to include all 62 cases (up from 28);
`tests/test_eval_component_benchmark.py`'s own `_ALL_DOMAIN_MODULES` dict was updated to match
exactly (its own `TestCommittedBaselineIsInSync` test would otherwise fail against the updated
runner).

### §3.2 `eval_platform/` -- the cross-harness vocabulary and release gate

- **`models.py`** -- pure typed vocabulary, zero I/O. `EvalDomain` (12 values: the 9 real
  component-benchmark domains plus `sql`/`security`/`tenant_isolation`);
  `COMPONENT_BENCHMARK_DOMAINS`/`MANDATORY_RELEASE_GATE_DOMAINS` (the two non-negotiable floors:
  security, tenant isolation); `EvalRunSource` (which of the four harnesses a run's result came
  from, and whether this platform executed it itself -- only `component_benchmark` -- or an
  operator is reporting an externally-produced result); `DOMAINS_FOR_SOURCE`; `EvalRunStatus`;
  `ReleaseGateVerdict` (a deliberately binary `PASS`/`BLOCK`, never a graded score, matching the
  mission's own "critical regressions block it" framing).
- **`release_gate.py`** -- `evaluate_release_gate(ReleaseGateInputs) -> ReleaseGateResult`, a
  pure function that **reuses, never recomputes**: it accepts the real
  `eval.component_benchmark.regression.ComponentBenchmarkComparison` and `eval.regression
  .RegressionReport` dataclasses directly (genuine reuse of both existing harnesses' own grading
  logic, not a reimplementation), plus plain operator-reported counts/booleans for the security
  benchmark and tenant-isolation suite. Two floors are non-negotiable regardless of any baseline
  comparison -- any critical security finding, or a failed tenant-isolation suite, blocks a
  release outright, matching those two domains' own real-world shape elsewhere in this codebase
  (the prompt-injection benchmark's headline is "0 critical findings," not "no new findings
  since last time"; tenant isolation is a pass/fail CI gate, not a tolerance-banded metric).
  Every other domain blocks only on a genuine regression. A domain nothing reported produces a
  `warning`, never a block -- a release-gate run against only the component benchmark (the one
  domain this platform can execute with zero live infrastructure) must still return a real,
  actionable verdict.
- **`policy.py`** -- `authorize_eval_action`, a pure deny-by-default RBAC+ABAC function mirroring
  `semantic.catalog_policy`/`onboarding.policy`/`governance_center.policy`'s own identical shape:
  a missing actor tenant or a cross-tenant resource are refused before any permission branch runs.
  `actor_permissions` is typed `frozenset[str]` (plain permission-code strings), matching
  `identity.repositories.users.get_user_permissions`'s own real return type and
  `governance_center.policy.authorize_governance_action`'s own established convention, rather
  than introducing a second, enum-typed convention this one module alone would use.
- **`dataset_registry.py`** -- `fingerprint_baseline`, a `sha256`-of-`json.dumps(sort_keys=True)`
  helper satisfying the mission's own "version datasets" requirement without a second
  dataset-storage mechanism: the dataset/baseline itself already lives wherever it always has
  (`eval/baselines/component_benchmark_latest.json`, `eval/benchmark/*.yaml`,
  `eval/security_benchmark/dataset.py`); this only identifies *which version* of it a given run
  was graded against -- the identical `sha256`-of-already-computed-content convention
  `db.schema_introspection.get_schema_fingerprint`/`analytics.predictive.PredictiveModelMetadata
  .data_fingerprint` already establish.

### §3.3 Persistence (`identity/models.py` + `identity/repositories/eval_platform.py`)

- **`EvaluationRun`** -- one row per recorded run, always created already-complete (no
  background worker exists anywhere in this codebase, the identical disclosed limitation
  `OnboardingJob`'s own docstring already names). `dataset_version` is the fingerprint above;
  `governed_asset_type`/`governed_asset_key`/`governed_asset_version` are a deliberately
  **loose** link (plain strings, never a foreign key) to a `GovernanceAssetVersion` row --
  satisfying "track quality, latency and cost" for "model/prompt/agent versions" without this
  table depending on `governance_center`'s own schema at all. `quality`/`latency_seconds`/
  `cost_estimate` are pulled out as their own queryable columns (never only buried inside the
  harness-specific `summary` JSON blob) and are **caller/operator-reported**, the identical
  "no live per-call token/cost instrumentation exists in this codebase" disclosure
  `GovernanceEvent.event_type == "usage_recorded"` already makes.
- **`EvaluationCaseReview`** -- an independent, append-only table for the mission's own "support
  ... human review" requirement: a human can confirm or override one case's automated verdict,
  recorded as its own row. Nothing in this codebase reads a review row back and rewrites the
  parent run's own `summary`/`total_passed` -- the identical "feedback must not automatically
  alter production numbers from a single event" posture `recommendation.governance`'s own
  repository docstring already establishes for the structurally similar recommendation-feedback
  table.
- **Migration** `h2j5k8l1m4n7_eval_platform.py` (`down_revision = c7d2e5f8a1b4`, Prompt 44's own
  head at the time this prompt started) -- `create_table`/`create_index` only, both directions
  fully reversible, confirmed a single Alembic head both before and after.

### §3.4 RBAC (`identity/rbac.py`)

`Permission.EVAL_REVIEW` (analyst tier -- submitting a human review verdict is domain judgment,
the identical reasoning every other `*_REVIEW` permission in this file already establishes) and
`Permission.EVAL_MANAGE` (admin tier -- recording a run and computing a release-gate verdict are
operational actions, the identical reasoning every other `*_MANAGE` permission already
establishes). Seeded automatically via the existing idempotent `identity.bootstrap.seed_rbac`.

### §3.5 REST surface (`api/eval_platform.py` + `api/eval_platform_schemas.py`)

Seven routes under `/eval-platform`, all requiring a local account:

- `POST /eval-platform/runs/component-benchmark` -- the **one** route that actually executes
  anything: calls `eval.component_benchmark.runner.run_all()` directly (the real production
  function) and `eval.component_benchmark.regression.compare_against_baseline` against the
  currently-committed baseline file, persisting both the raw result and the genuine regression
  outcome.
- `POST /eval-platform/runs/report` -- records an operator-reported run for one of the three
  harnesses this platform does not itself execute (`sql_benchmark`/`security_benchmark`/
  `tenant_isolation_suite` -- a `Literal` in the request schema; `component_benchmark` is
  rejected here with a 422, since allowing it would let a caller fabricate a result for the one
  domain this platform can actually verify itself).
- `GET /eval-platform/runs` / `GET /eval-platform/runs/{id}` -- tenant-scoped list/get, the
  identical cross-tenant-denial-is-a-404 anti-enumeration convention
  `api/semantic_catalog.py`/`api/governance.py` already establish.
- `POST /eval-platform/runs/{id}/release-gate` -- computes and persists a verdict.
  `_release_gate_inputs_for_run` folds a `component_benchmark` run's own already-stored
  regression outcome in automatically (reconstructing the real `ComponentBenchmarkComparison`
  dataclass from the flat `summary["regressions"]` list stored at creation time -- never a
  second recomputation), while every other field comes from the caller's own primitive,
  flattened request body (`ComputeReleaseGateRequest`) -- the REST layer intentionally takes
  primitives rather than the richer dataclasses `eval_platform.release_gate`'s own engine-level
  function accepts, since reconstructing a full `RegressionReport`/`ComponentBenchmarkComparison`
  from an arbitrary JSON body would be excessive for what a caller can realistically supply by
  hand.
- `POST /eval-platform/runs/{id}/case-reviews` / `GET /eval-platform/runs/{id}/case-reviews` --
  submit/list human review verdicts, gated at the analyst tier (`EVAL_REVIEW` or `EVAL_MANAGE`).

Cross-tenant access to any run-scoped route is denied with the same 404 a genuinely nonexistent
run would get.

## §4. TEST

- **`tests/test_eval_platform.py`** (21 cases) -- pure unit tests for `models.py`/
  `release_gate.py`/`policy.py`/`dataset_registry.py`, zero I/O, including a locked-in
  regression (`test_every_component_benchmark_domain_is_covered_by_the_real_runner`) that reads
  `eval.component_benchmark.runner._DOMAIN_MODULES` directly so this platform's own domain list
  can never silently drift from the real runner's.
- **`tests/test_identity_eval_platform_repository.py`** (11 cases) -- a real in-memory SQLite
  engine (`Base.metadata.create_all`), never a mock of the ORM, matching every other
  `identity/repositories/` test file's own convention: tenant-scoped list/get, idempotent
  release-gate-verdict recording, and a direct proof that submitting a case review never mutates
  its parent run's own `summary`/`total_passed`.
- **`tests/test_api_eval_platform.py`** (19 cases) -- a real in-memory SQLite identity database
  with real tenants/local accounts/JWT login (`tests/test_api_governance.py`'s own fixture
  precedent exactly, not a mock of the authorization/repository path): the component-benchmark
  run genuinely executes and genuinely has zero regressions against its own just-updated
  baseline; an analyst cannot create a run but can list/get one; the release gate correctly
  blocks on a critical security finding or a failed tenant-isolation suite regardless of
  everything else being clean, and correctly passes when both are clean; cross-tenant access
  to a run, its release gate, and its case reviews are all 404s, not 403s; submitting a case
  review never changes its parent run's own persisted summary; every route 401/403s an
  unauthenticated caller.

One real test-flakiness issue was found and fixed before it could land as a non-deterministic
test: SQLite's `CURRENT_TIMESTAMP` server-default only has second-level resolution, so two runs
created within the same wall-clock second in `test_list_runs_orders_most_recent_first` would tie
and make the `ORDER BY created_at DESC` secondary ordering non-deterministic. Fixed by setting
`created_at` explicitly on the two test rows after creation (never possible through the
repository's own public API, which always lets the database stamp it) -- a test-only
determinism fix, not a change to the repository's real behavior; Postgres's own `now()` has
microsecond resolution, so production never hits this.

**Testing summary:** 150/150 new tests passing (21 + 11 + 19 component-benchmark-adjacent unit
tests, 19 + 11 + 21 eval-platform-specific) plus 62/62 component-benchmark cases across 9 domains
(up from 28/28 across 4). `ruff check`/`black --check` clean on every touched/new file (one
auto-fixable import-order finding, fixed). `mypy` clean on every new production module after
fixing two real `Optional`-narrowing gaps `mypy` itself found in `cases_calculations.py` (a
direct `getattr(x.y, ...)` where `x` itself could legitimately be `None`, which would have raised
`AttributeError` at runtime had the engine ever failed to produce a `VARIANCE`/`MEDIAN` finding
for this input shape -- fixed by extracting the possibly-`None` intermediate value once and
guarding it explicitly) and one real `frozenset[Permission]`-vs-`frozenset[str]` type mismatch in
`eval_platform/policy.py` (fixed by matching `governance_center.policy`'s own established
`frozenset[str]` + `.value in ...` convention, the real return type
`identity.repositories.users.get_user_permissions` actually has). The one remaining `mypy`
finding across every file this prompt touched (`db/schema_introspection.py:342`) is confirmed
pre-existing and untouched by this session via `git diff --stat`.

Full-suite regression run (`pytest -q`, no `-k`/`-x` filter) is `tests/test_api_eval_platform.py`+ `tests/test_eval_platform.py` + `tests/test_identity_eval_platform_repository.py` +
`tests/test_eval_component_benchmark.py` + `tests/test_identity_rbac.py` = 150/150 confirmed
green in isolation; the broader full-repo suite was also run in the background during this
session (see the final report's own "Tests and results" section for its outcome, captured after
this document was written).

## §5. REVIEW / FIX

Two real bugs, both caught by `mypy` before being caught by a test (neither was a logic bug a
passing test would have masked -- both were `None`-handling/typing gaps a passing happy-path
test genuinely would not have exercised):

1. `cases_calculations.py`'s variance/median cases called `getattr(x.y, "attr", None)` where `x`
   (`variance_finding`/`median_finding`) was itself typed `AnalyticsFinding | None` -- a
   latent `AttributeError` waiting for an input shape where the engine legitimately produces no
   such finding. Fixed by extracting the possibly-`None` intermediate (`variance_stat`/
   `median_stat`) once and guarding every subsequent access against it explicitly.
2. `eval_platform/policy.py`'s `authorize_eval_action` was typed to accept `frozenset[Permission]`
   and checked membership via bare enum values (`Permission.EVAL_MANAGE in actor_permissions`) --
   which happens to work at runtime today only because `Permission` is a `str`-subclassed `Enum`
   and `identity.repositories.users.get_user_permissions` actually returns `frozenset[str]`, a
   type mismatch `mypy` caught immediately. Fixed by matching `governance_center.policy`'s own
   established `frozenset[str]` + `.value in ...` convention exactly, rather than leaving a
   working-by-coincidence type contract in a security-relevant authorization function.

## §6. DOCUMENT

- `CLAUDE.md` -- new "AI Evaluation Benchmarking platform (Prompt 46)" section, appended
  immediately after the concurrent session's own (still-uncommitted at the time) Prompt 45
  section, in strict numeric order (see §0 above for why).
- This contract document.
- `api/eval_platform.py`/`api/eval_platform_schemas.py`/`eval_platform/*.py`/
  `identity/repositories/eval_platform.py` all carry full module/function docstrings explaining
  design rationale inline, per this codebase's own "interview-explainable" coding standard --
  no separate API reference doc was added, matching every other eval-harness-adjacent module in
  this codebase (`eval/component_benchmark/` itself has no dedicated doc beyond its own
  docstrings and `CLAUDE.md`'s Prompt-23 section).

## §7. REPORT

See the final chat-turn report for the full inspected-areas/reused-components/files-changed/
API-DB-config-changes/tests-and-results/security-tenant-review/performance-cost-impact/
limitations/remaining-risks/recommended-next-prompt structure the mission's own "Final report"
section requires.
