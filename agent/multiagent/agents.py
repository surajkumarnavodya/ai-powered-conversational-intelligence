"""The specialist agents (Prompt 34): one spec and one handler each.

Each handler wraps an existing, already-tested function. It adds no new SQL,
no new statistics and no new authorization logic. Every tool call goes through
`ToolGateway`, and every claim it returns is checked by
`agent.multiagent.resolution` before it can reach the report.

Specialists:
- planner (llm): splits the question. Its output is text, which is re-checked
  and then routed. It can name no tool and no agent.
- governance (control): the request gate, and row redaction before any engine
  sees the rows. A deterministic control, not an LLM.
- semantic (retrieval): governed metric definitions (CONFIRMED_BUSINESS_TRUTH).
- sql_data (data): the only agent that touches the database, and only through
  `run_agent`. It is the sole source of DATABASE_FACT row counts.
- analytics (engine): statistics over the governed rows. Reads, never recomputes SQL.
- forecast (engine): projections. Always AI_INFERENCE, never observed fact.
- root_cause (engine): contribution attribution. Always AI_INFERENCE.
- recommendation (engine): dedupes the sub-runs' recommendations and links
  them to evidence. Always AI_INFERENCE.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from governance.request_policy import evaluate_request
from governance.result_policy import govern_rows_for_caller

from agent.analyst.evidence import build_outcome
from agent.analyst.graph import recommend_node
from agent.analyst.state import AnalystState
from agent.multiagent.contracts import AgentName, AgentOutput, AgentSpec, Claim
from agent.multiagent.policy import ToolGateway
from agent.multiagent.tools import (
    TOOL_ANALYTICS,
    TOOL_BUSINESS_CONTEXT,
    TOOL_DATA_QUALITY,
    TOOL_FORECAST,
    TOOL_GOVERNED_SQL,
    TOOL_LLM_PLAN,
    TOOL_OPTIMIZATION,
    TOOL_PREDICTIVE,
    TOOL_ROOT_CAUSE,
    TOOL_ROOT_CAUSE_ADVANCED,
    TOOL_SCENARIO,
)
from agent.provenance import DataTruthLevel
from agent.tools.types import ToolResult
from config.settings import Settings

SPECS: dict[AgentName, AgentSpec] = {
    AgentName.PLANNER: AgentSpec(
        name=AgentName.PLANNER,
        kind="llm",
        allowed_tools=frozenset({TOOL_LLM_PLAN}),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=3,
    ),
    AgentName.GOVERNANCE: AgentSpec(
        name=AgentName.GOVERNANCE,
        kind="control",
        allowed_tools=frozenset(),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=0,
    ),
    AgentName.SEMANTIC: AgentSpec(
        name=AgentName.SEMANTIC,
        kind="retrieval",
        allowed_tools=frozenset({TOOL_BUSINESS_CONTEXT}),
        max_truth_level=DataTruthLevel.CONFIRMED_BUSINESS_TRUTH,
        cost_units=2,
        max_attempts=2,
    ),
    AgentName.SQL_DATA: AgentSpec(
        name=AgentName.SQL_DATA,
        kind="data",
        allowed_tools=frozenset({TOOL_GOVERNED_SQL}),
        max_truth_level=DataTruthLevel.DATABASE_FACT,
        cost_units=5,
    ),
    AgentName.ANALYTICS: AgentSpec(
        name=AgentName.ANALYTICS,
        kind="engine",
        allowed_tools=frozenset({TOOL_ANALYTICS}),
        max_truth_level=DataTruthLevel.DATABASE_FACT,
        cost_units=1,
    ),
    AgentName.FORECAST: AgentSpec(
        name=AgentName.FORECAST,
        kind="engine",
        allowed_tools=frozenset({TOOL_FORECAST}),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=1,
    ),
    AgentName.ROOT_CAUSE: AgentSpec(
        name=AgentName.ROOT_CAUSE,
        kind="engine",
        # Prompt 37: TOOL_ROOT_CAUSE_ADVANCED is additive -- the handler
        # below only ever reaches for it when a caller's RootCausePair
        # names a dimension_hierarchy; TOOL_ROOT_CAUSE stays the default.
        allowed_tools=frozenset({TOOL_ROOT_CAUSE, TOOL_ROOT_CAUSE_ADVANCED}),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=1,
    ),
    AgentName.SCENARIO: AgentSpec(
        name=AgentName.SCENARIO,
        kind="engine",
        allowed_tools=frozenset({TOOL_SCENARIO}),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=1,
    ),
    AgentName.OPTIMIZATION: AgentSpec(
        name=AgentName.OPTIMIZATION,
        kind="engine",
        allowed_tools=frozenset({TOOL_OPTIMIZATION}),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=1,
    ),
    AgentName.PREDICTIVE: AgentSpec(
        name=AgentName.PREDICTIVE,
        kind="engine",
        allowed_tools=frozenset({TOOL_PREDICTIVE}),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=1,
    ),
    AgentName.DATA_QUALITY: AgentSpec(
        name=AgentName.DATA_QUALITY,
        kind="engine",
        allowed_tools=frozenset({TOOL_DATA_QUALITY}),
        # DATABASE_FACT, not AI_INFERENCE, unlike every other engine spec
        # above -- see agent.multiagent.contracts.AgentName.DATA_QUALITY's
        # own docstring and data_quality/models.py's module docstring for
        # why a data-quality finding is a genuine observed fact, not an
        # inference/prediction/simulation.
        max_truth_level=DataTruthLevel.DATABASE_FACT,
        cost_units=1,
    ),
    AgentName.RECOMMENDATION: AgentSpec(
        name=AgentName.RECOMMENDATION,
        kind="engine",
        allowed_tools=frozenset(),
        max_truth_level=DataTruthLevel.AI_INFERENCE,
        cost_units=0,
    ),
}


@dataclass
class DataSlot:
    """One sql_data task's result, held in memory for this turn only. Raw
    rows never leave the supervisor. They are not returned to the caller,
    logged or put in the trace."""

    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    governed_rows: list[Any] | None = None
    analytics: dict[str, Any] | None = None
    analytical_intent: str | None = None
    recommendations: list[dict] = field(default_factory=list)
    row_evidence_id: str | None = None


@dataclass
class Workspace:
    """Everything the agents of one turn share: the evidence index the
    supervisor issues, and the per-data-task slots. The evidence index is what
    `validate_outputs` checks citations against."""

    evidence: list[dict] = field(default_factory=list)
    evidence_ids: set[str] = field(default_factory=set)
    slots: dict[str, DataSlot] = field(default_factory=dict)
    next_index: int = 1

    def issue(self, items: list[dict]) -> None:
        for item in items:
            self.evidence.append(item)
            self.evidence_ids.add(item["id"])
        self.next_index += len(items)


@dataclass(frozen=True)
class AgentTask:
    """One unit of work for a specialist. `params` is supervisor-built and
    typed by the handler that reads it. `data_task` names the sql_data task
    whose result this task works on, so a claim is attributed to that task."""

    id: str
    agent: AgentName
    text: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def subject(self) -> str:
        return str(self.params.get("data_task", self.id))


@dataclass
class AgentContext:
    """One agent invocation's view of the turn. `spec` is the invoking agent's
    own spec, bound by the supervisor, so `call` only ever checks tools against
    the agent actually running. A handler cannot name another agent's spec."""

    spec: AgentSpec
    gateway: ToolGateway
    settings: Settings
    workspace: Workspace
    caller_roles: tuple[str, ...]

    def call(self, tool_name: str, payload: dict[str, Any]) -> ToolResult:
        return self.gateway.call(self.spec, tool_name, payload)


