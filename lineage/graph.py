"""Assembles a full `lineage.models.LineageGraph` from the independently-
reused layers (`lineage.schema_lineage`, `lineage.catalog_lineage`,
`lineage.presentation_lineage`) and provides the end-to-end trace
(upstream/downstream, bounded, cycle-safe) the mission's own "test a KPI
end-to-end" acceptance criterion exercises.

**Edge direction convention, consistent across every layer this module
assembles:** `edge.source_id` always *depends on* `edge.target_id` (a
query `READS` a table, a metric is `GOVERNED_BY` -- i.e. reads -- a
column, a visualization `VISUALIZES` -- i.e. depends on -- a query). This
is what makes the two trace directions below well-defined and uniform
across every node type in the chain, rather than each layer inventing its
own direction convention.

- **upstream** of a node = what it depends on (follow edges *from* this
  node, to their targets) -- "where did this KPI's number come from."
- **downstream** of a node = what depends on it (follow edges *into*
  this node, to their sources) -- impact analysis, "what breaks if this
  table/column changes."

**Authorization is never a redaction of values -- it is node
suppression.** A `COLUMN` node naming a column `config.sensitive_columns`
classifies "restricted" is removed from the graph entirely (never kept
with a placeholder value) for a caller lacking
`Permission.VIEW_RESTRICTED_COLUMNS` -- the identical "withhold outright,
never partially redact" posture `optimization.validator`/
`analytics.predictive` already establish for referencing a restricted
column, applied here to an existence/structure disclosure (a lineage
graph can reveal that a KPI reads a restricted column's *name*, even
without ever revealing a *value* from it) rather than to a data value.
`suppressed_restricted_nodes` discloses what was removed and why, the
same "never a silent omission" posture `DataQualityReport
.insufficient_checks` already establishes.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from semantic.catalog import CatalogEntrySnapshot

from agent.authz import Permission, has_role_permission
from config.sensitive_columns import load_sensitive_columns
from config.settings import Settings
from db.relationship_inference import InferredRelationship
from db.schema_introspection import TableSchemaInfo, get_schema_fingerprint
from lineage.catalog_lineage import build_catalog_lineage
from lineage.models import (
    LINEAGE_ENGINE_VERSION,
    LineageEdge,
    LineageGraph,
    LineageNode,
    LineageNodeType,
    LineageTraceResult,
    LineageTraceStep,
)
from lineage.presentation_lineage import (
    build_insight_lineage,
    build_query_node_and_reads,
    build_recommendation_lineage,
    build_visualization_lineage,
)
from lineage.schema_lineage import build_schema_lineage
from lineage.sql_lineage import extract_column_lineage

_LIMITATIONS: tuple[str, ...] = (
    "A query's own column-level lineage is parsed from its outermost SELECT only -- a "
    "subquery nested inside FROM is not recursed into, and a column read from a CTE is "
    "attributed to the CTE's own name, not traced into the CTE's base tables.",
    "A governed metric's column-level lineage is recovered from its approved_expression text "
    "only when it declares exactly one source table and the expression parses cleanly -- "
    "otherwise this graph falls back to table-level lineage for that metric, matching "
    "data_quality/lineage.py's own disclosed scope boundary.",
    "A composite (multi-column) foreign key is represented only as a table-to-table "
    "relationship (column-level JOINS_VIA edges are single-column only).",
    "Visualization/insight/recommendation nodes describe an already-produced object this "
    "platform does not recompute -- they are provided by the caller, not re-derived here.",
)


def _restricted_column_names(settings: Settings | None) -> frozenset[str]:
    """The identical, independently re-derived helper every other engine's
    own restricted-column gate establishes (`optimization.validator
    ._restricted_column_names`, `analytics.scenario._restricted_column_names`,
    `recommendation.engine._restricted_column_names`) -- a lowercased,
    table-agnostic set of column names classified "restricted" anywhere in
    `config/sensitive_columns.yaml`."""
    del settings  # reserved for a future per-database override; unused today, same as siblings
    classifications = load_sensitive_columns()
    return frozenset(
        column.lower() for (_table, column), tier in classifications.items() if tier == "restricted"
    )


def _suppress_restricted_columns(
    nodes: list[LineageNode],
    edges: list[LineageEdge],
    caller_roles: Sequence[str],
    settings: Settings | None,
) -> tuple[list[LineageNode], list[LineageEdge], tuple[str, ...]]:
    if has_role_permission(tuple(caller_roles), Permission.VIEW_RESTRICTED_COLUMNS):
        return nodes, edges, ()

    restricted = _restricted_column_names(settings)
    if not restricted:
        return nodes, edges, ()

    suppressed_ids = {
        node.id
        for node in nodes
        if node.type == LineageNodeType.COLUMN and (node.column or "").lower() in restricted
    }
    if not suppressed_ids:
        return nodes, edges, ()

    kept_nodes = [node for node in nodes if node.id not in suppressed_ids]
    kept_edges = [
        edge
        for edge in edges
        if edge.source_id not in suppressed_ids and edge.target_id not in suppressed_ids
    ]
    return kept_nodes, kept_edges, tuple(sorted(suppressed_ids))


def build_lineage_graph(
    database_id: str,
    tables: Sequence[TableSchemaInfo],
    catalog_entries: Sequence[CatalogEntrySnapshot] = (),
    inferred: Sequence[InferredRelationship] = (),
    queries: Sequence[Mapping[str, Any]] = (),
    caller_roles: Sequence[str] = (),
    settings: Settings | None = None,
) -> LineageGraph:
    """Assembles the full chain: schema (tables/columns/FKs) + governed
    catalog (metrics/dimensions) +, for each entry in `queries`, that
    query's own read/visualize/explain/recommend tail.

    Args:
        database_id: Which configured database this graph describes.
        tables: Already-introspected schema (never queried here).
        catalog_entries: Already-fetched, real `CatalogEntrySnapshot`s for
            this database (the caller's responsibility to have already
            scoped to the right tenant -- see `api/lineage.py`).
        inferred: Already-computed candidate relationships
            (`db.relationship_inference.infer_relationships`), optional.
        queries: Per-query dicts, each with at least `"sql"`; optionally
            `"fingerprint"`, `"visualization"` (`{"title", "type"}`),
            `"insight"` (`{"text", "grounded_fact_labels"}`), and
            `"recommendations"` (`[{"action", "evidence_count"}, ...]`).
        caller_roles: Resolved server-side roles -- see this module's own
            docstring for the restricted-column suppression this drives.
    """
    schema_nodes, schema_edges = build_schema_lineage(tables, database_id, inferred)
    catalog_nodes, catalog_edges = build_catalog_lineage(catalog_entries)

    all_nodes: list[LineageNode] = [*schema_nodes, *catalog_nodes]
    all_edges: list[LineageEdge] = [*schema_edges, *catalog_edges]

    for query_spec in queries:
        sql = query_spec.get("sql")
        if not sql:
            continue
        fingerprint = query_spec.get("fingerprint")
        lineage_result = extract_column_lineage(sql)
        query_node, read_edges = build_query_node_and_reads(
            sql, database_id, lineage_result, fingerprint
        )
        all_nodes.append(query_node)
        all_edges.extend(read_edges)
        fp = query_node.extra["fingerprint"]

        viz = query_spec.get("visualization")
        if viz:
            v_node, v_edge = build_visualization_lineage(
                fp, viz.get("title", ""), viz.get("type", "chart")
            )
            all_nodes.append(v_node)
            all_edges.append(v_edge)

        insight = query_spec.get("insight")
        if insight:
            i_node, i_edge = build_insight_lineage(
                fp, insight.get("text", ""), insight.get("grounded_fact_labels", ())
            )
            all_nodes.append(i_node)
            all_edges.append(i_edge)

        for idx, rec in enumerate(query_spec.get("recommendations", ())):
            r_node, r_edge = build_recommendation_lineage(
                fp, idx, rec.get("action", ""), int(rec.get("evidence_count", 0))
            )
            all_nodes.append(r_node)
            all_edges.append(r_edge)

    all_nodes, all_edges, suppressed = _suppress_restricted_columns(
        all_nodes, all_edges, caller_roles, settings
    )

    return LineageGraph(
        database_id=database_id,
        nodes=tuple(all_nodes),
        edges=tuple(all_edges),
        schema_fingerprint=get_schema_fingerprint(list(tables)),
        catalog_versions={entry.concept_key: entry.version for entry in catalog_entries},
        suppressed_restricted_nodes=suppressed,
        limitations=_LIMITATIONS,
        engine_version=LINEAGE_ENGINE_VERSION,
    )


def trace_lineage(
    graph: LineageGraph,
    start_node_id: str,
    direction: str = "upstream",
    max_depth: int = 6,
) -> LineageTraceResult:
    """Breadth-first, cycle-safe walk from `start_node_id`.

    `direction="upstream"` follows each node's own outgoing edges (what
    it depends on); `direction="downstream"` follows each node's own
    incoming edges (what depends on it -- impact analysis). See this
    module's own docstring for why this direction convention is uniform
    across every node type.
    """
    if direction not in ("upstream", "downstream"):
        raise ValueError(f"direction must be 'upstream' or 'downstream', got {direction!r}")

    nodes_by_id = {node.id: node for node in graph.nodes}
    if start_node_id not in nodes_by_id:
        return LineageTraceResult(
            start_node_id=start_node_id, direction=direction, max_depth=max_depth, steps=()
        )

    outgoing: dict[str, list[LineageEdge]] = {}
    incoming: dict[str, list[LineageEdge]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source_id, []).append(edge)
        incoming.setdefault(edge.target_id, []).append(edge)
    adjacency = outgoing if direction == "upstream" else incoming

    visited = {start_node_id}
    queue: deque[tuple[str, int]] = deque([(start_node_id, 0)])
    steps: list[LineageTraceStep] = []
    truncated = False

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            if adjacency.get(current_id):
                truncated = True
            continue
        for edge in adjacency.get(current_id, []):
            next_id = edge.target_id if direction == "upstream" else edge.source_id
            if next_id in visited or next_id not in nodes_by_id:
                continue
            visited.add(next_id)
            steps.append(
                LineageTraceStep(node=nodes_by_id[next_id], via_edge=edge, depth=depth + 1)
            )
            queue.append((next_id, depth + 1))

    return LineageTraceResult(
        start_node_id=start_node_id,
        direction=direction,
        max_depth=max_depth,
        steps=tuple(steps),
        truncated=truncated,
    )


__all__ = ["build_lineage_graph", "trace_lineage"]
