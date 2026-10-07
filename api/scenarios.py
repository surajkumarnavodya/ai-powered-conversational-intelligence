"""POST /scenarios/validate and POST /scenarios/run -- the governed
What-If Scenario Engine's REST surface (Prompt 38,
`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`).

**Neither route ever queries a database.** Both take the baseline
`columns`/`rows` directly in the request body -- the caller's own
already-fetched, already-governed result (e.g. from a prior `POST
/execute` "Confirm and Run"). This is the literal mechanism behind "run
scenarios without changing source data": `analytics.scenario` is a pure
function over whatever rows it's handed, and this route never becomes a
second SQL-execution gateway alongside `/execute` -- it has no `sql`
field, no database connection, nothing to validate as a query at all.

Behind `Settings.enable_what_if_scenarios` (default on, since this is a
pure, deterministic, zero-I/O computation -- the same posture
`api/analyst.py`'s own `Settings.enable_data_analyst_agent` gate
establishes for a heavier, off-by-default feature). When the flag is off
the routes answer 404, so they don't exist from a client's point of view.

Authorization: `Permission.ASK` (the same base gate every AI feature in
this codebase requires) plus a restricted-column check inside
`analytics.scenario.validate_scenario_parameters` itself -- a parameter
naming a column `config.sensitive_columns` classifies "restricted" is
rejected unless the caller also holds `Permission
.VIEW_RESTRICTED_COLUMNS`, mirroring `recommendation.engine`'s own
identical authorization re-check (`agent/authz.py`'s stated principle:
"no frontend-level restriction is a security boundary ... the
corresponding internal node re-checks independently").

Rate-limited and payload-bounded: `enforce_api_action_rate_limit` per
caller, and `rows`/`parameters` are each capped (`Settings
.max_result_rows`/`scenario_max_parameters`) so a caller cannot turn this
pure-compute endpoint into a resource-exhaustion vector just because it
never touches a database.
"""

from __future__ import annotations

import logging

from analytics.models import ScenarioParameter
from analytics.scenario import run_scenario, validate_scenario_parameters
from analytics.visualization import build_scenario_chart_spec
from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent.authz import Permission
from api.authz import require_permission
from api.rate_limit import enforce_api_action_rate_limit
from api.scenario_schemas import (
    ParameterValidationOut,
    ScenarioMetricComparisonOut,
    ScenarioRunRequest,
    ScenarioRunResponse,
    ScenarioValidateRequest,
    ScenarioValidateResponse,
)
from api.schemas import AccessibilityMetadataOut, ChartFieldOut, ChartSortOut, VisualizationSpecOut
from config.settings import get_settings
from security.oidc import AuthIdentity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

SCENARIOS_DISABLED_DETAIL = "The What-If Scenario Engine is not enabled on this server."
TOO_MANY_ROWS_DETAIL = "Too many baseline rows for this endpoint."
TOO_MANY_PARAMETERS_DETAIL = "Too many scenario parameters for this endpoint."


def _require_enabled() -> None:
    if not get_settings().enable_what_if_scenarios:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIOS_DISABLED_DETAIL)


def _check_payload_bounds(row_count: int, parameter_count: int) -> None:
    settings = get_settings()
    if row_count > settings.max_result_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_ROWS_DETAIL
        )
    if parameter_count > settings.scenario_max_parameters:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_PARAMETERS_DETAIL
        )


def _to_engine_parameters(payload_parameters) -> list[ScenarioParameter]:
    return [
        ScenarioParameter(
            name=p.name,
            affected_metric=p.affected_metric,
            unit=p.unit,
            change_type=p.change_type,
            scenario_value=p.scenario_value,
            target_label=p.target_label,
            min_value=p.min_value,
            max_value=p.max_value,
        )
        for p in payload_parameters
    ]


