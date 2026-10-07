# 37 — Advanced Root Cause Engine

Prompt 37 of the ongoing Enterprise AI Analytics & Recommendation
Platform initiative, following `14_ANOMALY_ROOT_CAUSE_CONTRACT.md` (the
original anomaly-detection/root-cause engine) and reusing Prompt 34's
multi-agent supervisor (`34_MULTI_AGENT_SUPERVISOR_CONTRACT.md`), which
had already wired Prompt 14's flat engine into one live (test-only, not
yet caller-triggered) agent. **INSPECT/PLAN/IMPLEMENT/TEST/REVIEW/FIX/
DOCUMENT** — one new computation module (`analytics/causal.py`), additive
model/engine extensions, one new multi-agent tool, additive config
surface, and a dedicated security regression test. No existing route,
node, prompt, or `Settings` field changed behavior for any pre-existing
caller.

## 1. Inspection: what already existed vs. the real gap

`analytics/root_cause.py::investigate_root_cause` (Prompt 14) already
does one, flat, single-level contribution breakdown between a current
and a baseline period: group by a dimension (or a flattened composite of
several), rank by `|contribution|`, keep only what clears a percentage-
of-magnitude bar. What it does **not** do, and what this prompt's mission
explicitly names:

- **No hierarchy.** A multi-column `dimension_columns` flattens into one
  composite label (`"Region / Category"`); it never drills from one
  dimension into a *subset* of rows at a second dimension, the way a
  human analyst would ("revenue is up — which region? — now, within that
  region, which category?").
- **No sample-size/confidence enforcement.** A contributor's
  `contribution_percent` is a pure arithmetic ratio over sums; nothing
  checks whether the underlying row counts are large enough, or whether
  the difference is distinguishable from random noise, for that ratio to
  mean anything. A single outlier row can clear the bar exactly as
  confidently as a stable, well-sampled trend.
- **No interaction testing.** Nothing compares a joint (two-dimension)
  breakdown against the two dimensions' own independent contributions to
  detect a genuine synergy effect versus two additive, unrelated moves.
- **No explicit correlation-vs-causation framing.** `RootCauseResult` had
  no field at all distinguishing "this is a statistical association" from
  a stronger claim — the distinction existed only informally, in prose,
  in that module's own docstring.

Separately, inspection found Prompt 34's multi-agent supervisor
(`agent/multiagent/agents.py::root_cause_handler`) already calls
`investigate_root_cause` as its one `root_cause` specialist, gated on an
explicit, caller-supplied `RootCausePair` — but `api/analyst.py`'s own
`/analyst` route never populates `root_cause_pairs` at all (confirmed by
reading the route directly), so in production today this path is
reachable only by a future caller of `run_supervised_analysis` that
supplies one explicitly, or by tests. This prompt's new capability rides
the same, already-established "explicit pair, planner never creates one"
gate — see §4 for what stays deliberately out of scope.

## 2. What was built

### `analytics/causal.py` (new)

`investigate_root_cause_advanced(current_rows, baseline_rows, columns,
dimension_hierarchy, value_column, settings=None) ->
AdvancedRootCauseResult` — reuses `investigate_root_cause` (Prompt 14,
byte-for-byte, never reimplemented) as the per-level computation, and
layers four new capabilities on top of it:

1. **Sample-size/confidence enforcement**
   (`StatisticalSignificance`/`_two_sample_significance`): every
   contributor Prompt 14's own contribution-percent bar already validated
   is re-checked with a Welch-style two-sample z-test over the *raw*
   per-row values underlying each period's total (stdlib only —
   `statistics.pvariance` for within-group variance, `math.erf` for the
   exact normal CDF; no numpy/scipy). A contributor with fewer than
   `Settings.root_cause_min_sample_size` rows on either side never gets a
   p-value computed at all (`SignificanceVerdict.INSUFFICIENT_SAMPLE`) —
   the literal "enforce sample-size/confidence thresholds and return
   insufficient evidence when necessary" requirement.
