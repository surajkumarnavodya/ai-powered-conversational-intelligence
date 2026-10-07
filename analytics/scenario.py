"""The governed What-If Scenario Engine -- Prompt 38
(`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`).

`run_scenario` turns an already-fetched, already-governed baseline result
(`columns`, `rows` -- the same shape every other `analytics/` module
takes) plus a set of typed `ScenarioParameter` assumptions into a
`ScenarioResult`: a baseline-vs-scenario comparison per affected metric,
with the real `analytics.engine.compute_analytics_result` run over both
sides (reused, never reimplemented). Pure, deterministic, no LLM -- the
same posture every other module under `analytics/` already establishes.

**"Run scenarios without changing source data," literally, not just by
convention**: this module never issues a query, never opens a database
connection, and never mutates its own `rows` argument -- every
scenario-adjusted row is a brand-new, separate copy built in memory.
`rows`'s caller (a future API route, the multi-agent supervisor's own
`sql_data` agent output) is responsible for having fetched them through
the application's existing, fully-governed read path in the first place;
this module's job starts only after that.

**Clearly separates observed facts, assumptions and modeled estimates**,
both through `analytics.models.ScenarioValueKind` (a value's *kind*,
orthogonal to `DataTruthLevel`) and structurally:
`ScenarioMetricComparison.baseline_kind`/`.scenario_kind` are fixed
`Literal`s, not merely conventionally-set defaults -- see that model's
own docstring.

**Reject invalid or unauthorized assumptions, never partially trust
one**: `validate_scenario_parameters` reports *every* parameter's full
set of violations; `run_scenario` runs the scenario only when every
parameter cleared validation -- one invalid parameter rejects the whole
scenario (mirrors `agent.analytical_plan`'s own "an invalid plan is never
partially trusted" precedent), never a scenario that silently drops the
bad parameter and runs the rest.

**Multiple parameters composing**: parameters are applied to the working
row set **in the order given**, each one scaling the *then-current*
value of its `affected_metric` (which may already reflect an earlier
parameter's own adjustment if they target overlapping rows) up or down
to match the parameter's own implied total. This is sequential
composition, not a jointly-solved system of assumptions -- a disclosed,
bounded scope (`ScenarioResult.limitations`), the same category of
honest simplification `analytics.causal`'s own pairwise-only interaction
testing already discloses elsewhere in this package.
"""

from __future__ import annotations

import math

from recommendation.models import Recommendation, RecommendationKind

from agent.authz import Permission, has_role_permission
from agent.provenance import DataTruthLevel, ProvenancedClaim
from analytics.engine import compute_analytics_result
from analytics.models import (
    SCENARIO_ENGINE_VERSION,
    AnalyticsResult,
    ForecastResult,
    ParameterValidation,
    ScenarioChangeType,
    ScenarioForecastPoint,
    ScenarioForecastResult,
    ScenarioMetricComparison,
    ScenarioParameter,
    ScenarioResult,
)
from config.sensitive_columns import load_sensitive_columns
from config.settings import Settings, get_settings
from security.audit_log import log_security_event

_SOURCE = "analytics.scenario.run_scenario"


def _restricted_column_names(settings: Settings) -> frozenset[str]:
    """Every column name (lowercased, table-agnostic) classified
    "restricted" anywhere in `config/sensitive_columns.yaml` -- the
    identical helper `recommendation.engine._restricted_column_names`
    already establishes, kept as a small, independent copy here rather
    than importing a private name across module boundaries (the two
    modules' own authorization checks are conceptually related but not
    coupled -- each should be free to evolve its own gate independently).
    """
    del settings  # reserved for a future per-database override; unused today
    classifications = load_sensitive_columns()
    return frozenset(
        column.lower() for (_table, column), tier in classifications.items() if tier == "restricted"
    )


def _apply_change(
    baseline_value: float, change_type: ScenarioChangeType, scenario_value: float
) -> float:
    """The one deterministic transform every `ScenarioChangeType` maps
    to -- see that enum's own docstring for the exact formula per type."""
    if change_type == ScenarioChangeType.PERCENT_CHANGE:
        return baseline_value * (1 + scenario_value / 100.0)
    if change_type == ScenarioChangeType.ABSOLUTE_DELTA:
        return baseline_value + scenario_value
    if change_type == ScenarioChangeType.ABSOLUTE_VALUE:
        return scenario_value
    if change_type == ScenarioChangeType.MULTIPLIER:
        return baseline_value * scenario_value
    raise AssertionError(
        f"unhandled ScenarioChangeType {change_type!r}"
    )  # unreachable: closed enum


