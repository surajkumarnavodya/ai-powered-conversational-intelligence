"""Typed contracts for governed model routing (Prompt 47,
`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`).

This module is pure vocabulary: enums, frozen value objects, and the
static per-task tables the routing policy reads. It imports nothing from
`agent/nodes.py`, `identity/`, or SQLAlchemy, holds no mutable state, and
makes no I/O or LLM call -- the same posture `governance_center/models.py`
and `optimization/models.py` already establish, and what makes
`model_routing.policy` testable as a pure function.

**A route decision is never a security decision.** Routing picks *which
model drafts a candidate answer*; it never decides whether that answer is
allowed to run. Every deterministic control stays exactly where it was:
`agent.sql_validator.validate_sql`'s AST allowlist, the restricted-column
gate in `agent.nodes.validate_sql_node`, RBAC (`agent/authz.py`), and
tenant isolation (`security/tenancy.py`). No module in this package
imports any of them, which is what makes "never use an LLM for
deterministic security validation" structural here rather than a
convention -- see
`tests/security/test_model_routing_never_weakens_deterministic_controls.py`.

`RiskLevel` below is therefore **not** an authorization concept. It
answers a narrower question: how consequential is a *weaker draft* for
this one task, and may routing trade accuracy for speed on it at all.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel

#: Bumped when the routing rules themselves change in a way that could
#: alter a decision for an unchanged request. Recorded on every
#: `RouteDecision` so a logged/returned decision is self-describing about
#: which rule set produced it -- the same reasoning
#: `analytics.engine.ANALYTICS_ENGINE_VERSION` already uses.
ROUTING_POLICY_VERSION = "1.0.0"


class RouteTask(str, Enum):
    """The LLM calls this application's SQL pipeline actually makes.

    Exactly the seven `*_from_llm` call sites in `agent/nodes.py`, and
    deliberately named identically to the corresponding keys in
    `governance_center.inventory._PROMPT_SOURCES` (Prompt 45) so a route
    and the governed prompt artifact it uses line up by name rather than
    by a second mapping table that could drift.

    Tasks outside the SQL pipeline (`rag_answer`, `attachment_qa`,
    `vision_describe`, `media_caption`, `media_search_answer`,
    `analyst_planner`, ...) are deliberately **not** listed: nothing in
    those paths threads a per-request model today (they read
    `Settings.ollama_model` directly), so listing them would imply a
    routing capability that does not exist. See this prompt's contract
    document for that disclosed scope boundary.
    """

    SQL_GENERATION = "sql_generation"
    QUERY_PLAN = "query_plan"
    SQL_REVIEW = "sql_review"
    METRIC_CONFORMANCE_REVIEW = "metric_conformance_review"
    ANALYTICAL_INTENT = "analytical_intent"
    ANALYTICAL_PLAN = "analytical_plan"
    SQL_INSIGHT = "sql_insight"


class ModelTier(str, Enum):
    """Relative capability/resource class of a model.

    Derived from `config/ollama_models.yaml`'s own hand-authored
    `resource_level` (low/medium/high) -- reused rather than re-derived
    from a parameter-size string, so there is one place an operator
    classifies a model. `UNKNOWN` is a real, distinct value, never
    silently coerced to a guess: a model with no catalog entry is not
    *known* to be small or strong, so it is never chosen as either an
    upgrade or a downgrade target (see `model_routing.policy`).
    """

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


#: `config/ollama_models.yaml`'s `resource_level` -> `ModelTier`. Any
#: other value (including the catalog's own `"unknown"` default for an
#: entry that omits the field) maps to `ModelTier.UNKNOWN`.
TIER_BY_RESOURCE_LEVEL: dict[str, ModelTier] = {
    "low": ModelTier.SMALL,
    "medium": ModelTier.MEDIUM,
    "high": ModelTier.LARGE,
}

#: Ordering used for "is this candidate stronger/weaker than that one".
#: `UNKNOWN` is absent on purpose -- it is not a point on this scale, and
#: `policy` filters it out before any comparison happens.
TIER_RANK: dict[ModelTier, int] = {
    ModelTier.SMALL: 0,
    ModelTier.MEDIUM: 1,
    ModelTier.LARGE: 2,
}

#: Relative compute units charged against a routing budget, per tier.
#:
#: **Deliberately not money.** Every model this application routes to runs
#: locally through Ollama, so there is no per-token price to report, and
#: inventing a dollar figure would present an estimate as an observed
#: fact. These are ordinal units standing in for "how much more of this
#: machine a stronger model consumes," used only to bound how often one
#: request may escalate -- never rendered as a currency anywhere.
TIER_COST_UNITS: dict[ModelTier, int] = {
    ModelTier.SMALL: 1,
    ModelTier.MEDIUM: 2,
    ModelTier.LARGE: 5,
    ModelTier.UNKNOWN: 2,
}


class RiskLevel(str, Enum):
    """How consequential a weaker draft is for one task -- see this
    module's own docstring for why this is not an authorization concept.

    Attributes:
        LOW: Advisory output that already fails open and is independently
            grounded (`analytical_intent`, whose absence reduces to
            pre-Prompt-11 behavior; `sql_insight`, gated by
            `agent.insight.is_insight_grounded`). Eligible for a smaller
            model.
        MEDIUM: Output that steers a later step but is itself re-checked
            (`query_plan`/`analytical_plan` -- the latter deterministically
            re-verified by `agent.plan_validator.validate_plan`, both then
            checked against the generated SQL by `sql_review`). Never
            downgraded; eligible for an upgrade.
        HIGH: Output whose correctness is the answer (`sql_generation`),
            or a governance check whose weakening would weaken semantic
            governance (`sql_review`, `metric_conformance_review` --
            Prompt 45 treats governed-metric precedence as a safety
            invariant). Never routed below the operator's configured
            default model, whatever the complexity signals say.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


