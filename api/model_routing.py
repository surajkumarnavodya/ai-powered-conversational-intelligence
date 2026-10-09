"""`/model-routing` -- the governed model router's REST surface
(Prompt 47, `47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`).

Three read-only routes. **None of them calls an LLM**, and only the
policy/preview pair reads anything beyond process-local state (the
tenant's own governed model-evaluation evidence, through
`model_routing.evidence`'s cache). Nothing here changes a route, a
budget, a breaker, or any model configuration: routing is configured in
`.env` and governed through `/governance` (Prompt 44's own approval
lifecycle), so a write route here would be a second, ungoverned way to
change which model answers a question.

- `GET /model-routing/policy` -- the resolved configuration and candidate
  set for the calling tenant. `Permission.ASK`: it names which models may
  serve this caller's questions, which is the same information
  `GET /models` already exposes to anyone who may ask one.
- `POST /model-routing/preview` -- what routing *would* decide for a
  question, through the same pure `model_routing.policy.decide_route` the
  live pipeline uses. Consumes no budget and no breaker probe.
- `GET /model-routing/metrics` -- the live per-route quality/latency/
  failure/cost rollup. `Permission.ADMIN_CONFIG`, exactly like
  `GET /metrics/performance`: aggregate operational data, no question or
  answer content, but still an internal view.

Behind `Settings.enable_model_routing` (404 when off, the same gate shape
`api/optimization.py`/`api/scenarios.py` use). The flag defaults off, so
these routes 404 on an untouched deployment -- correctly: there is no
routing to report.

`POST /model-routing/preview` takes a question, which means it accepts
untrusted text. That text is never put in a prompt, never executed, and
never stored -- it is scanned by `agent.complexity`'s regexes and
discarded, and the response echoes back only the matched signal *names*,
never the question itself.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent.authz import Permission
from api.authz import require_permission
from api.model_routing_schemas import (
    RoutableModelOut,
    RouteDecisionOut,
    RouteSummaryOut,
    RoutingMetricsResponse,
    RoutingPolicyResponse,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
    TaskPolicyOut,
)
from api.rate_limit import enforce_api_action_rate_limit
from config.settings import get_settings
from model_routing.catalog import build_routable_models
from model_routing.evidence import load_model_evaluation_evidence
from model_routing.metrics import get_route_metrics
from model_routing.models import (
    ROUTING_POLICY_VERSION,
    TASK_REQUIRED_CAPABILITIES,
    TASK_RISK,
    TIER_COST_UNITS,
    RouteDecision,
    RouteTask,
)
from model_routing.policy import decide_route
from model_routing.signals import assess_request
from security.oidc import AuthIdentity
from security.tenancy import resolve_tenant_id_for_identity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-routing", tags=["model-routing"])

ROUTING_DISABLED_DETAIL = "Governed model routing is not enabled on this server."


def _require_enabled() -> None:
    if not get_settings().enable_model_routing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ROUTING_DISABLED_DETAIL)


def _decision_out(decision: RouteDecision) -> RouteDecisionOut:
    return RouteDecisionOut(
        task=decision.task.value,
        model_id=decision.model_id,
        tier=decision.tier.value,
        reason=decision.reason.value,
        baseline_model_id=decision.baseline_model_id,
        risk_level=decision.risk_level.value,
        complexity_score=decision.complexity_score,
        required_capabilities=decision.required_capabilities,
        considered_model_ids=decision.considered_model_ids,
        fallback_chain=decision.fallback_chain,
        cost_units=decision.cost_units,
        evaluation_backed=decision.evaluation_backed,
        policy_version=decision.policy_version,
        truth_level=decision.truth_level.value,
    )


@router.get("/policy", response_model=RoutingPolicyResponse)
def get_routing_policy(
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> RoutingPolicyResponse:
    """The resolved routing configuration and candidate set.

    Evaluation evidence is scoped to the caller's own tenant, so one
    tenant never learns which models another tenant approved.
    """
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "model_routing_policy", settings, identity=identity)

    tenant_id = resolve_tenant_id_for_identity(identity)
    evidence = load_model_evaluation_evidence(tenant_id, settings)
    candidates = build_routable_models(settings, evidence)

    return RoutingPolicyResponse(
        enabled=settings.enable_model_routing,
        policy_version=ROUTING_POLICY_VERSION,
        default_model=settings.ollama_model,
        require_evaluation_evidence=settings.model_routing_require_evaluation_evidence,
        upgrade_complexity_threshold=settings.model_routing_upgrade_complexity_threshold,
        downgrade_complexity_threshold=settings.model_routing_downgrade_complexity_threshold,
        max_p95_latency_ms=settings.model_routing_max_p95_latency_ms,
        max_fallbacks=settings.model_routing_max_fallbacks,
        tier_cost_units={tier.value: units for tier, units in TIER_COST_UNITS.items()},
        models=tuple(
            RoutableModelOut(
                model_id=candidate.model_id,
                tier=candidate.tier.value,
                tier_known=candidate.tier_known,
                capabilities=candidate.capabilities,
                context_length=candidate.context_length,
                recommended=candidate.recommended,
                is_default=candidate.is_default,
                evaluation_backed=candidate.evaluation_backed,
                evaluation_quality_score=candidate.evaluation_quality_score,
                governance_version_id=candidate.governance_version_id,
            )
            for candidate in candidates
        ),
        tasks=tuple(
            TaskPolicyOut(
                task=task.value,
                risk_level=TASK_RISK[task].value,
                required_capabilities=tuple(sorted(TASK_REQUIRED_CAPABILITIES[task])),
            )
            for task in RouteTask
        ),
    )


@router.post("/preview", response_model=RoutingPreviewResponse)
def preview_routing(
    payload: RoutingPreviewRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> RoutingPreviewResponse:
    """What routing would decide for `question`, per task.

    Runs the real `decide_route` -- no second implementation to drift --
    with measured latency read but no budget charged and no breaker probe
    consumed, so previewing can never affect a live request. The question
    text is scanned for complexity signals and discarded; nothing about
    it is stored, logged or sent to a model.
    """
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "model_routing_preview", settings, identity=identity)

    tenant_id = resolve_tenant_id_for_identity(identity)
    evidence = load_model_evaluation_evidence(tenant_id, settings)
    candidates = build_routable_models(settings, evidence)
    profile = assess_request(payload.question, latency_sensitive=payload.latency_sensitive)
    observed = get_route_metrics().p95_latency_by_model()

    decisions = tuple(
        _decision_out(
            decide_route(
                task,
                profile,
                candidates,
                settings,
                observed_p95_latency_ms=observed,
            )
        )
        for task in RouteTask
    )
    return RoutingPreviewResponse(
        enabled=settings.enable_model_routing,
        complexity_signals=profile.complexity_signals,
        complexity_score=profile.complexity_score,
        decisions=decisions,
    )


@router.get("/metrics", response_model=RoutingMetricsResponse)
def get_routing_metrics(
    identity: AuthIdentity = Depends(require_permission(Permission.ADMIN_CONFIG)),
) -> RoutingMetricsResponse:
    """The live per-route rollup.

    Admin-only for the same reason `GET /metrics/performance` is: nothing
    here is question or answer content, but it is still an internal
    operational view. Process-local and reset on restart.
    """
    _require_enabled()
    snapshot = get_route_metrics().snapshot()
    return RoutingMetricsResponse(
        started_at=snapshot["started_at"],
        routes=tuple(RouteSummaryOut(**route) for route in snapshot["routes"]),
        decisions=snapshot["decisions"],
        escalations=snapshot["escalations"],
        de_escalations=snapshot["de_escalations"],
        decisions_by_reason=snapshot["decisions_by_reason"],
        budget_denials=snapshot["budget_denials"],
        breaker_denials=snapshot["breaker_denials"],
        total_cost_units=snapshot["total_cost_units"],
    )
