"""The one orchestration entry point: policies in, findings and alert
bundles out.

Runs the mission's own named flow, each step delegated to the module
that owns it, so this file stays a sequence rather than an
implementation:

    validate policies against approved metrics  (policy)
    -> filter the series by business calendar   (calendar)
    -> detect meaningful change                 (detect)
    -> investigate evidence                     (evidence)
    -> build a de-duplicable finding            (findings)
    -> rank by impact                           (semantic.intelligence.risk)
    -> decide alerts                            (suppression)

Pure and DB-free. Everything situational -- the tenant's catalog
entries, each metric's series, each metric's dimension breakdowns, the
already-open finding keys -- arrives as an argument, supplied by the
caller from the governed path. Persisting the findings is likewise the
caller's step (`identity.repositories.semantic_intelligence
.upsert_findings`), which is what lets this function be exercised end to
end with no database at all.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from semantic.intelligence.detect import Finding

from config.settings import Settings, get_settings
from kpi_monitoring.calendar import Series
from kpi_monitoring.detect import detect_change
from kpi_monitoring.evidence import Breakdown, investigate
from kpi_monitoring.findings import change_to_finding
from kpi_monitoring.models import (
    AlertBundle,
    AlertPreference,
    BusinessCalendar,
    ChangeEvidence,
    DetectedChange,
    MonitoringPolicy,
    MonitorResult,
    QuietPeriod,
)
from kpi_monitoring.policy import index_catalog_entries, validate_policies
from kpi_monitoring.suppression import decide_alerts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricSeries:
    """One policy's input data.

    `current_breakdown`/`baseline_breakdown` are optional: without them
    the movement is still detected and reported, just with an honest
    "no attribution could be made" -- which is a normal outcome, not a
    degraded one (see `kpi_monitoring.evidence`).
    """

    policy_id: str
    points: Series
    current_breakdown: Breakdown | None = None
    baseline_breakdown: Breakdown | None = None
    value_column: str | None = None


@dataclass(frozen=True)
class MonitorRun:
    """One run's full output: what moved, what it means, and who hears."""

    result: MonitorResult
    findings: tuple[Finding, ...] = ()
    alert_bundles: tuple[AlertBundle, ...] = ()
    changes_by_key: dict[str, DetectedChange] = field(default_factory=dict)


def monitor_kpis(
    *,
    database_id: str,
    policies: Sequence[MonitoringPolicy],
    catalog_entries: Sequence[Any],
    series_by_policy: dict[str, MetricSeries],
    calendar: BusinessCalendar | None = None,
    quiet_periods: Sequence[QuietPeriod] = (),
    preferences: Sequence[AlertPreference] = (),
    previously_open_keys: frozenset[str] = frozenset(),
    caller_roles: tuple[str, ...] = (),
    tenant_id: str | None = None,
    settings: Settings | None = None,
) -> MonitorRun:
    """Runs one monitoring pass.

    Args:
        database_id: Which configured database these metrics belong to
            -- the scope key the findings are stored under.
        policies: The configured monitoring policies.
        catalog_entries: This tenant's semantic-catalog rows/snapshots,
            used to decide which policies target an approved metric.
        series_by_policy: `policy_id -> MetricSeries`. A policy with no
            entry is reported as skipped rather than silently ignored.
        calendar: The tenant's business calendar.
        quiet_periods: Operator-declared silent windows.
        preferences: Alert recipients. Empty means no bundles are built.
        previously_open_keys: Finding keys already open before this run.
        caller_roles: For the restricted-dimension authorization check.
        tenant_id: Audit-log scope only.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        A `MonitorRun`. Findings are returned for the caller to persist;
        nothing is written here.
    """
    settings = settings or get_settings()
    catalog = index_catalog_entries(catalog_entries)
    runnable, violations = validate_policies(
        policies,
        catalog,
        caller_roles=caller_roles,
        tenant_id=tenant_id,
        settings=settings,
    )

    evidence_by_key: dict[str, ChangeEvidence] = {}
    changes_by_key: dict[str, DetectedChange] = {}
    findings: list[Finding] = []
    skipped: list[str] = []

    for policy in runnable:
        metric = catalog.approved[policy.metric_concept_key]
        data = series_by_policy.get(policy.policy_id)
        if data is None or not data.points:
            skipped.append(f"{policy.policy_id}: no series supplied for this policy")
            continue

        change, skip_reason = detect_change(
            policy, metric, data.points, calendar=calendar, settings=settings
        )
        if change is None:
            if skip_reason:
                skipped.append(skip_reason)
            continue

        evidence = investigate(
            change,
            policy,
            current_breakdown=data.current_breakdown,
            baseline_breakdown=data.baseline_breakdown,
            value_column=data.value_column,
            settings=settings,
        )
        finding = change_to_finding(change, evidence)

        evidence_by_key[finding.key] = evidence
        changes_by_key[finding.key] = change
        findings.append(finding)

    # Rank first, then truncate -- so a capped run keeps the most
    # impactful findings rather than whichever policies happened to be
    # configured first.
    findings.sort(key=lambda item: (-item.risk_score, item.key))
    limit = settings.kpi_monitor_max_findings_per_scan
    truncated = len(findings) > limit
    if truncated:
        dropped = findings[limit:]
        logger.info(
            "[kpi_monitor] %d finding(s) beyond the per-scan cap of %d were dropped "
            "(lowest-ranked first)",
            len(dropped),
            limit,
        )
        for finding in dropped:
            evidence_by_key.pop(finding.key, None)
            changes_by_key.pop(finding.key, None)
        findings = findings[:limit]

    bundles = decide_alerts(
        [(changes_by_key[finding.key], finding) for finding in findings],
        preferences,
        calendar=calendar,
        quiet_periods=quiet_periods,
        previously_open_keys=previously_open_keys,
    )

    result = MonitorResult(
        database_id=database_id,
        policies_evaluated=len(runnable),
        series_skipped=tuple(skipped),
        changes=tuple(changes_by_key[finding.key] for finding in findings),
        evidence_by_key=evidence_by_key,
        violations=violations,
        truncated=truncated,
    )
    return MonitorRun(
        result=result,
        findings=tuple(findings),
        alert_bundles=bundles,
        changes_by_key=changes_by_key,
    )


__all__ = ["MetricSeries", "MonitorRun", "monitor_kpis"]
