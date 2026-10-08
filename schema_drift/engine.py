"""Orchestrates one schema-drift scan: diff -> findings -> impact -> risk.

Pure with respect to this package's own four pieces (`schema_drift.diff`,
`schema_drift.findings`, `schema_drift.impact`, and reused
`semantic.intelligence.risk`) -- no I/O here. `api/schema_drift.py` is the
only place that does I/O (live introspection, loading/saving a snapshot,
persisting findings), passing already-fetched data in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from semantic.intelligence.detect import Finding
from semantic.intelligence.risk import prioritize

from db.schema_introspection import TableSchemaInfo
from lineage.models import LineageGraph
from schema_drift.diff import SchemaDriftFact, compare_schemas
from schema_drift.findings import facts_to_findings
from schema_drift.impact import assess_impact


@dataclass(frozen=True)
class ScanResult:
    database_id: str
    tables_compared: int
    facts: tuple[SchemaDriftFact, ...]
    findings: tuple[Finding, ...]


def scan_for_drift(
    database_id: str,
    previous_tables: Sequence[TableSchemaInfo] | None,
    current_tables: Sequence[TableSchemaInfo],
    previous_lineage_graph: LineageGraph | None,
    rename_similarity_threshold: float,
    impact_max_depth: int,
) -> ScanResult:
    """Runs one full scan.

    Args:
        previous_tables: The prior snapshot (`schema_drift.snapshot_store
            .load_snapshot`), or `None`/empty on a first scan.
        current_tables: A fresh live introspection.
        previous_lineage_graph: A `lineage.graph.build_lineage_graph`
            result built from `previous_tables` (plus this tenant's own
            published catalog entries) -- impact is assessed against what
            used to depend on the previous shape (see `schema_drift
            .impact`'s own docstring for why). `None` skips impact
            assessment entirely (every finding keeps `dependents=0`) --
            a caller with no catalog/lineage data available (e.g. no
            local account) can still get a plain structural diff.
    """
    facts = compare_schemas(
        previous_tables or [],
        current_tables,
        rename_similarity_threshold=rename_similarity_threshold,
    )
    findings = facts_to_findings(database_id, facts)
    if previous_lineage_graph is not None:
        findings = assess_impact(findings, previous_lineage_graph, database_id, impact_max_depth)
    scored = prioritize(findings)
    return ScanResult(
        database_id=database_id,
        tables_compared=len(current_tables),
        facts=tuple(facts),
        findings=tuple(scored),
    )


__all__ = ["ScanResult", "scan_for_drift"]
