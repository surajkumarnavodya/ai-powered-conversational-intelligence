"""Typed contracts for the Optimization Engine -- Prompt 39
(`39_OPTIMIZATION_ENGINE_CONTRACT.md`).

The mission's own named requirement, made concrete and structural:
"Define decision variables, objective, constraints, rules, source data
and version" -- and, separately, "never let an LLM invent constraints
or objectives." That second rule is enforced **by the shape of these
types themselves**, not by a runtime check: `ObjectiveTerm`/
`ConstraintTerm` are `(variable, coefficient)` numeric pairs referencing
an already-declared `DecisionVariable` by name -- there is no field
anywhere in this module that accepts a free-text formula, expression, or
natural-language constraint an LLM could author. A caller (human, API
client, or a future LLM-assisted *proposal* layer) can only ever compose
an `OptimizationProblem` out of plain numbers and already-declared
variable names; nothing here can parse or execute a string as logic.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel

#: Bumped when this module's typed shapes, the solver's variable/
#: constraint-building logic, or its sensitivity-sign conventions change
#: in a way a consumer should be able to detect -- master-contract rule
#: 11 ("record ... version metadata"), the identical convention every
#: other engine module in this codebase already follows.
OPTIMIZATION_ENGINE_VERSION = "1.0.0"


class DecisionVariableType(str, Enum):
    """The closed set of decision-variable kinds this engine supports --
    a standard LP/MILP vocabulary, not a free-form string.

    Attributes:
        CONTINUOUS: Any real value within `[lower_bound, upper_bound]`
            (e.g. a price, a fraction of capacity).
        INTEGER: A whole number within the same bounds (e.g. a headcount,
            a unit count).
        BINARY: Exactly `0` or `1` -- `lower_bound`/`upper_bound` are
            ignored and forced to `(0, 1)` regardless of what a caller
            supplies (a decision to do/not do something, e.g. "open this
            facility").
    """

    CONTINUOUS = "continuous"
    INTEGER = "integer"
    BINARY = "binary"


class DecisionVariable(BaseModel):
    """One typed decision variable -- the mission's own "decision
    variables ... with units [and] bounds" requirement.

    Attributes:
        name: A unique identifier referenced by `ObjectiveTerm.variable`/
            `ConstraintTerm.variable` -- never positional, always by name.
        var_type: See `DecisionVariableType`.
        lower_bound: Defaults to `0.0` (the conventional LP default, and
            the overwhelmingly common real-world case -- a negative
            price/headcount/allocation is rarely meaningful). Ignored
            for `BINARY`.
        upper_bound: `None` means unbounded above. Ignored for `BINARY`.
        unit: A short, free-form, descriptive unit label (e.g. `"USD"`,
            `"units"`, `"hours"`, `"headcount"`) -- informational only,
            the identical "descriptive, never re-derived" posture
            `analytics.visualization.ChartField.format` already
            establishes.
        description: A short, human-readable explanation of what this
            variable represents.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    var_type: DecisionVariableType
    lower_bound: float = 0.0
    upper_bound: float | None = None
    unit: str = ""
    description: str = ""


class ObjectiveSense(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class ObjectiveTerm(BaseModel):
    """One `coefficient * variable` term in a linear objective -- plain
    numeric data, never a formula string. See this module's own
    docstring for why that is the literal mechanism behind "never let an
    LLM invent ... objectives."
    """

    model_config = ConfigDict(frozen=True)

    variable: str = Field(..., min_length=1)
    coefficient: float


class Objective(BaseModel):
    """A linear objective: `sense (minimize/maximize) of (sum(terms) +
    constant)`. `constant` never affects *which* solution is optimal
    (it's a constant shift), only the reported `objective_value` -- kept
    anyway so a caller's own real-world objective (e.g. "maximize profit
    = revenue - fixed_costs") can be expressed exactly, not just up to an
    additive constant.
    """

    model_config = ConfigDict(frozen=True)

    sense: ObjectiveSense
    terms: tuple[ObjectiveTerm, ...] = ()
    constant: float = 0.0


class ConstraintOperator(str, Enum):
    LE = "<="
    GE = ">="
    EQ = "=="


class ConstraintTerm(BaseModel):
    """Mirrors `ObjectiveTerm` -- one `coefficient * variable` term on a
    constraint's left-hand side."""

    model_config = ConfigDict(frozen=True)

    variable: str = Field(..., min_length=1)
    coefficient: float


class Constraint(BaseModel):
    """One linear constraint: `sum(terms) operator rhs`. Plain numeric
    data only -- see this module's own docstring.

    Attributes:
        name: A unique identifier, used to report this constraint's own
            `ConstraintSensitivity` (slack/shadow price) back by name.
        terms: The left-hand side.
        operator: See `ConstraintOperator`.
        rhs: The right-hand side constant.
        description: A short, human-readable explanation (e.g. "total
            labor hours may not exceed the week's available capacity").
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    terms: tuple[ConstraintTerm, ...] = Field(..., min_length=1)
    operator: ConstraintOperator
    rhs: float
    description: str = ""


class OptimizationDomain(str, Enum):
    """The mission's own named problem domains, plus an honest escape
    hatch -- a closed set like every other enum in this codebase's
    typed-contract modules, not a free-form string, so a future
    dashboard/report can group by domain without string-matching."""

    PRICING = "pricing"
    RESOURCE_ALLOCATION = "resource_allocation"
    INVENTORY = "inventory"
    WORKFORCE = "workforce"
    CAPACITY = "capacity"
    OTHER = "other"


class OptimizationProblem(BaseModel):
    """One fully-specified optimization problem -- the mission's own
    "decision variables, objective, constraints, rules, source data and
    version" requirement, as one typed, immutable value.

    Attributes:
        problem_id: Caller-supplied identifier for this problem.
        name: A short, human-readable name.
        domain: See `OptimizationDomain`.
        decision_variables: Every variable the objective/constraints may
            reference -- referencing an undeclared name anywhere else in
            this problem is a validation failure (`optimization.validator
            .validate_problem`), never silently ignored.
        objective: See `Objective`.
        constraints: See `Constraint`. Empty constraints is a legitimate
            (if trivial) problem -- "what is the best value of X with no
            restrictions" is still a well-formed question.
        business_rules: Short, human-readable labels of which named
            business rules/policies this problem's constraints are meant
            to encode (e.g. `"minimum wage floor"`,
            `"no shift exceeds 12 hours"`) -- **descriptive provenance
            only in this pass, not a separately enforced or reusable
            constraint-template registry** (a disclosed, deliberate
            scope boundary -- see `39_OPTIMIZATION_ENGINE_CONTRACT.md`
            §4 for the extensibility this leaves for a future prompt).
            The actual enforcement is always and only through
            `constraints` above.
        source_data_description: A short, human-readable disclosure of
            where this problem's coefficients/bounds/RHS values came from
            (e.g. `"AdventureWorks SalesOrderDetail, Q3 2026 unit
            costs"`) -- the mission's own "source data" requirement, kept
            as free text since the actual provenance varies by caller and
            this engine never fetches data itself (see `optimization
            /engine.py`'s own module docstring).
        source_columns: Real database/report column names that informed
            this problem's own coefficients/bounds (when any did) --
            checked against `config.sensitive_columns` the identical way
            `analytics.scenario.ScenarioParameter.affected_metric`
            already is, so a problem built from a restricted column is
            rejected for a caller lacking `agent.authz.Permission
            .VIEW_RESTRICTED_COLUMNS` even though this engine itself
            never reads the database.
        model_version: `OPTIMIZATION_ENGINE_VERSION` at construction
            time.
    """

    model_config = ConfigDict(frozen=True)

    problem_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    domain: OptimizationDomain
    decision_variables: tuple[DecisionVariable, ...] = Field(..., min_length=1)
    objective: Objective
    constraints: tuple[Constraint, ...] = ()
    business_rules: tuple[str, ...] = ()
    source_data_description: str = ""
    source_columns: tuple[str, ...] = ()
    model_version: str = OPTIMIZATION_ENGINE_VERSION


class OptimizationStatus(str, Enum):
    """The closed set of outcomes `optimization.engine.solve_optimization_problem`
    can report -- mirrors (but is not the same closed set as) the HiGHS/
    scipy status codes it maps from, kept as this engine's own vocabulary
    so a caller never has to know scipy's own status-code conventions.

    Attributes:
        OPTIMAL: A provably optimal solution was found.
        INFEASIBLE: No assignment of the decision variables satisfies
            every constraint simultaneously -- see
            `OptimizationSolution.infeasibility_reasons`.
        UNBOUNDED: The objective can be improved without limit (a
            modeling error almost always -- e.g. a missing upper bound).
        UNAUTHORIZED: The caller lacks `Permission.RUN_OPTIMIZATION`, or
            this problem's own `source_columns` names a restricted
            column the caller lacks `Permission.VIEW_RESTRICTED_COLUMNS`
            for -- a property of *who is asking*, deliberately kept
            distinct from `INFEASIBLE` (a property of *the math*), so
            the two are never conflated in a log or a UI.
        INVALID: The problem itself failed structural validation (an
            undeclared variable reference, a malformed bound, too many
            variables/constraints, ...) before any solve was even
            attempted.
        ERROR: The solver raised or returned an unrecognized status --
            never silently swallowed.
    """

    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    UNAUTHORIZED = "unauthorized"
    INVALID = "invalid"
    ERROR = "error"


class VariableSolution(BaseModel):
    """One decision variable's optimal value.

    `reduced_cost` -- how much the objective would change per unit
    relaxation of this variable's own *bound* (not a constraint) -- is
    only ever set when `OptimizationSolution.sensitivity_available` is
    `True` (every variable `CONTINUOUS`); `None` otherwise, never a
    misleading number for a mixed-integer problem (see
    `OptimizationSolution.sensitivity_available`'s own docstring for
    why).
    """

    model_config = ConfigDict(frozen=True)

    variable: str
    value: float
    reduced_cost: float | None = None


class ConstraintSensitivity(BaseModel):
    """One constraint's slack and (when available) shadow price at the
    optimum.

    Attributes:
        constraint: Which `Constraint.name` this is about.
        slack: How much headroom this constraint has at the optimum, in
            the constraint's own original sense (e.g. for a `<=`
            constraint, `rhs - sum(terms)`; always `>= 0` for a
            satisfied inequality, `0` exactly for an equality).
        shadow_price: The dual value -- how much the optimal objective
            would change per unit relaxation of this constraint's own
            `rhs`, in the problem's own original objective sense
            (`MAXIMIZE`/`MINIMIZE`) and constraint sense (`<=`/`>=`/
            `==`), not scipy's own internal (possibly sign-flipped,
            possibly constraint-negated) representation -- see
            `optimization.engine._shadow_price_sign`'s own docstring for
            the exact, empirically-verified sign convention and why it's
            needed. `None` when `OptimizationSolution
            .sensitivity_available` is `False`.
        binding: `True` when `slack` is (numerically) zero -- this
            constraint is exactly satisfied at the optimum, the
            precondition for a non-zero shadow price to even be possible
            in LP duality theory.
    """

    model_config = ConfigDict(frozen=True)

    constraint: str
    slack: float
    shadow_price: float | None = None
    binding: bool


class OptimizationSolution(BaseModel):
    """`optimization.engine.solve_optimization_problem`'s full result --
    Prompt 39 (`39_OPTIMIZATION_ENGINE_CONTRACT.md`).

    Attributes:
        problem_id: Echoes `OptimizationProblem.problem_id`.
        name: Echoes `OptimizationProblem.name`.
        status: See `OptimizationStatus`.
        objective_value: The optimal objective value, in the problem's
            own original sense (never scipy's internal, possibly-negated
            minimization value) -- `None` unless `status == OPTIMAL`.
        variables: One `VariableSolution` per decision variable -- empty
            unless `status == OPTIMAL`.
        constraint_sensitivity: One `ConstraintSensitivity` per
            constraint -- empty unless `status == OPTIMAL`.
        sensitivity_available: `True` only when **every** decision
            variable is `CONTINUOUS` -- a mixed-integer problem's own
            LP-relaxation duals are not theoretically valid sensitivity
            information (a well-established result in optimization
            theory, not an arbitrary choice this engine makes), so this
            engine deliberately never reports `shadow_price`/
            `reduced_cost` for one, regardless of whether the underlying
            solver happens to return *some* number for it.
        infeasibility_reasons: Populated only when `status ==
            INFEASIBLE` -- always at least the solver's own message;
            this engine does not attempt a general IIS (irreducible
            infeasible subset) computation (disclosed, bounded scope).
        truth_level: Always `DataTruthLevel.AI_INFERENCE` -- a solved
            optimization is a *recommended* decision, not an observed
            fact, the identical framing `ForecastResult.truth_level`/
            `ScenarioResult.truth_level`/`AdvancedRootCauseResult
            .truth_level` already establish for a deterministic-but-
            still-inferential result (master rule 10: "never present
            inference, simulation or prediction as observed fact").
        limitations: Disclosed caveats -- e.g. "sensitivity analysis is
            not available for a mixed-integer problem," "this is a
            modeling recommendation, not a decision that has been made."
            Always non-empty for an `"optimal"` result.
        engine_version: `OPTIMIZATION_ENGINE_VERSION` at computation
            time.
    """

    model_config = ConfigDict(frozen=True)

    problem_id: str
    name: str
    status: OptimizationStatus
    objective_value: float | None = None
    variables: tuple[VariableSolution, ...] = ()
    constraint_sensitivity: tuple[ConstraintSensitivity, ...] = ()
    sensitivity_available: bool = False
    infeasibility_reasons: tuple[str, ...] = ()
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    limitations: tuple[str, ...] = ()
    engine_version: str = OPTIMIZATION_ENGINE_VERSION


class OptimizationValidation(BaseModel):
    """`optimization.validator.validate_problem`'s own result -- reports
    every problem found, never stopping at the first (the identical
    "no partial trust" convention `analytics.scenario
    .validate_scenario_parameters` already establishes).

    Attributes:
        problem_id: Echoes `OptimizationProblem.problem_id`.
        is_valid: `False` whenever `violations` is non-empty -- derived,
            never set independently of it.
        is_unauthorized: `True` when at least one violation is an
            authorization failure (missing `Permission.RUN_OPTIMIZATION`
            or a restricted `source_columns` entry) rather than a
            structural one -- a typed flag, not a string-matching
            inference over `violations`, so `optimization.engine
            .solve_optimization_problem` can report
            `OptimizationStatus.UNAUTHORIZED` vs. `.INVALID` precisely.
        violations: Every structural or authorization problem found
            (undeclared variable reference, malformed bound, too many
            variables/constraints, a restricted-column authorization
            failure, ...).
        warnings: Soft, disclosed-but-non-blocking notes -- e.g. a
            decision variable declared but never referenced by the
            objective or any constraint (not wrong, just possibly a
            typo worth a caller's attention).
    """

    model_config = ConfigDict(frozen=True)

    problem_id: str
    is_valid: bool
    is_unauthorized: bool = False
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
