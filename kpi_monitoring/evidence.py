"""Investigates a detected change -- baseline, affected dimensions,
confidence and limitations.

`analytics.root_cause.investigate_root_cause` (Prompt 14) does the
actual work, reused verbatim: it computes the magnitude, breaks it down
by the policy's dimension column(s), ranks contributors, validates each
against `Settings.root_cause_min_contribution_percent`, and refuses to
populate `contributors`/`confidence` at all unless at least one cleared
that bar. This module only maps its result onto `ChangeEvidence` and
adds the monitoring-specific limitations that function has no way to
know about.

**"Insufficient evidence" is a first-class outcome here, not a failure.**
A metric can move sharply for reasons no dimension breakdown explains --
or with no breakdown configured at all. In every such case this module
returns evidence with `has_sufficient_evidence=False`, an empty
contributor list, no confidence, and at least one stated limitation.
`ChangeEvidence`'s own model validator refuses to construct anything
else, so "a change was reported with an implied cause nobody checked"
is unrepresentable rather than merely discouraged.

The three distinct no-evidence cases are kept distinct in the
limitation text, because they need different responses from a reader:
no dimension columns were configured (fix the policy), the breakdown
rows were not supplied (fix the caller), or the breakdown was supplied
and genuinely explained nothing (the movement is broad-based, which is
itself a useful finding).
"""

from __future__ import annotations

import logging

from analytics.models import RootCauseResult
from analytics.root_cause import investigate_root_cause

from config.settings import Settings, get_settings
from kpi_monitoring.models import (
    ChangeEvidence,
    DetectedChange,
    DimensionContribution,
    MonitoringPolicy,
)

logger = logging.getLogger(__name__)

#: One breakdown dataset: the same `(columns, rows)` shape every
#: `analytics/` function already takes, so a caller hands through
#: exactly what `POST /execute` returned without reshaping it.
Breakdown = tuple[list[str], list[tuple]]


def _insufficient(
    reason: str, change: DetectedChange, dimension_columns: tuple[str, ...]
) -> ChangeEvidence:
    """Evidence that explains the movement's size but attributes nothing.

    `magnitude`/`baseline_total`/`current_total` are still populated from
    the change itself wherever they are known -- "here is what moved"
    stays available even when "here is what it moved with" does not, the
    same split `RootCauseResult` makes for its own two halves.
    """
    return ChangeEvidence(
        has_sufficient_evidence=False,
        baseline_total=change.baseline_value,
        current_total=change.current_value,
        magnitude=change.change_absolute,
        magnitude_percent=change.change_percent,
        limitations=(reason,),
        dimension_columns=dimension_columns,
    )


def _from_root_cause(result: RootCauseResult, dimension_columns: tuple[str, ...]) -> ChangeEvidence:
    """Maps a `RootCauseResult` onto `ChangeEvidence`.

    The `has_sufficient_evidence` flag is carried across verbatim rather
    than re-derived -- re-deriving it would be a second place for the
    "no attribution without evidence" rule to drift from Prompt 14's own.
    """
    limitations = tuple(result.limitations)
    if not result.has_sufficient_evidence and not limitations:
        # `investigate_root_cause` always explains itself, but this type
        # must never be constructible without a stated limitation, so a
        # fallback guarantees one rather than raising inside a run.
        limitations = (
            "no dimension value contributed enough of the change to be reported as a "
            "contributor; the movement appears broad-based rather than concentrated",
        )

    return ChangeEvidence(
        has_sufficient_evidence=result.has_sufficient_evidence,
        baseline_total=result.baseline_total,
        current_total=result.current_total,
        magnitude=result.magnitude,
        magnitude_percent=result.magnitude_percent,
        contributors=(
            tuple(
                DimensionContribution(
                    label=contributor.label,
                    current_value=contributor.current_value,
                    baseline_value=contributor.baseline_value,
                    contribution=contributor.contribution,
                    contribution_percent=contributor.contribution_percent,
                )
                for contributor in result.contributors
            )
            if result.has_sufficient_evidence
            else ()
        ),
        confidence=result.confidence if result.has_sufficient_evidence else None,
        limitations=limitations,
        dimension_columns=dimension_columns,
    )


def investigate(
    change: DetectedChange,
    policy: MonitoringPolicy,
    *,
    current_breakdown: Breakdown | None = None,
    baseline_breakdown: Breakdown | None = None,
    value_column: str | None = None,
    settings: Settings | None = None,
) -> ChangeEvidence:
    """Builds the evidence for one detected change.

    Args:
        change: The movement to investigate.
        policy: Its policy -- `dimension_columns` decides what the
            breakdown is attempted by.
        current_breakdown: `(columns, rows)` for the period that moved,
            broken down by the policy's dimension column(s). `None`
            means the caller supplied none, which is an honest
            insufficient-evidence outcome rather than an error.
        baseline_breakdown: The same shape for the comparison period.
        value_column: Which column holds the metric. Defaults to the
            last column of `current_breakdown`, which is the shape a
            `SELECT dimension, SUM(metric) ... GROUP BY dimension` query
            naturally produces.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        `ChangeEvidence` -- never raises. An investigation fault
        degrades to insufficient evidence with the reason stated, the
        same fail-open posture every accuracy aid in this codebase
        takes: losing the attribution must not lose the finding that a
        real movement happened.
    """
    settings = settings or get_settings()
    dimensions = tuple(policy.dimension_columns)

    if not dimensions:
        return _insufficient(
            "no dimension columns are configured on this policy, so the change could not "
            "be attributed to any part of the business",
            change,
            dimensions,
        )
    if not current_breakdown or not baseline_breakdown:
        return _insufficient(
            "no dimension breakdown was supplied for the current and baseline periods, so "
            "the change could not be attributed",
            change,
            dimensions,
        )

    columns, current_rows = current_breakdown
    baseline_columns, baseline_rows = baseline_breakdown
    if list(columns) != list(baseline_columns):
        return _insufficient(
            "the current and baseline breakdowns use different columns, so they cannot be "
            "compared",
            change,
            dimensions,
        )

    resolved_value_column = value_column or (columns[-1] if columns else "")
    if not resolved_value_column:
        return _insufficient(
            "the supplied breakdown has no columns, so there is no value column to " "investigate",
            change,
            dimensions,
        )

    missing = [column for column in dimensions if column not in columns]
    if missing:
        return _insufficient(
            f"the supplied breakdown does not contain this policy's dimension column(s) "
            f"({', '.join(missing)}), so the change could not be attributed",
            change,
            dimensions,
        )

    try:
        result = investigate_root_cause(
            current_rows=list(current_rows),
            baseline_rows=list(baseline_rows),
            columns=list(columns),
            dimension_columns=list(dimensions),
            value_column=resolved_value_column,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - an investigation fault must not lose the finding
        logger.warning(
            "[kpi_monitor] root-cause investigation failed for policy %s; reporting the "
            "change without attribution: %s",
            policy.policy_id,
            type(exc).__name__,
        )
        return _insufficient(
            "the attribution step could not be completed, so this change is reported "
            "without one",
            change,
            dimensions,
        )

    return _from_root_cause(result, dimensions)


__all__ = ["Breakdown", "investigate"]
