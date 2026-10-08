"""Renders one scan's findings into an actionable markdown report/alert --
"generate actionable reports/alerts; do not silently rewrite production
definitions." Pure text rendering over already-computed `Finding`s
(risk-scored, impact-assessed) -- no I/O, no further computation.

Grouped by risk tier (high first) so a reader sees the most consequential
changes first, mirroring `semantic.intelligence.risk.prioritize`'s own
"ordered by risk, not volume" principle applied to presentation. Every
entry discloses its own truth level inline -- a `schema_drift_column_
renamed` finding is never rendered as if it were as certain as a
`schema_drift_column_removed` one.
"""

from __future__ import annotations

from collections.abc import Sequence

from semantic.intelligence.detect import Finding

_TIER_HEADINGS: dict[str, str] = {
    "high": "## High risk",
    "medium": "## Medium risk",
    "low": "## Low risk",
}
_TIER_ORDER: tuple[str, ...] = ("high", "medium", "low")


def _finding_line(finding: Finding) -> str:
    impacted = finding.payload.get("impacted") or []
    impact_note = (
        f" -- impacts {len(impacted)} downstream node(s)"
        if impacted
        else " -- no known downstream dependents"
    )
    truth_note = (
        "DATABASE FACT" if finding.truth_level == "database_fact" else "AI INFERENCE (unconfirmed)"
    )
    return f"- **{finding.title}** [{truth_note}]{impact_note}\n  {finding.detail}"


def render_markdown_report(
    database_id: str, findings: Sequence[Finding], tables_compared: int
) -> str:
    """One scan's findings as a markdown report -- `ScanOut.report_markdown`
    and `scripts/run_schema_drift_scan.py`'s own stdout both use this
    verbatim, so there is exactly one rendering, not an API-only and a
    CLI-only copy."""
    if not findings:
        return (
            f"# Schema drift report -- {database_id}\n\n"
            f"No drift detected across {tables_compared} table(s)/view(s)."
        )

    lines = [
        f"# Schema drift report -- {database_id}",
        "",
        f"{len(findings)} finding(s) across {tables_compared} table(s)/view(s) compared.",
        "",
    ]
    by_tier: dict[str, list[Finding]] = {tier: [] for tier in _TIER_ORDER}
    for finding in findings:
        by_tier.setdefault(finding.risk_tier, []).append(finding)

    for tier in _TIER_ORDER:
        items = by_tier.get(tier) or []
        if not items:
            continue
        lines.append(_TIER_HEADINGS[tier])
        lines.append("")
        lines.extend(_finding_line(f) for f in items)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_markdown_report"]
