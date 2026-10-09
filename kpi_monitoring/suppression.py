"""Decides which findings actually reach which recipient.

**Suppression governs alerting, never evidence.** Every finding this
package produces is persisted and appears in the review queue regardless
of what happens here. What suppression decides is whether a human is
*told* about it now. Dropping the finding instead would destroy the
record of a real movement, which is precisely what someone goes looking
for afterwards ("did we know about this?").

Five independent reasons, deliberately accumulated rather than
short-circuited -- a finding suppressed both by a quiet period and by a
user's severity floor reports both, because an operator asking "why
didn't I get paged" needs the whole answer, not the first match:

- `NON_BUSINESS_PERIOD` -- the movement is *in* a period the business
  calendar excludes (a holiday, a shutdown, a period still being
  collected). Complementary to, not a substitute for,
  `kpi_monitoring.calendar.exclude_non_business_periods`, which keeps
  the same period out of *baselines*; see that module for why both are
  needed.
- `QUIET_PERIOD` -- an operator-declared window where findings accrue
  silently (a release freeze, a known migration).
- `DUPLICATE_OF_OPEN_FINDING` -- this exact finding key is already open
  and undecided from an earlier run. Re-paging about something nobody
  has acted on yet is the classic way an alerting system trains its
  users to ignore it. This is the *alert-level* half of duplicate
  suppression; the storage-level half is
  `identity.repositories.semantic_intelligence.upsert_findings`'
  content-hash check, which stops an unchanged finding from even
  versioning. The two are different and both are needed: the first stops
  repeat notification, the second stops repeat history.
- `BELOW_USER_SEVERITY` / `METRIC_MUTED_BY_USER` -- this recipient's own
  preferences, applied per recipient so one user's mute never affects
  another's.

Finally, `AlertPreference.max_alerts_per_run` caps what survives, after
ranking. A misconfigured threshold then produces a short, ranked digest
instead of a hundred pages -- the behaviour the mission's "test alert
noise" requirement is really about.

Pure and DB-free, like the rest of this package: the set of
already-open keys is supplied by the caller (the API route reads it),
never queried here.
"""

from __future__ import annotations

from collections.abc import Sequence

from semantic.intelligence.detect import Finding

from kpi_monitoring.models import (
    AlertBundle,
    AlertDecision,
    AlertPreference,
    BusinessCalendar,
    DetectedChange,
    QuietPeriod,
    SuppressionReason,
)

#: Severity ordering for `AlertPreference.min_risk_tier`. Matches
#: `semantic.intelligence.risk`'s own three tiers exactly -- a fourth
#: tier there would need adding here too, which `_tier_rank` makes loud
#: (a `KeyError`) rather than silently treating as lowest.
_TIER_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def _tier_rank(tier: str) -> int:
    return _TIER_RANK[tier]


def _shared_reasons(
    change: DetectedChange,
    finding: Finding,
    *,
    calendar: BusinessCalendar | None,
    quiet_periods: Sequence[QuietPeriod],
    previously_open_keys: frozenset[str],
) -> tuple[list[SuppressionReason], list[str]]:
    """Reasons that apply to every recipient alike, with their details.

    Computed once per finding rather than per recipient: a quiet period
    is a property of the run, not of who is reading.
    """
    reasons: list[SuppressionReason] = []
    details: list[str] = []

    if calendar is not None and not calendar.is_business_period(change.period):
        reasons.append(SuppressionReason.NON_BUSINESS_PERIOD)
        details.append(f"{change.period} is not a business period in calendar {calendar.name!r}")

    for quiet in quiet_periods:
        if quiet.covers(change.period):
            reasons.append(SuppressionReason.QUIET_PERIOD)
            details.append(
                f"quiet period {quiet.name!r} covers {change.period}"
                + (f": {quiet.reason}" if quiet.reason else "")
            )
            break

    if finding.key in previously_open_keys:
        reasons.append(SuppressionReason.DUPLICATE_OF_OPEN_FINDING)
        details.append(
            "an identical finding for this metric and period is already open and undecided"
        )

    return reasons, details


def decide_alerts(
    items: Sequence[tuple[DetectedChange, Finding]],
    preferences: Sequence[AlertPreference],
    *,
    calendar: BusinessCalendar | None = None,
    quiet_periods: Sequence[QuietPeriod] = (),
    previously_open_keys: frozenset[str] = frozenset(),
) -> tuple[AlertBundle, ...]:
    """Builds one alert bundle per recipient.

    Args:
        items: `(change, scored finding)` pairs from this run.
        preferences: One entry per recipient. An empty sequence produces
            no bundles at all -- nobody has asked to be told, so nobody
            is, which is different from (and safer than) alerting
            everyone by default.
        calendar: The tenant's business calendar.
        quiet_periods: Operator-declared silent windows.
        previously_open_keys: Finding keys already open and undecided
            before this run, supplied by the caller.

    Returns:
        One `AlertBundle` per preference, in the order given. Every
        finding appears in every bundle as an `AlertDecision` -- alerted
        or not, with its reasons -- so a bundle is a complete, auditable
        account of the run for that recipient rather than only the
        things that got through.
    """
    # Rank once, highest risk first, with a stable tie-break on the key
    # so two runs over identical data produce identical ordering. The
    # same ordering `identity.repositories.semantic_intelligence
    # .list_findings` uses for the review queue, so the digest and the
    # queue agree about what matters most.
    ranked = sorted(items, key=lambda pair: (-pair[1].risk_score, pair[1].key))

    shared: dict[str, tuple[list[SuppressionReason], list[str]]] = {}
    for change, finding in ranked:
        shared[finding.key] = _shared_reasons(
            change,
            finding,
            calendar=calendar,
            quiet_periods=quiet_periods,
            previously_open_keys=previously_open_keys,
        )

    bundles: list[AlertBundle] = []
    for preference in preferences:
        decisions: list[AlertDecision] = []
        alerted_so_far = 0
        for change, finding in ranked:
            reasons, details = shared[finding.key]
            reasons = list(reasons)
            details = list(details)

            if change.metric_concept_key in preference.muted_metric_keys:
                reasons.append(SuppressionReason.METRIC_MUTED_BY_USER)
                details.append("this recipient has muted this metric")
            if _tier_rank(finding.risk_tier) < _tier_rank(preference.min_risk_tier):
                reasons.append(SuppressionReason.BELOW_USER_SEVERITY)
                details.append(
                    f"risk tier {finding.risk_tier!r} is below this recipient's "
                    f"minimum {preference.min_risk_tier!r}"
                )

            would_alert = not reasons
            if would_alert and alerted_so_far >= preference.max_alerts_per_run:
                # Deliberately not its own SuppressionReason: the finding
                # is not suppressed on its merits, it simply fell outside
                # this digest. The detail says so plainly.
                would_alert = False
                details.append(
                    f"beyond this recipient's per-run limit of "
                    f"{preference.max_alerts_per_run} alerts (ranked by risk)"
                )

            if would_alert:
                alerted_so_far += 1

            decisions.append(
                AlertDecision(
                    finding_key=finding.key,
                    metric_concept_key=change.metric_concept_key,
                    period=change.period,
                    risk_score=finding.risk_score,
                    risk_tier=finding.risk_tier,
                    alerted=would_alert,
                    suppressed_by=tuple(reasons),
                    suppression_detail="; ".join(details),
                )
            )
        bundles.append(AlertBundle(user_id=preference.user_id, decisions=tuple(decisions)))

    return tuple(bundles)


__all__ = ["decide_alerts"]
