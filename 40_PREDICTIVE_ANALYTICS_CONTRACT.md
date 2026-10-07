# 40 — Governed Predictive Analytics Engine

Prompt 40 of the ongoing Enterprise AI Analytics & Recommendation
Platform initiative, expanding `analytics.forecasting` (Prompt 16's
time-series projection engine) into general-purpose, governed regression
and classification — demand, churn, risk, and beyond. **INSPECT/PLAN/
IMPLEMENT/TEST/REVIEW/FIX/DOCUMENT** — one new sibling module within the
existing `analytics/` package (`analytics/predictive.py`), additive
model extensions, a new multi-agent specialist, a new standalone REST
surface (`api/predictive.py`), additive config surface, and a dedicated
cross-cutting security regression test. No existing route, node,
prompt, or `Settings` field changed behavior for any pre-existing
caller.

## 1. Inspection: what already existed vs. the real gap

Exhaustive grep confirmed: no regression/classification/ML-model
concept existed anywhere in this codebase prior to this prompt.
`analytics.forecasting.generate_forecast` fits one of five deterministic
stdlib-only baseline models to a single chronological series and
projects it forward — it has no concept of multiple input features, no
train/test split, no classification at all. `analytics.scenario`/
`analytics.causal` compare or perturb already-known values; neither fits
a model. The closest existing building blocks worth reusing, all found
by inspection:

- `analytics.forecasting.ForecastEvaluation`'s own train/held-out-
  evaluation split concept, and `ForecastModelMetadata`'s "record model
  version, training window, parameters" requirement — both directly
  informed this engine's own `PredictiveModelMetadata`/evaluation-on-
  test-split design.
- `analytics.scenario`/`optimization.validator`'s independently
  re-derived `_restricted_column_names` authorization pattern — reused
  verbatim for feature/target column authorization.
- `db.schema_introspection.get_schema_fingerprint`'s "hash a
  deterministic serialization" convention — reused for this engine's
  own data/model fingerprints.
- `scikit-learn` (`LinearRegression`/`LogisticRegression`/evaluation
  metrics) and `scipy.stats` (`pearsonr`/`ks_2samp`) were **already
  genuine transitive dependencies** (scikit-learn via
  sentence-transformers; scipy already a direct dependency as of
  Prompt 39) — pinned explicitly now that `analytics/predictive.py`
  imports scikit-learn directly. **No new dependency was added.**

## 2. What was built

### `analytics/predictive.py` (new)

`train_predictive_model` is a pure function over an already-fetched
baseline dataset (`columns`, `rows`): it never queries a database and
never calls an LLM. The model catalog is a small, closed, reviewed set
(`PredictiveModelType.LINEAR_REGRESSION`/`LOGISTIC_REGRESSION`) — never
an arbitrary class name a caller (or an LLM) could inject.

Every governance control the mission names, each with its own code path
and its own dedicated test:

1. **Data sufficiency** — `Settings.predictive_min_training_rows`/
   `predictive_min_rows_per_feature`/`predictive_min_rows_per_class`,
   checked before any split or fit is attempted.
2. **Leakage checks** (`_detect_leakage`) — a feature identical to the
   target, or correlated with it at/above `Settings
   .predictive_leakage_correlation_threshold` (Pearson correlation via
   `scipy.stats.pearsonr`), is flagged; rejected by default (`Settings
   .predictive_reject_on_leakage`), always disclosed in
   `PredictiveModelResult.leakage_findings` either way.
3. **Train/test separation** — every evaluation metric is computed
   **only** on a held-out test split the model was never fit on. The
   split itself is a **deterministic, hash-based ordering**
   (`_deterministic_order`: `sha256(f"{seed}:{index}")`), not Python's
   or numpy's own global random state — the literal mechanism behind
   "test reproducibility," verified directly
   (`TestReproducibility::test_the_same_data_and_seed_always_produce_the_same_result`).
   Classification uses a **stratified** variant
   (`_stratified_split`) so both splits contain examples of every class.