2. **Known causes vs. distractors** (`ContributorEvidence.is_distractor`):
   a contributor that clears the contribution-percent bar but fails the
   significance check is a distractor — numerically large, statistically
   unsupported — reported in `HierarchicalRootCauseNode.distractors`,
   never promoted into a claim.
3. **Hierarchical drill-down** (`HierarchicalRootCauseNode.children`): for
   a multi-level `dimension_hierarchy` (e.g. `[["Region"], ["Category"]]`),
   only a *statistically supported* (never a distractor) top-level
   contributor is drilled into, over the real subset of rows matching its
   own label — "only drill where there's already evidence" bounds the
   recursion to `Settings.root_cause_max_hierarchy_depth`, never a
   combinatorial expansion of every contributor at every level.
4. **Interaction testing** (`InteractionEffect`/`_detect_interactions`):
   when a hierarchy names exactly two single-column levels, this module
   runs the textbook two-way, unweighted-means fixed-effects interaction
   decomposition (`interaction = cell_contribution - (row_mean(a) +
   col_mean(b) - grand_mean)`) over the full observed `(label_a, label_b)`
   grid — **exactly `0` for a perfectly additive dataset**, verified
   directly (`TestInteractionDetection
   ::test_a_perfectly_additive_dataset_has_exactly_zero_interaction`), and
   strictly non-zero whenever one specific combination moves differently
   than its two main effects alone would predict. A genuine interaction is
   only ever reported (`is_statistically_justified=True`) when that cell's
   own sample clears the significance bar *and* its residual clears
   `Settings.root_cause_interaction_min_percent`.

### Correlation, never confirmed causation (structural, not conventional)

`AdvancedRootCauseResult.causation_confirmed` is declared
`Literal[False]` — pydantic rejects construction with `True` (proven
directly: `tests/security/test_causal_never_asserts_causation.py
::TestTypeLevelGuarantee::test_causation_confirmed_cannot_be_constructed_as_true`),
and mypy rejects it statically too. `evidence_type` is a new, deliberately
**singleton** enum, `CausalEvidenceStrength.CORRELATION_ONLY` — the only
value any code path in this module can ever produce, until (if ever) a
future module with a real controlled experiment/counterfactual method
earns a second member. Every result carries a fixed, non-LLM-authored
`causation_disclaimer` constant. `truth_level` is always
`DataTruthLevel.AI_INFERENCE` (the same framing `ForecastResult
.truth_level` already established for a deterministic-but-still-
inferential result — the raw contribution arithmetic is `DATABASE_FACT`-
equivalent, but *which* contributors to trust and how the hierarchy/
interactions are framed is an interpretation over it).

### `analytics/engine.py` (extended, minimal diff)

One new, additive function: `group_values_by_label` — the identical
grouping/null-skipping/composite-label convention
`group_and_sum_by_label` already established (Prompt 14), but returning
every underlying raw value per label instead of just the sum. Does not
modify `group_and_sum_by_label` itself at all — a second, independent
pass over the same rows, so neither function's contract changes for its
existing callers.

### `analytics/models.py` (extended, additive only)

New types: `CausalEvidenceStrength`, `CAUSATION_DISCLAIMER`,
`SignificanceVerdict`, `StatisticalSignificance`, `ContributorEvidence`,
`InteractionEffect`, `HierarchicalRootCauseNode` (recursive, via
`model_rebuild()`), `AdvancedRootCauseResult`, `CAUSAL_ENGINE_VERSION`.
Every pre-existing Prompt-13/14/16 type (`RootCauseResult`,
`ContributorStat`, `ForecastResult`, ...) is byte-for-byte unchanged.

### Multi-agent wiring (`agent/multiagent/`)

- `tools.py`: a new tool, `TOOL_ROOT_CAUSE_ADVANCED =
  "root_cause_engine_advanced"`, wrapping `investigate_root_cause_advanced`
  — a separate tool name from the existing `TOOL_ROOT_CAUSE`, so each
  carries its own independent circuit breaker/retry policy. The existing
  `TOOL_ROOT_CAUSE`/`_root_cause` binding is completely unmodified.
