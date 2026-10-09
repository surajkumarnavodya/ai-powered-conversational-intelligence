"""Risk scoring and review-queue ordering (Prompt 35).

The review queue is ordered by risk, not by volume. A single conflict on a
published metric must outrank any number of low-risk synonym candidates. The
score is a transparent sum of named parts, and each part is returned as a
reason, so an SME can see why an item sits where it does.

    base (kind)           conflict 50, ambiguity 40, rule candidate 20,
                          metric relationship 10, synonym 5, term cluster 5
    + published subjects  15 per published concept, at most two
    + dependents          5 per dependent, at most five
    + inference gap       (1 - confidence) * 20, rounded
    + impact (optional)   a caller-supplied, already-capped term for a kind
                          whose severity varies continuously (a KPI move's
                          own size); 0 for every other kind

Tiers: high at 70 and above, medium at 40 and above, otherwise low.
"""

from __future__ import annotations

from dataclasses import replace

from semantic.intelligence.detect import Finding

_BASE: dict[str, int] = {
    "conflict": 50,
    "ambiguity": 40,
    "rule_candidate": 20,
    "metric_relationship": 10,
    "synonym": 5,
    "term_cluster": 5,
    # Prompt 43 (43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md):
    # schema_drift.findings' own kinds, scored by the same formula --
    # base (by structural severity) + 15 per published/confirmed
    # downstream dependent (schema_drift.impact sets `published_count`
    # for a CONFIRMED_BUSINESS_TRUTH impacted node) + 5 per dependent,
    # at most five, + an inference-confidence gap. A table/column
    # removal or a primary-key change is base-level comparable to a
    # catalog conflict (both mean "something currently relied upon is
    # now wrong"); an additive change (a new table/column/view/FK) is
    # low-risk on its own regardless of what (nothing yet) depends on it.
    "schema_drift_table_removed": 60,
    "schema_drift_view_removed": 45,
    "schema_drift_column_removed": 45,
    "schema_drift_primary_key_changed": 45,
    "schema_drift_foreign_key_removed": 40,
    "schema_drift_column_renamed": 35,
    "schema_drift_object_type_changed": 30,
    "schema_drift_column_type_changed": 30,
    "schema_drift_column_nullability_changed": 20,
    "schema_drift_unique_constraint_changed": 20,
    "schema_drift_foreign_key_added": 10,
    "schema_drift_table_added": 5,
    "schema_drift_view_added": 5,
    "schema_drift_column_added": 5,
    # Prompt 50 (50_PROACTIVE_AI_ANALYST_CONTRACT.md):
    # kpi_monitoring.findings' own kinds. A monitoring policy may only
    # target a *published* catalog metric, so every one of these is a
    # movement in confirmed business truth -- which is why their bases
    # sit near a catalog conflict's rather than a synonym candidate's.
    # A threshold breach outranks an ordinary move because an operator
    # explicitly declared that level significant; a drop outranks a
    # spike because a KPI falling is more often the thing someone needs
    # to act on, while a spike is as often a good surprise or a data
    # artifact; a trend break is the weakest, since it fires for a point
    # that is statistically unusual without clearing the operator's own
    # threshold. Move *size* is not in this table -- it varies
    # continuously and arrives through `impact_bonus` below.
    "kpi_threshold_breach": 45,
    "kpi_drop": 40,
    "kpi_spike": 35,
    "kpi_trend_break": 20,
}
HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40


def score(finding: Finding, dependents: int = 0, impact_bonus: int = 0) -> Finding:
    """Returns `finding` with `risk_score`, `risk_tier` and `reasons` set. The
    input is not mutated.

    `impact_bonus` (Prompt 50) is an optional, already-bounded term for a
    kind whose severity varies continuously rather than being fixed by the
    kind alone -- a KPI movement, where two findings of the same kind can
    differ by orders of magnitude and the larger one must rank first. It
    defaults to 0, so every caller that predates it scores identically.
    Callers that use it are responsible for capping it (see
    `kpi_monitoring.findings._impact_bonus`); the total is clamped to 100
    either way.
    """
    published = min(finding.published_count, 2)
    dependent_points = min(dependents, 5) * 5
    gap = round((1.0 - finding.confidence) * 20)
    impact = max(0, impact_bonus)
    total = min(100, _BASE.get(finding.kind, 0) + 15 * published + dependent_points + gap + impact)
    tier = "high" if total >= HIGH_THRESHOLD else "medium" if total >= MEDIUM_THRESHOLD else "low"
    reasons = [f"{finding.kind} base {_BASE.get(finding.kind, 0)}"]
    if published:
        reasons.append(f"{published} published concept(s): +{15 * published}")
    if dependents:
        reasons.append(f"{dependents} dependent(s): +{dependent_points}")
    if gap:
        reasons.append(f"confidence {finding.confidence:.2f}: +{gap}")
    if impact:
        reasons.append(f"impact: +{impact}")
    return replace(
        finding,
        risk_score=total,
        risk_tier=tier,
        reasons=tuple(reasons),
        dependents=dependents,
    )


def prioritize(findings: list[Finding]) -> list[Finding]:
    """Scores and sorts the queue, riskiest first. Ties break on kind and key,
    so the order is deterministic across runs."""
    scored = [score(f, f.dependents) for f in findings]
    return sorted(scored, key=lambda f: (-f.risk_score, f.kind, f.key))
