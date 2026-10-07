"""Unit tests for `optimization/engine.py` and `optimization/validator.py`
-- Prompt 39 (`39_OPTIMIZATION_ENGINE_CONTRACT.md`)'s governed
Optimization Engine.

Covers, matching the mission's own explicit testing requirement
("feasible, infeasible, boundary and regression cases"):

- feasible problems (continuous LP, mixed-integer),
- infeasible and unbounded problems, reported distinctly from invalid/
  unauthorized ones,
- boundary cases (a variable at its own bound, an exactly-binding
  constraint, an empty-constraint trivial problem),
- regression cases (hand-computed/independently-verified textbook LP
  examples, pinned so a future change to the sign-convention/solver
  wiring is caught immediately),
- unauthorized assumptions (missing `Permission.RUN_OPTIMIZATION`, a
  restricted `source_columns` entry),
- structural validation ("no partial trust": one invalid reference
  rejects the whole problem), and
- the "never let an LLM invent constraints or objectives" structural
  guarantee (no field anywhere in the typed contract accepts a formula
  string).

All example numbers in this file were independently hand-computed and
cross-checked by directly exercising the module (via a scratch script,
not shown in the final diff) before being written into the test file --
not "whatever the code happened to produce." The two worked textbook
examples (the `TestRegression` class) are standard operations-research
textbook problems with well-known, published optimal solutions and
shadow prices, used here specifically because they are independently
verifiable, not merely self-consistent.
"""

from __future__ import annotations

import math

import pytest

from config.settings import Settings
from optimization.engine import solve_optimization_problem
from optimization.models import (
    Constraint,
    ConstraintOperator,
    ConstraintTerm,
    DecisionVariable,
    DecisionVariableType,
    Objective,
    ObjectiveSense,
    ObjectiveTerm,
    OptimizationDomain,
    OptimizationProblem,
    OptimizationStatus,
)
from optimization.validator import validate_problem

_SETTINGS = Settings(optimization_max_variables=50, optimization_max_constraints=100)
_ANALYST = ("analyst",)


def _dv(name: str, lb: float = 0.0, ub: float | None = None, t=DecisionVariableType.CONTINUOUS):
    return DecisionVariable(name=name, var_type=t, lower_bound=lb, upper_bound=ub)


def _problem(**overrides: object) -> OptimizationProblem:
    defaults: dict = dict(
        problem_id="p1",
        name="Test problem",
        domain=OptimizationDomain.OTHER,
        decision_variables=(_dv("x"),),
        objective=Objective(
            sense=ObjectiveSense.MINIMIZE, terms=(ObjectiveTerm(variable="x", coefficient=1.0),)
        ),
    )
    defaults.update(overrides)
    return OptimizationProblem(**defaults)