- `agents.py`: `AgentSpec` for `AgentName.ROOT_CAUSE` now allows both tool
  names. `root_cause_handler` gained one new branch: when
  `task.params["dimension_hierarchy"]` is set **and**
  `Settings.enable_advanced_root_cause` is on, it calls the new tool
  (`_root_cause_advanced`) instead of the old one — otherwise it takes
  **exactly** the pre-Prompt-37 code path (`_root_cause_basic`, a direct
  extraction of the handler's own previous body, unchanged). Every claim
  the advanced path builds carries its own correlation-not-causation
  caveat inline (`"... correlation, not confirmed causation)"`); a
  distractor becomes a disclosed `open_item`, never a claim; a justified
  interaction becomes its own claim.
- `supervisor.py`: `RootCausePair` gained one new, optional field,
  `dimension_hierarchy: tuple[tuple[str, ...], ...] | None = None`
  (default `None` reproduces the pre-Prompt-37 dataclass shape exactly).
  `run_supervised_analysis`'s dispatch loop threads it into the task's
  params only when set.

### "Governed dimensions" (`retrieval/retriever.py`)

New `extract_governing_dimensions` — the mission's "compare baselines
across time and governed dimensions" requirement, mirroring Prompt 10's
own `extract_governing_metrics` exactly: filters already-retrieved
`BUSINESS_CONCEPT` chunks (zero new query) down to `concept_type ==
"dimension"` ones, returning each one's business name and underlying
schema column (`Chunk.table_name`, which
`business_concept_chunk_from_catalog_entry` populates from the catalog
entry's own `technical_name`). **Not yet wired into any live
`dimension_hierarchy` resolution** — see §4; built and tested so a future
caller (a planner deciding which column names to offer the advanced
engine) can prefer an officially governed dimension name over an
arbitrary schema column with confidence it is `CONFIRMED_BUSINESS_TRUTH`.

### Config (`config/settings.py`)

`enable_advanced_root_cause: bool = True`, `root_cause_min_sample_size:
int = Field(default=5, ge=1)`, `root_cause_significance_alpha: float =
Field(default=0.05, gt=0.0, lt=1.0)`, `root_cause_max_hierarchy_depth:
int = Field(default=3, ge=1, le=6)`, `root_cause_interaction_min_percent:
float = Field(default=5.0, gt=0.0)` — same on-by-default-but-escapable,
fully-documented posture as every flag in this series.

## 3. Proof: known causes, distractors, no-cause, and insufficient data

All four, directly in `tests/test_analytics_causal.py`:

- **Known cause**: a single, large, 6-row-per-side contributor with
  `p < 0.05` — `TestKnownCause`.
- **Distractor**: a contribution that clears the base percentage bar but
  has only 1 row on each side (below `root_cause_min_sample_size`) — never
  reaches `contributor_evidence`, only `distractors`, and
  `has_sufficient_evidence` is `False` — `TestDistractor`.
- **No cause**: the exact broadly-distributed fixture
  `tests/test_analytics_root_cause.py`'s own Prompt-14 insufficient-
  evidence test already uses — never even becomes a base-level
  contributor — `TestNoCause`.
- **Insufficient data**: empty current/baseline rows, and a malformed
  `dimension_hierarchy` (raises `ValueError`, never a misleading silent
  result) — `TestInsufficientData`.
- **Hierarchical drill-down**: `TestHierarchicalDrillDown` — only the
  statistically supported top-level contributor (`North`) is drilled
  into; the real within-region driver (`Electronics`) is correctly
  isolated at depth 1; a 3-level hierarchy with
  `root_cause_max_hierarchy_depth=2` stops after exactly one drill and
  discloses the cap.
- **Interaction testing**: `TestInteractionDetection` — a perfectly
  additive dataset reports exactly `0.0` residual for every joint cell
  (not merely "small"); a single-combination-only dataset reports a real,
  statistically justified `25.0`-percentage-point residual; an
  insufficient-sample joint cell is never justified regardless of residual
  size; interaction testing is skipped (with a disclosed note) when the
  first two hierarchy levels aren't both single-column.
- **Correlation, never causation**: `TestCorrelationNeverCausation`, plus
  the dedicated cross-cutting regression file
  `tests/security/test_causal_never_asserts_causation.py` (type-level
  pydantic rejection of `causation_confirmed=True`, and a text-level scan
  of every claim/rationale/disclaimer string across five representative
  scenarios — including through the one live multi-agent handler — for
  an unhedged causal assertion).
- **Reuse, not reimplementation**: `TestKnownCause
  ::test_reuses_prompt_14s_engine_verbatim_rather_than_reimplementing_it`
  asserts `result.root.base_result == investigate_root_cause(...)` called
  directly with the same inputs.

Every number in every test was independently hand-computed and
cross-checked by directly exercising the module before being written
into the test file — not "whatever the code happened to produce."

## 4. Explicitly out of scope

- **Still no live, automatic trigger.** Exactly like Prompt 14's own
  disclosed gap: this application's pipeline generates one SQL query per
  question, and nothing decides "this question needs a second,
  baseline-comparison query" and issues one. `investigate_root_cause_advanced`
  is reachable today the identical way `investigate_root_cause` already
  was — through `RootCausePair` (now optionally carrying a
  `dimension_hierarchy`) passed into `run_supervised_analysis` — which
  `/analyst` still never populates. This prompt upgrades the *engine*,
  not the trigger mechanism; building that trigger (recognizing a "why"
  question, generating the comparison query, choosing a sensible
  hierarchy) remains the next, separate undertaking Prompt 14 already
  recommended and this prompt does not silently absorb.
- **`extract_governing_dimensions` has no live caller either** — built
  and tested (mirroring Prompt 10's `extract_governing_metrics`), but
  nothing yet resolves a `dimension_hierarchy` from it. The same "compute
  + test, defer the deepest wiring" deferral this codebase has used
  repeatedly (Prompts 13 through 16 each did this at least once).
- **Interaction testing is pairwise only.** A 3+-way interaction is out of
  scope — a disclosed, bounded choice, matching `RankingStat`'s own
  flattened-composite-label precedent for `MULTIDIMENSIONAL` results.
- **The two-way interaction decomposition assumes a reasonably complete
  grid.** A sparse/highly unbalanced set of observed `(label_a, label_b)`
  combinations makes the unweighted row/column means a cruder
  approximation of each dimension's "true" main effect — disclosed in
  `_detect_interactions`'s own docstring, the identical category of
  approximation `analytics.anomaly`'s own rolling z-score (no detrending)
  already discloses elsewhere in this package.
- **No per-role restriction on who can call these functions** — same as
  Prompt 14: neither module performs I/O or touches identity/
  authorization context; both inherit whatever already gates the one live
  caller (`root_cause_handler`, itself gated by `Permission.ASK` and the
  supervisor's own request-governance gate).

## 5. Testing

6 test files touched (3 new, 3 extended):

- `tests/test_analytics_causal.py` (new, 20 tests) — known cause,
  distractor, no-cause, insufficient data, hierarchical drill-down + depth
  cap, interaction detection (additive/single-combo/insufficient-sample/
  wrong-shape-hierarchy), and the correlation-never-causation structural
  checks.
- `tests/security/test_causal_never_asserts_causation.py` (new, 7 tests)
  — the cross-cutting type-level + text-level causation guarantee, across
  both `analytics/causal.py` directly and the one live multi-agent
  handler end to end.
- `tests/test_analytics_engine.py` (extended, +4,
  `TestGroupValuesByLabel`) — the new grouping helper's own null-handling/
  composite-label/empty-input behavior.
- `tests/test_retrieval_governing_metrics.py` (extended, +6,
  `TestExtractGoverningDimensions`) — the new governed-dimension
  extraction, mirroring the existing governed-metric test class.
- `tests/test_multiagent_supervisor.py` (extended, +2) — a
  `dimension_hierarchy`-carrying `RootCausePair` routes through the
  advanced engine with correlation-caveat-bearing claims; disabling
  `enable_advanced_root_cause` falls back to the pre-Prompt-37 basic
  path's exact claim text (`"attribution estimate"`), even when a
  hierarchy is supplied.
- `tests/test_analytics_root_cause.py`/`tests/test_analytics_models.py` —
  re-run completely unmodified; both pass, proving Prompt 14's own engine
  and typed contracts were not touched by this prompt.

**Full backend suite: 4020+ tests pass, zero regressions**
(`pytest -q`, re-run after every change in this prompt — exact count at
submission time: 4020 passed before this prompt's own new tests were
added, plus this prompt's own ~39 new tests). `ruff check`/`black --check`
clean on every new/changed file. `mypy` clean on every new/changed
production file (the one pre-existing, unrelated error in
`db/schema_introspection.py` predates this prompt and was not touched).
`bandit` clean (0 findings) on every new/changed production module.

## 6. Security / tenant-isolation / performance review

- **Security**: `analytics/causal.py` is pure functions with no I/O and no
  LLM call of its own — neither prompt-injectable nor able to reach any
  new data. The causation-never-confirmed guarantee is enforced at the
  type level (pydantic `Literal[False]`, mypy-checked), not merely by
  convention — see §2/§3. A dedicated cross-cutting regression test
  (`tests/security/test_causal_never_asserts_causation.py`) locks this in
  at both the type and text level, including through the one live
  multi-agent handler.
- **Tenant isolation**: no new tenant-scoped data read or written. The one
  live caller (`root_cause_handler`) operates on rows the supervisor's own
  governance step (`govern_rows_for_caller`) already redacted before any
  engine sees them — unchanged by this prompt. `extract_governing_dimensions`
  reads only chunks `retrieve_business_context` already retrieved under
  the caller's own tenant/role scope (the same guarantee
  `extract_governing_metrics` already relies on).
- **Performance**: the advanced engine adds pure-Python arithmetic over
  rows already held in memory — no new network call, no new database
  round-trip. Hierarchical recursion is bounded by
  `Settings.root_cause_max_hierarchy_depth` (default `3`) and only ever
  drills into contributors that already cleared both the contribution and
  significance bars, never a combinatorial expansion. Disabling
  `Settings.enable_advanced_root_cause` removes this cost entirely with
  zero other behavior change (the handler takes the identical pre-
  Prompt-37 path).

## 7. Known limitations / remaining risks

- No live, automatic trigger exists yet for a "why" question to generate
  the second, baseline-comparison dataset this engine needs — see §4.
- `extract_governing_dimensions` has no live caller yet — see §4.
- Interaction testing is pairwise-only and assumes a reasonably complete
  observed grid — see §4.
- The significance test is a Welch-style z-test (large-sample
  approximation), not an exact Student's t-test — a disclosed, stdlib-only
  simplification consistent with every other statistical approximation
  already in `analytics/` (e.g. the anomaly engine's own rolling z-score).
  For `root_cause_min_sample_size`'s default of `5`, this approximation is
  reasonable but not exact for very small samples.

## Recommended next prompt

Teach the agent pipeline to recognize a "why did X change" question
(Prompt 11's `AnalyticalIntentType.ROOT_CAUSE`/`ANOMALY`, already
classified but still unused for this) and generate the second,
baseline-comparison SQL query `investigate_root_cause_advanced` needs,
resolving its `dimension_hierarchy` preferentially from
`extract_governing_dimensions` — the concrete next step that closes both
of this prompt's own disclosed "not live-wired" gaps at once, and the
same recommendation Prompt 14 already made, now with a genuinely richer
engine on the other end of it.
