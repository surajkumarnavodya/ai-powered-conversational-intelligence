"""Regression test for a governance invariant that spans three modules
at once -- `optimization/models.py`, `optimization/engine.py`, and
`agent/multiagent/agents.py`'s `optimization_handler` -- so per
`tests/security/README.md`'s own rule, it belongs here rather than being
split across module-specific test files.

Master-contract rule 10 ("never present inference, simulation or
prediction as observed fact") and the mission's own "never let an LLM
invent constraints or objectives," applied specifically to the
Optimization Engine (Prompt 39, `39_OPTIMIZATION_ENGINE_CONTRACT.md`):

1. **Structural, not conventional**: `ObjectiveTerm`/`ConstraintTerm`
   (and their REST-layer mirrors, `ObjectiveTermIn`/`ConstraintTermIn`)
   have exactly two fields -- `variable` and `coefficient` -- across
   every layer of this feature. There is no field anywhere an LLM-
   authored formula/expression string could occupy.
2. **Behavioral**: `OptimizationSolution.truth_level` is always
   `DataTruthLevel.AI_INFERENCE`, across every representative outcome
   (optimal continuous, optimal mixed-integer, infeasible, invalid,
   unauthorized) -- never promoted to `DATABASE_FACT`/
   `CONFIRMED_BUSINESS_TRUTH`.
3. **Text-level, through the one live multi-agent handler**: every claim
   `optimization_handler` actually renders says "modeled recommendation"
   and "not an observed fact" explicitly.
"""

from __future__ import annotations

from agent.provenance import DataTruthLevel
from api.optimization_schemas import ConstraintTermIn, ObjectiveTermIn
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

_ANALYST = ("analyst",)


def _dv(name: str, lb: float = 0.0, ub: float | None = None, t=DecisionVariableType.CONTINUOUS):
    return DecisionVariable(name=name, var_type=t, lower_bound=lb, upper_bound=ub)


class TestStructuralNoFormulaField:
    def test_engine_objective_term_has_exactly_variable_and_coefficient(self):
        assert set(ObjectiveTerm.model_fields) == {"variable", "coefficient"}

    def test_engine_constraint_term_has_exactly_variable_and_coefficient(self):
        assert set(ConstraintTerm.model_fields) == {"variable", "coefficient"}

    def test_api_objective_term_has_exactly_variable_and_coefficient(self):
        assert set(ObjectiveTermIn.model_fields) == {"variable", "coefficient"}

    def test_api_constraint_term_has_exactly_variable_and_coefficient(self):
        assert set(ConstraintTermIn.model_fields) == {"variable", "coefficient"}


