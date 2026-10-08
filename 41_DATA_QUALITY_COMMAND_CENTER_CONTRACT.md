# 41 — Data Quality Command Center

Prompt 41 of the ongoing Enterprise AI Analytics & Recommendation
Platform initiative, the first dedicated data-quality capability in this
codebase — completeness, validity, uniqueness, consistency, referential
integrity, freshness, and distribution stability, scored with
explainable evidence and connected to affected governed metrics via
lineage. **INSPECT/PLAN/IMPLEMENT/TEST/REVIEW/FIX/DOCUMENT** — one new
top-level package (`data_quality/`), a new multi-agent specialist, a new
standalone REST surface (`api/data_quality.py`), additive config
surface, and a dedicated cross-cutting security regression test. No
existing route, node, prompt, or `Settings` field changed behavior for
any pre-existing caller.

## 1. Inspection: what already existed vs. the real gap

Exhaustive grep confirmed: no dedicated data-quality concept (null-spike
detection, duplicate-key detection, orphan/referential checks, a quality
score) existed anywhere in this codebase prior to this prompt. The
closest existing building blocks, all found by inspection and reused
rather than reimplemented:

- `analytics.engine.compute_analytics_result` already computes, per
  column, a count/null-count/is-numeric/min/max/mean
  (`ColumnSummaryStat`) and z-score/IQR outlier findings
  (`OutlierFinding`) over any raw `(columns, rows)` result — this
  prompt's own COMPLETENESS (null rate) and part of VALIDITY (outliers)
  reuse this directly rather than recomputing null counts or outlier
  statistics a second time.
- `analytics.predictive.detect_feature_drift` already runs a two-sample
  Kolmogorov-Smirnov test between a reference and a new dataset — this
  prompt's own DISTRIBUTION_STABILITY dimension reuses it verbatim.
- `onboarding/profiling.py`/`onboarding/pii_detection.py` compute
  per-column statistics (null fraction, distinct count, likely-PII
  signals) for a *brand-new, unprofiled* database during onboarding —
  structurally a different problem (no connection secret is ever
  persisted there, no re-runnable check exists after the one-time
  onboarding pass) and was not reused directly; this prompt's own engine
  instead targets an *already-connected, already-queried* database's own
  result sets, repeatable on demand.
- `optimization.models`/`analytics.scenario.models`'s "never let an LLM
  invent a constraint/assumption" structural pattern (a typed
  `(variable, coefficient)` pair, never a formula string) — reused
  verbatim for `data_quality.models.ConsistencyRule` (a typed
  `left_column OP right_column` comparison, never an expression an LLM
  could author).
- `optimization.validator`/`analytics.predictive`/`recommendation.engine`'s
  independently re-derived `_restricted_column_names` authorization
  pattern — reused for this engine's own column-level restricted-data
  suppression (see §2's own authorization note for why suppression,
  not whole-request rejection, is the right shape here).
- `identity.models.SemanticCatalogEntry.source_tables` — a real,
  persisted field confirmed by direct inspection of `identity/models.py`;
  `source_columns` does **not** exist on that table (every reader that
  renders a `source_columns` field hardcodes it to `()` — confirmed by
  grep). This is what bounds this prompt's own lineage feature to
  table-level matching, a disclosed scope boundary rather than a
  silently approximate column-level claim.

## 2. What was built

### `data_quality/` (new top-level package)

