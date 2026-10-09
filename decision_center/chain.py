"""signal -> evidence -> explanation -> recommendation -> action -> outcome.

The acceptance criterion, assembled from what the data model actually
supports -- and honest about where it does not.

**What is solid.** `workflows.recommendation_id` is a real foreign key
(Prompt 49), and a workflow carries `outcome_summary` /
`outcome_measured_impact` (also Prompt 49). So
recommendation -> action -> outcome is a `DECLARED` chain end to end,
and an outcome is genuinely attributable to the recommendation that
prompted it.

**Where a signal's own chain is solid.** Evidence and explanation do not
need a cross-record link at all: Prompt 35's finding row carries its own
`evidence`, and Prompt 50 additionally records the baseline, period,
contributors, confidence and limitations in the finding's payload. So
signal -> evidence -> explanation is complete inside a single record,
which is why those links carry no `link_type`.

**The one real break.** Nothing in the schema links a *signal* to a
recommendation. The recommendation engine (Prompt 17) builds from a
query result at `/ask` time; it has never consumed a finding. Two
records about the same subject are therefore offered as
`LinkType.CORRELATED` -- which says "these concern the same database or
metric", and specifically does **not** say one followed from the other.
A chain that gets no further says so in `absent_reason`, rather than
rendering as though an executive had simply not acted yet.

Correlation is deliberately narrow: same tenant (guaranteed by the
caller's own tenant-scoped reads), same `database_id`, and -- when the
signal names a governed metric -- a recommendation whose own text
mentions that metric. A looser rule would manufacture chains, which is
worse than reporting an honest break.
"""

from __future__ import annotations

from collections.abc import Sequence

from decision_center.models import (
    ChainLink,
    ChainStage,
    DecisionChain,
    Headline,
    HeadlineKind,
    LinkType,
)

#: How many signals get a chain built for them. Chains are the expensive
#: part of a briefing to read, not to compute -- an executive works the
#: top of the list, and fifty chains is a wall rather than a summary.
DEFAULT_MAX_CHAINS = 10


def _correlates(signal: Headline, recommendation: Headline) -> bool:
    """Whether a recommendation plausibly concerns the same subject.

    Narrow on purpose. Same database is necessary; when the signal names
    a governed metric, that metric must also appear in the
    recommendation's own text. Without the metric check, every signal on
    a database would correlate with every recommendation on it, which
    produces confident-looking nonsense.
    """
    signal_db = signal.provenance.database_id
    recommendation_db = recommendation.provenance.database_id
    if not signal_db or not recommendation_db or signal_db != recommendation_db:
        return False

    metric = signal.provenance.metric_concept_key
    if not metric:
        # No metric to match on: a bare same-database match is too weak
        # to offer even as a correlation.
        return False

    haystack = f"{recommendation.title} {recommendation.detail}".lower()
    return metric.lower() in haystack


