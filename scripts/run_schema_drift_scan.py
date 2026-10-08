"""Standalone entry point: a local, no-authentication schema-drift scan
suitable for cron/Task Scheduler alerting -- "continuous schema-drift
detection" at the operator level.

Usage (from repo root, with the venv activated):

    python scripts\\run_schema_drift_scan.py              # every configured database
    python scripts\\run_schema_drift_scan.py --database default

Prints `schema_drift.reports.render_markdown_report`'s own output for
each configured, reachable database and exits non-zero if any finding is
`risk_tier == "high"` -- the literal "alert" mechanism this feature's own
mission calls for, usable as a cron/CI step without a running API server
or a local account.

**Disclosed, narrower than `POST /schema-drift/scan`:** this script never
persists a governed review record (`identity.models.SemanticFinding` --
that needs a real actor/tenant, i.e. a local account) and never assesses
impact against a tenant's published catalog through lineage (same
reason) -- `schema_drift.engine.scan_for_drift` is called with
`previous_lineage_graph=None`, so every finding here is a plain
structural fact, `dependents=0`. It still captures/advances the same
on-disk snapshot (`schema_drift.snapshot_store`) the API route reads, so
alternating between this script and the API route never desyncs the
"previous" baseline either one diffs against.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import configure_logging, get_settings  # noqa: E402
from db.connection import get_read_only_engine, test_connection  # noqa: E402
from db.schema_introspection import introspect_schema  # noqa: E402
from schema_drift.engine import scan_for_drift  # noqa: E402
from schema_drift.reports import render_markdown_report  # noqa: E402
from schema_drift.snapshot_store import load_snapshot, save_snapshot  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        action="append",
        dest="databases",
        help="Scan only this configured database name (repeatable). Default: every configured database.",
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    targets = [c for c in settings.databases if not args.databases or c.name in args.databases]
    if not targets:
        logger.error("No matching configured database to scan.")
        sys.exit(1)

    any_high_risk = False
    any_scanned = False
    for config in targets:
        check = test_connection(config)
        if not check.success:
            logger.warning("Database %r is unreachable, skipping: %s", config.name, check.message)
            continue

        engine = get_read_only_engine(config)
        current_tables = introspect_schema(engine, config.db_schema)
        previous_tables = load_snapshot(config.name, settings)

        result = scan_for_drift(
            config.name,
            previous_tables,
            current_tables,
            previous_lineage_graph=None,
            rename_similarity_threshold=settings.schema_drift_rename_similarity_threshold,
            impact_max_depth=settings.schema_drift_impact_max_trace_depth,
        )
        save_snapshot(config.name, list(current_tables), settings)
        any_scanned = True

        print(render_markdown_report(config.name, result.findings, result.tables_compared))
        if any(f.risk_tier == "high" for f in result.findings):
            any_high_risk = True

    if not any_scanned:
        logger.error("No configured database could be reached for a scan.")
        sys.exit(1)
    if any_high_risk:
        sys.exit(2)


if __name__ == "__main__":
    main()