Handler = Callable[[AgentContext, AgentTask], AgentOutput]


def _failed(task: AgentTask, detail: str) -> AgentOutput:
    return AgentOutput(agent=task.agent, task_id=task.subject, status="failed", detail=detail)


def _skipped(task: AgentTask, detail: str, open_item: str | None = None) -> AgentOutput:
    return AgentOutput(
        agent=task.agent,
        task_id=task.subject,
        status="skipped",
        detail=detail,
        open_items=(open_item,) if open_item else (),
    )


def planner_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    result = ctx.call(
        TOOL_LLM_PLAN,
        {"question": task.text, "max_items": task.params["max_items"]},
    )
    if not result.success:
        return _failed(task, "planner tool failed")
    return AgentOutput(
        agent=task.agent,
        task_id=task.id,
        status="ok",
        data=dict(result.output),
        detail=f"planning mode {result.output['mode']}",
    )


def governance_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    """Two modes. "request" decides whether a question may be answered at all.
    "rows" redacts the rows a data task produced before any engine sees them.
    Both run locally and deterministically."""
    if task.params["mode"] == "request":
        verdict = evaluate_request(task.text, ctx.caller_roles)
        return AgentOutput(
            agent=task.agent,
            task_id=task.id,
            status="ok",
            data={
                "decision": str(verdict.decision.value),
                "message": verdict.message,
                "category": verdict.category,
            },
        )
    slot = ctx.workspace.slots[task.params["data_task"]]
    slot.governed_rows = govern_rows_for_caller(slot.columns, slot.rows, ctx.caller_roles)
    changed = slot.governed_rows != slot.rows
    return AgentOutput(
        agent=task.agent,
        task_id=task.subject,
        status="ok",
        data={"changed": changed},
        detail="rows redacted" if changed else "no redaction needed",
    )