def _resolve_baseline_value(
    columns: list[str],
    rows: list[tuple],
    dimension_columns: list[str],
    affected_metric: str,
    target_label: str | None,
) -> float | None:
    """The real, observed value a parameter's `affected_metric` (and
    optional `target_label`) resolves to in `rows` -- a straight sum,
    the same aggregate convention `analytics.root_cause
    .investigate_root_cause`'s own `current_total`/`baseline_total`
    already use.

    Returns `None` when it cannot be resolved at all: the column doesn't
    exist, there are no rows, `target_label` was given without
    `dimension_columns`, or no row's own dimension label matches
    `target_label` -- every one of those is reflected as a distinct
    violation by the caller (`validate_scenario_parameters`), never
    silently treated as "zero."
    """
    if affected_metric not in columns or not rows:
        return None
    value_idx = columns.index(affected_metric)

    if target_label is None:
        values = [row[value_idx] for row in rows if row[value_idx] is not None]
        return sum(float(v) for v in values) if values else None

    if not dimension_columns or any(c not in columns for c in dimension_columns):
        return None
    dim_indices = [columns.index(c) for c in dimension_columns]
    total = 0.0
    found = False
    for row in rows:
        if row[value_idx] is None or any(row[i] is None for i in dim_indices):
            continue
        label = " / ".join(str(row[i]) for i in dim_indices)
        if label == target_label:
            total += float(row[value_idx])
            found = True
    return total if found else None


def validate_scenario_parameters(
    parameters: list[ScenarioParameter],
    columns: list[str],
    rows: list[tuple],
    dimension_columns: list[str] | None = None,
    caller_roles: tuple[str, ...] = (),
    settings: Settings | None = None,
) -> tuple[ParameterValidation, ...]:
    """Validates every parameter independently, reporting every problem
    found rather than stopping at the first -- see this module's own
    docstring for the full "reject invalid or unauthorized assumptions"
    design.

    Args:
        parameters: The scenario's own typed assumptions.
        columns: The real baseline result's column names.
        rows: The real baseline result's rows -- read only, never
            mutated.
        dimension_columns: Which columns identify a row's label, needed
            only when a parameter sets `target_label`. Defaults to none.
        caller_roles: The roles of whoever is asking to run this
            scenario -- used only for the restricted-column
            authorization check below.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        One `ParameterValidation` per input parameter, in the same order
        -- never fewer, never reordered, so a caller can zip them back
        against the original parameter list unambiguously.
    """
    settings = settings or get_settings()
    dimension_columns = dimension_columns or []
    restricted = _restricted_column_names(settings)

    results: list[ParameterValidation] = []
    for param in parameters:
        violations: list[str] = []
        warnings: list[str] = []
        baseline_value: float | None = None
        implied: float | None = None

        if param.affected_metric not in columns:
            violations.append(
                f"unknown column {param.affected_metric!r} -- not present in the baseline result"
            )
        if param.target_label is not None and not dimension_columns:
            violations.append("target_label requires dimension_columns to be specified")
        if (
            param.min_value is not None
            and param.max_value is not None
            and param.min_value > param.max_value
        ):
            violations.append("invalid bounds: min_value exceeds max_value")
        if not math.isfinite(param.scenario_value):
            violations.append("scenario_value must be a finite number (not NaN/infinite)")

        restricted_hit = param.affected_metric.lower() in restricted
        if restricted_hit and not has_role_permission(
            caller_roles, Permission.VIEW_RESTRICTED_COLUMNS
        ):
            violations.append(f"not authorized: {param.affected_metric!r} is classified restricted")
            log_security_event(
                "scenario_parameter_suppressed",
                "info",
                "A what-if scenario parameter referencing a restricted column was rejected "
                "for a caller lacking VIEW_RESTRICTED_COLUMNS.",
                parameter=param.name,
                column=param.affected_metric,
            )

        column_known = param.affected_metric in columns
        if column_known and math.isfinite(param.scenario_value):
            baseline_value = _resolve_baseline_value(
                columns, rows, dimension_columns, param.affected_metric, param.target_label
            )
            if baseline_value is None:
                if not rows:
                    violations.append(
                        "no baseline rows available to resolve a real value for this metric"
                    )
                elif param.target_label is not None:
                    violations.append(
                        f"target label {param.target_label!r} was not found in the baseline data"
                    )
            else:
                candidate = _apply_change(baseline_value, param.change_type, param.scenario_value)
                if not math.isfinite(candidate):
                    violations.append("the implied scenario value is not finite")
                else:
                    implied = candidate
                    if param.min_value is not None and param.scenario_value < param.min_value:
                        violations.append(
                            f"scenario_value {param.scenario_value:g} is below "
                            f"min_value {param.min_value:g}"
                        )
                    if param.max_value is not None and param.scenario_value > param.max_value:
                        violations.append(
                            f"scenario_value {param.scenario_value:g} is above "
                            f"max_value {param.max_value:g}"
                        )

        is_unrealistic = False
        if baseline_value is not None and implied is not None and baseline_value != 0:
            percent_change = 100 * (implied - baseline_value) / abs(baseline_value)
            if abs(percent_change) > settings.scenario_unrealistic_change_percent_threshold:
                is_unrealistic = True
                note = (
                    f"implies a {percent_change:+.1f}% change from baseline, beyond the "
                    f"configured {settings.scenario_unrealistic_change_percent_threshold:g}% "
                    "sanity threshold -- treat this result as exploratory, not plausible"
                )
                if settings.scenario_reject_unrealistic:
                    violations.append(f"rejected as unrealistic: {note}")
                else:
                    warnings.append(f"unrealistic (not rejected): {note}")

        results.append(
            ParameterValidation(
                parameter_name=param.name,
                is_valid=not violations,
                is_unrealistic=is_unrealistic,
                violations=tuple(violations),
                warnings=tuple(warnings),
                baseline_value=baseline_value,
                implied_scenario_value=implied,
            )
        )
    return tuple(results)