class TestRegression:
    """Two independently-verifiable, published textbook LP problems --
    pinned so a future change to sign conventions or solver wiring is
    caught immediately, not discovered later against production data."""

    def test_classic_maximize_textbook_problem(self):
        """maximize 3x + 5y s.t. x<=4, 2y<=12, 3x+2y<=18 -- the standard
        operations-research textbook example. Known optimum: x=2, y=6,
        Z=36; known shadow prices: c1=0 (non-binding), c2=1.5, c3=1.0."""
        problem = OptimizationProblem(
            problem_id="textbook",
            name="Textbook LP",
            domain=OptimizationDomain.OTHER,
            decision_variables=(_dv("x"), _dv("y")),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE,
                terms=(
                    ObjectiveTerm(variable="x", coefficient=3.0),
                    ObjectiveTerm(variable="y", coefficient=5.0),
                ),
            ),
            constraints=(
                Constraint(
                    name="c1",
                    terms=(ConstraintTerm(variable="x", coefficient=1.0),),
                    operator=ConstraintOperator.LE,
                    rhs=4.0,
                ),
                Constraint(
                    name="c2",
                    terms=(ConstraintTerm(variable="y", coefficient=2.0),),
                    operator=ConstraintOperator.LE,
                    rhs=12.0,
                ),
                Constraint(
                    name="c3",
                    terms=(
                        ConstraintTerm(variable="x", coefficient=3.0),
                        ConstraintTerm(variable="y", coefficient=2.0),
                    ),
                    operator=ConstraintOperator.LE,
                    rhs=18.0,
                ),
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.OPTIMAL
        assert sol.objective_value == pytest.approx(36.0)
        values = {v.variable: v.value for v in sol.variables}
        assert values == pytest.approx({"x": 2.0, "y": 6.0})

        sensitivity = {c.constraint: c for c in sol.constraint_sensitivity}
        assert sensitivity["c1"].binding is False
        assert sensitivity["c1"].shadow_price == pytest.approx(0.0)
        assert sensitivity["c2"].binding is True
        assert sensitivity["c2"].shadow_price == pytest.approx(1.5)
        assert sensitivity["c3"].binding is True
        assert sensitivity["c3"].shadow_price == pytest.approx(1.0)
        assert sol.sensitivity_available is True

    def test_minimize_with_a_ge_and_an_eq_constraint(self):
        """minimize 2x + 3y s.t. x+y>=10 (GE) -- known optimum x=10, y=0,
        cost=20, shadow price +2. Re-run with the constraint expressed
        as x+y==10 (EQ) instead -- identical optimum and shadow price,
        proving the GE->LE-negation and EQ code paths agree."""
        ge_problem = OptimizationProblem(
            problem_id="ge",
            name="GE minimize",
            domain=OptimizationDomain.OTHER,
            decision_variables=(_dv("x"), _dv("y")),
            objective=Objective(
                sense=ObjectiveSense.MINIMIZE,
                terms=(
                    ObjectiveTerm(variable="x", coefficient=2.0),
                    ObjectiveTerm(variable="y", coefficient=3.0),
                ),
            ),
            constraints=(
                Constraint(
                    name="c",
                    terms=(
                        ConstraintTerm(variable="x", coefficient=1.0),
                        ConstraintTerm(variable="y", coefficient=1.0),
                    ),
                    operator=ConstraintOperator.GE,
                    rhs=10.0,
                ),
            ),
        )
        ge_sol = solve_optimization_problem(ge_problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert ge_sol.objective_value == pytest.approx(20.0)
        assert ge_sol.constraint_sensitivity[0].shadow_price == pytest.approx(2.0)

        eq_problem = ge_problem.model_copy(
            update={
                "constraints": (
                    Constraint(
                        name="c",
                        terms=ge_problem.constraints[0].terms,
                        operator=ConstraintOperator.EQ,
                        rhs=10.0,
                    ),
                )
            }
        )
        eq_sol = solve_optimization_problem(eq_problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert eq_sol.objective_value == pytest.approx(20.0)
        assert eq_sol.constraint_sensitivity[0].shadow_price == pytest.approx(2.0)

    def test_maximize_with_a_ge_constraint_cancels_both_sign_flips(self):
        """maximize -2x s.t. x>=3, x<=10 -- known optimum x=3, objective
        -6, shadow price of x>=3 is -2 (both the GE-negation flip and the
        MAXIMIZE flip apply and cancel out -- the one combination not
        exercised by the two tests above)."""
        problem = OptimizationProblem(
            problem_id="ge_max",
            name="GE maximize",
            domain=OptimizationDomain.OTHER,
            decision_variables=(_dv("x", ub=10.0),),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE,
                terms=(ObjectiveTerm(variable="x", coefficient=-2.0),),
            ),
            constraints=(
                Constraint(
                    name="c",
                    terms=(ConstraintTerm(variable="x", coefficient=1.0),),
                    operator=ConstraintOperator.GE,
                    rhs=3.0,
                ),
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.variables[0].value == pytest.approx(3.0)
        assert sol.objective_value == pytest.approx(-6.0)
        assert sol.constraint_sensitivity[0].shadow_price == pytest.approx(-2.0)


class TestFeasible:
    def test_a_mixed_integer_problem_solves_and_respects_integrality(self):
        problem = OptimizationProblem(
            problem_id="mip",
            name="MIP",
            domain=OptimizationDomain.WORKFORCE,
            decision_variables=(
                _dv("x", ub=10.0, t=DecisionVariableType.INTEGER),
                _dv("y", ub=10.0),
            ),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE,
                terms=(
                    ObjectiveTerm(variable="x", coefficient=1.0),
                    ObjectiveTerm(variable="y", coefficient=1.0),
                ),
            ),
            constraints=(
                Constraint(
                    name="c",
                    terms=(
                        ConstraintTerm(variable="x", coefficient=1.0),
                        ConstraintTerm(variable="y", coefficient=1.0),
                    ),
                    operator=ConstraintOperator.LE,
                    rhs=10.5,
                ),
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.OPTIMAL
        x_value = next(v.value for v in sol.variables if v.variable == "x")
        assert x_value == pytest.approx(round(x_value))  # genuinely integral

    def test_a_binary_variable_is_forced_to_0_or_1_regardless_of_supplied_bounds(self):
        problem = OptimizationProblem(
            problem_id="bin",
            name="Binary",
            domain=OptimizationDomain.CAPACITY,
            decision_variables=(
                DecisionVariable(
                    name="open_facility",
                    var_type=DecisionVariableType.BINARY,
                    lower_bound=-5.0,
                    upper_bound=99.0,
                ),
            ),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE,
                terms=(ObjectiveTerm(variable="open_facility", coefficient=1.0),),
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.OPTIMAL
        assert sol.variables[0].value == pytest.approx(1.0)

    def test_an_empty_constraint_set_is_a_legitimate_trivial_problem(self):
        problem = _problem(decision_variables=(_dv("x", ub=5.0),), constraints=())
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.OPTIMAL
        assert sol.variables[0].value == pytest.approx(0.0)  # minimize x, lb=0


class TestInfeasibleAndUnbounded:
    def test_contradictory_bounds_are_infeasible(self):
        problem = _problem(
            decision_variables=(_dv("x", lb=5.0),),
            constraints=(
                Constraint(
                    name="c",
                    terms=(ConstraintTerm(variable="x", coefficient=1.0),),
                    operator=ConstraintOperator.LE,
                    rhs=2.0,
                ),
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.INFEASIBLE
        assert sol.infeasibility_reasons
        assert sol.variables == ()
        assert sol.objective_value is None

    def test_an_unbounded_objective_is_reported_distinctly(self):
        problem = _problem(
            decision_variables=(_dv("x"),),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE, terms=(ObjectiveTerm(variable="x", coefficient=1.0),)
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.UNBOUNDED
        assert sol.status != OptimizationStatus.INFEASIBLE


class TestBoundaryCases:
    def test_a_variable_resting_exactly_at_its_own_upper_bound(self):
        problem = _problem(
            decision_variables=(_dv("x", ub=5.0),),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE, terms=(ObjectiveTerm(variable="x", coefficient=1.0),)
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.variables[0].value == pytest.approx(5.0)

    def test_an_exactly_binding_constraint_has_zero_slack(self):
        problem = _problem(
            decision_variables=(_dv("x"),),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE, terms=(ObjectiveTerm(variable="x", coefficient=1.0),)
            ),
            constraints=(
                Constraint(
                    name="c",
                    terms=(ConstraintTerm(variable="x", coefficient=1.0),),
                    operator=ConstraintOperator.LE,
                    rhs=7.0,
                ),
            ),
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.constraint_sensitivity[0].slack == pytest.approx(0.0)
        assert sol.constraint_sensitivity[0].binding is True

    def test_lower_bound_equal_to_upper_bound_pins_the_variable(self):
        problem = _problem(decision_variables=(_dv("x", lb=3.0, ub=3.0),))
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.OPTIMAL
        assert sol.variables[0].value == pytest.approx(3.0)


class TestUnauthorized:
    def test_missing_run_optimization_permission_is_unauthorized_not_invalid(self):
        problem = _problem()
        sol = solve_optimization_problem(problem, caller_roles=("viewer",), settings=_SETTINGS)
        assert sol.status == OptimizationStatus.UNAUTHORIZED
        assert sol.variables == ()

    def test_a_restricted_source_column_is_rejected_for_a_viewer(self, monkeypatch):
        import optimization.validator as validator_module

        monkeypatch.setattr(
            validator_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "UnitCost"): "restricted"},
        )
        problem = _problem(source_columns=("UnitCost",))
        validation = validate_problem(problem, caller_roles=("viewer",), settings=_SETTINGS)
        assert validation.is_valid is False
        assert validation.is_unauthorized is True

    def test_the_same_restricted_source_column_is_allowed_for_an_analyst(self, monkeypatch):
        import optimization.validator as validator_module

        monkeypatch.setattr(
            validator_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "UnitCost"): "restricted"},
        )
        problem = _problem(source_columns=("UnitCost",))
        validation = validate_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert validation.is_valid is True


class TestNoPartialTrust:
    def test_an_undeclared_variable_reference_rejects_the_whole_problem(self):
        problem = _problem(
            objective=Objective(
                sense=ObjectiveSense.MINIMIZE,
                terms=(ObjectiveTerm(variable="not_declared", coefficient=1.0),),
            )
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.INVALID
        assert sol.variables == ()
        assert sol.objective_value is None

    def test_malformed_bounds_rejects_the_problem(self):
        problem = _problem(decision_variables=(_dv("x", lb=10.0, ub=5.0),))
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.INVALID

    def test_nan_bound_is_rejected(self):
        problem = _problem(decision_variables=(_dv("x", lb=math.nan),))
        validation = validate_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert validation.is_valid is False

    def test_too_many_variables_is_rejected_before_any_solve(self):
        settings = Settings(**{**_SETTINGS.__dict__, "optimization_max_variables": 1})
        problem = _problem(decision_variables=(_dv("x"), _dv("y")))
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=settings)
        assert sol.status == OptimizationStatus.INVALID

    def test_an_unused_variable_is_a_warning_not_a_violation(self):
        problem = _problem(decision_variables=(_dv("x"), _dv("unused")))
        validation = validate_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert validation.is_valid is True
        assert any("unused" in w for w in validation.warnings)


class TestNeverLetAnLlmInventConstraintsOrObjectives:
    """The structural half of the mission's own named requirement --
    nothing in the typed contract accepts a formula string an LLM could
    author."""

    def test_objective_term_has_no_expression_or_formula_field(self):
        fields = set(ObjectiveTerm.model_fields)
        assert fields == {"variable", "coefficient"}

    def test_constraint_term_has_no_expression_or_formula_field(self):
        fields = set(ConstraintTerm.model_fields)
        assert fields == {"variable", "coefficient"}

    def test_a_constraint_term_referencing_an_undeclared_variable_cannot_silently_run(self):
        """The only way a "constraint" could smuggle in extra logic --
        referencing a variable that isn't really declared -- is caught
        by validation, never silently solved."""
        problem = _problem(
            constraints=(
                Constraint(
                    name="c",
                    terms=(ConstraintTerm(variable="phantom", coefficient=1.0),),
                    operator=ConstraintOperator.LE,
                    rhs=10.0,
                ),
            )
        )
        sol = solve_optimization_problem(problem, caller_roles=_ANALYST, settings=_SETTINGS)
        assert sol.status == OptimizationStatus.INVALID