4. **Feature validation** — every feature/target column must exist, a
   feature may not also be the target, and (a disclosed, bounded scope
   for this engine version) every feature and a regression target must
   be numeric — categorical features are rejected outright, never
   silently coerced.
5. **Versioning and evaluation** — `PredictiveModelMetadata` records
   the model type, feature list, target, split sizes/fraction, random
   seed, and both a data and a model fingerprint;
   `RegressionMetrics`(MAE/RMSE/R²)/`ClassificationMetrics`(accuracy/
   macro-precision/recall/F1), each with its own `formula` string —
   master-contract rule 11.
6. **Authorization** — a feature or target column `config
   .sensitive_columns` classifies restricted is rejected
   (`PredictiveModelStatus.UNAUTHORIZED`) unless the caller holds
   `Permission.VIEW_RESTRICTED_COLUMNS`.
7. **"Store model/data fingerprints, features and metrics"** — every
   successful training run is audit-logged (`security.audit_log
   .log_security_event`, `"predictive_model_trained"`) carrying both
   fingerprints, the feature list, and split sizes — **never** the raw
   data or the fitted coefficients. **Deliberate, disclosed choice, not
   an oversight**: this engine (like every other one in this package)
   has no persisted model registry in this pass — "storing" happens
   through the identical audit-log mechanism every other authorization/
   governance event in this codebase already uses, not a new database
   table. See §4 for the full reasoning.

**Uncertainty, exposed per task type** (`PredictionWithUncertainty`):
regression predictions carry a residual-based prediction interval
(the same z-based convention `analytics.forecasting.ForecastPoint`
already establishes); classification predictions carry the full
per-class probability distribution (`predict_proba`).

**`detect_feature_drift`** — a standalone, two-dataset comparison
(mirroring `RootCauseResult`'s own "standalone, not one more finding"
precedent) using a two-sample Kolmogorov-Smirnov test
(`scipy.stats.ks_2samp`) per feature, flagged at `Settings
.predictive_drift_p_value_threshold`. A column missing from either side,
non-numeric, or with too few points is skipped and disclosed in
`notes`, never silently ignored.

### `analytics/models.py` (extended, additive only)

New types: `PredictiveDomain`, `PredictiveTaskType`, `PredictiveModelType`,
`PredictiveModelStatus`, `LeakageFinding`, `RegressionMetrics`,
`ClassificationMetrics`, `PredictiveModelMetadata`,
`PredictionWithUncertainty`, `PredictiveModelResult`,
`FeatureDriftFinding`, `FeatureDriftReport`. Every pre-existing type is
byte-for-byte unchanged.

### Multi-agent wiring (`agent/multiagent/`)

A new `AgentName.PREDICTIVE` specialist, gated on an explicit,
caller-supplied `PredictiveRequest` (`agent/multiagent/supervisor.py`)
— the planner never creates one. Like `ScenarioRequest` (and unlike
`OptimizationRequest`), it carries a `data_task`: the training data
comes from one `sql_data` subquestion's own already-governed rows, never
a second query. The tool is registered at `Permission.ASK` (the
identical, now-recurring reasoning `TOOL_OPTIMIZATION`'s own comment
already documents: the registry-level gate is a pure formality, and the
*real*, specific `Permission.VIEW_RESTRICTED_COLUMNS` check happens
inside `train_predictive_model` itself, surfaced as a properly disclosed
`PredictiveModelStatus.UNAUTHORIZED` open item, never a bare,
unexplained registry denial) — this prompt did not need to rediscover
Prompt 39's own bug-fix the hard way, since the pattern was already
established and followed from the start.

### REST surface (`api/predictive.py`, `api/predictive_schemas.py`, new)

`POST /predictive/train` and `POST /predictive/drift` — **neither route
ever queries a database or calls an LLM.** Both take the dataset(s)
directly in the request body. Gated by `Settings
.enable_predictive_analytics` (404 when off), `Permission.ASK`, rate
limiting, and payload bounds (`Settings.max_result_rows`/
`predictive_max_features`/`predictive_max_predict_rows`).

