# 39 — Governed Optimization Engine

Prompt 39 of the ongoing Enterprise AI Analytics & Recommendation
Platform initiative, inspecting and reusing the conventions Prompt 37
(causal), Prompt 38 (scenario), and Prompt 34 (multi-agent supervisor)
already established, applied to a genuinely new kind of problem: optimal
decision-variable values subject to constraints, not a comparison or a
projection. **INSPECT/PLAN/IMPLEMENT/TEST/REVIEW/FIX/DOCUMENT** — one new
top-level package (`optimization/`), a new RBAC permission, a new
multi-agent specialist, a new standalone REST surface
(`api/optimization.py`), additive config surface, and a dedicated
cross-cutting security regression test. No existing route, node, prompt,
or `Settings` field changed behavior for any pre-existing caller.

## 1. Inspection: what already existed vs. the real gap

Exhaustive grep confirmed: no decision-optimization/LP/MILP concept
existed anywhere in this codebase prior to this prompt.
`analytics.scenario`/`analytics.causal` compare real-vs-hypothetical or
real-vs-real datasets; nothing searches for an *optimal* assignment of
variables subject to constraints. `recommendation.engine` reasons over
already-computed facts into a suggested next step; it has no notion of a
mathematical objective/constraint system at all. The closest existing
building blocks worth reusing, all found by inspection:

- `analytics.scenario.ScenarioParameter`'s "typed, bounded, named
  assumption" shape, `recommendation.engine._restricted_column_names`'s
  authorization pattern, and `analytics.causal.AdvancedRootCauseResult
  .causation_confirmed: Literal[False]`'s type-level-guarantee precedent
  — all informed this engine's own typed-contract design (see §2).
- `agent.multiagent.agents.scenario_handler`'s "explicit request, planner
  never creates one" gate, and `agent.tools.registry.ToolRegistry`'s
  identity-injection mechanics — reused verbatim for the new
  `optimization` specialist.
- `api/scenarios.py`'s "feature flag → 404, `Permission`-gated, rate-
  limited, payload-bounded, never touches a database" route-construction
  pattern — reused verbatim for `api/optimization.py`.
- `scipy` was already a **transitive** dependency (via scikit-learn/
  sentence-transformers' own ML dependencies, used by embeddings/media
  search) with a real, modern LP/MILP solver (`scipy.optimize.linprog`,
  HiGHS method) already installed and verified working — pinned
  explicitly in `requirements.txt` now that `optimization/engine.py`
  imports it directly, the identical "was transitive, now direct, so pin
  it" pattern `pymupdf`/`defusedxml`/`pydantic` already establish there.
  **No new dependency was added to solve the mission's "deterministic
  optimization where possible" requirement.**

## 2. What was built

### `optimization/models.py` (new)

The mission's own named requirement, made concrete and **structural**:
"Define decision variables, objective, constraints, rules, source data
and version" — and, separately, "never let an LLM invent constraints or
objectives." That second rule is enforced by the shape of these types
themselves, not a runtime check: `ObjectiveTerm`/`ConstraintTerm` are
`(variable, coefficient)` **numeric pairs** referencing an already-
declared `DecisionVariable` by name — there is no field anywhere in this
module, or in its REST-layer mirror (`api/optimization_schemas.py`),
that accepts a free-text formula/expression string. Proven directly
(`tests/security/test_optimization_never_presents_recommendation_as_fact.py
::TestStructuralNoFormulaField`): both term types, at both layers, have
exactly two fields.

- `DecisionVariable`: `name`, `var_type` (`CONTINUOUS`/`INTEGER`/
  `BINARY`), `lower_bound`/`upper_bound`, `unit`, `description`.
- `Objective`: `sense` (`MINIMIZE`/`MAXIMIZE`), `terms`, `constant`.
- `Constraint`: `name`, `terms`, `operator` (`<=`/`>=`/`==`), `rhs`,
  `description`.
- `OptimizationProblem`: the full typed problem — `domain` (`PRICING`/
  `RESOURCE_ALLOCATION`/`INVENTORY`/`WORKFORCE`/`CAPACITY`/`OTHER`),
  `decision_variables`, `objective`, `constraints`, `business_rules`
  (descriptive provenance only — see §4 for why this is a disclosed,
  deliberately bounded scope rather than a full reusable rule-template
  registry), `source_data_description`, `source_columns` (for the
  restricted-column authorization check), `model_version`.
- `OptimizationStatus`: `OPTIMAL`/`INFEASIBLE`/`UNBOUNDED`/
  `UNAUTHORIZED`/`INVALID`/`ERROR` — `UNAUTHORIZED` (a property of *who
  is asking*) and `INFEASIBLE` (a property of *the math*) are
  deliberately distinct values, never conflated in a log or a UI.