def _rows_matching_label(
    columns: list[str], rows: list[list], dimension_columns: list[str], label: str
) -> list[int]:
    dim_indices = [columns.index(c) for c in dimension_columns]
    matching: list[int] = []
    for i, row in enumerate(rows):
        if any(row[j] is None for j in dim_indices):
            continue
        if " / ".join(str(row[j]) for j in dim_indices) == label:
            matching.append(i)
    return matching


def _apply_parameter_to_rows(
    working_rows: list[list],
    columns: list[str],
    dimension_columns: list[str],
    param: ScenarioParameter,
) -> None:
    """Mutates `working_rows` in place (a caller-owned scratch copy, never
    the original baseline rows) so that the matching rows' total for
    `param.affected_metric` equals this parameter's own implied total,
    scaling every matching row **proportionally** to its current share of
    that total -- this is what lets `run_scenario` recompute a full,
    internally-consistent `AnalyticsResult` (rankings, outliers, growth)
    over the adjusted rows, rather than only ever knowing one aggregate
    number.

    When the current total is exactly `0` (every matching row is
    currently zero), the target total is distributed evenly across the
    matching rows instead -- proportional scaling has nothing to scale
    from, and an even split is the least-arbitrary alternative.
    """
    value_idx = columns.index(param.affected_metric)
    if param.target_label is None:
        matching = [i for i, row in enumerate(working_rows) if row[value_idx] is not None]
    else:
        matching = [
            i
            for i in _rows_matching_label(
                columns, working_rows, dimension_columns, param.target_label
            )
            if working_rows[i][value_idx] is not None
        ]
    if not matching:
        return

    current_total = sum(working_rows[i][value_idx] for i in matching)
    target_total = _apply_change(current_total, param.change_type, param.scenario_value)

    if current_total != 0:
        factor = target_total / current_total
        for i in matching:
            working_rows[i][value_idx] = working_rows[i][value_idx] * factor
    else:
        even_share = target_total / len(matching)
        for i in matching:
            working_rows[i][value_idx] = even_share


