"""Standalone entry point: a local, no-authentication KPI monitoring run
suitable for cron/Task Scheduler alerting -- "proactive monitoring" at
the operator level.

Usage (from repo root, with the venv activated):

    python scripts\\run_kpi_monitor.py --input monitoring.json
    python scripts\\run_kpi_monitor.py --input monitoring.json --quiet-ok

The input file is a JSON document with the same shape
`POST /kpi-monitoring/scan` takes, plus the series themselves:

    {
      "database_id": "default",
      "catalog_metrics": [
        {"concept_key": "revenue", "business_name": "Net Revenue"}
      ],
      "policies": [
        {"policy_id": "p-revenue", "metric_concept_key": "revenue",
         "comparison": "previous_period", "min_change_percent": 10.0,
         "dimension_columns": ["region"]}
      ],
      "series": [
        {"policy_id": "p-revenue",
         "points": [["2026-05", 1000.0], ["2026-06", 640.0]],
         "value_column": "revenue",
         "current_breakdown": {"columns": ["region", "revenue"],
                               "rows": [["East", 160.0]]},
         "baseline_breakdown": {"columns": ["region", "revenue"],
                                "rows": [["East", 500.0]]}}
      ],
      "calendar": {"name": "acme", "non_business_periods": ["2026-12"]},
      "quiet_periods": [{"name": "freeze", "periods": ["2026-07"]}],
      "preferences": [{"user_id": "ops", "min_risk_tier": "medium"}]
    }

Prints `kpi_monitoring.reports.render_markdown` and exits `2` if any
finding is `risk_tier == "high"` -- the literal alert mechanism, usable
as a cron/CI step without a running API server or a local account. The
same exit-code convention `scripts/run_schema_drift_scan.py` already
uses.

**Disclosed, and narrower than `POST /kpi-monitoring/scan`:**

- It **never persists a governed review record**. Writing to
  `identity.models.SemanticFinding` needs a real actor and tenant, i.e.
  a local account, which a cron job does not have. Findings here are
  printed, not filed -- so duplicate suppression's *storage* half does
  not apply, and a run repeats its output until the underlying data
  changes. The API route is the path that remembers.
- It takes `catalog_metrics` from the input file rather than reading the
  tenant's published catalog, for the same reason. An operator is
  asserting "these are my approved metrics" in a file the operator
  controls; the API route resolves that from the governed catalog
  instead, which is the stronger guarantee of the two.
- It never queries a database. The series are supplied, exactly as they
  are to the API route -- see `kpi_monitoring/__init__.py` for why that
  boundary is structural.

The file is read as plain data: no expression in it is ever evaluated,
and a metric key that does not appear in `catalog_metrics` is refused,
not monitored.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import configure_logging, get_settings  # noqa: E402
from kpi_monitoring.engine import MetricSeries, monitor_kpis  # noqa: E402
from kpi_monitoring.models import (  # noqa: E402
    AlertPreference,
    BusinessCalendar,
    ComparisonPeriod,
    MonitoringPolicy,
    QuietPeriod,
)
from kpi_monitoring.reports import render_markdown  # noqa: E402

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_BAD_INPUT = 1
EXIT_HIGH_RISK_FINDING = 2


class _CatalogMetric:
    """A published metric, as asserted by the input file.

    Shaped to satisfy `kpi_monitoring.policy.index_catalog_entries`'
    duck-typed projection -- the same attributes a real
    `identity.models.SemanticCatalogEntry` row exposes -- so the CLI and
    the API route share one validation path rather than two.
    """

    def __init__(self, raw: dict[str, Any]) -> None:
        self.concept_key = str(raw.get("concept_key", ""))
        self.concept_type = str(raw.get("concept_type", "metric"))
        self.status = str(raw.get("status", "published"))
        self.business_name = str(raw.get("business_name", "") or self.concept_key)
        self.dimensions = tuple(raw.get("dimensions", ()) or ())
        self.version = int(raw.get("version", 1) or 1)


def _breakdown(raw: dict[str, Any] | None) -> tuple[list[str], list[tuple]] | None:
    if not raw:
        return None
    return (
        [str(column) for column in raw.get("columns", [])],
        [tuple(row) for row in raw.get("rows", [])],
    )


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("the input file must contain a JSON object")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to the JSON monitoring definition.")
    parser.add_argument(
        "--quiet-ok",
        action="store_true",
        help="Exit 0 even when a high-risk finding is present (report only).",
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()

    if not settings.enable_kpi_monitoring:
        print("KPI monitoring is disabled (ENABLE_KPI_MONITORING=false).")
        sys.exit(EXIT_OK)

    path = Path(args.input)
    if not path.exists():
        print(f"Input file not found: {path}")
        sys.exit(EXIT_BAD_INPUT)

    try:
        document = _load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # The filename is the operator's own and safe to print; the
        # exception type is enough to act on without echoing file
        # contents into a log.
        print(f"Could not read {path}: {type(exc).__name__}")
        sys.exit(EXIT_BAD_INPUT)

    try:
        policies = tuple(
            MonitoringPolicy(
                policy_id=str(raw["policy_id"]),
                metric_concept_key=str(raw["metric_concept_key"]),
                comparison=ComparisonPeriod(raw.get("comparison", "previous_period")),
                min_change_percent=float(
                    raw.get("min_change_percent", settings.kpi_monitor_min_change_percent)
                ),
                lower_threshold=raw.get("lower_threshold"),
                upper_threshold=raw.get("upper_threshold"),
                seasonal_lag=int(raw.get("seasonal_lag", 12)),
                trailing_window=int(raw.get("trailing_window", 4)),
                dimension_columns=tuple(raw.get("dimension_columns", ()) or ()),
                enabled=bool(raw.get("enabled", True)),
            )
            for raw in document.get("policies", [])
        )
        series = {
            str(raw["policy_id"]): MetricSeries(
                policy_id=str(raw["policy_id"]),
                points=tuple((str(period), float(value)) for period, value in raw["points"]),
                current_breakdown=_breakdown(raw.get("current_breakdown")),
                baseline_breakdown=_breakdown(raw.get("baseline_breakdown")),
                value_column=raw.get("value_column"),
            )
            for raw in document.get("series", [])
        }
        calendar_raw = document.get("calendar")
        calendar = (
            BusinessCalendar(
                name=str(calendar_raw.get("name", "default")),
                non_business_periods=frozenset(calendar_raw.get("non_business_periods", ())),
            )
            if calendar_raw
            else None
        )
        quiet_periods = tuple(
            QuietPeriod(
                name=str(raw.get("name", "quiet")),
                periods=frozenset(raw.get("periods", ())),
                reason=str(raw.get("reason", "")),
            )
            for raw in document.get("quiet_periods", [])
        )
        preferences = tuple(
            AlertPreference(
                user_id=str(raw.get("user_id", "operator")),
                min_risk_tier=raw.get("min_risk_tier", "low"),
                muted_metric_keys=frozenset(raw.get("muted_metric_keys", ())),
                max_alerts_per_run=int(raw.get("max_alerts_per_run", 20)),
            )
            for raw in document.get("preferences", [])
        ) or (AlertPreference(user_id="operator"),)
        catalog = [_CatalogMetric(raw) for raw in document.get("catalog_metrics", [])]
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Malformed monitoring definition: {type(exc).__name__}: {exc}")
        sys.exit(EXIT_BAD_INPUT)

    run = monitor_kpis(
        database_id=str(document.get("database_id", "default")),
        policies=policies,
        catalog_entries=catalog,
        series_by_policy=series,
        calendar=calendar,
        quiet_periods=quiet_periods,
        preferences=preferences,
        settings=settings,
    )

    print(render_markdown(run))

    high_risk = [finding for finding in run.findings if finding.risk_tier == "high"]
    if high_risk and not args.quiet_ok:
        print(f"\n{len(high_risk)} high-risk finding(s) -- exiting {EXIT_HIGH_RISK_FINDING}.")
        sys.exit(EXIT_HIGH_RISK_FINDING)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
