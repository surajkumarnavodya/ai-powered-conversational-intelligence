"""Maps a schema-drift finding to what depends on it -- "map impact to
metrics, semantic definitions, golden questions, dashboards, queries and
recommendations through lineage," via genuine reuse of Prompt 42's
`lineage.graph.trace_lineage`, never a second impact-analysis
implementation (unlike `semantic/intelligence/impact.py`'s own
metric-to-metric-only analysis, which this module does not touch or
duplicate -- this one is schema-object-to-anything-downstream, exactly
what `lineage.graph`'s own `JOINS_VIA`/`GOVERNED_BY`/`READS`/`VISUALIZES`/
`EXPLAINS`/`SUPPORTED_BY` edge chain already models).

**The graph passed in must be built from the *previous* schema snapshot**
(the one a drift finding's `REMOVED`/`CHANGED` facts describe), not the
current one -- what we want to know is "what used to depend on the thing
that just changed," which only the previous shape's own lineage nodes can
answer. A finding about something newly `ADDED` correctly gets an empty
impact (nothing could have depended on a column/table that didn't exist
yet) -- `lineage.graph.trace_lineage` already returns an empty result for
a `start_node_id` absent from the graph, so this module needs no special
case for that.

Never mutates a `Finding` in place (it's frozen) -- `dataclasses.replace`
returns a new one carrying `dependents`/`published_count`/an `impacted`
list in `payload`, which `semantic.intelligence.risk.score` (called
afterward by `schema_drift.engine`) reads to compute the final
`risk_score`/`risk_tier`. This module never itself assigns a risk score
or tier -- that stays `risk.score`'s own single responsibility.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from semantic.intelligence.detect import Finding

from agent.provenance import DataTruthLevel
from lineage.graph import trace_lineage
from lineage.models import LineageGraph
from lineage.schema_lineage import column_node_id, table_node_id


def _start_node_id(database_id: str, table: str, column: str | None) -> str:
    if column is None:
        return table_node_id(database_id, table)
    return column_node_id(database_id, table, column)


def assess_finding_impact(
    finding: Finding, graph: LineageGraph, database_id: str, max_depth: int
) -> Finding:
    """Traces downstream from one finding's own table/column node and
    returns an updated `Finding` carrying what it found.

    `finding.payload` must already carry `table`/`column`
    (`schema_drift.findings.fact_to_finding`'s own contract) -- a finding
    from any other source is returned unchanged rather than raising,
    since this function is applied uniformly over a list that could in
    principle mix finding kinds.
    """
    table = finding.payload.get("table")
    if not table:
        return finding
    column = finding.payload.get("column")

    result = trace_lineage(
        graph,
        _start_node_id(database_id, table, column),
        direction="downstream",
        max_depth=max_depth,
    )
    if not result.steps:
        return finding

    impacted: list[dict[str, Any]] = [
        {
            "node_id": step.node.id,
            "type": step.node.type.value,
            "label": step.node.label,
            "truth_level": step.node.truth_level.value,
            "concept_key": step.node.concept_key,
            "concept_version": step.node.concept_version,
            "depth": step.depth,
        }
        for step in result.steps
    ]
    published_count = sum(
        1
        for step in result.steps
        if step.node.truth_level == DataTruthLevel.CONFIRMED_BUSINESS_TRUTH
    )

    return replace(
        finding,
        dependents=len(result.steps),
        published_count=published_count,
        payload={**finding.payload, "impacted": impacted, "impact_truncated": result.truncated},
    )


def assess_impact(
    findings: Sequence[Finding], graph: LineageGraph, database_id: str, max_depth: int
) -> list[Finding]:
    """`assess_finding_impact` applied to every finding, in order."""
    return [assess_finding_impact(finding, graph, database_id, max_depth) for finding in findings]


__all__ = ["assess_finding_impact", "assess_impact"]
