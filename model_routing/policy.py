"""The routing decision itself -- one pure, deterministic function.

`decide_route` performs no I/O, holds no state, calls no LLM, and reads
nothing from a clock or a random source. Everything situational is an
argument: the candidate set, the request profile, measured latency, which
models currently have an open circuit, and whether the budget still
allows an escalation. The router (`model_routing.router`) is what gathers
those facts; this module only weighs them.

That split is what makes the mission's own "route by complexity, risk,
latency and capability" auditable: the same inputs always produce the
same `RouteDecision`, so a decision can be reproduced in a test and
previewed over HTTP (`POST /model-routing/preview`) without touching a
model, a database, or a breaker.

The four levers, and where each enters:

- **Complexity** -- `RequestProfile.complexity_score`, thresholded
  against `Settings.model_routing_upgrade_complexity_threshold` /
  `model_routing_downgrade_complexity_threshold`.
- **Risk** -- `models.TASK_RISK`. A `HIGH`-risk task is never routed
  below the operator's configured default, whatever the complexity
  signals say. This is the conservative direction on purpose: the cost
  of a weaker draft of the SQL itself, or of the checks over it, is not
  symmetric with the compute saved.
- **Latency** -- two independent brakes. `RequestProfile
  .latency_sensitive` blocks escalation for a request that is already
  short on wall clock, and a candidate whose *measured* p95
  (`model_routing.metrics`) exceeds `Settings
  .model_routing_max_p95_latency_ms` is dropped from the escalation set.
  The second is real evidence from this deployment, not a vendor claim.
- **Capability** -- `models.TASK_REQUIRED_CAPABILITIES` against
  `config/ollama_models.yaml`'s own tags, and only ever for a model
  routing would swap *to*; the configured default is always legal (see
  that table's own note for why).

**Governance gate.** With `Settings
.model_routing_require_evaluation_evidence` on (the default), a model is
only ever *chosen over* the configured default when it carries an
approved/deployed governed evaluation record (`model_routing.evidence`,
reading Prompt 44's `governance_center`). That applies to a downgrade as
well as an upgrade: swapping in a cheaper model is still a change to how
answers are produced, and "governed routing" means an operator reviewed
and approved the model that routing may move to, in either direction.
An operator who wants catalog-only routing can turn the requirement off
explicitly; nothing infers that preference.
"""

from __future__ import annotations

from collections.abc import Mapping

from config.settings import Settings
from model_routing.catalog import find_model
from model_routing.models import (
    TASK_REQUIRED_CAPABILITIES,
    TASK_RISK,
    TIER_COST_UNITS,
    TIER_RANK,
    ModelTier,
    RequestProfile,
    RiskLevel,
    RoutableModel,
    RouteDecision,
    RouteReason,
    RouteTask,
)


#: Used when `Settings.ollama_model` is somehow absent from the candidate
#: set. `Settings` itself unions the default into `ollama_allowed_models`
#: (`_fill_default_ollama_allowed_models`), so this is defensive only --
#: it keeps `decide_route` total rather than raising on a config shape
#: that should be impossible.
def _baseline_model(candidates: tuple[RoutableModel, ...], model_id: str) -> RoutableModel:
    found = find_model(candidates, model_id)
    if found is not None:
        return found
    return RoutableModel(
        model_id=model_id,
        tier=ModelTier.UNKNOWN,
        tier_known=False,
        capabilities=(),
        is_default=True,
    )


def _decision(
    *,
    task: RouteTask,
    chosen: RoutableModel,
    baseline: RoutableModel,
    reason: RouteReason,
    risk: RiskLevel,
    profile: RequestProfile,
    required: frozenset[str],
    considered: tuple[RoutableModel, ...],
    fallback_chain: tuple[str, ...] = (),
) -> RouteDecision:
    return RouteDecision(
        task=task,
        model_id=chosen.model_id,
        tier=chosen.tier,
        reason=reason,
        baseline_model_id=baseline.model_id,
        risk_level=risk,
        complexity_score=profile.complexity_score,
        required_capabilities=tuple(sorted(required)),
        considered_model_ids=tuple(candidate.model_id for candidate in considered),
        fallback_chain=fallback_chain,
        cost_units=TIER_COST_UNITS[chosen.tier],
        evaluation_backed=chosen.evaluation_backed,
    )