def semantic_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    result = ctx.call(
        TOOL_BUSINESS_CONTEXT,
        {"question": task.text, "database_id": task.params["database_id"]},
    )
    if not result.success:
        return _failed(task, "semantic tool failed")
    claims = tuple(
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=f"Governed metric: {metric.get('name', 'unnamed')}",
            truth_level=DataTruthLevel.CONFIRMED_BUSINESS_TRUTH,
            key=f"metric:{metric.get('name', '')}",
            value=metric.get("name"),
        )
        for metric in result.output["metrics"]
    )
    return AgentOutput(agent=task.agent, task_id=task.id, status="ok", claims=claims)


def sql_data_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    result = ctx.call(TOOL_GOVERNED_SQL, {"question": task.text})
    if not result.success:
        return _failed(task, "the query step failed")
    final = result.output
    outcome = build_outcome(final, task.id, ctx.workspace.next_index, task.text)
    ctx.workspace.issue(list(outcome["evidence"]))
    row_evidence = next((e["id"] for e in outcome["evidence"] if e["kind"] == "row_count"), None)
    ctx.workspace.slots[task.id] = DataSlot(
        columns=list(final.get("result_columns") or []),
        rows=list(final.get("result_rows") or []),
        analytical_intent=_intent_value(final.get("analytical_intent")),
        recommendations=list(final.get("recommendations") or []),
        row_evidence_id=row_evidence,
    )
    claims = tuple(
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=item["claim"],
            truth_level=DataTruthLevel.DATABASE_FACT,
            key="row_count",
            value=item["row_count"],
            grounded_in=(item["id"],),
        )
        for item in outcome["evidence"]
        if item["kind"] == "row_count"
    )
    return AgentOutput(
        agent=task.agent,
        task_id=task.id,
        status="ok",
        claims=claims,
        open_items=tuple(outcome["open_items"]),
        data={"sub_status": outcome["status"]},
        detail=outcome["detail"],
    )


def analytics_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    slot = ctx.workspace.slots[task.params["data_task"]]
    if slot.governed_rows is None or not slot.columns:
        return _skipped(task, "no governed rows to analyse")
    result = ctx.call(
        TOOL_ANALYTICS,
        {"columns": slot.columns, "rows": slot.governed_rows},
    )
    if not result.success:
        return _failed(task, "analytics tool failed")
    slot.analytics = result.output
    subject = task.subject
    claims: list[Claim] = [
        Claim(
            agent=task.agent,
            task_id=subject,
            text=f"The result has {result.output['row_count']} row(s).",
            truth_level=DataTruthLevel.DATABASE_FACT,
            key="row_count",
            value=result.output["row_count"],
            grounded_in=(slot.row_evidence_id,) if slot.row_evidence_id else (),
        )
    ]
    for index, finding in enumerate(result.output.get("findings") or []):
        claim_obj = finding.get("claim") or {}
        claims.append(
            Claim(
                agent=task.agent,
                task_id=subject,
                text=str(claim_obj.get("value", "")),
                truth_level=DataTruthLevel(str(_enum_value(claim_obj.get("level")))),
                key=f"finding:{index}",
                grounded_in=(slot.row_evidence_id,) if slot.row_evidence_id else (),
            )
        )
    return AgentOutput(agent=task.agent, task_id=subject, status="ok", claims=tuple(claims))


