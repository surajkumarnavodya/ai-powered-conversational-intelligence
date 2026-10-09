"""The business-health rollup.

Every indicator is a count, or a ratio of counts, over governed records
this tenant actually holds. All are `DATABASE_FACT` -- they are
observations about this platform's own state, arrived at by counting,
with no model involved.

**What this module deliberately does not do: say whether the business is
healthy.** It reports "7 open high-risk signals" and that lower is
better; it does not report "health: amber". A threshold turning 7 into
amber is a business judgment nobody has given this platform, and
inventing one would be exactly the "present inference as observed fact"
the master contract forbids -- made worse by how authoritative a single
colour-coded verdict looks on an executive screen.

`direction` carries the only judgment that *is* safe to make: which way
is better. That is a property of the metric itself (nobody wants more
overdue actions), not a claim about this tenant's situation.
"""

from __future__ import annotations

from collections.abc import Sequence

from decision_center.models import Headline, HeadlineKind, HealthIndicator

#: Statuses a workflow is in while it still needs someone to act.
#: Mirrors `workflow.models.is_active_status`'s own set; kept as a
#: literal here so this package does not import `workflow/` for one
#: predicate, and asserted against the real enum in the tests.
_ACTIVE_WORKFLOW_STATUSES = frozenset(
    {"proposed", "approval_required", "approved", "assigned", "in_progress"}
)

#: Recommendation statuses that represent a reviewer having agreed.
_ACCEPTED_RECOMMENDATION_STATUSES = frozenset({"accepted", "partially_useful", "resolved"})

#: Recommendation statuses that represent a reviewer having judged at all.
_JUDGED_RECOMMENDATION_STATUSES = _ACCEPTED_RECOMMENDATION_STATUSES | {
    "rejected",
    "incorrect",
    "reviewed",
}


def build_health(
    signals: Sequence[Headline],
    risks: Sequence[Headline],
    recommendations: Sequence[Headline],
    actions: Sequence[Headline],
    pending_decisions: Sequence[Headline],
) -> tuple[HealthIndicator, ...]:
    """Counts the governed records into the headline health numbers.

    Args:
        signals: KPI movement and anomaly headlines.
        risks: Risk headlines (schema drift, semantic findings).
        recommendations: Recommendation headlines.
        actions: Workflow headlines (action and outcome alike).
        pending_decisions: Governance and onboarding headlines.

    Returns:
        One indicator per measure, in a fixed order so the rollup reads
        the same way every time.
    """
    open_signals = [item for item in signals if item.status == "open"]
    high_risk = [item for item in [*signals, *risks] if item.severity_tier == "high"]
    open_risks = [item for item in risks if item.status == "open"]

    active_actions = [item for item in actions if item.status in _ACTIVE_WORKFLOW_STATUSES]
    completed_actions = [item for item in actions if item.status == "completed"]
    with_outcome = [item for item in actions if item.kind is HeadlineKind.OUTCOME]

    judged = [item for item in recommendations if item.status in _JUDGED_RECOMMENDATION_STATUSES]
    accepted = [
        item for item in recommendations if item.status in _ACCEPTED_RECOMMENDATION_STATUSES
    ]

    indicators: list[HealthIndicator] = [
        HealthIndicator(
            key="open_signals",
            label="Open KPI signals",
            value=float(len(open_signals)),
            direction="lower_is_better",
            detail="Movements and anomalies nobody has decided on yet.",
        ),
        HealthIndicator(
            key="high_risk_items",
            label="High-risk items",
            value=float(len(high_risk)),
            direction="lower_is_better",
            detail="Signals and risks the producing capability scored in its top tier.",
        ),
        HealthIndicator(
            key="open_risks",
            label="Open risks",
            value=float(len(open_risks)),
            direction="lower_is_better",
            detail="Schema drift and semantic findings awaiting review.",
        ),
        HealthIndicator(
            key="pending_decisions",
            label="Pending decisions",
            value=float(len(pending_decisions)),
            direction="lower_is_better",
            detail="Governed assets and onboarding jobs waiting on a human.",
        ),
        HealthIndicator(
            key="active_actions",
            label="Actions in flight",
            value=float(len(active_actions)),
            direction="neutral",
            detail="Workflows not yet completed, rejected or expired.",
        ),
        HealthIndicator(
            key="completed_actions",
            label="Actions completed",
            value=float(len(completed_actions)),
            direction="higher_is_better",
            detail="Workflows recorded as completed.",
        ),
    ]

    if judged:
        indicators.append(
            HealthIndicator(
                key="recommendation_acceptance_rate",
                label="Recommendation acceptance rate",
                value=round(len(accepted) / len(judged) * 100.0, 1),
                unit="percent",
                direction="higher_is_better",
                detail=(
                    f"{len(accepted)} of {len(judged)} judged recommendations were accepted, "
                    f"partially useful or resolved. Recommendations nobody has judged are "
                    f"excluded -- counting them as rejections would understate the rate."
                ),
            )
        )
    else:
        indicators.append(
            HealthIndicator(
                key="recommendation_acceptance_rate",
                label="Recommendation acceptance rate",
                value=0.0,
                unit="percent",
                direction="higher_is_better",
                detail="No recommendation has been judged yet, so there is no rate to report.",
            )
        )

    if completed_actions:
        indicators.append(
            HealthIndicator(
                key="outcome_capture_rate",
                label="Outcome capture rate",
                value=round(len(with_outcome) / len(completed_actions) * 100.0, 1),
                unit="percent",
                direction="higher_is_better",
                detail=(
                    f"{len(with_outcome)} of {len(completed_actions)} completed actions "
                    f"recorded an outcome. A completed action with no recorded outcome is "
                    f"work whose effect was never measured."
                ),
            )
        )
    else:
        indicators.append(
            HealthIndicator(
                key="outcome_capture_rate",
                label="Outcome capture rate",
                value=0.0,
                unit="percent",
                direction="higher_is_better",
                detail="No action has been completed yet, so there is no rate to report.",
            )
        )

    return tuple(indicators)


__all__ = ["build_health"]
