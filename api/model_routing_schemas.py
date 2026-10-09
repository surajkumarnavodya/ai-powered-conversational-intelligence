"""Request/response models for `api/model_routing.py` (Prompt 47).

Every response here is operational metadata -- task names, model ids,
tiers, reasons, aggregate latency and ordinal compute units. **No field
in this module can carry a question, a prompt, SQL, an answer, a
connection detail or a credential**, which is a property of the shapes
themselves rather than of a redaction step that could be forgotten: the
only free-form strings are a model id (drawn from
`Settings.ollama_allowed_models`) and enum values.

Frozen responses and `extra="forbid"` requests, the same convention
`api/optimization_schemas.py` and the rest of `api/` already follow.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RoutableModelOut(BaseModel):
    """One model routing may consider, as `GET /model-routing/policy`
    reports it."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    tier: str
    tier_known: bool
    capabilities: tuple[str, ...]
    context_length: int | None = None
    recommended: bool
    is_default: bool
    evaluation_backed: bool
    evaluation_quality_score: float | None = None
    governance_version_id: str | None = None


class TaskPolicyOut(BaseModel):
    """One task's static routing rules -- its risk class and the
    capabilities an alternative model must carry to serve it."""

    model_config = ConfigDict(frozen=True)

    task: str
    risk_level: str
    required_capabilities: tuple[str, ...]


class RoutingPolicyResponse(BaseModel):
    """The resolved routing configuration for the calling tenant."""

    model_config = ConfigDict(frozen=True)

    enabled: bool
    policy_version: str
    default_model: str
    require_evaluation_evidence: bool
    upgrade_complexity_threshold: int
    downgrade_complexity_threshold: int
    max_p95_latency_ms: float
    max_fallbacks: int
    tier_cost_units: dict[str, int] = Field(
        description=(
            "Ordinal compute units per tier -- never a currency. Every "
            "routable model runs locally through Ollama, so there is no "
            "per-token price to report and inventing one would present an "
            "estimate as an observed fact."
        )
    )
    models: tuple[RoutableModelOut, ...]
    tasks: tuple[TaskPolicyOut, ...]


class RouteDecisionOut(BaseModel):
    """One decision, as the preview route reports it."""

    model_config = ConfigDict(frozen=True)

    task: str
    model_id: str
    tier: str
    reason: str
    baseline_model_id: str
    risk_level: str
    complexity_score: int
    required_capabilities: tuple[str, ...]
    considered_model_ids: tuple[str, ...]
    fallback_chain: tuple[str, ...]
    cost_units: int
    evaluation_backed: bool
    policy_version: str
    truth_level: str = Field(
        description=(
            "Always 'ai_inference'. A routing decision is a judgment about "
            "which model was likely to do better, never an observed fact "
            "about output quality."
        )
    )


class RoutingPreviewRequest(BaseModel):
    """Ask what routing *would* decide for a question, without running it."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1)
    latency_sensitive: bool = Field(
        default=False,
        description=(
            "Simulates a request with little wall-clock headroom left, "
            "which blocks escalation to a slower model."
        ),
    )


class RoutingPreviewResponse(BaseModel):
    """What routing would do, per task, for the supplied question.

    Computed by the same pure `model_routing.policy.decide_route` the live
    pipeline uses -- never a parallel re-implementation, so a preview and
    a real run cannot disagree. No LLM call, no database query, and no
    budget or circuit-breaker state is consumed.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool
    complexity_signals: tuple[str, ...]
    complexity_score: int
    decisions: tuple[RouteDecisionOut, ...]


class RouteSummaryOut(BaseModel):
    """One `(task, model)` route's measured behavior."""

    model_config = ConfigDict(frozen=True)

    task: str
    model_id: str
    tier: str
    calls: int
    successes: int
    availability_failures: int
    quality_failures: int
    quality_failure_rate: float = Field(
        description=(
            "Share of judged calls this application's own deterministic "
            "parsers/validators rejected. A real observed signal about this "
            "route -- explicitly NOT an accuracy score: a parseable answer "
            "can still be wrong. Benchmarked accuracy lives in eval/ and in "
            "a governed model version's own recorded evaluation_results."
        )
    )
    fallback_uses: int
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    max_latency_ms: float
    cost_units: int


class RoutingMetricsResponse(BaseModel):
    """The live per-route rollup (`model_routing.metrics`).

    Single-process and in-memory, resetting on restart -- the same
    disclosed limitation `GET /metrics/performance` already carries, for
    the same reason (no shared store is part of this architecture).
    """

    model_config = ConfigDict(frozen=True)

    started_at: str
    routes: tuple[RouteSummaryOut, ...]
    decisions: int
    escalations: int
    de_escalations: int
    decisions_by_reason: dict[str, int]
    budget_denials: int
    breaker_denials: int
    total_cost_units: int


__all__ = [
    "RoutableModelOut",
    "RouteDecisionOut",
    "RouteSummaryOut",
    "RoutingMetricsResponse",
    "RoutingPolicyResponse",
    "RoutingPreviewRequest",
    "RoutingPreviewResponse",
    "TaskPolicyOut",
]
