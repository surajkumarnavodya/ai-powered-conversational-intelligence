"""The Optimization Engine's solver -- Prompt 39
(`39_OPTIMIZATION_ENGINE_CONTRACT.md`).

`solve_optimization_problem` is a pure function over an already fully-
specified `optimization.models.OptimizationProblem`: it never fetches
data, never calls an LLM, and never invents a constraint or objective
term the caller didn't supply (see `optimization/models.py`'s own module
docstring for why that is structurally true, not just a convention this
function happens to follow).

**Deterministic optimization**: `scipy.optimize.linprog` (HiGHS method)
-- the same solver scipy itself recommends as its modern default,
already a genuine direct dependency of this codebase as of this prompt
(see `requirements.txt`). `linprog` accepts both continuous and
mixed-integer problems directly via its own `integrality` parameter, so
this module uses exactly one solver entry point for both cases --
never two parallel code paths to keep in sync.

**Sensitivity information, only when it is theoretically valid**: shadow
prices/reduced costs (LP duals) are a real, well-defined concept only
for a *continuous* linear program -- a mixed-integer problem's duals
(whatever a solver happens to report for its final LP relaxation) do not
have the same marginal-value interpretation once integrality constraints
bind, a standard result in optimization theory, not an arbitrary choice
this engine makes. `sensitivity_available` is `True`, and shadow prices/
reduced costs are ever populated, **only** when every declared decision
variable is `CONTINUOUS`.
"""

from __future__ import annotations

from scipy.optimize import linprog

from config.settings import Settings, get_settings
from optimization.models import (
    ConstraintOperator,
    ConstraintSensitivity,
    DecisionVariableType,
    ObjectiveSense,
    OptimizationProblem,
    OptimizationSolution,
    OptimizationStatus,
    VariableSolution,
)
from optimization.validator import validate_problem

#: Absolute slack tolerance below which a constraint is considered
#: "binding" at the optimum -- a fixed numerical tolerance (not derived
#: from the problem's own data), disclosed here rather than left
#: implicit.
_BINDING_TOLERANCE = 1e-6

#: scipy/HiGHS status codes this module explicitly recognizes --
#: anything else maps to OptimizationStatus.ERROR, never silently
#: swallowed. See https://docs.scipy.org/doc/scipy/reference/generated/
#: scipy.optimize.linprog.html for the full, upstream-documented set.
_STATUS_MAP = {
    0: OptimizationStatus.OPTIMAL,
    2: OptimizationStatus.INFEASIBLE,
    3: OptimizationStatus.UNBOUNDED,
}


def _shadow_price_sign(operator: ConstraintOperator, sense: ObjectiveSense) -> float:
    """The sign multiplier that converts scipy's own internal dual value
    (computed against a possibly sign-flipped, possibly constraint-
    negated formulation) back into this constraint's own shadow price,
    in the problem's own original objective sense and constraint sense.

    **Empirically verified**, not merely derived on paper, against six
    hand-computed textbook/worked examples (one per `(operator, sense)`
    combination this function can be called with -- `ConstraintOperator
    .EQ` has no constraint-type component, since an equality is never
    negated the way a `>=` constraint is converted to `<=` internally):

    - `LE` + `MINIMIZE`: scipy's marginal used as-is (flip = `+1`).
    - `GE` + `MINIMIZE`: negated (flip = `-1`) -- verified against
      `minimize 2x+3y s.t. x+y>=10`, shadow price `+2`, scipy marginal
      `-2`.
    - `LE`/`EQ` + `MAXIMIZE`: negated (flip = `-1`) -- verified against
      the classic `maximize 3x+5y s.t. x<=4, 2y<=12, 3x+2y<=18` textbook
      problem (shadow prices `0`, `1.5`, `1.0`; scipy marginals `-0`,
      `-1.5`, `-1.0`).
    - `GE` + `MAXIMIZE`: used as-is (flip = `+1`, the two flips cancel)
      -- verified against `maximize -2x s.t. x>=3, x<=10`, shadow price
      `-2`, scipy marginal `-2`.
    - `EQ` + `MINIMIZE`: used as-is (flip = `+1`) -- verified against
      `minimize 2x+3y s.t. x+y=10`, shadow price `+2`, scipy marginal
      `+2`.

    General rule: `flip = (-1 if operator == GE else +1) * (-1 if sense
    == MAXIMIZE else +1)`.
    """
    constraint_flip = -1.0 if operator == ConstraintOperator.GE else 1.0
    objective_flip = -1.0 if sense == ObjectiveSense.MAXIMIZE else 1.0
    return constraint_flip * objective_flip