def _eligible(
    candidates: tuple[RoutableModel, ...],
    required: frozenset[str],
    unavailable_model_ids: frozenset[str],
) -> tuple[RoutableModel, ...]:
    """Candidates routing could legitimately swap to for this task.

    Three filters, all of them conservative: a known tier (an
    un-catalogued model is never *chosen*, see `ModelTier.UNKNOWN`), the
    task's required capabilities, and no currently-open circuit.
    """
    return tuple(
        candidate
        for candidate in candidates
        if candidate.tier_known
        and required.issubset(set(candidate.capabilities))
        and candidate.model_id not in unavailable_model_ids
    )


def _stronger_than(
    candidates: tuple[RoutableModel, ...], baseline: RoutableModel
) -> tuple[RoutableModel, ...]:
    """Candidates ranked strictly above `baseline`.

    When the baseline's own tier is unknown there is no rank to compare
    against, so only `LARGE` qualifies -- the one tier that is
    unambiguously an escalation from anything.
    """
    baseline_rank = TIER_RANK.get(baseline.tier)
    if baseline_rank is None:
        return tuple(c for c in candidates if c.tier is ModelTier.LARGE)
    return tuple(c for c in candidates if TIER_RANK[c.tier] > baseline_rank)


def _weaker_than(
    candidates: tuple[RoutableModel, ...], baseline: RoutableModel
) -> tuple[RoutableModel, ...]:
    """Candidates ranked strictly below `baseline` -- the mirror of
    `_stronger_than`, with `SMALL` as the only unambiguous de-escalation
    from an unknown baseline tier."""
    baseline_rank = TIER_RANK.get(baseline.tier)
    if baseline_rank is None:
        return tuple(c for c in candidates if c.tier is ModelTier.SMALL)
    return tuple(c for c in candidates if TIER_RANK[c.tier] < baseline_rank)


def _drop_slow(
    candidates: tuple[RoutableModel, ...],
    observed_p95_latency_ms: Mapping[str, float],
    ceiling_ms: float,
) -> tuple[RoutableModel, ...]:
    """Drops candidates this deployment has actually measured as slower
    than `ceiling_ms` at p95. A model with no measurement yet is kept --
    absence of evidence is not evidence of slowness, and the breaker/
    fallback path handles a model that turns out badly."""
    kept: list[RoutableModel] = []
    for candidate in candidates:
        observed = observed_p95_latency_ms.get(candidate.model_id)
        if observed is not None and observed > ceiling_ms:
            continue
        kept.append(candidate)
    return tuple(kept)


def _best_upgrade(
    candidates: tuple[RoutableModel, ...],
    strategy: str = "capability",
    observed_p95_latency_ms: Mapping[str, float] | None = None,
) -> RoutableModel:
    """Strongest sensible escalation, with a fully deterministic tie-break.

    The ordering comes from `model_routing.tasks.strategy_sort_key`, whose
    `"capability"` key is this function's own original ordering (recorded
    evaluation score highest first, then tier, then the `recommended`
    flag, then model id) -- so the default `MODEL_ROUTING_STRATEGY`
    leaves this byte-for-byte unchanged. `"cost"` and `"latency"` make
    the cheapest or fastest *eligible* candidate preferred instead.

    A strategy only ever reorders candidates `_eligible` has already
    approved. It cannot reach a model outside `ollama_allowed_models`,
    relax a task's risk level, or widen a budget: a strategy chooses
    between permitted options and is never itself a permission.
    """
    from model_routing.tasks import strategy_sort_key

    return sorted(candidates, key=strategy_sort_key(strategy, observed_p95_latency_ms))[0]


def _best_downgrade(candidates: tuple[RoutableModel, ...]) -> RoutableModel:
    """Cheapest sensible de-escalation: lowest tier first, then the
    highest recorded evaluation score within that tier, then
    `recommended`, then model id."""
    return sorted(
        candidates,
        key=lambda c: (
            TIER_RANK[c.tier],
            -(c.evaluation_quality_score if c.evaluation_quality_score is not None else -1.0),
            not c.recommended,
            c.model_id,
        ),
    )[0]