- `OptimizationSolution`: `status`, `objective_value`, `variables`
  (`VariableSolution`, with an optional `reduced_cost`),
  `constraint_sensitivity` (`ConstraintSensitivity`: `slack`,
  `shadow_price`, `binding`), `sensitivity_available`,
  `infeasibility_reasons`, `truth_level` (always `AI_INFERENCE`),
  `limitations`, `engine_version`.

### `optimization/validator.py` (new)

`validate_problem` — the mission's own "reject invalid or unauthorized
assumptions" requirement. Reports *every* violation found, never
stopping at the first (the identical "no partial trust" convention
`analytics.scenario.validate_scenario_parameters` already establishes):
undeclared variable references (objective and every constraint),
duplicate names, non-finite/malformed bounds, too many variables/
constraints, and the two authorization checks below. `OptimizationValidation
.is_unauthorized` is a typed flag (not a string-matching inference over
`violations`), so `optimization.engine.solve_optimization_problem` can
report `UNAUTHORIZED` vs. `INVALID` precisely.

### `optimization/engine.py` (new) — deterministic LP/MILP solving

`solve_optimization_problem` validates, then solves via
`scipy.optimize.linprog` (HiGHS) — one solver entry point for both
continuous and mixed-integer problems (via `linprog`'s own `integrality`
parameter), never two parallel code paths to keep in sync.

**Sensitivity information, only when it is theoretically valid.**
Shadow prices (LP duals) are a well-defined concept only for a
*continuous* linear program — a mixed-integer problem's duals (whatever
HiGHS happens to report for its final LP relaxation) do not have the
same marginal-value interpretation once integrality constraints bind, a
standard result in optimization theory, not an arbitrary choice this
engine makes. `sensitivity_available` is `True`, and shadow prices/
reduced costs are ever populated, **only** when every decision variable
is `CONTINUOUS` — verified directly
(`TestSolve::test_a_mixed_integer_problem_never_reports_sensitivity`,
`tests/test_optimization_engine.py::TestFeasible
::test_a_mixed_integer_problem_solves_and_respects_integrality`).

**The sign-convention math was empirically verified, not just derived on
paper**, against six hand-computed combinations of
`(ConstraintOperator, ObjectiveSense)` — see `optimization.engine
._shadow_price_sign`'s own docstring for the full derivation and the
exact worked numbers, and `tests/test_optimization_engine.py
::TestRegression` for the three regression tests this produced (a
published textbook maximize-LP with three constraints, a minimize-with-
GE-vs-EQ equivalence check, and a maximize-with-GE sign-cancellation
check).

### RBAC — a new, dedicated permission

`agent.authz.Permission.RUN_OPTIMIZATION` (new), granted starting at the
`_ANALYST` tier (the identical reasoning `VIEW_RESTRICTED_COLUMNS`
already uses) — the mission's own "require authorization" requirement,
deliberately **not** folded into `Permission.ASK`: an optimization
result is a suggested real-world decision (a price, a shift schedule, an
allocation), a more consequential capability than a read-only question.
Enforced at the REST layer (`Depends(require_permission
(Permission.RUN_OPTIMIZATION))`) and, independently, inside
`optimization.validator.validate_problem` itself (`has_role_permission`)
— the second check is the one that actually fires for the multi-agent
specialist, which deliberately registers its own tool at the `ASK` tier
(see the next section for why) and relies on the engine's own richer,
disclosed `UNAUTHORIZED` status instead of a bare, unexplained denial.

### Audit

Every authorization denial inside `validate_problem` is logged via
`security.audit_log.log_security_event` (`"optimization_unauthorized"`)
— never the problem's own coefficients/values, only `problem_id`/
`domain`/(for the restricted-column case) which columns, the identical
"log metadata, never the sensitive content" posture `recommendation
.engine`'s own `"recommendation_evidence_suppressed"` event already
establishes.

### Multi-agent wiring (`agent/multiagent/`) — and a real bug this prompt's
### own test suite found and fixed

A new `AgentName.OPTIMIZATION` specialist, gated on an explicit,
caller-supplied `OptimizationRequest` (`agent/multiagent/supervisor.py`)
— the planner never creates one. Unlike `ScenarioRequest`/`RootCausePair`,
it carries no `data_task`: a problem is already fully specified by its
own caller, never derived from a `sql_data` task's rows.