#: Per-task risk. Static and reviewed, never inferred at runtime.
TASK_RISK: dict[RouteTask, RiskLevel] = {
    RouteTask.SQL_GENERATION: RiskLevel.HIGH,
    RouteTask.SQL_REVIEW: RiskLevel.HIGH,
    RouteTask.METRIC_CONFORMANCE_REVIEW: RiskLevel.HIGH,
    RouteTask.QUERY_PLAN: RiskLevel.MEDIUM,
    RouteTask.ANALYTICAL_PLAN: RiskLevel.MEDIUM,
    RouteTask.ANALYTICAL_INTENT: RiskLevel.LOW,
    RouteTask.SQL_INSIGHT: RiskLevel.LOW,
}

#: Capabilities (as tagged in `config/ollama_models.yaml`) an
#: *alternative* candidate must carry to serve a task.
#:
#: These never disqualify `Settings.ollama_model` itself -- that file's
#: own docstring is explicit that its metadata is "purely cosmetic/
#: informational ... never a gate," so an operator's configured default
#: stays a legal route for every task even with no catalog entry. The
#: filter applies only when routing considers swapping *away* from that
#: default, where the catalog is the only evidence available.
TASK_REQUIRED_CAPABILITIES: dict[RouteTask, frozenset[str]] = {
    RouteTask.SQL_GENERATION: frozenset({"sql"}),
    RouteTask.SQL_REVIEW: frozenset({"sql", "reasoning"}),
    RouteTask.METRIC_CONFORMANCE_REVIEW: frozenset({"sql", "reasoning"}),
    RouteTask.QUERY_PLAN: frozenset({"reasoning"}),
    RouteTask.ANALYTICAL_PLAN: frozenset({"reasoning"}),
    RouteTask.ANALYTICAL_INTENT: frozenset({"text"}),
    RouteTask.SQL_INSIGHT: frozenset({"text"}),
}


class RouteReason(str, Enum):
    """Why a decision landed on the model it did -- a closed set, so a
    reason can be asserted on in a test and aggregated in a metric
    rollup, rather than a free-form sentence."""

    ROUTING_DISABLED = "routing_disabled"
    CALLER_PINNED_MODEL = "caller_pinned_model"
    DEFAULT_NO_CHANGE = "default_no_change"
    UPGRADED_FOR_COMPLEXITY = "upgraded_for_complexity"
    DOWNGRADED_FOR_SIMPLICITY = "downgraded_for_simplicity"
    NO_ELIGIBLE_CANDIDATE = "no_eligible_candidate"
    NO_EVALUATION_EVIDENCE = "no_evaluation_evidence"
    UPGRADE_BLOCKED_BY_LATENCY = "upgrade_blocked_by_latency"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CIRCUIT_OPEN = "circuit_open"