def _fallback_chain(
    chosen: RoutableModel,
    baseline: RoutableModel,
    eligible: tuple[RoutableModel, ...],
    max_fallbacks: int,
) -> tuple[str, ...]:
    """Models to try, in order, if `chosen` turns out to be unavailable.

    The configured default comes first whenever it is not already the
    choice -- it is the one model an operator has definitely decided to
    run -- followed by the remaining eligible candidates, strongest
    first, deterministically. Never contains `chosen`, never contains a
    duplicate.

    A chain is **not** filtered by the task's own risk level, so a
    HIGH-risk task whose stronger candidates are all unreachable can end
    up falling back to a smaller model. That is deliberate: a fallback
    only ever engages when the alternative is failing the request
    outright, the generated SQL is still gated by
    `agent.sql_validator`'s deterministic allowlist whichever model drafted
    it, and the ordering above means a weaker model is only ever reached
    last. The decision records the whole chain, so what actually happened
    is inspectable rather than inferred.
    """
    ordered: list[str] = []
    if baseline.model_id != chosen.model_id:
        ordered.append(baseline.model_id)
    for candidate in sorted(eligible, key=lambda c: (-TIER_RANK[c.tier], c.model_id)):
        if candidate.model_id in (chosen.model_id, *ordered):
            continue
        ordered.append(candidate.model_id)
    return tuple(ordered[:max_fallbacks])


