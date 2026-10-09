"""Budgets that bound how often routing may escalate.

Two scopes, both built on machinery this codebase already has rather than
on new primitives:

1. **Per request** -- `agent.multiagent.policy.TurnBudget`, reused
   verbatim. It is already exactly "hard caps on one unit of work:
   invocations and cost units, with a refusal reason", which is this
   need precisely. Its field names (`max_agent_calls`) read as
   multi-agent-specific, which is a naming artifact of where it was
   first written, not a behavioral mismatch -- `new_request_budget`
   below is the one place that mapping happens, so no caller in this
   package has to know.
2. **Per tenant, per hour** -- `agent.rate_limit
   .SlidingWindowRateLimiter` behind a `BoundedLimiterCache`, the exact
   shape `get_share_invite_limiter`/`get_onboarding_connection_test_limiter`
   already use. It meters *escalations* (a decision to use a stronger
   model than configured), not questions: ordinary questions are already
   metered by the pre-existing `/ask` rate and concurrency limits, and
   double-counting them here would silently tighten a limit an operator
   already tuned.

**A spent budget never fails a question.** It removes the *option* to
escalate, so the request proceeds on the operator's configured default
model -- the behavior it would have had with this feature switched off.
That is deliberate and matches how every accuracy aid in this pipeline
degrades (`plan_query_node`, `retrieve_golden_examples_node`,
`compute_analytics_node`): a cost control is not a reason a user cannot
get an answer. The refusal is recorded (`model_routing.metrics`'s
`budget_denials`, and `RouteReason.BUDGET_EXHAUSTED` on the decision
itself), so a deployment hitting its ceiling constantly is visible
rather than silent.
"""

from __future__ import annotations

import logging

from agent.multiagent.policy import TurnBudget
from agent.rate_limit import BoundedLimiterCache, SlidingWindowRateLimiter
from config.settings import Settings

logger = logging.getLogger(__name__)

#: Keyed by the server-resolved tenant id (`security.tenancy`), never by
#: anything a client sends -- see `docs/MULTI_TENANCY.md` rule 1.
_escalation_limiters = BoundedLimiterCache()


def new_request_budget(settings: Settings) -> TurnBudget:
    """A fresh budget for one `/ask` run.

    Sized from `Settings.model_routing_max_llm_calls_per_request` and
    `model_routing_max_cost_units_per_request`. The call cap exists
    independently of the cost cap because a run that somehow makes far
    more LLM calls than this graph's own retry budget allows is a bug
    worth bounding, regardless of which tier each call used.
    """
    return TurnBudget(
        max_agent_calls=settings.model_routing_max_llm_calls_per_request,
        max_cost_units=settings.model_routing_max_cost_units_per_request,
    )


def get_escalation_limiter(tenant_id: str, max_per_hour: int) -> SlidingWindowRateLimiter:
    """The per-tenant hourly escalation limiter.

    Process-local, like every other limiter in `agent/rate_limit.py`: a
    multi-worker deployment gets one window per worker, the same
    already-disclosed limitation that module's own docstring states.
    """
    return _escalation_limiters.get_or_create(
        tenant_id,
        max_events=max_per_hour,
        window_seconds=3600.0,
        name=f"model_escalation[{tenant_id}]",
    )


def escalation_allowed(
    budget: TurnBudget | None, tenant_id: str | None, cost_units: int, settings: Settings
) -> bool:
    """Whether routing may escalate right now.

    Checks the request budget first (cheap, local, no shared state) and
    only then the tenant window, so a request that could not afford the
    escalation anyway never consumes a slot from the tenant's hourly
    allowance.

    Args:
        budget: This run's `TurnBudget`, or `None` for a caller with no
            per-request budget (the `POST /model-routing/preview` route,
            which decides nothing and spends nothing).
        tenant_id: The server-resolved tenant, or `None` when there is no
            identity to meter (an unauthenticated caller in
            `AUTH_MODE=none`, a script calling `run_agent` directly).
            `None` skips the tenant window rather than inventing one
            shared bucket every anonymous caller would land in -- the
            same bug class the scale-out pass already fixed for rate
            limiting, where local-auth users were pooled into a single
            "no owner" bucket.
        cost_units: What the candidate escalation would charge.
        settings: Application settings.

    Returns:
        True when the escalation may proceed. A `False` here is never an
        error -- see this module's own docstring.
    """
    if budget is not None and budget.can_charge(cost_units) is not None:
        return False
    if tenant_id is None:
        return True
    limiter = get_escalation_limiter(tenant_id, settings.model_routing_escalations_per_hour)
    return limiter.check().allowed


def charge(budget: TurnBudget | None, cost_units: int) -> None:
    """Charges one routed call against this run's budget, if it has one."""
    if budget is not None:
        budget.charge(cost_units)


def reset_escalation_limiters() -> None:
    """Clears every per-tenant window. Tests use this for isolation."""
    _escalation_limiters.clear()


__all__ = [
    "charge",
    "escalation_allowed",
    "get_escalation_limiter",
    "new_request_budget",
    "reset_escalation_limiters",
]