class RoutableModel(BaseModel):
    """One model routing may consider, with the facts it is judged on.

    Built by `model_routing.catalog.build_routable_models` from
    `Settings.ollama_allowed_models` (the allowlist that is, and remains,
    the only gate on what this application will ever call) enriched with
    `config/ollama_models.yaml` metadata. Nothing here is discovered from
    a live Ollama server: installed-status discovery stays exactly where
    it was, on `GET /models`, never on the request path.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1)
    tier: ModelTier
    tier_known: bool = Field(
        description=(
            "False when this model has no `config/ollama_models.yaml` entry "
            "(or its entry omits `resource_level`). Such a model is a "
            "perfectly usable default -- it is simply never *chosen* as an "
            "upgrade or downgrade target, since there is no evidence it is "
            "stronger or cheaper than what is already configured."
        )
    )
    capabilities: tuple[str, ...] = ()
    context_length: int | None = None
    recommended: bool = False
    is_default: bool = False
    evaluation_backed: bool = Field(
        default=False,
        description=(
            "True when an approved/deployed `model`-typed governed asset "
            "version exists for this model id with recorded evaluation "
            "results (`governance_center`, Prompt 44) -- see "
            "`model_routing.evidence`. This is the literal mechanism "
            "behind the mission's 'when evaluation supports it': with "
            "`Settings.model_routing_require_evaluation_evidence` on, a "
            "model with no such record is never escalated to."
        ),
    )
    evaluation_quality_score: float | None = Field(
        default=None,
        description=(
            "An operator-recorded score read verbatim out of that governed "
            "version's own `evaluation_results` -- never computed, "
            "measured, or inferred by this module. None when no numeric "
            "score was recorded."
        ),
    )
    governance_version_id: str | None = None


class RequestProfile(BaseModel):
    """What routing knows about one request before any LLM call.

    Built by `model_routing.signals.assess_request` from signals this
    pipeline already computes -- `agent.complexity.detect_complexity_signals`
    and `agent.intent`'s classification -- never from a second classifier
    and never from an LLM call of its own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    complexity_signals: tuple[str, ...] = ()
    complexity_score: int = Field(default=0, ge=0)
    analytical_intent: str | None = None
    implies_planning: bool = False
    has_ambiguity: bool = False
    latency_sensitive: bool = Field(
        default=False,
        description=(
            "True when this request has little wall-clock headroom left "
            "(e.g. it is already deep into `Settings.request_timeout_seconds`). "
            "Blocks escalation to a slower model -- the 'route by latency' "
            "lever at request scope, alongside the measured per-model p95 "
            "check in `model_routing.policy`."
        ),
    )


class RouteDecision(BaseModel):
    """One task's resolved model choice, and why.

    Always `DataTruthLevel.AI_INFERENCE`: a routing decision is this
    platform's own judgment about which model is likely to do better on a
    request, never an observed fact about output quality. The field is a
    fixed `Literal`, so pydantic itself refuses a decision claiming to be
    a `DATABASE_FACT` -- the same type-level guarantee
    `optimization.models` and `analytics.scenario` already make for their
    own results.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: RouteTask
    model_id: str = Field(min_length=1)
    tier: ModelTier
    reason: RouteReason
    baseline_model_id: str = Field(
        description="`Settings.ollama_model`, the choice this decision departed from (or kept)."
    )
    risk_level: RiskLevel
    complexity_score: int = Field(ge=0)
    required_capabilities: tuple[str, ...] = ()
    considered_model_ids: tuple[str, ...] = ()
    fallback_chain: tuple[str, ...] = Field(
        default=(),
        description=(
            "Models to try, in order, if the chosen one is unavailable at "
            "call time. Never includes the chosen model itself, and always "
            "ends at the configured default when that default is not "
            "already the choice."
        ),
    )
    cost_units: int = Field(ge=0)
    evaluation_backed: bool = False
    policy_version: str = ROUTING_POLICY_VERSION
    truth_level: Literal[DataTruthLevel.AI_INFERENCE] = DataTruthLevel.AI_INFERENCE

    @property
    def changed_from_baseline(self) -> bool:
        """Whether this decision actually moved away from the configured
        default -- what `model_routing.metrics` counts as an escalation/
        de-escalation rather than an ordinary run."""
        return self.model_id != self.baseline_model_id


class RouteFailureKind(str, Enum):
    """Why a routed call did not succeed -- kept separate because the two
    kinds mean different things and get different treatment.

    Attributes:
        AVAILABILITY: The model/server could not be reached, or the model
            is not pulled (`agent.exceptions.OllamaUnavailableError`).
            This is a health signal: it trips the circuit breaker and
            triggers the fallback chain.
        QUALITY: The model answered, but this application's own
            deterministic parser/validator rejected the output
            (`MalformedLLMOutputError`, an unparseable plan, a review
            verdict that could not be read). Recorded against the route's
            quality counters, and deliberately **not** fed to the breaker
            -- a model that is up and merely wrong is not a fault to open
            a circuit on, and doing so would let one bad question disable
            a healthy model for every other caller.
    """

    AVAILABILITY = "availability"
    QUALITY = "quality"


class RouteOutcome(BaseModel):
    """What actually happened on one routed call -- fed to
    `model_routing.metrics` and the circuit breaker.

    `latency_ms` is a real measurement of this call, unlike the decision
    that preceded it; it is recorded here rather than claimed on
    `RouteDecision`, so a decision and its result are never conflated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: RouteTask
    model_id: str = Field(min_length=1)
    tier: ModelTier
    succeeded: bool
    latency_ms: float = Field(ge=0.0)
    failure_kind: RouteFailureKind | None = None
    cost_units: int = Field(default=0, ge=0)
    used_fallback: bool = False


__all__ = [
    "ROUTING_POLICY_VERSION",
    "TASK_REQUIRED_CAPABILITIES",
    "TASK_RISK",
    "TIER_BY_RESOURCE_LEVEL",
    "TIER_COST_UNITS",
    "TIER_RANK",
    "ModelTier",
    "RequestProfile",
    "RiskLevel",
    "RoutableModel",
    "RouteDecision",
    "RouteFailureKind",
    "RouteOutcome",
    "RouteReason",
    "RouteTask",
]