### Config (`config/settings.py`)

`enable_predictive_analytics: bool = True`,
`predictive_min_training_rows`/`predictive_min_rows_per_feature`/
`predictive_min_rows_per_class`, `predictive_leakage_correlation_threshold`,
`predictive_reject_on_leakage`, `predictive_drift_p_value_threshold`,
`predictive_max_features`, `predictive_max_predict_rows` — same
on-by-default-but-escapable, fully-documented posture as every flag in
this series.

## 3. Proof: reproducibility, drift, insufficient data, and authorization

All directly in `tests/test_predictive_analytics.py`:

- **Reproducibility**: the same data and seed always produce identical
  data/model fingerprints, coefficients, and metrics; changing the
  underlying data changes both fingerprints — `TestReproducibility`.
- **Drift**: near-identical distributions report no drift (p-value above
  threshold); a shifted distribution is flagged (p-value below
  threshold); a column missing from one side or non-numeric is skipped
  and disclosed, never silently ignored — `TestDrift`.
- **Insufficient data**: too few rows overall, a single class, and a
  too-small class are each their own distinct, disclosed rejection
  reason — `TestInsufficientData`.
- **Authorization**: a restricted feature *or* target column is rejected
  for a `"viewer"` and allowed for an `"analyst"` —
  `TestAuthorization`.
- **Leakage**: an identical feature is hard-rejected by default; the
  identical input still trains (with the finding disclosed) once
  `Settings.predictive_reject_on_leakage` is off; an unrelated feature is
  never flagged — `TestLeakage`.
- **Feature validation**: an unknown column, a feature that is also the
  target, a non-numeric feature, and a mismatched `model_type`/
  `task_type` pairing are each rejected before any fit is attempted —
  `TestFeatureValidation`.
- **Regression and classification correctness**: a clean, non-collinear
  linear relationship is recovered exactly (coefficients `[2.0, 3.0]`,
  intercept `5.0`, R²`=1.0`); a clean separable classification problem
  reaches 100% test accuracy with well-formed class probabilities —
  `TestRegression`/`TestClassification`.

All example numbers in every test file were independently hand-computed
and cross-checked by directly exercising the module (via a scratch
script, not shown in the final diff) before being written into the test
files — not "whatever the code happened to produce."

## 4. Explicitly out of scope

- **No persisted model registry.** "Store model/data fingerprints,
  features and metrics" is satisfied via the typed result object
  (always returned) plus an audit-log entry (always written) — never a
  new database table, no "train once, predict many times later"
  persisted model a caller can reference by id across separate calls.
  Every call is self-contained: train and (optionally) predict happen
  together, in one deterministic, stateless invocation — the identical
  "engine first, persistence/governance later, if ever" posture this
  platform's own Prompt-17-then-18 and Prompt-39 precedents already
  established.
- **No categorical feature encoding.** Every feature (and a regression
  target) must already be numeric; a caller must pre-encode categorical
  features (one-hot, ordinal, ...) before calling this engine. A
  disclosed, bounded scope for this engine version, not a silent
  limitation.
- **No automatic integration with the scenario/optimization layers.** A
  predictive model's own coefficients are not automatically fed into
  `analytics.scenario`/`optimization.engine` as assumption bounds or
  objective coefficients in this pass — a natural, disclosed follow-up.