def forecast_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    slot = ctx.workspace.slots[task.params["data_task"]]
    if slot.analytical_intent != "forecast" or slot.analytics is None:
        return _skipped(task, "forecast not requested for this result")
    points = _growth_points(slot.analytics)
    if len(points) < 2:
        return _skipped(
            task, "no time series to project", "Forecast skipped: no usable time series."
        )
    result = ctx.call(
        TOOL_FORECAST,
        {"points": points, "horizon": ctx.settings.forecast_default_horizon},
    )
    if not result.success:
        return _failed(task, "forecast tool failed")
    output = result.output
    subject = task.subject
    if output["status"] != "ok":
        reasons = "; ".join(output.get("rejection_reasons") or ["insufficient data"])
        return AgentOutput(
            agent=task.agent,
            task_id=subject,
            status="ok",
            open_items=(f"Forecast not produced: {reasons}.",),
        )
    claims = tuple(
        Claim(
            agent=task.agent,
            task_id=subject,
            text=f"Projected {point['period']}: {point['forecast']} (estimate, not observed)",
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"forecast:{point['period']}",
            value=point["forecast"],
            grounded_in=(slot.row_evidence_id,) if slot.row_evidence_id else (),
        )
        for point in output["points"]
    )
    return AgentOutput(agent=task.agent, task_id=subject, status="ok", claims=claims)


def _root_cause_basic(
    ctx: AgentContext, task: AgentTask, current: Any, baseline: Any
) -> AgentOutput:
    """Prompt 14's own, flat, contribution-percent-only shape -- byte-for-
    byte the pre-Prompt-37 behavior. Reached whenever `task.params` names
    no `dimension_hierarchy` (the common case; every pre-existing caller
    stays on this path, unchanged)."""
    result = ctx.call(
        TOOL_ROOT_CAUSE,
        {
            "current_rows": current.governed_rows,
            "baseline_rows": baseline.governed_rows,
            "columns": current.columns,
            "dimension_columns": task.params["dimension_columns"],
            "value_column": task.params["value_column"],
        },
    )
    if not result.success:
        return _failed(task, "root cause tool failed")
    output = result.output
    if not output["has_sufficient_evidence"]:
        return AgentOutput(
            agent=task.agent,
            task_id=task.id,
            status="ok",
            open_items=("No root cause stated: the evidence was not sufficient.",),
        )
    grounded = tuple(e for e in (current.row_evidence_id, baseline.row_evidence_id) if e)
    claims = tuple(
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=(
                f"{contributor['label']} accounts for about "
                f"{contributor['contribution_percent']:.1f}% of the change (attribution estimate)"
            ),
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"contributor:{contributor['label']}",
            value=contributor["contribution_percent"],
            grounded_in=grounded,
        )
        for contributor in output["contributors"]
    )
    return AgentOutput(agent=task.agent, task_id=task.id, status="ok", claims=claims)


