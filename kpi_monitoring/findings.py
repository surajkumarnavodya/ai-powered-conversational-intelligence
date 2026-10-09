"""Bridges a detected change into Prompt 35's review-queue `Finding`.

This package stores nothing of its own. A KPI finding is a
`semantic.intelligence.detect.Finding`, persisted by
`identity.repositories.semantic_intelligence.upsert_findings` into the
same `semantic_findings` table the catalog detectors (Prompt 35) and
schema drift (Prompt 43) already use -- making monitoring the third
consumer of one governed review queue rather than a third place to look
for open items. Everything that queue already provides comes for free:
tenant scoping, content versioning with an append-only history,
accept/dismiss decisions, rollback, and risk-ordered listing.

**Duplicate suppression is the `finding_key`.** The key hashes the
policy, the metric and the *period* -- so:

- re-running the same scan over the same data finds the same key with
  the same content hash and records `unchanged`, raising nothing;
- a restatement of that period's data changes the content hash, which
  versions the row and reopens it for a fresh decision, because the
  numbers a reviewer saw are no longer the numbers on file;
- the next period is genuinely a different key, and is a new finding.

Including the period is the deliberate part. Keying on policy+metric
alone would collapse every month's movement into one perpetually-updated
row, destroying the history of what happened when; keying on content
alone would re-raise an identical-looking movement in a later period as
a duplicate of an older one.

**Kind names are kept short on purpose.** `identity.models
.SemanticFinding.kind` is `String(64)` (widened from 32 by migration
`m6q9r2s5t8u1`, after `schema_drift`'s own 39-character kinds turned out
to be rejected by PostgreSQL while SQLite ignored the length). Every kind
this module emits is well inside that; `_assert_kinds_fit` fails at
import if a future edit is not, rather than at the first PostgreSQL
write.
"""

from __future__ import annotations

import hashlib

from semantic.intelligence.detect import Finding
from semantic.intelligence.risk import score

from kpi_monitoring.models import (
    ChangeEvidence,
    ChangeKind,
    DetectedChange,
    finding_payload,
)

#: The hard limit on `identity.models.SemanticFinding.kind`. SQLite
#: silently ignores a VARCHAR length, so an over-long kind passes every
#: test in this suite and only fails against the real PostgreSQL identity
#: database -- which is exactly why this is asserted at import time
#: instead of being left to a deployment to discover. Restated here
#: rather than imported, because this package deliberately never imports
#: `identity` (see its own module docstring).
KIND_MAX_LENGTH = 64


def _assert_kinds_fit() -> None:
    too_long = [kind.value for kind in ChangeKind if len(kind.value) > KIND_MAX_LENGTH]
    if too_long:
        raise RuntimeError(
            f"ChangeKind value(s) exceed SemanticFinding.kind's {KIND_MAX_LENGTH}-character "
            f"column and would fail on PostgreSQL: {too_long}"
        )


_assert_kinds_fit()


def finding_key(change: DetectedChange) -> str:
    """Stable identity for one movement: policy, metric and period.

    Hashed the same way `semantic.intelligence.detect._finding_key` and
    `schema_drift.findings._finding_key` already hash theirs, so every
    key in the shared table has the same shape and length.
    """
    raw = "|".join(["kpi", change.policy_id, change.metric_concept_key, change.period])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _title(change: DetectedChange) -> str:
    """A one-line summary stating only what was observed.

    Deliberately free of any attribution: the title is what a reviewer
    sees in a list, and a cause named there would be read as established
    long before anyone opens the evidence. Direction and size are
    arithmetic over supplied rows, so stating them plainly is accurate.
    """
    name = change.metric_display_name or change.metric_concept_key
    if change.kind is ChangeKind.THRESHOLD_BREACH:
        return f"{name} crossed a configured threshold in {change.period}"
    if change.change_percent is None:
        return f"{name} moved unusually in {change.period}"
    verb = "fell" if change.direction == "down" else "rose"
    return f"{name} {verb} {abs(change.change_percent):.1f}% in {change.period}"


