"""Request and response models for `api/scenarios.py` (Prompt 38,
`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`).

Request models forbid unknown fields and strip whitespace, the same
convention `api/schemas.py`'s own request models already establish, so a
client cannot slip in a `caller_roles`/`tenant_id` override -- those are
server-side only, resolved from the authenticated identity.

Response models mirror `analytics.models`' own scenario types field-by-
field with plain primitives (strings instead of enums), the identical
"mirror the internal engine model, don't re-export it" convention
`VisualizationSpecOut`/`ChartFieldOut` already establish in
`api/schemas.py`.

`rows` is a plain JSON `list[list[Any]]` on the way in (the caller's own
already-fetched result, e.g. from a prior `/execute` call -- **this
route never queries a database itself**, see `api/scenarios.py`'s own
module docstring for why that is the literal mechanism behind "run
scenarios without changing source data").
"""

from __future__ import annotations

from typing import Any

from analytics.models import ScenarioChangeType
from pydantic import BaseModel, ConfigDict, Field

from api.schemas import VisualizationSpecOut


class ScenarioParameterIn(BaseModel):
    """Mirrors `analytics.models.ScenarioParameter`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1)
    affected_metric: str = Field(..., min_length=1)
    unit: str = Field(..., min_length=1)
    change_type: ScenarioChangeType
    scenario_value: float
    target_label: str | None = None
    min_value: float | None = None
    max_value: float | None = None


class ScenarioValidateRequest(BaseModel):
    """`POST /scenarios/validate`'s request body -- validates every
    parameter against a caller-supplied baseline, without running
    anything."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    columns: list[str] = Field(..., min_length=1)
    rows: list[list[Any]] = Field(default_factory=list)
    dimension_columns: list[str] = Field(default_factory=list)
    parameters: list[ScenarioParameterIn] = Field(..., min_length=1)


class ScenarioRunRequest(ScenarioValidateRequest):
    """`POST /scenarios/run`'s request body -- the same baseline/
    parameter shape as validation, plus a name for the run."""

    scenario_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


class ParameterValidationOut(BaseModel):
    """Mirrors `analytics.models.ParameterValidation`."""

    model_config = ConfigDict(frozen=True)

    parameter_name: str
    is_valid: bool
    is_unrealistic: bool
    violations: tuple[str, ...]
    warnings: tuple[str, ...]
    baseline_value: float | None
    implied_scenario_value: float | None


class ScenarioValidateResponse(BaseModel):
    """`POST /scenarios/validate`'s response -- every parameter's own
    verdict, never a bare pass/fail."""

    model_config = ConfigDict(frozen=True)

    parameter_validations: tuple[ParameterValidationOut, ...]


class ScenarioMetricComparisonOut(BaseModel):
    """Mirrors `analytics.models.ScenarioMetricComparison`.
    `baseline_kind`/`scenario_kind` are always the literal strings
    `"observed"`/`"modeled_estimate"` -- never anything else, matching
    the internal model's own fixed `Literal` guarantee."""

    model_config = ConfigDict(frozen=True)

    metric: str
    baseline_value: float
    scenario_value: float
    delta: float
    delta_percent: float | None
    baseline_kind: str
    scenario_kind: str


class ScenarioRunResponse(BaseModel):
    """`POST /scenarios/run`'s response -- mirrors
    `analytics.models.ScenarioResult`. `visualization_spec` reuses the
    exact same `VisualizationSpecOut`/`ChartFieldOut`/`ChartSortOut`/
    `AccessibilityMetadataOut` shapes `ExecuteResponse.visualization_spec`
    already established (`api/schemas.py`) -- no second chart-output
    shape for this route."""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    name: str
    status: str
    parameter_validations: tuple[ParameterValidationOut, ...]
    rejection_reasons: tuple[str, ...]
    comparisons: tuple[ScenarioMetricComparisonOut, ...]
    truth_level: str
    limitations: tuple[str, ...]
    engine_version: str
    visualization_spec: VisualizationSpecOut | None = None