def _flatten_contributor_evidence(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Every `ContributorEvidence` dict across a `HierarchicalRootCauseNode`
    tree (depth-first, root first) -- a plain list is all a claim-building
    loop needs; the tree shape itself stays available in `AgentOutput.data`
    (never sent to the caller, see that field's own docstring) for a future
    UI that wants to render the hierarchy."""
    flattened = list(node.get("contributor_evidence") or [])
    for child in node.get("children") or []:
        flattened.extend(_flatten_contributor_evidence(child))
    return flattened


def _root_cause_advanced(
    ctx: AgentContext, task: AgentTask, current: Any, baseline: Any
) -> AgentOutput:
    """Prompt 37's hierarchical/significance/interaction upgrade -- reached
    only when `task.params["dimension_hierarchy"]` is set and
    `Settings.enable_advanced_root_cause` is on. Every claim text carries
    its own correlation-not-causation caveat; a distractor (a large raw
    contribution that failed the significance check) is disclosed as an
    open item, never asserted as a claim."""
    result = ctx.call(
        TOOL_ROOT_CAUSE_ADVANCED,
        {
            "current_rows": current.governed_rows,
            "baseline_rows": baseline.governed_rows,
            "columns": current.columns,
            "dimension_hierarchy": [list(level) for level in task.params["dimension_hierarchy"]],
            "value_column": task.params["value_column"],
        },
    )
    if not result.success:
        return _failed(task, "advanced root cause tool failed")
    output = result.output
    grounded = tuple(e for e in (current.row_evidence_id, baseline.row_evidence_id) if e)
    open_items: list[str] = []

    if not output["has_sufficient_evidence"]:
        open_items.append(
            "No root cause stated: no contributor was both large enough and statistically "
            "significant. " + output["causation_disclaimer"]
        )
        return AgentOutput(
            agent=task.agent, task_id=task.id, status="ok", open_items=tuple(open_items)
        )

    claims: list[Claim] = []
    for evidence in _flatten_contributor_evidence(output["root"]):
        contributor = evidence["contributor"]
        significance = evidence["significance"]
        claims.append(
            Claim(
                agent=task.agent,
                task_id=task.id,
                text=(
                    f"{contributor['label']} is associated with about "
                    f"{contributor['contribution_percent']:.1f}% of the change "
                    f"(p={significance['p_value']:.3f}, n={significance['current_n']}+"
                    f"{significance['baseline_n']}; correlation, not confirmed causation)"
                ),
                truth_level=DataTruthLevel.AI_INFERENCE,
                key=f"contributor:{contributor['label']}",
                value=contributor["contribution_percent"],
                grounded_in=grounded,
            )
        )
    for distractor in _flatten_contributor_evidence_distractors(output["root"]):
        contributor = distractor["contributor"]
        open_items.append(
            f"{contributor['label']} showed a {contributor['contribution_percent']:.1f}% "
            "raw contribution but was not statistically distinguishable from random "
            "variation -- not claimed as evidence."
        )
    for interaction in output["interactions"]:
        if not interaction["is_statistically_justified"]:
            continue
        claims.append(
            Claim(
                agent=task.agent,
                task_id=task.id,
                text=(
                    f"{interaction['label_a']} x {interaction['label_b']} shows a statistically "
                    f"justified interaction effect ({interaction['interaction_residual_percent']:+.1f}"
                    " pts beyond their independent contributions; correlation, not confirmed "
                    "causation)"
                ),
                truth_level=DataTruthLevel.AI_INFERENCE,
                key=f"interaction:{interaction['label_a']}|{interaction['label_b']}",
                value=interaction["interaction_residual_percent"],
                grounded_in=grounded,
            )
        )
    if not open_items:
        open_items.append(output["causation_disclaimer"])
    return AgentOutput(
        agent=task.agent,
        task_id=task.id,
        status="ok",
        claims=tuple(claims),
        open_items=tuple(open_items),
    )


def _flatten_contributor_evidence_distractors(node: dict[str, Any]) -> list[dict[str, Any]]:
    flattened = list(node.get("distractors") or [])
    for child in node.get("children") or []:
        flattened.extend(_flatten_contributor_evidence_distractors(child))
    return flattened


def root_cause_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    current = ctx.workspace.slots[task.params["current_task"]]
    baseline = ctx.workspace.slots[task.params["baseline_task"]]
    if current.governed_rows is None or baseline.governed_rows is None:
        return _skipped(task, "a comparison input is missing")
    if current.columns != baseline.columns:
        return _skipped(
            task,
            "comparison result shapes differ",
            "Root cause not attempted: result shapes differ.",
        )
    if task.params.get("dimension_hierarchy") and ctx.settings.enable_advanced_root_cause:
        return _root_cause_advanced(ctx, task, current, baseline)
    return _root_cause_basic(ctx, task, current, baseline)


def scenario_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    """Prompt 38 (38_WHATIF_SCENARIO_ENGINE_CONTRACT.md): runs a governed
    what-if scenario over one `sql_data` task's own already-governed
    rows -- never a second query, never caller-side raw data. Gated,
    like `root_cause`, on an explicit caller-supplied request
    (`agent.multiagent.supervisor.ScenarioRequest`) -- the planner never
    creates one, a model proposing a hypothetical is not authority to
    run it.

    `caller_roles` is intentionally **not** included in the dict passed
    to `ctx.call` -- `ToolGateway` refuses any tool input that tries to
    set an identity field; `agent.tools.registry.ToolRegistry.execute`
    injects the real, already-authorized roles into the tool's own
    payload automatically (see that module's own docstring).
    """
    slot = ctx.workspace.slots[task.params["data_task"]]
    if slot.governed_rows is None or not slot.columns:
        return _skipped(task, "no governed rows to run a scenario against")
    result = ctx.call(
        TOOL_SCENARIO,
        {
            "scenario_id": task.params["scenario_id"],
            "name": task.params["name"],
            "columns": slot.columns,
            "rows": slot.governed_rows,
            "dimension_columns": task.params.get("dimension_columns") or [],
            "parameters": task.params["parameters"],
        },
    )
    if not result.success:
        return _failed(task, "scenario tool failed")
    output = result.output
    if output["status"] == "rejected":
        reasons = "; ".join(output["rejection_reasons"]) or "parameter validation failed"
        return AgentOutput(
            agent=task.agent,
            task_id=task.id,
            status="ok",
            open_items=(f"Scenario not run: {reasons}.",),
        )
    grounded = (slot.row_evidence_id,) if slot.row_evidence_id else ()
    claims = tuple(
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=(
                f"Under this scenario, {comparison['metric']} would move from "
                f"{comparison['baseline_value']:.2f} (observed) to "
                f"{comparison['scenario_value']:.2f} (modeled estimate)"
                + (
                    f", a {comparison['delta_percent']:+.1f}% change"
                    if comparison["delta_percent"] is not None
                    else ""
                )
                + "."
            ),
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"scenario_metric:{comparison['metric']}",
            value=comparison["scenario_value"],
            grounded_in=grounded,
        )
        for comparison in output["comparisons"]
    )
    return AgentOutput(agent=task.agent, task_id=task.id, status="ok", claims=claims)


def optimization_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    """Prompt 39 (39_OPTIMIZATION_ENGINE_CONTRACT.md): solves a governed
    decision-optimization problem. Gated, like `scenario`/`root_cause`,
    on an explicit caller-supplied request
    (`agent.multiagent.supervisor.OptimizationRequest`) -- the planner
    never creates one, a model proposing constraints/an objective is not
    authority to solve them. Self-contained (no `data_task` needed): the
    problem is already fully specified by its own caller, not derived
    from a `sql_data` task's rows.

    `caller_roles` is never included in the dict passed to `ctx.call` --
    the registry injects the real, already-authorized roles
    automatically (see `scenario_handler`'s own docstring for the full
    reasoning, identical here).
    """
    result = ctx.call(TOOL_OPTIMIZATION, {"problem": task.params["problem"]})
    if not result.success:
        return _failed(task, "optimization tool failed")
    output = result.output
    status = output["status"]
    if status != "optimal":
        reasons = "; ".join(output["infeasibility_reasons"]) or "no solution was found"
        return AgentOutput(
            agent=task.agent,
            task_id=task.id,
            status="ok",
            open_items=(f"Optimization not solved ({status}): {reasons}.",),
        )
    claims = [
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=(
                f"Optimal solution for {output['name']}: objective value "
                f"{output['objective_value']:.2f} (modeled recommendation, not an observed fact)."
            ),
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"optimization_objective:{output['problem_id']}",
            value=output["objective_value"],
        )
    ]
    claims.extend(
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=(
                f"Recommended {variable['variable']} = {variable['value']:.2f} "
                "(modeled recommendation, not an observed fact)."
            ),
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"optimization_variable:{output['problem_id']}:{variable['variable']}",
            value=variable["value"],
        )
        for variable in output["variables"]
    )
    return AgentOutput(agent=task.agent, task_id=task.id, status="ok", claims=tuple(claims))


def predictive_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    """Prompt 40 (40_PREDICTIVE_ANALYTICS_CONTRACT.md): trains (and,
    when requested, scores new rows with) a governed predictive model
    over one `sql_data` task's own already-governed rows -- never a
    second query, never caller-side raw data. Gated, like `scenario`
    (which also reads a `data_task`'s rows), on an explicit caller-
    supplied request (`agent.multiagent.supervisor.PredictiveRequest`)
    -- the planner never creates one, a model proposing which columns to
    train on is not authority to do so.

    `caller_roles` is never included in the dict passed to `ctx.call` --
    the registry injects the real, already-authorized roles
    automatically (see `scenario_handler`'s own docstring for the full
    reasoning, identical here).
    """
    slot = ctx.workspace.slots[task.params["data_task"]]
    if slot.governed_rows is None or not slot.columns:
        return _skipped(task, "no governed rows to train a predictive model against")
    result = ctx.call(
        TOOL_PREDICTIVE,
        {
            "model_id": task.params["model_id"],
            "name": task.params["name"],
            "domain": task.params["domain"],
            "task_type": task.params["task_type"],
            "model_type": task.params["model_type"],
            "columns": slot.columns,
            "rows": slot.governed_rows,
            "feature_columns": task.params["feature_columns"],
            "target_column": task.params["target_column"],
            "predict_rows": task.params.get("predict_rows"),
            "test_fraction": task.params.get("test_fraction", 0.2),
            "random_seed": task.params.get("random_seed", 0),
        },
    )
    if not result.success:
        return _failed(task, "predictive tool failed")
    output = result.output
    if output["status"] != "trained":
        reasons = "; ".join(output["rejection_reasons"]) or "no model was trained"
        return AgentOutput(
            agent=task.agent,
            task_id=task.id,
            status="ok",
            open_items=(f"Predictive model not trained ({output['status']}): {reasons}.",),
        )

    if output["regression_metrics"]:
        metrics = output["regression_metrics"]
        metrics_text = (
            f"MAE={metrics['mae']:.3f}, RMSE={metrics['rmse']:.3f}, R2={metrics['r2']:.3f}"
        )
    else:
        metrics = output["classification_metrics"]
        metrics_text = f"accuracy={metrics['accuracy']:.3f}, F1={metrics['f1']:.3f}"

    grounded = (slot.row_evidence_id,) if slot.row_evidence_id else ()
    claims = [
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=(
                f"Trained {output['name']} ({metrics_text}, evaluated on a held-out test "
                "split) -- a modeled estimate from historical data, not an observed fact."
            ),
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"predictive_model:{output['model_id']}",
            value=output["metadata"]["model_fingerprint"],
            grounded_in=grounded,
        )
    ]
    claims.extend(
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=(
                f"Predicted {prediction['value']} for the requested row -- a modeled "
                "estimate, not an observed fact."
            ),
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"predictive_prediction:{output['model_id']}:{prediction['index']}",
            value=prediction["value"],
            grounded_in=grounded,
        )
        for prediction in output["predictions"]
    )
    return AgentOutput(agent=task.agent, task_id=task.id, status="ok", claims=tuple(claims))


def data_quality_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    """Prompt 41 (41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md): assesses
    one `sql_data` task's own already-governed rows for completeness/
    validity/uniqueness/consistency findings -- never a second query,
    never caller-side raw data. Gated, like `predictive`/`scenario`, on
    an explicit caller-supplied request
    (`agent.multiagent.supervisor.DataQualityRequest`) -- the planner
    never creates one, a model proposing which checks to run is not
    authority to do so.

    Unlike every other engine handler in this module, every claim this
    handler renders is `DataTruthLevel.DATABASE_FACT`, never
    `AI_INFERENCE` -- a data-quality finding is a deterministic
    measurement of real, already-fetched rows, not a prediction/
    simulation/inference about something not yet observed (see
    `data_quality/models.py`'s own module docstring).

    `caller_roles` is never included in the dict passed to `ctx.call` --
    the registry injects the real, already-authorized roles
    automatically (see `predictive_handler`'s own docstring, identical
    reasoning here).
    """
    slot = ctx.workspace.slots[task.params["data_task"]]
    if slot.governed_rows is None or not slot.columns:
        return _skipped(task, "no governed rows to run a data-quality scan against")
    result = ctx.call(
        TOOL_DATA_QUALITY,
        {
            "table": task.params["table_name"],
            "columns": slot.columns,
            "rows": slot.governed_rows,
            "null_rate_overrides": task.params.get("null_rate_overrides") or {},
            "date_rules": task.params.get("date_rules") or [],
            "categorical_rules": task.params.get("categorical_rules") or [],
            "unique_key_sets": task.params.get("unique_key_sets") or [],
            "consistency_rules": task.params.get("consistency_rules") or [],
        },
    )
    if not result.success:
        return _failed(task, "data-quality tool failed")
    output = result.output
    grounded = (slot.row_evidence_id,) if slot.row_evidence_id else ()

    if not output["findings"]:
        return AgentOutput(
            agent=task.agent,
            task_id=task.id,
            status="ok",
            open_items=(f"No data-quality findings for '{task.params['table_name']}'.",),
        )

    claims = tuple(
        Claim(
            agent=task.agent,
            task_id=task.id,
            text=finding["claim"]["value"],
            truth_level=DataTruthLevel.DATABASE_FACT,
            key=f"data_quality_finding:{task.params['table_name']}:{finding['dimension']}:{finding['column']}",
            value=finding["severity"],
            grounded_in=grounded,
        )
        for finding in output["findings"]
    )
    return AgentOutput(agent=task.agent, task_id=task.id, status="ok", claims=claims)


def recommendation_handler(ctx: AgentContext, task: AgentTask) -> AgentOutput:
    """Reuses the analyst's own recommend node, unchanged, over this turn's
    evidence and candidates. Nothing is recomputed here."""
    candidates = [
        {"subquestion_id": subject, "recommendation": rec}
        for subject, slot in ctx.workspace.slots.items()
        for rec in slot.recommendations
    ]
    if not candidates:
        return AgentOutput(agent=task.agent, task_id=task.id, status="ok")
    state = cast(
        AnalystState,
        {
            "evidence": ctx.workspace.evidence,
            "recommendation_candidates": candidates,
            "trace": [],
        },
    )
    update = recommend_node(state)
    claims = tuple(
        Claim(
            agent=task.agent,
            task_id=rec["subquestion_id"],
            text=rec["claim"],
            truth_level=DataTruthLevel.AI_INFERENCE,
            key=f"recommendation:{rec['id']}",
            grounded_in=tuple(rec["evidence_ids"]),
        )
        for rec in update["recommendations"]
    )
    return AgentOutput(agent=task.agent, task_id=task.id, status="ok", claims=claims)


HANDLERS: dict[AgentName, Handler] = {
    AgentName.PLANNER: planner_handler,
    AgentName.GOVERNANCE: governance_handler,
    AgentName.SEMANTIC: semantic_handler,
    AgentName.SQL_DATA: sql_data_handler,
    AgentName.ANALYTICS: analytics_handler,
    AgentName.FORECAST: forecast_handler,
    AgentName.ROOT_CAUSE: root_cause_handler,
    AgentName.SCENARIO: scenario_handler,
    AgentName.OPTIMIZATION: optimization_handler,
    AgentName.PREDICTIVE: predictive_handler,
    AgentName.DATA_QUALITY: data_quality_handler,
    AgentName.RECOMMENDATION: recommendation_handler,
}


def _intent_value(intent: Any) -> str | None:
    """The classified intent as a plain string, or None when there is none."""
    if intent is None:
        return None
    value = intent.get("intent") if isinstance(intent, dict) else intent
    return None if value is None else str(getattr(value, "value", value))


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _growth_points(analytics: dict[str, Any]) -> list[tuple[str, float]]:
    """The period/value series from the analytics result's growth finding.
    Empty when the result had no time series."""
    for finding in analytics.get("findings") or []:
        growth = finding.get("growth")
        if growth:
            return [(str(p["period"]), float(p["value"])) for p in growth.get("points") or []]
    return []
