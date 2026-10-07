"""POST /optimization/validate and POST /optimization/solve -- the
governed Optimization Engine's REST surface (Prompt 39,
`39_OPTIMIZATION_ENGINE_CONTRACT.md`).

**Neither route ever queries a database or calls an LLM.** Both take a
fully-specified `OptimizationProblem` directly in the request body --
decision variables, a linear objective, and linear constraints, every
one of them plain numeric data (variable names + coefficients), never a
formula string. This is the literal, structural mechanism behind "never
let an LLM invent constraints or objectives" at the API layer: there is
no field anywhere in this request shape an LLM-authored expression could
occupy.

Behind `Settings.enable_optimization_engine` (default on, since this is
a pure, deterministic, zero-I/O computation -- the same posture
`api/scenarios.py`'s own `Settings.enable_what_if_scenarios` gate
establishes). When the flag is off the routes answer 404.

Authorization: `Permission.RUN_OPTIMIZATION` (a dedicated permission,
deliberately not just `Permission.ASK` -- an optimization result is a
suggested real-world decision, more consequential than a read-only
question) plus a restricted-column check inside `optimization.validator
.validate_problem` itself for `OptimizationProblem.source_columns`.
Every solve attempt (successful or not) is audit-logged by the engine's
own validator (`security.audit_log.log_security_event`, on an
authorization denial) -- see that module's own docstring.

Rate-limited and payload-bounded: `enforce_api_action_rate_limit` per
caller, and `decision_variables`/`constraints` are each capped
(`Settings.optimization_max_variables`/`optimization_max_constraints`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent.authz import Permission
from api.authz import require_permission
from api.optimization_schemas import (
    ConstraintSensitivityOut,
    OptimizationProblemIn,
    OptimizationSolveRequest,
    OptimizationSolveResponse,
    OptimizationValidateRequest,
    OptimizationValidateResponse,
    VariableSolutionOut,
)
from api.rate_limit import enforce_api_action_rate_limit
from config.settings import get_settings
from optimization.engine import solve_optimization_problem
from optimization.models import (
    Constraint,
    ConstraintTerm,
    DecisionVariable,
    Objective,
    ObjectiveTerm,
    OptimizationProblem,
)
from optimization.validator import validate_problem
from security.oidc import AuthIdentity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimization", tags=["optimization"])

OPTIMIZATION_DISABLED_DETAIL = "The Optimization Engine is not enabled on this server."
TOO_MANY_VARIABLES_DETAIL = "Too many decision variables for this endpoint."
TOO_MANY_CONSTRAINTS_DETAIL = "Too many constraints for this endpoint."


def _require_enabled() -> None:
    if not get_settings().enable_optimization_engine:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=OPTIMIZATION_DISABLED_DETAIL
        )


def _check_payload_bounds(variable_count: int, constraint_count: int) -> None:
    settings = get_settings()
    if variable_count > settings.optimization_max_variables:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_VARIABLES_DETAIL
        )
    if constraint_count > settings.optimization_max_constraints:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_CONSTRAINTS_DETAIL
        )


def _to_engine_problem(payload: OptimizationProblemIn) -> OptimizationProblem:
    return OptimizationProblem(
        problem_id=payload.problem_id,
        name=payload.name,
        domain=payload.domain,
        decision_variables=tuple(
            DecisionVariable(
                name=v.name,
                var_type=v.var_type,
                lower_bound=v.lower_bound,
                upper_bound=v.upper_bound,
                unit=v.unit,
                description=v.description,
            )
            for v in payload.decision_variables
        ),
        objective=Objective(
            sense=payload.objective.sense,
            terms=tuple(
                ObjectiveTerm(variable=t.variable, coefficient=t.coefficient)
                for t in payload.objective.terms
            ),
            constant=payload.objective.constant,
        ),
        constraints=tuple(
            Constraint(
                name=c.name,
                terms=tuple(
                    ConstraintTerm(variable=t.variable, coefficient=t.coefficient) for t in c.terms
                ),
                operator=c.operator,
                rhs=c.rhs,
                description=c.description,
            )
            for c in payload.constraints
        ),
        business_rules=tuple(payload.business_rules),
        source_data_description=payload.source_data_description,
        source_columns=tuple(payload.source_columns),
    )


@router.post("/validate", response_model=OptimizationValidateResponse)
def validate_optimization_problem(
    payload: OptimizationValidateRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.RUN_OPTIMIZATION)),
) -> OptimizationValidateResponse:
    """Validates a problem's own structure and the caller's authorization
    -- undeclared variable references, malformed bounds, too many
    variables/constraints, a restricted `source_columns` entry -- without
    solving anything. Never touches a database."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "optimization_validate", settings, identity=identity)
    _check_payload_bounds(len(payload.problem.decision_variables), len(payload.problem.constraints))

    problem = _to_engine_problem(payload.problem)
    validation = validate_problem(problem, tuple(identity.roles), settings)
    return OptimizationValidateResponse(
        problem_id=validation.problem_id,
        is_valid=validation.is_valid,
        is_unauthorized=validation.is_unauthorized,
        violations=validation.violations,
        warnings=validation.warnings,
    )


@router.post("/solve", response_model=OptimizationSolveResponse)
def solve_optimization_problem_route(
    payload: OptimizationSolveRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.RUN_OPTIMIZATION)),
) -> OptimizationSolveResponse:
    """Solves a problem -- `status` is `"invalid"`/`"unauthorized"` (never
    a partial solve) when validation found any violation; otherwise the
    real solver's own outcome. Never touches a database or calls an
    LLM."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "optimization_solve", settings, identity=identity)
    _check_payload_bounds(len(payload.problem.decision_variables), len(payload.problem.constraints))

    problem = _to_engine_problem(payload.problem)
    solution = solve_optimization_problem(problem, tuple(identity.roles), settings)

    return OptimizationSolveResponse(
        problem_id=solution.problem_id,
        name=solution.name,
        status=solution.status.value,
        objective_value=solution.objective_value,
        variables=tuple(
            VariableSolutionOut(variable=v.variable, value=v.value, reduced_cost=v.reduced_cost)
            for v in solution.variables
        ),
        constraint_sensitivity=tuple(
            ConstraintSensitivityOut(
                constraint=c.constraint,
                slack=c.slack,
                shadow_price=c.shadow_price,
                binding=c.binding,
            )
            for c in solution.constraint_sensitivity
        ),
        sensitivity_available=solution.sensitivity_available,
        infeasibility_reasons=solution.infeasibility_reasons,
        truth_level=solution.truth_level.value,
        limitations=solution.limitations,
        engine_version=solution.engine_version,
    )