def run_scenario(
    scenario_id: str,
    name: str,
    columns: list[str],
    rows: list[tuple],
    dimension_columns: list[str] | None = None,
    parameters: list[ScenarioParameter] | None = None,
    caller_roles: tuple[str, ...] = (),
    settings: Settings | None = None,
) -> ScenarioResult:
    """The engine's single entry point.

    Args:
        scenario_id: Caller-supplied identifier for this run.
        name: A short, human-readable scenario name.
        columns: The real baseline result's column names.
        rows: The real baseline result's rows -- never mutated; a
            brand-new, in-memory copy is built for the scenario side.
        dimension_columns: Which columns identify a row's label, needed
            only for a parameter with `target_label` set.
        parameters: The scenario's own typed assumptions. An empty list
            (or `None`) is a valid, trivial "no-op scenario" -- the
            scenario side is then identical to the baseline.
        caller_roles: The roles of whoever is asking to run this
            scenario.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        A `ScenarioResult` -- `status="rejected"` (with every parameter's
        own violations flattened into `rejection_reasons`) if *any*
        parameter failed validation; `status="ok"` only when every one
        passed.
    """
    settings = settings or get_settings()
    dimension_columns = dimension_columns or []
    parameters = parameters or []

    if len(parameters) > settings.scenario_max_parameters:
        return ScenarioResult(
            scenario_id=scenario_id,
            name=name,
            status="rejected",
            rejection_reasons=(
                f"too many parameters ({len(parameters)}); the configured maximum is "
                f"{settings.scenario_max_parameters}",
            ),
        )

    validations = validate_scenario_parameters(
        parameters, columns, rows, dimension_columns, caller_roles, settings
    )
    if any(not v.is_valid for v in validations):
        reasons = tuple(
            f"{v.parameter_name}: {violation}" for v in validations for violation in v.violations
        )
        return ScenarioResult(
            scenario_id=scenario_id,
            name=name,
            status="rejected",
            parameter_validations=validations,
            rejection_reasons=reasons,
        )

    limitations: list[str] = []
    for validation in validations:
        limitations.extend(f"{validation.parameter_name}: {w}" for w in validation.warnings)

    affected_metrics_seen: list[str] = []
    for param in parameters:
        if param.affected_metric not in affected_metrics_seen:
            affected_metrics_seen.append(param.affected_metric)

    overlap_metrics = {
        metric
        for metric in affected_metrics_seen
        if sum(1 for p in parameters if p.affected_metric == metric) > 1
    }
    if overlap_metrics:
        limitations.append(
            "multiple parameters target the same metric ("
            + ", ".join(sorted(overlap_metrics))
            + ") -- applied sequentially in the order given, not jointly solved"
        )

    working_rows: list[list] = [list(row) for row in rows]
    for param in parameters:
        _apply_parameter_to_rows(working_rows, columns, dimension_columns, param)
    scenario_rows: list[tuple] = [tuple(row) for row in working_rows]

    comparisons: list[ScenarioMetricComparison] = []
    for metric in affected_metrics_seen:
        value_idx = columns.index(metric)
        baseline_values = [row[value_idx] for row in rows if row[value_idx] is not None]
        scenario_values = [row[value_idx] for row in scenario_rows if row[value_idx] is not None]
        baseline_total = sum(float(v) for v in baseline_values) if baseline_values else 0.0
        scenario_total = sum(float(v) for v in scenario_values) if scenario_values else 0.0
        delta = scenario_total - baseline_total
        delta_percent = round(100 * delta / abs(baseline_total), 1) if baseline_total != 0 else None
        comparisons.append(
            ScenarioMetricComparison(
                metric=metric,
                baseline_value=baseline_total,
                scenario_value=scenario_total,
                delta=delta,
                delta_percent=delta_percent,
            )
        )

    baseline_analytics: AnalyticsResult | None = compute_analytics_result(columns, rows, settings)
    scenario_analytics: AnalyticsResult | None = compute_analytics_result(
        columns, scenario_rows, settings
    )

    return ScenarioResult(
        scenario_id=scenario_id,
        name=name,
        status="ok",
        parameter_validations=validations,
        comparisons=tuple(comparisons),
        baseline_analytics=baseline_analytics,
        scenario_analytics=scenario_analytics,
        limitations=tuple(limitations),
        engine_version=SCENARIO_ENGINE_VERSION,
    )