def _detail(change: DetectedChange, evidence: ChangeEvidence) -> str:
    """The body a reviewer reads, with observation and inference kept
    visibly apart -- observed numbers first, then either an explicitly
    hedged attribution or an explicit statement that none could be
    made."""
    parts: list[str] = []
    name = change.metric_display_name or change.metric_concept_key

    observed = f"{name} was {change.current_value:,.2f} in {change.period}"
    if change.baseline_value is not None:
        observed += f", against {change.baseline_value:,.2f} ({change.baseline_label})"
        if change.change_percent is not None:
            observed += f" -- a change of {change.change_percent:+.1f}%"
    parts.append(observed + ".")

    if change.threshold_crossed is not None:
        parts.append(f"This crossed the configured threshold of {change.threshold_crossed:,.2f}.")
    if change.detection_methods:
        parts.append(
            "Statistical methods that also flagged this point: "
            f"{', '.join(change.detection_methods)}."
        )

    if evidence.has_sufficient_evidence and evidence.contributors:
        top = ", ".join(
            f"{contributor.label} ({contributor.contribution_percent:+.1f}% of the change)"
            for contributor in evidence.contributors[:3]
        )
        parts.append(
            f"Largest contributors to the change: {top}. These are contributions to an "
            f"arithmetic total, not a demonstrated cause."
        )
    else:
        parts.append(
            "No attribution was made: " + (evidence.limitations[0] if evidence.limitations else "")
        )

    return " ".join(part for part in parts if part.strip())


def _impact_bonus(change: DetectedChange, max_bonus: int) -> int:
    """How much the *size* of the move adds to its rank.

    `semantic.intelligence.risk.score` ranks by kind, which is right for
    a catalog conflict (every conflict is comparably serious) but wrong
    on its own for a KPI: two drops of the same kind can differ by two
    orders of magnitude, and an operator wants the big one first. This
    scales the move's own percentage, capped, so a 400% spike cannot
    crowd out everything else by sheer arithmetic.

    A threshold breach with no computable percentage still earns the
    full bonus: the operator declared that level significant, which is a
    stronger statement than any percentage this function could infer.
    """
    if change.change_percent is None:
        return max_bonus if change.threshold_crossed is not None else 0
    magnitude = abs(change.change_percent)
    # 100% of the baseline (a doubling or a total collapse) earns the
    # full bonus; smaller moves scale linearly below that.
    return int(round(min(magnitude, 100.0) / 100.0 * max_bonus))


def change_to_finding(
    change: DetectedChange,
    evidence: ChangeEvidence,
    *,
    published_count: int = 1,
    dependents: int = 0,
    max_impact_bonus: int = 30,
) -> Finding:
    """Builds the persisted finding for one change.

    Args:
        change: The detected movement.
        evidence: Its investigation result.
        published_count: How many published/confirmed concepts this
            finding touches. Defaults to 1 because a monitoring policy
            may only target a *published* catalog metric in the first
            place (see `kpi_monitoring.policy`), so every KPI finding is
            about confirmed business truth by construction -- which is
            precisely the weighting `risk.score`'s own published-subject
            term exists to express.
        dependents: Downstream dependents of the metric, if the caller
            computed them (`lineage`). 0 when unknown.
        max_impact_bonus: Ceiling on the move-size term.

    Returns:
        A scored `Finding`, `AI_INFERENCE` like every other finding in
        that queue -- the judgment that a movement deserves attention is
        an inference even though the numbers it cites are observed.
    """
    # `ChangeEvidence` guarantees no confidence without evidence, but not
    # the converse -- `analytics.root_cause` may report supporting
    # contributors and still leave confidence unset. Keying off the value
    # itself covers both, and is the honest default either way: 0.5 says
    # "no stated confidence", not "half sure".
    confidence = evidence.confidence if evidence.confidence is not None else 0.5
    finding = Finding(
        kind=change.kind.value,
        key=finding_key(change),
        title=_title(change),
        detail=_detail(change, evidence),
        subjects=(
            {
                "concept_key": change.metric_concept_key,
                "policy_id": change.policy_id,
                "period": change.period,
            },
        ),
        evidence=(
            {
                "observed": {
                    "period": change.period,
                    "current_value": change.current_value,
                    "baseline_value": change.baseline_value,
                    "baseline_label": change.baseline_label,
                    "change_percent": change.change_percent,
                },
                "truth_level": "database_fact",
            },
            {
                "attribution": {
                    "has_sufficient_evidence": evidence.has_sufficient_evidence,
                    "dimension_columns": list(evidence.dimension_columns),
                    "contributors": [
                        {
                            "label": contributor.label,
                            "contribution_percent": contributor.contribution_percent,
                        }
                        for contributor in evidence.contributors
                    ],
                    "limitations": list(evidence.limitations),
                },
                "truth_level": "ai_inference",
            },
        ),
        confidence=confidence,
        published_count=published_count,
        payload=finding_payload(change, evidence),
    )
    return score(
        finding, dependents=dependents, impact_bonus=_impact_bonus(change, max_impact_bonus)
    )


def is_kpi_kind(kind: str) -> bool:
    """Whether a stored finding came from this package -- the same
    `kind`-prefix test `schema_drift.findings.is_schema_drift_kind`
    uses, so a kind-scoped read of the shared table stays a one-liner."""
    return kind.startswith("kpi_")


__all__ = ["KIND_MAX_LENGTH", "change_to_finding", "finding_key", "is_kpi_kind"]
