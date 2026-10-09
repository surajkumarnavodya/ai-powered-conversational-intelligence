"""The single markdown rendering of an executive briefing.

One implementation, shared by `POST /briefing/generate`'s own
`report_markdown` field and by any future operator-facing entry point --
the same "there is exactly one report format, not an API-only and a
CLI-only copy" rule `kpi_monitoring.reports` and `schema_drift.reports`
already establish.

**What the renderer will not do.** It never states a cause, never
promotes an estimate or a recommendation into a fact, and never prints a
metric's `approved_expression`, a raw row or a sampled value. Every
section is labelled with the kind of claim it contains, every item
carries its evidence reference and versions, and a stale item is marked
in place rather than quietly omitted.

**It always renders the coverage table**, including (and especially) for
an empty briefing: "nothing happened" and "nothing was checked" must
never look the same on the page.
"""

from __future__ import annotations

from briefing.models import (
    BriefingItem,
    BriefingSection,
    ClaimClass,
    ExecutiveBriefing,
)

_SECTION_TITLES: dict[BriefingSection, str] = {
    BriefingSection.KPI_MOVEMENT: "KPI movement",
    BriefingSection.ANOMALIES: "Significant anomalies",
    BriefingSection.DRIVERS: "Drivers",
    BriefingSection.RISKS: "Risks",
    BriefingSection.OPPORTUNITIES: "Opportunities",
    BriefingSection.UNRESOLVED_RECOMMENDATIONS: "Unresolved recommendations",
    BriefingSection.DATA_QUALITY: "Data-quality issues",
}

_CLAIM_CLASS_LABELS: dict[ClaimClass, str] = {
    ClaimClass.OBSERVED_FACT: "observed fact",
    ClaimClass.ESTIMATE: "estimate",
    ClaimClass.RECOMMENDATION: "recommendation",
}

_SECTION_NOTES: dict[BriefingSection, str] = {
    BriefingSection.DRIVERS: (
        "A driver is a contribution to an arithmetic total, not a demonstrated cause."
    ),
    BriefingSection.RISKS: (
        "Each item below is an inference about observed numbers, not an established " "outcome."
    ),
    BriefingSection.OPPORTUNITIES: (
        "Each item below is an inference or a suggestion, never an observed result."
    ),
    BriefingSection.UNRESOLVED_RECOMMENDATIONS: (
        "Suggestions awaiting a decision. Nothing here has been confirmed or acted on."
    ),
}


def _render_item(item: BriefingItem) -> list[str]:
    label = _CLAIM_CLASS_LABELS[item.claim_class]
    lines = [f"#### [{item.risk_tier}/{item.risk_score}] {item.headline}", ""]
    lines.append(f"- Claim type: **{label}** (truth level `{item.truth_level.value}`)")
    if item.detail:
        lines.append(f"- {item.detail}")
    if item.is_stale:
        lines.append(f"- **Stale evidence.** {item.stale_reason}")
    for link in item.evidence:
        numbers = (
            ", ".join(f"{key}={value:,.4g}" for key, value in sorted(link.observed.items()))
            or "no numeric aggregate recorded"
        )
        versions = ", ".join(f"{key}={value}" for key, value in sorted(link.versions.items()))
        lines.append(
            f"- Evidence: `{link.reference_kind}` `{link.reference_id}` "
            f"from `{link.source}` ({numbers}); versions: {versions}"
        )
    for limitation in item.limitations:
        lines.append(f"- Limitation: {limitation}")
    lines.append("")
    return lines


def render_markdown(briefing: ExecutiveBriefing) -> str:
    """Renders one briefing as markdown."""
    heading = briefing.period.value.capitalize()
    label = f" -- {briefing.period_label}" if briefing.period_label else ""
    lines: list[str] = [
        f"# {heading} executive briefing: {briefing.database_id}{label}",
        "",
        f"- Reference time: {briefing.as_of}",
        f"- Claims: {len(briefing.items)} "
        f"({briefing.observed_fact_count} observed fact, "
        f"{briefing.estimate_count} estimate, "
        f"{briefing.recommendation_count} recommendation)",
        f"- Engine version: {briefing.engine_version}",
        "",
        "Observed facts are arithmetic over governed query results. Estimates are this "
        "platform's own inferences about those numbers. Recommendations are suggestions "
        "awaiting a human decision. Nothing in this briefing is confirmed business "
        "truth, and nothing states a cause.",
        "",
    ]

    if briefing.is_empty:
        lines.append("## No claims in this period")
        lines.append("")
        lines.append(
            "Read the coverage table below before concluding that nothing happened: a "
            "source marked not supplied was never checked."
        )
        lines.append("")

    for section in BriefingSection:
        items = briefing.items_for(section)
        if not items:
            continue
        lines.append(f"## {_SECTION_TITLES[section]}")
        lines.append("")
        note = _SECTION_NOTES.get(section)
        if note:
            lines.append(note)
            lines.append("")
        if section in briefing.truncated_sections:
            lines.append("Capped: lower-ranked claims in this section are not shown.")
            lines.append("")
        for item in items:
            lines.extend(_render_item(item))

    lines.append("## Coverage")
    lines.append("")
    lines.append("Distinguishes 'nothing happened' from 'nothing was checked'.")
    lines.append("")
    lines.append("| Source | Supplied | Records in | Claims out | Note |")
    lines.append("| --- | --- | --- | --- | --- |")
    for entry in briefing.coverage:
        lines.append(
            f"| `{entry.source}` | {'yes' if entry.supplied else 'no'} | "
            f"{entry.input_count} | {entry.items_produced} | {entry.note or '-'} |"
        )
    lines.append("")

    if briefing.empty_sections:
        lines.append("## Sections with nothing to report")
        lines.append("")
        for section in briefing.empty_sections:
            lines.append(f"- {_SECTION_TITLES[section]}")
        lines.append("")

    if briefing.stale_inputs:
        lines.append("## Stale evidence")
        lines.append("")
        lines.append(
            "Reported rather than hidden: a stale record is still shown in its own "
            "section, marked, and ranked below fresh records."
        )
        lines.append("")
        for stale in briefing.stale_inputs:
            lines.append(f"- `{stale.source}` `{stale.reference_id}`: {stale.detail}")
        lines.append("")

    if briefing.withheld_claims:
        lines.append("## Withheld claims")
        lines.append("")
        lines.append(
            "These were proposed but not shown, because their own evidence did not " "support them."
        )
        lines.append("")
        for problem in briefing.withheld_claims:
            lines.append(
                f"- [{_SECTION_TITLES[problem.section]}] **{problem.code}**: " f"{problem.detail}"
            )
        lines.append("")

    if briefing.limitations:
        lines.append("## Limitations")
        lines.append("")
        for limitation in briefing.limitations:
            lines.append(f"- {limitation}")
        lines.append("")

    return "\n".join(lines)


__all__ = ["render_markdown"]