class TestTruthLevelNeverPromoted:
    def _solve(self, **overrides):
        defaults: dict = dict(
            problem_id="p1",
            name="Test",
            domain=OptimizationDomain.OTHER,
            decision_variables=(_dv("x", ub=5.0),),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE, terms=(ObjectiveTerm(variable="x", coefficient=1.0),)
            ),
        )
        defaults.update(overrides)
        return solve_optimization_problem(OptimizationProblem(**defaults), caller_roles=_ANALYST)

    def test_an_optimal_continuous_result_is_ai_inference(self):
        sol = self._solve()
        assert sol.status == OptimizationStatus.OPTIMAL
        assert sol.truth_level == DataTruthLevel.AI_INFERENCE

    def test_an_optimal_mixed_integer_result_is_ai_inference(self):
        sol = self._solve(decision_variables=(_dv("x", ub=5.0, t=DecisionVariableType.INTEGER),))
        assert sol.status == OptimizationStatus.OPTIMAL
        assert sol.truth_level == DataTruthLevel.AI_INFERENCE

    def test_an_infeasible_result_is_still_ai_inference(self):
        sol = self._solve(
            decision_variables=(_dv("x", lb=5.0),),
            objective=Objective(
                sense=ObjectiveSense.MINIMIZE, terms=(ObjectiveTerm(variable="x", coefficient=1.0),)
            ),
            constraints=(
                Constraint(
                    name="c",
                    terms=(ConstraintTerm(variable="x", coefficient=1.0),),
                    operator=ConstraintOperator.LE,
                    rhs=2.0,
                ),
            ),
        )
        assert sol.status == OptimizationStatus.INFEASIBLE
        assert sol.truth_level == DataTruthLevel.AI_INFERENCE

    def test_an_invalid_result_is_still_ai_inference(self):
        sol = self._solve(
            objective=Objective(
                sense=ObjectiveSense.MINIMIZE,
                terms=(ObjectiveTerm(variable="not_declared", coefficient=1.0),),
            )
        )
        assert sol.status == OptimizationStatus.INVALID
        assert sol.truth_level == DataTruthLevel.AI_INFERENCE

    def test_an_unauthorized_result_is_still_ai_inference(self):
        sol = solve_optimization_problem(
            OptimizationProblem(
                problem_id="p1",
                name="Test",
                domain=OptimizationDomain.OTHER,
                decision_variables=(_dv("x", ub=5.0),),
                objective=Objective(
                    sense=ObjectiveSense.MAXIMIZE,
                    terms=(ObjectiveTerm(variable="x", coefficient=1.0),),
                ),
            ),
            caller_roles=("viewer",),
        )
        assert sol.status == OptimizationStatus.UNAUTHORIZED
        assert sol.truth_level == DataTruthLevel.AI_INFERENCE


class TestHandlerClaimTextAlwaysDisclosesRecommendation:
    """Exercises the one live caller (`agent.multiagent.agents
    .optimization_handler`) end to end -- the actual text a user would
    see."""

    def test_every_claim_discloses_modeled_recommendation_not_fact(self, monkeypatch):
        import agent.multiagent.supervisor as supervisor
        import agent.multiagent.tools as tools
        from agent.multiagent.policy import reset_breakers
        from agent.multiagent.supervisor import OptimizationRequest, run_supervised_analysis
        from config.settings import Settings

        reset_breakers()
        settings = Settings()

        def fake_sql(question, **kwargs):
            rows = [("North", 100.0)]
            return {
                "status": "succeeded",
                "sql": "SELECT 1",
                "row_count": 1,
                "result_columns": ["region", "total"],
                "result_rows": rows,
                "analytical_result": None,
                "analytical_intent": None,
                "recommendations": [],
            }

        monkeypatch.setattr(supervisor, "get_settings", lambda: settings)
        monkeypatch.setattr(tools, "run_agent", fake_sql)
        monkeypatch.setattr(supervisor, "_database_id", lambda s, t: "default")
        monkeypatch.setattr(
            tools, "plan_subquestions", lambda q, s, m, n: (["Revenue by region?"], "planner")
        )
        monkeypatch.setattr(
            tools, "retrieve_business_context", lambda *a, **k: type("R", (), {"items": []})()
        )

        problem = OptimizationProblem(
            problem_id="p1",
            name="Pricing mix",
            domain=OptimizationDomain.PRICING,
            decision_variables=(_dv("x", ub=4.0), _dv("y", ub=6.0)),
            objective=Objective(
                sense=ObjectiveSense.MAXIMIZE,
                terms=(
                    ObjectiveTerm(variable="x", coefficient=3.0),
                    ObjectiveTerm(variable="y", coefficient=5.0),
                ),
            ),
        )

        try:
            result = run_supervised_analysis(
                "Maximize profit",
                caller_roles=("analyst",),
                tenant_id="tenant-a",
                optimization_requests=[OptimizationRequest(problem=problem)],
            )
        finally:
            reset_breakers()

        opt_claims = [c for c in result.claims if c.agent.value == "optimization"]
        assert opt_claims
        for claim in opt_claims:
            assert "modeled recommendation" in claim.text
            assert "not an observed fact" in claim.text
            assert claim.truth_level.value == "ai_inference"