def apply_scenario_to_forecast(
    forecast_result: ForecastResult,
    parameter: ScenarioParameter,
    settings: Settings | None = None,
) -> ScenarioForecastResult:
    """Applies exactly one `ScenarioParameter` uniformly to every point of
    an already-computed `analytics.forecasting.ForecastResult` -- e.g.
    "what if growth is 20% higher than forecasted."

    Deliberately simplistic, disclosed as such: this perturbs the
    forecast's own already-fitted point estimates directly, it does not
    refit the underlying model against a hypothetically different
    history. For a genuinely refit scenario, feed an adjusted historical
    series into `analytics.forecasting.generate_forecast` directly
    instead -- that function is unchanged by this one.

    Args:
        forecast_result: An already-computed forecast (reused, never
            recomputed).
        parameter: The single assumption to apply -- `target_label` and
            `min_value`/`max_value` are not meaningful here and are
            ignored (a forecast has no per-row dimension to target); use
            `validate_scenario_parameters` separately first if bounds
            enforcement is needed before calling this.
        settings: Defaults to `config.settings.get_settings()` (accepted
            for signature symmetry with every other function in this
            module; not currently read).
    """
    del settings  # accepted for symmetry; this function has no tunable of its own today
    if forecast_result.status != "ok" or not forecast_result.points:
        return ScenarioForecastResult(
            status="rejected",
            rejection_reasons=('the source forecast is not status="ok" -- nothing to adjust',),
        )
    if not math.isfinite(parameter.scenario_value):
        return ScenarioForecastResult(
            status="rejected",
            rejection_reasons=("scenario_value must be a finite number (not NaN/infinite)",),
        )

    points = tuple(
        ScenarioForecastPoint(
            period=point.period,
            baseline_forecast=point.forecast,
            scenario_forecast=_apply_change(
                point.forecast, parameter.change_type, parameter.scenario_value
            ),
        )
        for point in forecast_result.points
    )

    limitations = list(forecast_result.limitations) + [
        "this is a uniform adjustment of an already-fitted forecast's own point estimates, "
        "not a refit of the underlying model against a hypothetically different history."
    ]
    return ScenarioForecastResult(
        status="ok", points=points, parameter=parameter, limitations=tuple(limitations)
    )


def recommendations_for_scenario(result: ScenarioResult) -> tuple[Recommendation, ...]:
    """Builds `recommendation.models.Recommendation`s from an already-
    computed `ScenarioResult` -- the identical "integrate with the
    recommendation contract" pattern `analytics.forecasting
    .recommendations_for_forecast` already established for its own
    engine.

    Deliberately a standalone function, not wired into
    `agent.nodes.generate_recommendations_node` or any other live path in
    this pass -- the identical "compute + test, defer the deepest wiring"
    deferral every engine module in this package has used at least once
    (see `38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`'s own disclosed scope).
    """
    recommendations: list[Recommendation] = []

    if result.status == "rejected":
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.ACTION,
                claim=ProvenancedClaim(
                    value=(
                        "This scenario could not be run ("
                        + "; ".join(result.rejection_reasons)
                        + "). Review the flagged parameter(s) and try again."
                    ),
                    level=DataTruthLevel.AI_INFERENCE,
                    source=_SOURCE,
                ),
                rationale="analytics.scenario.run_scenario rejected this scenario.",
            )
        )
        return tuple(recommendations)

    for comparison in result.comparisons:
        if comparison.delta_percent is None:
            continue
        direction = "increase" if comparison.delta >= 0 else "decrease"
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.NEXT_QUESTION,
                claim=ProvenancedClaim(
                    value=(
                        f"Under this scenario, {comparison.metric} would {direction} by "
                        f"{abs(comparison.delta_percent):g}% (estimate, not an observed fact)."
                    ),
                    level=DataTruthLevel.AI_INFERENCE,
                    source=_SOURCE,
                ),
                rationale="analytics.scenario.run_scenario's own baseline/scenario comparison.",
                affected_entity=comparison.metric,
            )
        )
    return tuple(recommendations)