A peer of `analytics/`/`optimization/`/`recommendation/`, not a
submodule of any of them — it answers a different question ("is this
data trustworthy," not "what does this data show," "what should we
decide," or "what should we do about it").

**`data_quality/models.py`** — typed contracts, mirroring
`optimization.models`'s own "never let an LLM invent a check" structural
guarantee: `DateColumnRule`, `CategoricalColumnRule`, `ConsistencyRule`
(a closed `ConsistencyOperator` enum, never a formula string),
`ReferentialCheck`, `FreshnessRule` (an `as_of` the caller supplies,
never the server's own wall clock — the identical "no internal
timestamp" posture `ForecastModelMetadata` already establishes), eight
typed per-kind finding-detail shapes, `DataQualityFinding`,
`DimensionScore`, `TableQualityScore`, `DataQualityReport`, and the
top-level `TableQualityInput`.

**The one deliberate departure from every sibling engine in this
series**: every `DataQualityFinding.claim.level`/`DataQualityReport
.truth_level` is `DataTruthLevel.DATABASE_FACT`, not `AI_INFERENCE` —
unlike `analytics.scenario`/`optimization`/`analytics.predictive`/
`analytics.causal` (all `AI_INFERENCE` ceilings), a null rate, a
duplicate key, an orphaned foreign-key value, an out-of-range date, an
unexpected category, and a stale timestamp are deterministic
measurements of real, already-fetched rows, not predictions/
simulations/inferences about something not yet observed.

**`data_quality/engine.py`** — `assess_data_quality`/`assess_table_quality`
are pure functions over already-fetched table data: never query a
database, never call an LLM. Seven dimensions, each independently gated
on a table actually declaring a relevant check (a dimension with zero
configured checks is never assessed — `DimensionScore.score` is `None`,
not a false `100`, so "we didn't look" is never confused with "we
looked and it's clean"):

1. **COMPLETENESS** — a column's null rate at/above `Settings
   .data_quality_null_spike_threshold` (or a per-column override) —
   reuses `analytics.engine.compute_analytics_result`'s own
   `ColumnSummaryStat`.
2. **VALIDITY** — `DateColumnRule` (an unparseable or out-of-bound
   value, each its own finding reason), `CategoricalColumnRule` (a value
   outside the declared allowed set), plus the outliers
   `analytics.engine` already found.
3. **UNIQUENESS** — `unique_key_sets`: a composite key appearing in more
   than one row (any row with a null key component is skipped, never
   silently counted either way).
4. **CONSISTENCY** — `ConsistencyRule`: a declared `left OP right`
   comparison over numeric or ISO-date/datetime values only; a
   non-comparable or null-sided row is skipped and counted separately,
   never silently treated as a pass.
5. **REFERENTIAL_INTEGRITY** — `ReferentialCheck`: a non-null child
   value absent from the caller-supplied `parent_key_values` is an
   orphan.
6. **FRESHNESS** — `FreshnessRule`: the table's own most recent
   timestamp compared against the caller-supplied `as_of`.
7. **DISTRIBUTION_STABILITY** — reuses `analytics.predictive
   .detect_feature_drift` against a caller-supplied reference dataset.

**Explainable scoring**: every `DimensionScore`/`TableQualityScore`
carries its own disclosed `formula` string (master-contract rule 11).
`TableQualityScore.overall_score` is the unweighted mean of every
assessed (non-`None`) dimension score — a dimension never assessed does
not drag the average toward either extreme.

**Authorization**: a finding whose column `config.sensitive_columns`
classifies "restricted" is suppressed outright for a caller lacking
`Permission.VIEW_RESTRICTED_COLUMNS` — never partially redacted.
Deliberately **not** a whole-request rejection (unlike
`optimization.validator`/`analytics.predictive`, which reject the whole
problem/model for touching a restricted column): a data-quality scan is
diagnostic, read-only work over data the caller already has, so only
the specific restricted finding is withheld, disclosed via
`DataQualityReport.insufficient_checks` and audit-logged
(`security.audit_log.log_security_event`,
`"data_quality_restricted_column_suppressed"`).

### `data_quality/lineage.py` (new)

`build_lineage_report` connects a finding to affected governed metrics
by **table** name only, never by column (see §1's own citation for why
— the catalog schema has no `source_columns` field). Takes a
lightweight per-table severity-count summary (`TableFindingsSummary`),
not the full report — a caller never needs to re-transmit raw finding
detail already returned by a prior scan. **Never asserts a governed
metric IS wrong** — only that it *potentially* reads a table with an
open finding (exposure, not a verdict about the metric's own
correctness); every `limitations` entry says so explicitly. Only a
`"published"` catalog entry is ever linked (a `draft`/`reviewed` one
passed in by mistake is silently excluded — defense in depth, the real
enforcement is the caller only querying published rows).

### REST surface (`api/data_quality.py`, `api/data_quality_schemas.py`, new)

`POST /data-quality/scan` — **never queries a database or calls an
LLM**; the dataset(s) are taken directly in the request body. Gated by
`Settings.enable_data_quality_engine` (404 when off), `Permission.ASK`,
rate limiting, and payload bounds (`data_quality_max_tables`/
`data_quality_max_rows_per_table`).

`POST /data-quality/lineage` — the one route in this feature that *does*
touch a database: it reads the tenant's own published semantic-catalog
entries (`identity.repositories.semantic_catalog.list_entries`) and
connects them to the caller's own already-computed per-table severity
counts. Gated by `require_local_user` (a published catalog entry carries
tenant-scoped business context, so this route needs a resolvable
tenant, the identical requirement every other semantic-catalog-reading
route already has) and rate limiting.

`frontend/vite.config.ts`'s dev-proxy `BACKEND_ROUTES` list was missing
`/data-quality` — found and fixed, the same recurring dev-proxy gap
every new backend prefix since Prompt 26 has needed fixed.

### Multi-agent wiring (`agent/multiagent/`)

A new `AgentName.DATA_QUALITY` specialist, gated on an explicit,
caller-supplied `DataQualityRequest` (`agent/multiagent/supervisor.py`)
— the planner never creates one. Like `ScenarioRequest`/
`PredictiveRequest`, it carries a `data_task`: the data scanned comes
from one `sql_data` subquestion's own already-governed rows, never a
second query. **Scoped to the single-table checks only**
(completeness, validity, uniqueness, consistency) — referential
integrity, freshness, and distribution stability each need a second
dataset (a parent table, a reference period) this specialist does not
have, the identical disclosed scope boundary `RootCausePair`'s own
plain `investigate_root_cause` path has for the live graph. Registered
at `Permission.ASK` at the registry level (the real, specific
restricted-column suppression happens inside `assess_table_quality`
itself) — reused Prompt 39's own found-and-fixed two-tier pattern from
the start.

Its `AgentSpec.max_truth_level` is `DataTruthLevel.DATABASE_FACT` — the
only engine specialist in this series whose ceiling is not
`AI_INFERENCE`, matching the engine's own honest classification.

### Config (`config/settings.py`)

`enable_data_quality_engine: bool = True`,
`data_quality_null_spike_threshold`,
`data_quality_default_max_staleness_seconds`,
`data_quality_max_reported_samples`, `data_quality_max_tables`,
`data_quality_max_rows_per_table` — the mission's own "configurable
thresholds" requirement, same on-by-default-but-escapable, fully
documented posture as every flag in this series.

## 3. Proof: detection, scoring, authorization, and honest classification

All example numbers in every test file were independently hand-computed
and cross-checked by directly exercising the module (via a scratch
script, not part of this diff) before being written into the test
files — not "whatever the code happened to produce":

- **Every dimension's own detection logic** — `tests/test_data_quality_engine.py`
  (`TestCompleteness`/`TestValidity`/`TestUniqueness`/`TestConsistency`/
  `TestReferentialIntegrity`/`TestFreshness`/`TestDistributionStability`):
  a null spike, a per-column threshold override, an unparseable date
  versus an out-of-range one (two distinct, separately counted
  findings), an unexpected category, a duplicate composite key (with a
  null-key row correctly excluded from both numerator and denominator),
  a violated cross-column consistency rule (numeric and date both
  supported, a null-sided row correctly skipped), an orphaned
  referential value, a stale timestamp (a hand-computed 7,948,800-second
  staleness, exactly 92 days), and a shifted distribution via the reused
  KS test.
- **Hand-verified scoring** — `TestScoring`: a 10-row, 4-check table's
  overall score (`77.1`) and every per-dimension score (`70.0`/`90.0`/
  `71.4`/`None`/`None`) independently computed by hand and matched
  exactly; a clean table scores `100.0` on every assessed dimension with
  zero findings; `assess_data_quality` correctly averages only the
  tables that had at least one dimension assessed.
- **Authorization** — `TestAuthorization`: a restricted column's finding
  is suppressed for a `"viewer"` and allowed for an `"analyst"`, with the
  suppression disclosed in `insufficient_checks`; suppression never
  rejects the rest of the scan (a different column's finding on the same
  table is unaffected).
- **No LLM-invented checks** — `TestNoLlmInventedRule`: `ConsistencyRule`
  has no formula/expression field, only typed column names and a closed,
  six-member `ConsistencyOperator` enum.
- **Lineage** — `tests/test_data_quality_lineage.py`: basic table-to-concept
  matching, the severity-floor filter, an unmapped table disclosed
  (never silently dropped), and a non-published entry never linked even
  when passed in by mistake.
- **The honest-classification invariant, proven three ways** —
  `tests/security/test_data_quality_findings_stay_observed_fact.py`:
  structurally (`ConsistencyRule` has no formula field), behaviorally
  (every finding and the overall report's `truth_level` is
  `DATABASE_FACT` across every dimension, including an empty/clean
  table), and at the text level through the one live multi-agent handler
  (claims carry `DATABASE_FACT`, and deliberately do **not** say "modeled
  estimate"/"not an observed fact" — saying so about a genuine observed
  fact would itself be dishonest in the other direction) — plus lineage's
  own "never asserts a metric IS wrong" framing, checked against the
  actual `limitations` text.
- **Multi-agent wiring** — `tests/test_multiagent_supervisor.py`'s new
  `TestDataQualityAndWorkspace` (+4): runs only for an explicit request,
  never attempted otherwise, a no-findings scan is disclosed as an open
  item (never a claim), and `caller_roles` cannot be smuggled through the
  tool's own payload (the registry injects it).
- **REST surface** — `tests/test_api_data_quality.py` (+10): the feature
  flag (both routes 404 when off), a hand-verified scan response
  (`null_count`/`total_count` matching the request exactly), payload
  bounds (too many tables, too many rows), an unknown request field
  rejected (`extra="forbid"`), consistency/referential/freshness rules
  round-tripping through the REST layer, a table linked to a published
  concept via `/lineage`, an unmapped table disclosed, and an
  unrecognized `min_severity` returning a 422 (not a 500).

**Full backend suite: 4236/4236 tests pass, zero regressions**
(`pytest -q`, re-run after every change in this prompt — up from 4181
before this prompt's own 55 new tests: 26 + 7 + 10 across three new test
files, 8 in a new `tests/security/` file, and 4 added to the existing
`tests/test_multiagent_supervisor.py`).

## 4. Explicitly out of scope

- **Lineage is table-level only.** The semantic catalog does not record
  which specific column(s) a governed metric reads — a disclosed,
  honest scope boundary (§1), not an approximation presented as
  precise.
- **The multi-agent specialist covers only the four single-dataset
  dimensions** (completeness/validity/uniqueness/consistency).
  Referential integrity, freshness, and distribution stability are
  fully built and tested but reachable only through the standalone REST
  route today — the identical "stubbed for the live graph, fully
  callable directly" posture `analytics.root_cause`'s own disclosed gap
  already established for a different engine.
- **No persisted scan history or trend-over-time dashboard.** Every call
  is self-contained — the same "engine first, persistence/governance
  later, if ever" posture Prompts 17-then-18 and 39/40 already
  established for their own engines.
- **No automatic trigger proposes a scan from a natural-language
  question.** Both REST routes and the multi-agent specialist require an
  explicit, already-structured request — the identical posture
  `analytics.scenario`/`optimization`/`analytics.predictive` already
  establish for themselves.
- **No column-level profiling reuse from `onboarding/profiling.py`.**
  That module targets a brand-new, unprofiled database during
  onboarding (no persisted connection secret, no re-runnable check); this
  engine targets an already-connected database's own result sets,
  repeatable on demand — a deliberately separate, simpler data shape
  (caller-supplied `columns`/`rows`), not a reuse of that module's own
  connection-aware profiling pipeline.

## 5. Testing

5 test files touched (4 new, 1 extended):

- `tests/test_data_quality_engine.py` (new, 26 tests) — every dimension's
  detection logic, scoring, authorization, and the structural
  no-LLM-invented-rule guarantee.
- `tests/test_data_quality_lineage.py` (new, 7 tests) — table-to-concept
  matching, the severity floor, unmapped-table disclosure, and the
  published-only guarantee.
- `tests/test_api_data_quality.py` (new, 10 tests) — feature flag, scan
  response shape matching the hand-verified computation exactly, payload
  bounds, unknown-field rejection, rule round-tripping, and both
  `/lineage` outcomes.
- `tests/security/test_data_quality_findings_stay_observed_fact.py` (new,
  8 tests) — the cross-cutting structural, behavioral, and text-level
  honest-classification guarantees, plus lineage's own
  exposure-not-a-verdict framing.
- `tests/test_multiagent_supervisor.py` (extended, +4,
  `TestDataQualityAndWorkspace`) — explicit-request-only gating,
  no-findings disclosed as an open item, and the
  identity-cannot-be-smuggled-through-the-tool-call guarantee.

**Full backend suite: 4236/4236 tests pass, zero regressions**
(`pytest -q`, re-run after every change in this prompt). `ruff check`/
`black --check` clean on every new/changed file. `mypy` found two real
type errors in `data_quality/engine.py` during this pass's own review
(a cross-type date/numeric comparison in `_compare`, and a mis-typed
loop variable reused across two differently-typed rule lists in
`_validity_rule_findings`) — both fixed by normalizing each comparison
branch to one concrete type (via a `TypeVar` constrained to
`float`/`datetime`) and renaming the reused loop variable; `mypy` is now
clean on every new/changed production and test file (one pre-existing,
unrelated error in `db/schema_introspection.py` was confirmed present
before this prompt and untouched by it).

## 6. Security / tenant-isolation / performance review

- **Security**: `data_quality/engine.py`/`lineage.py` are pure functions
  with no I/O beyond reading the small, hand-authored `config
  /sensitive_columns.yaml` file, and no LLM call of their own — neither
  prompt-injectable nor able to reach any new data. Authorization is
  independently re-checked inside the engine itself (not merely a
  frontend/REST-layer restriction), the same `agent/authz.py`-stated
  principle every other engine in this codebase already follows.
  `api/data_quality.py`'s `/lineage` route never exposes a draft/
  reviewed catalog entry (defense in depth at both the repository query
  and the lineage function itself).
- **Tenant isolation**: `/scan` never touches a database and never reads
  tenant-scoped storage — the caller-supplied dataset(s) are exactly
  what was sent. `/lineage` resolves `tenant_id` from the authenticated
  local account (`security.tenancy.resolve_actor_tenant_id`), never from
  a client-supplied value, and scopes `list_entries` to that tenant —
  the identical pattern every other semantic-catalog-reading route
  already establishes. The one live multi-agent caller operates on rows
  the supervisor's own governance step (`govern_rows_for_caller`)
  already redacted before any engine sees them — unchanged by this
  prompt.
- **Performance**: every dimension's own computation is linear in row
  count (no nested scans); payload bounds
  (`data_quality_max_tables`/`data_quality_max_rows_per_table`) cap this
  pure-compute endpoint's worst-case cost regardless of what a caller
  sends. Disabling `Settings.enable_data_quality_engine` removes the REST
  surface entirely (404) with zero other behavior change; the
  multi-agent specialist is already a no-op for every turn that supplies
  no `data_quality_requests`.

## 7. Known limitations / remaining risks

- Lineage is table-level only — see §4.
- The multi-agent specialist covers only four of the seven dimensions —
  see §4.
- No persisted scan history/trend dashboard — see §4.
- No automatic natural-language trigger for a scan — see §4.
- Outlier detection (reused from `analytics.engine`) inherits that
  module's own disclosed scope: it only runs for a `CATEGORICAL_AGGREGATE`/
  `MULTIDIMENSIONAL`-shaped result with at least 3 (zscore) or 4 (IQR)
  distinct labels — a smaller or differently-shaped table gets no
  outlier check, disclosed via the reused engine's own
  `insufficient_data_reasons`, not silently skipped.

## Recommended next prompt

Wire the four currently-REST-only dimensions (referential integrity,
freshness, distribution stability, plus a genuinely multi-table
orchestration) into the multi-agent supervisor by extending
`DataQualityRequest` to optionally name a second `data_task` (a parent
table, a reference period) — the identical "two-dataset" extension
`analytics.root_cause`'s own disclosed gap already names for a different
engine — and/or add a persisted scan-history table (following
`identity.models.RecommendationRecord`'s own exact pattern) so a quality
score can be tracked over time and a regression in it can itself become
a `recommendation.engine` finding under the existing `DATA_QUALITY`
category.