- **No multi-class logistic-regression coefficient disclosure beyond the
  first class.** `PredictiveModelMetadata.coefficients` reports the
  first class's coefficient vector for a multi-class problem (scikit-
  learn's own `coef_[0]`) — a disclosed, bounded scope; binary
  classification (the mission's own churn/risk examples) is unaffected.
- **No general nonlinear model support.** Linear/logistic regression
  only, matching this engine's own closed, reviewed model catalog —
  never an arbitrary scikit-learn estimator a caller could name.

## 5. Testing

5 test files touched (4 new, 1 extended):

- `tests/test_predictive_analytics.py` (new, 26 tests) — regression,
  classification, reproducibility, drift, insufficient data, leakage,
  authorization, feature validation, and train/test separation.
- `tests/test_api_predictive.py` (new, 14 tests) — feature flag,
  training response shape matching the hand-verified fit exactly,
  prediction scoring, an unrecognized `domain` as a 422 (not a 500),
  client-supplied-identity rejection, leakage/insufficient-data/
  classification paths, payload bounds, and both drift outcomes.
- `tests/security/test_predictive_never_presents_prediction_as_fact.py`
  (new, 8 tests) — the cross-cutting structural (closed model catalog),
  behavioral (`truth_level` never promoted across six representative
  outcomes), and text-level (through the live multi-agent handler)
  guarantees.
- `tests/test_multiagent_supervisor.py` (extended, +4,
  `TestPredictiveAndWorkspace`) — explicit-request-only gating,
  insufficient data disclosed as an open item (never a claim), and the
  identity-cannot-be-smuggled-through-the-tool-call guarantee.
- `analytics/forecasting.py`'s own existing test coverage
  (`tests/test_analytics_forecasting.py`) — re-run unmodified; passes,
  confirming the engine this prompt expands was not itself touched.

**Full backend suite: 4181/4181 tests pass, zero regressions**
(`pytest -q`, re-run after every change in this prompt). `ruff check`/
`black --check` clean on every new/changed file. `mypy` clean on every
new/changed production and test file (the 7 remaining errors are
pre-existing, in files this prompt never touched — confirmed via `git
status`). `bandit`: 0 findings across every new/changed production
module (2616 lines scanned).

## 6. Security / tenant-isolation / performance review

- **Security**: `analytics/predictive.py` is a pure function with no I/O
  beyond reading the small, hand-authored `config/sensitive_columns.yaml`
  file, and no LLM call of its own — neither prompt-injectable nor able
  to reach any new data. The closed model catalog
  (`PredictiveModelType`) structurally prevents an arbitrary or
  LLM-authored model class from ever being requested. Authorization is
  independently re-checked inside the engine itself (not merely a
  frontend/REST-layer restriction) — the same `agent/authz.py`-stated
  "no frontend-level restriction is a security boundary ... the
  corresponding internal node re-checks independently" principle every
  other engine in this codebase already follows.
- **Tenant isolation**: `api/predictive.py`'s two routes never touch a
  database and never read tenant-scoped storage — the caller-supplied
  dataset(s) are exactly what was sent. The one live multi-agent caller
  operates on rows the supervisor's own governance step
  (`govern_rows_for_caller`) already redacted before any engine sees
  them — unchanged by this prompt.
- **Performance**: `scikit-learn`'s `LinearRegression`/
  `LogisticRegression` are fast, closed-form/convex solvers; payload
  bounds (`predictive_max_features`/`predictive_max_predict_rows`/
  `max_result_rows`) cap this pure-compute endpoint's own worst-case
  cost regardless of what a caller sends. Disabling `Settings
  .enable_predictive_analytics` removes the REST surface entirely (404)
  with zero other behavior change; the multi-agent specialist is already
  a no-op for every turn that supplies no `predictive_requests`.

## 7. Known limitations / remaining risks

- No persisted model registry — every training run is self-contained;
  see §4.
- No categorical feature encoding — numeric features only; see §4.
- No automatic integration with the scenario/optimization engines' own
  coefficients/bounds; see §4.
- Multi-class classification discloses only the first class's
  coefficient vector; see §4.
- No nonlinear model support (linear/logistic only); see §4.

## Recommended next prompt

Add a persisted model registry (a new, dedicated table, following the
exact pattern Prompt 18's `RecommendationRecord` already established for
a different engine's own outputs) so a model trained once can be scored
against many later, separate requests by its own `model_fingerprint` —
closing this prompt's own most consequential disclosed gap ("no
persisted model registry," §4) — and/or categorical feature encoding
(one-hot/ordinal), extending feature validation to accept and
transparently encode non-numeric columns rather than rejecting them
outright.