def decide_route(
    task: RouteTask,
    profile: RequestProfile,
    candidates: tuple[RoutableModel, ...],
    settings: Settings,
    *,
    observed_p95_latency_ms: Mapping[str, float] | None = None,
    unavailable_model_ids: frozenset[str] = frozenset(),
    escalation_allowed: bool = True,
) -> RouteDecision:
    """Decides which model serves `task` for this request.

    Args:
        task: Which pipeline LLM call this is.
        profile: `model_routing.signals.assess_request`'s output.
        candidates: `model_routing.catalog.build_routable_models`'s output.
        settings: Application settings (the feature flag, the thresholds,
            and `ollama_model` as the baseline).
        observed_p95_latency_ms: Measured p95 per model id, normally from
            `model_routing.metrics.get_route_metrics().p95_latency_by_model()`.
            Empty/`None` means nothing has been measured yet, which never
            blocks a route.
        unavailable_model_ids: Models whose circuit is currently open
            (`model_routing.router`, reusing `agent.multiagent.policy
            .CircuitBreaker`). Never chosen, and never offered as a
            fallback.
        escalation_allowed: `False` when this request's compute-unit
            budget or the tenant's hourly escalation cap is already spent
            (`model_routing.budget`). Blocks an upgrade; never blocks
            answering the question.

    Returns:
        A frozen `RouteDecision`. There is always a decision -- when no
        swap is warranted or possible the decision names the configured
        default and says why, so "nothing happened" is still recorded
        rather than inferred from an absence.
    """
    baseline = _baseline_model(candidates, settings.ollama_model)
    risk = TASK_RISK[task]
    required = TASK_REQUIRED_CAPABILITIES[task]
    latency = observed_p95_latency_ms or {}

    if not settings.enable_model_routing:
        return _decision(
            task=task,
            chosen=baseline,
            baseline=baseline,
            reason=RouteReason.ROUTING_DISABLED,
            risk=risk,
            profile=profile,
            required=required,
            considered=(),
        )

    eligible = _eligible(candidates, required, unavailable_model_ids)
    if settings.model_routing_require_evaluation_evidence:
        evidence_backed = tuple(c for c in eligible if c.evaluation_backed)
    else:
        evidence_backed = eligible

    # The configured default's own circuit is open: this is the one case
    # where routing moves off the default without being asked to, because
    # the alternative is a call that is already known to be failing.
    # Evidence-backed candidates are preferred, but an un-reviewed
    # working model still beats a reviewed broken one.
    if baseline.model_id in unavailable_model_ids:
        rescue_pool = evidence_backed or eligible
        if rescue_pool:
            chosen = _best_upgrade(rescue_pool, settings.model_routing_strategy, latency)
            return _decision(
                task=task,
                chosen=chosen,
                baseline=baseline,
                reason=RouteReason.CIRCUIT_OPEN,
                risk=risk,
                profile=profile,
                required=required,
                considered=rescue_pool,
                fallback_chain=_fallback_chain(
                    chosen, chosen, rescue_pool, settings.model_routing_max_fallbacks
                ),
            )

    # Risk decides which *direction* a task may move at all, and the
    # complexity score decides whether it does.
    #
    # A LOW-risk task is never escalated, even for a genuinely hard
    # question: classifying a complex question's intent is still a 13-way
    # label, and narrating an already-computed result is still narration
    # (`agent.insight.is_insight_grounded` is what keeps that honest, not
    # model size). Spending the largest model on either would be the
    # opposite of cost optimization. A HIGH/MEDIUM-risk task is
    # correspondingly never de-escalated -- see `TASK_RISK`.
    wants_upgrade = risk is not RiskLevel.LOW and (
        profile.complexity_score >= settings.model_routing_upgrade_complexity_threshold
    )
    wants_downgrade = (
        risk is RiskLevel.LOW
        and profile.complexity_score <= settings.model_routing_downgrade_complexity_threshold
    )

    def unchanged(reason: RouteReason, considered: tuple[RoutableModel, ...]) -> RouteDecision:
        return _decision(
            task=task,
            chosen=baseline,
            baseline=baseline,
            reason=reason,
            risk=risk,
            profile=profile,
            required=required,
            considered=considered,
            fallback_chain=_fallback_chain(
                baseline, baseline, eligible, settings.model_routing_max_fallbacks
            ),
        )

    if wants_upgrade:
        if profile.latency_sensitive:
            return unchanged(RouteReason.UPGRADE_BLOCKED_BY_LATENCY, ())
        if not escalation_allowed:
            return unchanged(RouteReason.BUDGET_EXHAUSTED, ())
        stronger = _stronger_than(eligible, baseline)
        if not stronger:
            return unchanged(RouteReason.NO_ELIGIBLE_CANDIDATE, stronger)
        reviewed = _stronger_than(evidence_backed, baseline)
        if not reviewed:
            return unchanged(RouteReason.NO_EVALUATION_EVIDENCE, stronger)
        fast_enough = _drop_slow(reviewed, latency, settings.model_routing_max_p95_latency_ms)
        if not fast_enough:
            return unchanged(RouteReason.UPGRADE_BLOCKED_BY_LATENCY, reviewed)
        chosen = _best_upgrade(fast_enough, settings.model_routing_strategy, latency)
        return _decision(
            task=task,
            chosen=chosen,
            baseline=baseline,
            reason=RouteReason.UPGRADED_FOR_COMPLEXITY,
            risk=risk,
            profile=profile,
            required=required,
            considered=fast_enough,
            fallback_chain=_fallback_chain(
                chosen, baseline, eligible, settings.model_routing_max_fallbacks
            ),
        )

    if wants_downgrade:
        weaker = _weaker_than(evidence_backed, baseline)
        if not weaker:
            return unchanged(
                (
                    RouteReason.NO_EVALUATION_EVIDENCE
                    if _weaker_than(eligible, baseline)
                    else RouteReason.NO_ELIGIBLE_CANDIDATE
                ),
                _weaker_than(eligible, baseline),
            )
        chosen = _best_downgrade(weaker)
        return _decision(
            task=task,
            chosen=chosen,
            baseline=baseline,
            reason=RouteReason.DOWNGRADED_FOR_SIMPLICITY,
            risk=risk,
            profile=profile,
            required=required,
            considered=weaker,
            fallback_chain=_fallback_chain(
                chosen, baseline, eligible, settings.model_routing_max_fallbacks
            ),
        )

    return unchanged(RouteReason.DEFAULT_NO_CHANGE, ())


__all__ = ["decide_route"]