def _build_arrays(problem: OptimizationProblem):
    """Builds every scipy-facing array from `problem`, plus the bookkeeping
    `solve_optimization_problem` needs to map scipy's own row-indexed
    output back onto this problem's own named variables/constraints."""
    names = [v.name for v in problem.decision_variables]
    index = {name: i for i, name in enumerate(names)}
    n = len(names)

    objective_flip = -1.0 if problem.objective.sense == ObjectiveSense.MAXIMIZE else 1.0
    c = [0.0] * n
    for term in problem.objective.terms:
        c[index[term.variable]] += objective_flip * term.coefficient

    integrality = [
        1 if v.var_type in (DecisionVariableType.INTEGER, DecisionVariableType.BINARY) else 0
        for v in problem.decision_variables
    ]
    bounds: list[tuple[float, float | None]] = []
    for v in problem.decision_variables:
        if v.var_type == DecisionVariableType.BINARY:
            bounds.append((0.0, 1.0))
        else:
            bounds.append((v.lower_bound, v.upper_bound))

    a_ub: list[list[float]] = []
    b_ub: list[float] = []
    a_eq: list[list[float]] = []
    b_eq: list[float] = []
    #: constraint.name -> ("ub" | "eq", row index within that bucket)
    row_refs: dict[str, tuple[str, int]] = {}

    for constraint in problem.constraints:
        row = [0.0] * n
        for cterm in constraint.terms:
            row[index[cterm.variable]] += cterm.coefficient

        if constraint.operator == ConstraintOperator.LE:
            row_refs[constraint.name] = ("ub", len(a_ub))
            a_ub.append(row)
            b_ub.append(constraint.rhs)
        elif constraint.operator == ConstraintOperator.GE:
            row_refs[constraint.name] = ("ub", len(a_ub))
            a_ub.append([-x for x in row])
            b_ub.append(-constraint.rhs)
        else:  # EQ
            row_refs[constraint.name] = ("eq", len(a_eq))
            a_eq.append(row)
            b_eq.append(constraint.rhs)

    return index, c, integrality, bounds, a_ub, b_ub, a_eq, b_eq, row_refs