def _to_validation_out(validations) -> tuple[ParameterValidationOut, ...]:
    return tuple(
        ParameterValidationOut(
            parameter_name=v.parameter_name,
            is_valid=v.is_valid,
            is_unrealistic=v.is_unrealistic,
            violations=v.violations,
            warnings=v.warnings,
            baseline_value=v.baseline_value,
            implied_scenario_value=v.implied_scenario_value,
        )
        for v in validations
    )


@router.post("/validate", response_model=ScenarioValidateResponse)
def validate_scenario(
    payload: ScenarioValidateRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> ScenarioValidateResponse:
    """Validates every parameter against the caller-supplied baseline --
    bounds, unknown columns, missing/target-label data, unrealistic
    magnitude, and restricted-column authorization -- without running
    anything. Never touches a database."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "scenario_validate", settings, identity=identity)
    _check_payload_bounds(len(payload.rows), len(payload.parameters))

    rows = [tuple(row) for row in payload.rows]
    parameters = _to_engine_parameters(payload.parameters)
    validations = validate_scenario_parameters(
        parameters, payload.columns, rows, payload.dimension_columns, identity.roles, settings
    )
    return ScenarioValidateResponse(parameter_validations=_to_validation_out(validations))


@router.post("/run", response_model=ScenarioRunResponse)
def run_scenario_route(
    payload: ScenarioRunRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> ScenarioRunResponse:
    """Runs the scenario -- `status="rejected"` (with every parameter's
    own violations) if any parameter failed validation, never a partial
    run with the bad ones silently dropped. Never touches a database;
    `rows`/`columns` are exactly what the caller sent."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "scenario_run", settings, identity=identity)
    _check_payload_bounds(len(payload.rows), len(payload.parameters))

    rows = [tuple(row) for row in payload.rows]
    parameters = _to_engine_parameters(payload.parameters)
    result = run_scenario(
        payload.scenario_id,
        payload.name,
        payload.columns,
        rows,
        payload.dimension_columns,
        parameters,
        tuple(identity.roles),
        settings,
    )

    visualization_spec_out: VisualizationSpecOut | None = None
    try:
        chart_spec = build_scenario_chart_spec(result, settings)
        if chart_spec is not None:
            visualization_spec_out = VisualizationSpecOut(
                chart_type=chart_spec.chart_type.value,
                title=chart_spec.title,
                fields=tuple(
                    ChartFieldOut(
                        column=f.column,
                        role=f.role.value,
                        aggregation=f.aggregation,
                        format=f.format,
                    )
                    for f in chart_spec.fields
                ),
                sort=(
                    ChartSortOut(column=chart_spec.sort.column, direction=chart_spec.sort.direction)
                    if chart_spec.sort is not None
                    else None
                ),
                limit=chart_spec.limit,
                is_downsampled=chart_spec.is_downsampled,
                notices=chart_spec.notices,
                accessibility=AccessibilityMetadataOut(
                    alt_text=chart_spec.accessibility.alt_text,
                    summary=chart_spec.accessibility.summary,
                ),
                reason=chart_spec.reason,
                engine_version=chart_spec.engine_version,
            )
    except Exception as exc:  # noqa: BLE001 - an accuracy aid must never block the response
        logger.warning(
            "[scenarios] visualization spec computation failed, proceeding without: %s", exc
        )
        visualization_spec_out = None

    return ScenarioRunResponse(
        scenario_id=result.scenario_id,
        name=result.name,
        status=result.status,
        parameter_validations=_to_validation_out(result.parameter_validations),
        rejection_reasons=result.rejection_reasons,
        comparisons=tuple(
            ScenarioMetricComparisonOut(
                metric=c.metric,
                baseline_value=c.baseline_value,
                scenario_value=c.scenario_value,
                delta=c.delta,
                delta_percent=c.delta_percent,
                baseline_kind=c.baseline_kind.value,
                scenario_kind=c.scenario_kind.value,
            )
            for c in result.comparisons
        ),
        truth_level=result.truth_level.value,
        limitations=result.limitations,
        engine_version=result.engine_version,
        visualization_spec=visualization_spec_out,
    )