**A real, found-and-fixed design bug**: the new tool was first
registered with `permission=Permission.RUN_OPTIMIZATION` at the
*registry* level. `tests/test_multiagent_supervisor.py
::TestOptimizationAndWorkspace::test_an_unauthorized_caller_never_gets_a_solved_result`
caught that this made the registry's own `ToolGateway`/`ToolRegistry
.execute` deny the call with a bare, generic `AgentOutput(status="denied",
detail="tool not permitted")` **before** `optimization_handler`'s own,
richer `UNAUTHORIZED`-status handling ever ran — a materially worse,
unexplained user-facing outcome than `scenario`/`root_cause` give for
their own (always-granted) `Permission.ASK`-gated tools. Fixed by
registering `TOOL_OPTIMIZATION` at `Permission.ASK` too (a pure
formality every caller who can reach the supervisor at all already
clears) and letting the engine's own internal `validate_problem` be the
*only* place `Permission.RUN_OPTIMIZATION` is actually enforced for this
path — restoring the identical two-tier pattern `scenario`/`root_cause`
already use, verified by re-running the fixed test.

### REST surface (`api/optimization.py`, `api/optimization_schemas.py`, new)

`POST /optimization/validate` and `POST /optimization/solve` — **neither
route ever queries a database or calls an LLM.** Both take a fully-
specified problem directly in the request body. Gated by `Settings
.enable_optimization_engine` (404 when off), `Permission.RUN_OPTIMIZATION`,
rate limiting, and payload bounds (`Settings
.optimization_max_variables`/`optimization_max_constraints`).

### Config (`config/settings.py`)

`enable_optimization_engine: bool = True`, `optimization_max_variables:
int = Field(default=50, gt=0)`, `optimization_max_constraints: int =
Field(default=100, gt=0)` — same on-by-default-but-escapable,
fully-documented posture as every flag in this series.

## 3. Proof: feasible, infeasible, boundary, and regression cases

All directly in `tests/test_optimization_engine.py`:

- **Feasible**: a continuous LP, a mixed-integer problem (verified
  genuinely integral), a binary variable forced to `{0,1}` regardless of
  caller-supplied bounds, a trivial empty-constraint problem —
  `TestFeasible`.
- **Infeasible/unbounded**: contradictory bounds reported as `INFEASIBLE`
  with a non-empty `infeasibility_reasons`; an unbounded objective
  reported as `UNBOUNDED`, distinctly — `TestInfeasibleAndUnbounded`.
- **Boundary**: a variable resting exactly at its own upper bound; a
  constraint with exactly zero slack (`binding=True`); a variable whose
  `lower_bound == upper_bound` (pinned to one value) — `TestBoundaryCases`.
- **Regression**: a published, independently-verifiable textbook LP
  (known optimum `x=2, y=6, Z=36`; known shadow prices `0`/`1.5`/`1.0`),
  a GE-vs-EQ equivalence check, and the one sign-cancellation combination
  not otherwise exercised — `TestRegression`.
- **Unauthorized**: missing `Permission.RUN_OPTIMIZATION` reported as
  `UNAUTHORIZED` (not `INVALID`); a restricted `source_columns` entry
  rejected for a `"viewer"`, allowed for an `"analyst"` — `TestUnauthorized`.
- **No partial trust**: an undeclared variable reference, malformed
  bounds, a `NaN` bound, and too many variables each reject the *whole*
  problem; an unused (but otherwise valid) variable is a `warnings` note,
  never a `violations` entry — `TestNoPartialTrust`.
- **Never let an LLM invent constraints/objectives**: the structural
  two-field proof, plus a test that an undeclared-variable "constraint"
  (the only way extra logic could be smuggled in) is caught by
  validation, never silently solved — `TestNeverLetAnLlmInventConstraintsOrObjectives`.

## 4. Explicitly out of scope

- **`business_rules` is descriptive provenance only in this pass, not a
  separately enforced or reusable constraint-template registry.** A
  caller names which named business rules/policies a problem's
  constraints are meant to encode (e.g. `"minimum wage floor"`), but
  this prompt does not build a YAML-backed, reusable rule library a
  problem could reference by name instead of restating the constraint —
  a disclosed, deliberately bounded scope given this prompt's own
  already-large surface area; the actual enforcement is always and only
  through `constraints`. A natural "extensible optimization framework"
  follow-up.
- **No automatic integration with the scenario/analytics/recommendation
  layers' own live data.** An `OptimizationProblem` is self-contained and
  caller-specified; this pass does not add a mechanism that automatically
  pulls real per-row costs/prices from a `sql_data` task's own results
  into a problem's coefficients (unlike `ScenarioRequest`, which *does*
  read a `data_task`'s rows) — a deliberate, disclosed deferral, the same
  "compute + test, defer the deepest wiring" posture this series has used
  repeatedly.
- **No IIS (irreducible infeasible subset) computation.** An infeasible
  result reports the solver's own message, never an automated diagnosis
  of *which* constraints are mutually contradictory.
- **No general nonlinear/quadratic objective support** — this engine is
  linear programming/mixed-integer linear programming only, matching
  `scipy.optimize.linprog`'s own scope; a quadratic objective (e.g. a
  risk term) would need a different solver entry point entirely, not
  attempted here.
- **No persistence, lifecycle, or governance workflow** for a solved
  problem — unlike Prompt 18's recommendation governance, there is no
  `OptimizationRecord` table or saved-run history in this pass.

## 5. Testing

5 test files touched (4 new, 1 extended):

- `tests/test_optimization_engine.py` (new, 22 tests) — regression
  (3 hand-verified textbook problems), feasible, infeasible/unbounded,
  boundary, unauthorized, no-partial-trust, and the structural
  no-formula-field proof.
- `tests/test_api_optimization.py` (new, 14 tests) — feature flag,
  validation response shape, an unrecognized `var_type` as a 422 (not a
  500), client-supplied-identity rejection, payload bounds, a full solve
  matching the regression numbers exactly, whole-problem rejection, and
  the mixed-integer sensitivity-suppression guarantee at the API layer.
- `tests/security/test_optimization_never_presents_recommendation_as_fact.py`
  (new, 10 tests) — the cross-cutting structural (two-field terms, at
  both layers), behavioral (`truth_level` never promoted across five
  representative outcomes), and text-level (through the live multi-agent
  handler) guarantees.
- `tests/test_multiagent_supervisor.py` (extended, +4,
  `TestOptimizationAndWorkspace`) — explicit-request-only gating, an
  infeasible problem disclosed as an open item (never a claim), and the
  unauthorized-caller fix described in §2 above, verified.
- `agent/authz.py`'s own existing test coverage (`tests/test_api_authz.py`)
  — re-run unmodified; passes, confirming the new `RUN_OPTIMIZATION`
  permission didn't disturb any existing role/permission check.

**Full backend suite: 4129/4129 tests pass, zero regressions**
(`pytest -q`, re-run after every change in this prompt). `ruff check`/
`black --check` clean on every new/changed file. `mypy` clean on every
new/changed production and test file (the 7 remaining errors are
pre-existing, in files this prompt never touched — confirmed via `git
status`). `bandit`: 0 findings across every new/changed production
module (2608 lines scanned in `optimization/` alone).

## 6. Security / tenant-isolation / performance review

- **Security**: `optimization/engine.py`/`validator.py` are pure
  functions with no I/O beyond reading the small, hand-authored
  `config/sensitive_columns.yaml` file, and no LLM call of their own —
  neither prompt-injectable nor able to reach any new data. The
  "never let an LLM invent constraints/objectives" guarantee is
  structural (no formula field exists anywhere in the typed contract, at
  either the engine or the REST layer), not a runtime filter that could
  be bypassed or forgotten. Authorization is layered: a dedicated RBAC
  permission at the REST boundary, re-checked independently inside the
  engine itself for every other caller (the multi-agent specialist, a
  future batch job) — the same `agent/authz.py`-stated "no frontend-level
  restriction is a security boundary ... the corresponding internal node
  re-checks independently" principle every other engine in this codebase
  already follows.
- **Tenant isolation**: `api/optimization.py`'s two routes never touch a
  database and never read tenant-scoped storage — the caller-supplied
  problem is exactly what was sent, with no tenant-scoped lookup to
  isolate. The one live multi-agent caller is self-contained (no
  `data_task`, so no governed-rows dependency at all).
- **Performance**: `scipy.optimize.linprog` (HiGHS) is a mature,
  industrial-strength solver; payload bounds
  (`optimization_max_variables`/`optimization_max_constraints`, defaults
  50/100) cap this pure-compute endpoint's own worst-case cost regardless
  of what a caller sends. Disabling `Settings.enable_optimization_engine`
  removes the REST surface entirely (404) with zero other behavior
  change; the multi-agent specialist is already a no-op for every turn
  that supplies no `optimization_requests`.

## 7. Known limitations / remaining risks

- `business_rules` is descriptive only, not a reusable, enforced
  constraint-template registry — see §4.
- No automatic pull of real baseline data into a problem's own
  coefficients — a problem is always fully caller-specified — see §4.
- No IIS computation for infeasible problems; no nonlinear/quadratic
  objective support — see §4.
- No persistence/governance/dashboard for a solved optimization — see §4.

## Recommended next prompt

Promote `OptimizationProblem.business_rules` from descriptive labels
into a real, reusable, named constraint-template registry (mirroring
`config/sensitive_columns.yaml`'s own hand-authored-YAML, loaded-fresh
convention), and/or teach the multi-agent supervisor to resolve an
`OptimizationProblem`'s own coefficients from a `sql_data` task's real
rows (the identical integration `ScenarioRequest` already has) rather
than requiring every problem to be fully caller-specified — closing both
of this prompt's own disclosed "not yet wired" gaps, the same recurring,
deliberate deferral every engine prompt in this series has made at least
once.
