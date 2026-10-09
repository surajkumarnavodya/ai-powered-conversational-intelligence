"""The single markdown rendering of a monitoring run.

One implementation, shared by `POST /kpi-monitoring/scan`'s own
`report_markdown` field and by `scripts/run_kpi_monitor.py`'s stdout --
the same "there is exactly one report format, not an API-only and a
CLI-only copy" rule `schema_drift.reports` already establishes, and for
the same reason: an operator reading a cron job's output and a reviewer
reading the API response must be looking at the same thing.

**What the renderer will not do.** It never states a cause. A finding
with sufficient evidence renders its contributors as contributions with
an explicit caveat; a finding without renders the limitation instead.
It also never prints a metric's `approved_expression` or any raw row --
only the aggregates already on the typed result.
"""

from __future__ import annotations

from kpi_monitoring.engine import MonitorRun
from kpi_monitoring.models import KPI_MONITORING_VERSION


def _format_value(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def render_markdown(run: MonitorRun) -> str:
    """Renders one run as markdown."""
    result = run.result
    lines: list[str] = [
        f"# KPI monitoring -- {result.database_id}",
        "",
        f"- Policies evaluated: {result.policies_evaluated}",
        f"- Findings: {len(run.findings)}"
        + (" (truncated at the configured cap)" if result.truncated else ""),
        f"- Engine version: {KPI_MONITORING_VERSION}",
        "",
    ]

    if result.violations:
        lines.append("## Policies refused")
        lines.append("")
        for violation in result.violations:
            lines.append(f"- `{violation.policy_id}` -- **{violation.code}**: {violation.detail}")
        lines.append("")

    if not run.findings:
        lines.append("No meaningful change was detected.")
        lines.append("")
    else:
        lines.append("## Findings, most impactful first")
        lines.append("")
        for finding in run.findings:
            change = run.changes_by_key[finding.key]
            evidence = result.evidence_by_key.get(finding.key)
            lines.append(f"### [{finding.risk_tier}/{finding.risk_score}] {finding.title}")
            lines.append("")
            lines.append(
                f"- Observed: {_format_value(change.current_value)} in {change.period}, "
                f"against {_format_value(change.baseline_value)} ({change.baseline_label})"
            )
            if change.change_percent is not None:
                lines.append(f"- Change: {change.change_percent:+.1f}%")
            if change.threshold_crossed is not None:
                lines.append(f"- Threshold crossed: {_format_value(change.threshold_crossed)}")
            if change.detection_methods:
                lines.append(f"- Also flagged by: {', '.join(change.detection_methods)}")
            lines.append(f"- Ranked because: {'; '.join(finding.reasons)}")

            if evidence is not None and evidence.has_sufficient_evidence:
                lines.append(
                    f"- Attribution (confidence {evidence.confidence:.2f}) -- contributions "
                    f"to the arithmetic total, **not** a demonstrated cause:"
                )
                for contributor in evidence.contributors:
                    lines.append(
                        f"  - {contributor.label}: "
                        f"{contributor.contribution_percent:+.1f}% of the change"
                    )
            elif evidence is not None:
                lines.append("- No attribution was made.")
            if evidence is not None and evidence.limitations:
                lines.append("- Limitations:")
                for limitation in evidence.limitations:
                    lines.append(f"  - {limitation}")
            lines.append("")

    if result.series_skipped:
        lines.append("## Series not evaluated")
        lines.append("")
        lines.append("Reported so that a quiet run is distinguishable from an unchecked one.")
        lines.append("")
        for reason in result.series_skipped:
            lines.append(f"- {reason}")
        lines.append("")

    if run.alert_bundles:
        lines.append("## Alerts")
        lines.append("")
        lines.append(
            "This platform has no delivery channel; the following is what *would* be "
            "sent per recipient."
        )
        lines.append("")
        for bundle in run.alert_bundles:
            lines.append(
                f"- `{bundle.user_id}`: {bundle.alerted_count} alerted, "
                f"{bundle.suppressed_count} suppressed"
            )
        lines.append("")

    return "\n".join(lines)


__all__ = ["render_markdown"]
