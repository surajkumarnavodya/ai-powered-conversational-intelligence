"""The stateful façade `agent/nodes.py` calls.

`model_routing.policy` decides; this module gathers the facts that
decision needs, enforces the two controls a pure function cannot
(circuit breakers and budgets), performs the call with fallback, and
records what happened.

One `ModelRouter` per `/ask` run, held on `AgentState` -- the same
request-scoped discipline `selected_model`/`selected_database` already
follow, and for the same reason: a per-request budget and a caller's own
pinned model must never leak into a concurrent request. Nothing
process-global here is per-request state; the two process-wide objects it
touches (the circuit breakers in `agent.multiagent.policy` and the
rollup in `model_routing.metrics`) are deliberately shared, because a
model being down and a model being slow are facts about this deployment,
not about one question.

**Fallback semantics, and why the two failure kinds differ.** An
`OllamaUnavailableError` means the server or the model could not be
reached: the breaker records it, and the next model in the decision's
`fallback_chain` is tried. Any other `AgentError`
(`MalformedLLMOutputError`, `OffTopicQuestionError`, ...) means the model
answered and this application's own deterministic handling took over --
re-raised unchanged to the calling node, which already knows what to do
with it, and never retried on a different model. Retrying a parse failure
elsewhere would spend budget on something a different model is no more
likely to fix on the same prompt, and it would also quietly double the
graph's own retry semantics, which are the node's business, not routing's.

**Routing never widens what a caller may do.** A caller-pinned
`AskRequest.model` (already validated against
`Settings.ollama_allowed_models` by `agent.model_registry
.validate_model_selection` before the graph ever runs) always wins:
`ModelRouter` returns it for every task, with
`RouteReason.CALLER_PINNED_MODEL`. Routing only ever chooses among models
from that same allowlist, so it can never reach a model the operator did
not configure, and it never changes a permission, a tenant, or a
validation outcome.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from agent.exceptions import AgentError, OffTopicQuestionError, OllamaUnavailableError
from agent.multiagent.policy import CircuitBreaker, get_breaker
from config.settings import Settings
from model_routing.budget import charge, escalation_allowed, new_request_budget
from model_routing.catalog import build_routable_models, find_model
from model_routing.evidence import load_model_evaluation_evidence
from model_routing.metrics import get_route_metrics
from model_routing.models import (
    TIER_COST_UNITS,
    ModelTier,
    RequestProfile,
    RoutableModel,
    RouteDecision,
    RouteFailureKind,
    RouteOutcome,
    RouteReason,
    RouteTask,
)
from model_routing.policy import decide_route

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Breaker key prefix. Keyed per *model*, not per (task, model): a model
#: that cannot be reached cannot be reached for any task, and a per-task
#: key would need `len(RouteTask)` consecutive failures before any one of
#: them opened.
_BREAKER_PREFIX = "ollama_model:"


def _breaker_name(model_id: str) -> str:
    return f"{_BREAKER_PREFIX}{model_id}"


class ModelRouter:
    """Routes one request's LLM calls. Not thread-safe by design -- one
    instance belongs to one `/ask` run, which is single-threaded."""

    def __init__(
        self,
        settings: Settings,
        *,
        tenant_id: str | None = None,
        pinned_model: str | None = None,
        candidates: tuple[RoutableModel, ...] | None = None,
    ) -> None:
        """
        Args:
            settings: Application settings.
            tenant_id: The server-resolved tenant (`AgentState["tenant_id"]`),
                used to scope governed evaluation evidence and the hourly
                escalation budget. Never read from anything a client sends.
            pinned_model: The caller's own already-validated
                `AskRequest.model`, when they supplied one. Overrides
                routing entirely -- see this module's own docstring.
            candidates: Pre-built candidate set, for a caller that already
                has one (the preview route, tests). `None` builds it from
                `settings` plus governed evidence.
        """
        self._settings = settings
        self._tenant_id = tenant_id
        self._pinned_model = pinned_model
        self._budget = new_request_budget(settings)
        self._metrics = get_route_metrics()
        # Every decision this router made, in order. Read back once by
        # `agent.graph.run_agent` into `AgentState["route_decisions"]`
        # after the graph has run -- see that field's own comment for why
        # the record lives here rather than being threaded through each
        # node's return value.
        self._decisions: list[RouteDecision] = []
        if candidates is not None:
            self._candidates = candidates
        else:
            evidence = load_model_evaluation_evidence(tenant_id, settings)
            self._candidates = build_routable_models(settings, evidence)

    @property
    def candidates(self) -> tuple[RoutableModel, ...]:
        return self._candidates

    @property
    def decisions(self) -> tuple[RouteDecision, ...]:
        """Every decision made on this request, oldest first."""
        return tuple(self._decisions)

    def _unavailable(self, now: float) -> frozenset[str]:
        """Models whose circuit is currently refusing calls, read without
        consuming any half-open probe (`CircuitBreaker.would_refuse`)."""
        return frozenset(
            candidate.model_id
            for candidate in self._candidates
            if self._breaker(candidate.model_id).would_refuse(now)
        )

    def _breaker(self, model_id: str) -> CircuitBreaker:
        return get_breaker(
            _breaker_name(model_id),
            failure_threshold=self._settings.model_routing_breaker_failure_threshold,
            cooldown_seconds=self._settings.model_routing_breaker_cooldown_seconds,
        )

    def _pinned_decision(self, task: RouteTask, profile: RequestProfile) -> RouteDecision:
        """The decision for a caller who named their own model.

        Still a real, recorded `RouteDecision` rather than a bypass, so
        the metrics rollup and the response both account for every LLM
        call the same way. No fallback chain: a caller who asked for a
        specific model is not silently served a different one.
        """
        from model_routing.models import TASK_REQUIRED_CAPABILITIES, TASK_RISK

        pinned = self._pinned_model or self._settings.ollama_model
        candidate = find_model(self._candidates, pinned)
        tier = candidate.tier if candidate is not None else ModelTier.UNKNOWN
        return RouteDecision(
            task=task,
            model_id=pinned,
            tier=tier,
            reason=RouteReason.CALLER_PINNED_MODEL,
            baseline_model_id=self._settings.ollama_model,
            risk_level=TASK_RISK[task],
            complexity_score=profile.complexity_score,
            required_capabilities=tuple(sorted(TASK_REQUIRED_CAPABILITIES[task])),
            considered_model_ids=(),
            fallback_chain=(),
            cost_units=TIER_COST_UNITS[tier],
            evaluation_backed=bool(candidate and candidate.evaluation_backed),
        )

    def decide(self, task: RouteTask, profile: RequestProfile) -> RouteDecision:
        """Resolves which model serves `task`, and records the decision.

        Budget checking is deliberately ordered *after* an optimistic
        policy pass: the per-tenant hourly limiter consumes a slot when
        it is consulted, so consulting it for a task the policy was never
        going to escalate anyway would burn an operator's allowance on
        nothing. The policy is a pure function, so running it twice costs
        nothing measurable.
        """
        if self._pinned_model is not None:
            decision = self._pinned_decision(task, profile)
            self._record_decision(decision)
            return decision

        now = time.monotonic()
        decision = decide_route(
            task,
            profile,
            self._candidates,
            self._settings,
            observed_p95_latency_ms=self._metrics.p95_latency_by_model(),
            unavailable_model_ids=self._unavailable(now),
            escalation_allowed=True,
        )
        if decision.reason is RouteReason.UPGRADED_FOR_COMPLEXITY and not escalation_allowed(
            self._budget,
            self._tenant_id,
            # Probed with the most expensive escalation this policy could
            # ever pick, so the per-request budget is a genuine ceiling
            # rather than one a single call can overshoot.
            TIER_COST_UNITS[ModelTier.LARGE],
            self._settings,
        ):
            decision = decide_route(
                task,
                profile,
                self._candidates,
                self._settings,
                observed_p95_latency_ms=self._metrics.p95_latency_by_model(),
                unavailable_model_ids=self._unavailable(now),
                escalation_allowed=False,
            )
        self._record_decision(decision)
        if decision.changed_from_baseline:
            logger.info(
                "[model_routing] task=%s model=%s (baseline=%s) reason=%s "
                "tier=%s complexity=%d risk=%s evaluation_backed=%s",
                decision.task.value,
                decision.model_id,
                decision.baseline_model_id,
                decision.reason.value,
                decision.tier.value,
                decision.complexity_score,
                decision.risk_level.value,
                decision.evaluation_backed,
            )
        return decision

    def _record_decision(self, decision: RouteDecision) -> None:
        self._decisions.append(decision)
        self._metrics.record_decision(decision)

    def _record(
        self,
        decision: RouteDecision,
        model_id: str,
        *,
        succeeded: bool,
        started: float,
        failure_kind: RouteFailureKind | None,
        used_fallback: bool,
    ) -> None:
        candidate = find_model(self._candidates, model_id)
        tier = candidate.tier if candidate is not None else ModelTier.UNKNOWN
        cost_units = TIER_COST_UNITS[tier]
        charge(self._budget, cost_units)
        self._metrics.record_outcome(
            RouteOutcome(
                task=decision.task,
                model_id=model_id,
                tier=tier,
                succeeded=succeeded,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                failure_kind=failure_kind,
                cost_units=cost_units,
                used_fallback=used_fallback,
            )
        )

    def execute(
        self, task: RouteTask, profile: RequestProfile, call: Callable[[str], T]
    ) -> tuple[T, RouteDecision]:
        """Runs one routed LLM call, with fallback on unavailability.

        Args:
            task: Which pipeline LLM call this is.
            profile: This request's routing profile.
            call: Performs the real call for a given model id -- normally
                a one-line lambda closing over the existing
                `agent.llm_client.*_from_llm` arguments, so routing never
                reimplements or re-wraps a prompt.

        Returns:
            `(result, decision)`. The decision is returned so the calling
            node can record it on `AgentState` without re-deriving it.

        Raises:
            Whatever `call` raises, unchanged. An
            `OllamaUnavailableError` is only raised once every model in
            the chain has failed, and the error surfaced is the last
            one's -- so an operator sees a real, current failure rather
            than a stale first attempt.
        """
        decision = self.decide(task, profile)
        attempts = (decision.model_id, *decision.fallback_chain)
        last_unavailable: OllamaUnavailableError | None = None

        for index, model_id in enumerate(attempts):
            breaker = self._breaker(model_id)
            if not breaker.allow(time.monotonic()):
                logger.info(
                    "[model_routing] skipping model=%s for task=%s: circuit open",
                    model_id,
                    task.value,
                )
                continue
            started = time.perf_counter()
            try:
                result = call(model_id)
            except OllamaUnavailableError as exc:
                breaker.record_failure(time.monotonic())
                self._record(
                    decision,
                    model_id,
                    succeeded=False,
                    started=started,
                    failure_kind=RouteFailureKind.AVAILABILITY,
                    used_fallback=index > 0,
                )
                last_unavailable = exc
                continue
            except OffTopicQuestionError:
                """The model judged the question unanswerable as SQL,
                which is it doing its job -- `agent.input_guard`'s
                pre-filter and this backstop both exist to reach exactly
                this outcome. Recorded as a success, not a quality
                failure: counting it as one would inflate a route's
                `quality_failure_rate` for correct behavior."""
                breaker.record_success()
                self._record(
                    decision,
                    model_id,
                    succeeded=True,
                    started=started,
                    failure_kind=None,
                    used_fallback=index > 0,
                )
                raise
            except AgentError:
                # The model answered, and this application's own
                # deterministic parsing/validation rejected the result
                # (a malformed SQL block, an unreadable plan). A health
                # signal it is not -- see this module's own docstring.
                breaker.record_success()
                self._record(
                    decision,
                    model_id,
                    succeeded=False,
                    started=started,
                    failure_kind=RouteFailureKind.QUALITY,
                    used_fallback=index > 0,
                )
                raise
            else:
                breaker.record_success()
                self._record(
                    decision,
                    model_id,
                    succeeded=True,
                    started=started,
                    failure_kind=None,
                    used_fallback=index > 0,
                )
                return result, decision

        if last_unavailable is not None:
            raise last_unavailable
        # Every candidate was skipped by its own open circuit. Surfaced as
        # the same error class an unreachable model already produces, so
        # every caller's existing handling applies unchanged.
        raise OllamaUnavailableError(
            f"No model is currently available for {task.value}: every candidate "
            f"({', '.join(attempts)}) has an open circuit after repeated failures."
        )


__all__ = ["ModelRouter"]