def solve_optimization_problem(
    problem: OptimizationProblem,
    caller_roles: tuple[str, ...] = (),
    settings: Settings | None = None,
) -> OptimizationSolution:
    """Validates, then solves, `problem`.

    Args:
        problem: The fully-specified problem.
        caller_roles: The roles of whoever is asking to solve it.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        An `OptimizationSolution`. `status` is `UNAUTHORIZED`/`INVALID`
        (never a partial solve) when `optimization.validator
        .validate_problem` found any violation; otherwise the real
        solver's own outcome (`OPTIMAL`/`INFEASIBLE`/`UNBOUNDED`/
        `ERROR`).
    """
    settings = settings or get_settings()
    validation = validate_problem(problem, caller_roles, settings)
    if not validation.is_valid:
        status = (
            OptimizationStatus.UNAUTHORIZED
            if validation.is_unauthorized
            else OptimizationStatus.INVALID
        )
        return OptimizationSolution(
            problem_id=problem.problem_id,
            name=problem.name,
            status=status,
            infeasibility_reasons=validation.violations,
            limitations=(
                "This problem was not solved -- it failed validation. See "
                "infeasibility_reasons for every reason found.",
            ),
        )

    sensitivity_available = all(
        v.var_type == DecisionVariableType.CONTINUOUS for v in problem.decision_variables
    )
    index, c, integrality, bounds, a_ub, b_ub, a_eq, b_eq, row_refs = _build_arrays(problem)

    try:
        result = linprog(
            c,
            A_ub=a_ub or None,
            b_ub=b_ub or None,
            A_eq=a_eq or None,
            b_eq=b_eq or None,
            bounds=bounds,
            integrality=integrality,
            method="highs",
        )
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a solver failure must be reported, never crash the caller
        return OptimizationSolution(
            problem_id=problem.problem_id,
            name=problem.name,
            status=OptimizationStatus.ERROR,
            infeasibility_reasons=(f"the solver raised an unexpected error: {exc}",),
            limitations=("This problem was not solved due to an unexpected solver error.",),
        )

    status = _STATUS_MAP.get(int(result.status), OptimizationStatus.ERROR)

    if status != OptimizationStatus.OPTIMAL:
        return OptimizationSolution(
            problem_id=problem.problem_id,
            name=problem.name,
            status=status,
            infeasibility_reasons=(str(result.message),),
            limitations=(
                "This problem was not solved to optimality -- see infeasibility_reasons.",
            ),
        )

    objective_flip = -1.0 if problem.objective.sense == ObjectiveSense.MAXIMIZE else 1.0
    objective_value = objective_flip * result.fun + problem.objective.constant

    variables = []
    for v in problem.decision_variables:
        value = float(result.x[index[v.name]])
        reduced_cost = None
        if sensitivity_available:
            lower_marginal = float(result.lower.marginals[index[v.name]])
            upper_marginal = float(result.upper.marginals[index[v.name]])
            reduced_cost = objective_flip * (lower_marginal + upper_marginal)
        variables.append(VariableSolution(variable=v.name, value=value, reduced_cost=reduced_cost))

    constraint_sensitivity = []
    for constraint in problem.constraints:
        lhs_value = sum(
            term.coefficient * result.x[index[term.variable]] for term in constraint.terms
        )
        if constraint.operator == ConstraintOperator.LE:
            slack = constraint.rhs - lhs_value
        elif constraint.operator == ConstraintOperator.GE:
            slack = lhs_value - constraint.rhs
        else:
            slack = -abs(lhs_value - constraint.rhs)  # EQ: always <= 0, "binding" by definition
        binding = abs(slack) < _BINDING_TOLERANCE

        shadow_price = None
        if sensitivity_available:
            bucket, row_idx = row_refs[constraint.name]
            marginal = float(
                (result.ineqlin if bucket == "ub" else result.eqlin).marginals[row_idx]
            )
            shadow_price = (
                _shadow_price_sign(constraint.operator, problem.objective.sense) * marginal
            )

        constraint_sensitivity.append(
            ConstraintSensitivity(
                constraint=constraint.name,
                slack=round(slack, 10),
                shadow_price=round(shadow_price, 10) if shadow_price is not None else None,
                binding=binding,
            )
        )

    limitations = [
        "This is a recommended, modeled decision, not a decision that has been made -- treat "
        "it as an AI inference over the data and assumptions supplied, not an observed fact.",
    ]
    if not sensitivity_available:
        limitations.append(
            "sensitivity analysis (shadow prices / reduced costs) is not available for a "
            "mixed-integer problem -- a well-established limitation of LP duality theory, not "
            "a computation this engine chose to skip."
        )

    return OptimizationSolution(
        problem_id=problem.problem_id,
        name=problem.name,
        status=OptimizationStatus.OPTIMAL,
        objective_value=round(objective_value, 10),
        variables=tuple(variables),
        constraint_sensitivity=tuple(constraint_sensitivity),
        sensitivity_available=sensitivity_available,
        limitations=tuple(limitations),
    )


__all__ = ["solve_optimization_problem"]
