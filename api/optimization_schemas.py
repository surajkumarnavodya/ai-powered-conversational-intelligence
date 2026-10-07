"""Request and response models for `api/optimization.py` (Prompt 39,
`39_OPTIMIZATION_ENGINE_CONTRACT.md`).

Request models forbid unknown fields and strip whitespace, the same
convention `api/scenario_schemas.py`'s own request models already
establish, so a client cannot slip in a `caller_roles` override -- roles
are resolved server-side from the authenticated identity.

Response models mirror `optimization.models`' own types field-by-field
with plain primitives (strings instead of enums), the identical "mirror
the internal engine model, don't re-export it" convention
`VisualizationSpecOut`/`ParameterValidationOut` already establish.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from optimization.models import (
    ConstraintOperator,
    DecisionVariableType,
    ObjectiveSense,
    OptimizationDomain,
)


class DecisionVariableIn(BaseModel):
    """Mirrors `optimization.models.DecisionVariable`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1)
    var_type: DecisionVariableType
    lower_bound: float = 0.0
    upper_bound: float | None = None
    unit: str = ""
    description: str = ""


class ObjectiveTermIn(BaseModel):
    """Mirrors `optimization.models.ObjectiveTerm`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    variable: str = Field(..., min_length=1)
    coefficient: float


class ObjectiveIn(BaseModel):
    """Mirrors `optimization.models.Objective`."""

    model_config = ConfigDict(extra="forbid")

    sense: ObjectiveSense
    terms: list[ObjectiveTermIn] = Field(default_factory=list)
    constant: float = 0.0


class ConstraintTermIn(BaseModel):
    """Mirrors `optimization.models.ConstraintTerm`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    variable: str = Field(..., min_length=1)
    coefficient: float


class ConstraintIn(BaseModel):
    """Mirrors `optimization.models.Constraint`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1)
    terms: list[ConstraintTermIn] = Field(..., min_length=1)
    operator: ConstraintOperator
    rhs: float
    description: str = ""


class OptimizationProblemIn(BaseModel):
    """Mirrors `optimization.models.OptimizationProblem`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    problem_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    domain: OptimizationDomain
    decision_variables: list[DecisionVariableIn] = Field(..., min_length=1)
    objective: ObjectiveIn
    constraints: list[ConstraintIn] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    source_data_description: str = ""
    source_columns: list[str] = Field(default_factory=list)


class OptimizationValidateRequest(BaseModel):
    """`POST /optimization/validate`'s request body."""

    model_config = ConfigDict(extra="forbid")

    problem: OptimizationProblemIn


class OptimizationSolveRequest(BaseModel):
    """`POST /optimization/solve`'s request body -- identical shape to
    validation; solving always validates first and never partially
    solves an invalid problem."""

    model_config = ConfigDict(extra="forbid")

    problem: OptimizationProblemIn


class OptimizationValidateResponse(BaseModel):
    """Mirrors `optimization.models.OptimizationValidation`."""

    model_config = ConfigDict(frozen=True)

    problem_id: str
    is_valid: bool
    is_unauthorized: bool
    violations: tuple[str, ...]
    warnings: tuple[str, ...]


class VariableSolutionOut(BaseModel):
    """Mirrors `optimization.models.VariableSolution`."""

    model_config = ConfigDict(frozen=True)

    variable: str
    value: float
    reduced_cost: float | None


class ConstraintSensitivityOut(BaseModel):
    """Mirrors `optimization.models.ConstraintSensitivity`."""

    model_config = ConfigDict(frozen=True)

    constraint: str
    slack: float
    shadow_price: float | None
    binding: bool


class OptimizationSolveResponse(BaseModel):
    """Mirrors `optimization.models.OptimizationSolution`."""

    model_config = ConfigDict(frozen=True)

    problem_id: str
    name: str
    status: str
    objective_value: float | None
    variables: tuple[VariableSolutionOut, ...]
    constraint_sensitivity: tuple[ConstraintSensitivityOut, ...]
    sensitivity_available: bool
    infeasibility_reasons: tuple[str, ...]
    truth_level: str
    limitations: tuple[str, ...]
    engine_version: str