def build_chain(
    signal: Headline,
    recommendations: Sequence[Headline],
    workflows_by_recommendation: dict[str, Headline],
) -> DecisionChain:
    """Assembles one signal's chain as far as the data supports.

    Args:
        signal: The signal headline (a finding).
        recommendations: Candidate recommendation headlines for this
            tenant.
        workflows_by_recommendation: `recommendation record id ->`
            workflow headline, built by the caller from the declared
            `workflows.recommendation_id` foreign key.

    Returns:
        A `DecisionChain` whose every stage is either present, or absent
        with a stated reason.
    """
    links: list[ChainLink] = [
        ChainLink(
            stage=ChainStage.SIGNAL,
            present=True,
            headline_id=signal.headline_id,
            summary=signal.title,
        )
    ]

    # --- evidence and explanation: inside the signal's own record ---
    if signal.evidence:
        links.append(
            ChainLink(
                stage=ChainStage.EVIDENCE,
                present=True,
                headline_id=signal.headline_id,
                summary=f"{len(signal.evidence)} recorded item(s)",
            )
        )
    else:
        links.append(
            ChainLink(
                stage=ChainStage.EVIDENCE,
                present=False,
                absent_reason="the producing capability recorded no evidence for this signal",
            )
        )

    if signal.detail.strip():
        links.append(
            ChainLink(
                stage=ChainStage.EXPLANATION,
                present=True,
                headline_id=signal.headline_id,
                summary=signal.detail[:200],
            )
        )
    else:
        links.append(
            ChainLink(
                stage=ChainStage.EXPLANATION,
                present=False,
                absent_reason="the producing capability recorded no explanation for this signal",
            )
        )

    # --- recommendation: correlated only, never declared ---
    matched = next(
        (
            candidate
            for candidate in recommendations
            if candidate.kind is HeadlineKind.RECOMMENDATION and _correlates(signal, candidate)
        ),
        None,
    )
    if matched is None:
        links.append(
            ChainLink(
                stage=ChainStage.RECOMMENDATION,
                present=False,
                absent_reason=(
                    "no recommendation concerns this subject. Note that nothing in this "
                    "platform links a signal to a recommendation: the recommendation engine "
                    "builds from query results, not from findings, so even a related "
                    "recommendation would be a correlation rather than a declared link"
                ),
            )
        )
        links.append(
            ChainLink(
                stage=ChainStage.ACTION,
                present=False,
                absent_reason="no recommendation to act on was linked to this signal",
            )
        )
        links.append(
            ChainLink(
                stage=ChainStage.OUTCOME,
                present=False,
                absent_reason="no action was linked to this signal",
            )
        )
        return DecisionChain(chain_id=f"chain:{signal.headline_id}", links=tuple(links))

    links.append(
        ChainLink(
            stage=ChainStage.RECOMMENDATION,
            present=True,
            headline_id=matched.headline_id,
            summary=matched.title,
            link_type=LinkType.CORRELATED,
        )
    )

    # --- action: a real foreign key ---
    record_id = matched.provenance.source_record_id
    action = workflows_by_recommendation.get(record_id)
    if action is None:
        links.append(
            ChainLink(
                stage=ChainStage.ACTION,
                present=False,
                absent_reason="no workflow has been raised against this recommendation",
            )
        )
        links.append(
            ChainLink(
                stage=ChainStage.OUTCOME,
                present=False,
                absent_reason="no action exists to have produced an outcome",
            )
        )
        return DecisionChain(chain_id=f"chain:{signal.headline_id}", links=tuple(links))

    links.append(
        ChainLink(
            stage=ChainStage.ACTION,
            present=True,
            headline_id=action.headline_id,
            summary=f"{action.title} ({action.status or 'status not recorded'})",
            link_type=LinkType.DECLARED,
        )
    )

    # --- outcome: recorded on the workflow itself ---
    if action.kind is HeadlineKind.OUTCOME:
        links.append(
            ChainLink(
                stage=ChainStage.OUTCOME,
                present=True,
                headline_id=action.headline_id,
                summary=next(
                    (item.value for item in action.evidence if item.label == "outcome"),
                    "recorded",
                )[:200],
                link_type=LinkType.DECLARED,
            )
        )
    else:
        links.append(
            ChainLink(
                stage=ChainStage.OUTCOME,
                present=False,
                absent_reason=(
                    f"the action is {action.status or 'open'} and has recorded no outcome yet"
                ),
            )
        )

    return DecisionChain(chain_id=f"chain:{signal.headline_id}", links=tuple(links))


def build_chains(
    signals: Sequence[Headline],
    recommendations: Sequence[Headline],
    workflows_by_recommendation: dict[str, Headline],
    max_chains: int = DEFAULT_MAX_CHAINS,
) -> tuple[DecisionChain, ...]:
    """Builds chains for the highest-severity signals first.

    Ordering matches the review queue's own (severity descending, then a
    stable id tie-break), so the chain list and the queue agree about
    what matters most.
    """
    ranked = sorted(signals, key=lambda item: (-item.severity, item.headline_id))
    return tuple(
        build_chain(signal, recommendations, workflows_by_recommendation)
        for signal in ranked[:max_chains]
    )


__all__ = ["DEFAULT_MAX_CHAINS", "build_chain", "build_chains"]
