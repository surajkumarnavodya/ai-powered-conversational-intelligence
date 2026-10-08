"""Builds `METRIC`/`DIMENSION`/`ENTITY`/`DOMAIN` nodes and `GOVERNED_BY`
edges from governed semantic-catalog entries (`semantic.catalog
.CatalogEntrySnapshot`) -- never a second query of its own.

**A real improvement over `data_quality/lineage.py`'s own disclosed
table-level-only scope, where the catalog itself makes it possible.**
`SemanticCatalogEntry` has no `source_columns` field (confirmed by
inspection, same citation that module's own docstring already makes) --
but a `METRIC` entry's `approved_expression` *is* a real SQL-expression
string (e.g. `"SUM(SalesAmount) / COUNT(DISTINCT SalesOrderNumber)"`),
and this module deterministically parses it with the same column/
aggregate extraction `lineage.sql_lineage` already uses for a full query,
to recover which specific columns the metric reads -- wherever the
expression names a column unambiguously found in exactly one of the
entry's own declared `source_tables`. Where it can't (an unqualified
column name that exists in more than one declared source table, or an
unparseable expression), this module falls back to the honest,
table-level-only edge `data_quality/lineage.py` already uses -- never a
silent wrong guess.

**Truth level is the catalog's own, reused verbatim.**
`semantic.catalog.status_to_truth_level` is the single source of truth
for "is this confirmed" -- a `published` entry's edges are
`CONFIRMED_BUSINESS_TRUTH`; a `draft`/`reviewed`/`superseded` entry's are
`AI_INFERENCE`. This module never re-derives that mapping.
"""

from __future__ import annotations

from collections.abc import Sequence

from semantic.catalog import CatalogConceptType, CatalogEntrySnapshot, status_to_truth_level

from lineage.models import LineageEdge, LineageEdgeType, LineageNode, LineageNodeType
from lineage.schema_lineage import column_node_id, table_node_id
from lineage.sql_lineage import extract_column_lineage

_NODE_TYPE_BY_CONCEPT: dict[CatalogConceptType, LineageNodeType] = {
    CatalogConceptType.METRIC: LineageNodeType.METRIC,
    CatalogConceptType.DIMENSION: LineageNodeType.DIMENSION,
    # ENTITY/DOMAIN concepts don't have their own lineage node type in
    # this chain (the mission names metrics/dimensions, not every catalog
    # concept type) -- represented as METRIC-shaped nodes would be
    # misleading, so they are skipped by `build_catalog_lineage` below,
    # disclosed via this dict's own incompleteness rather than a silent
    # mislabel.
}


def concept_node_id(entry: CatalogEntrySnapshot) -> str:
    return f"concept:{entry.tenant_id}:{entry.database_id}:{entry.concept_key}"


def _try_column_level_edges(
    entry: CatalogEntrySnapshot,
) -> list[tuple[str, str]] | None:
    """Returns `(table, column)` pairs the metric's own expression
    unambiguously names, resolved against its declared `source_tables`
    -- or `None` when that isn't possible (falls back to table-level)."""
    if not entry.approved_expression or len(entry.source_tables) != 1:
        # Column-level resolution needs exactly one declared source table
        # to disambiguate an unqualified column name against -- more than
        # one source table with an unqualified expression is a genuine
        # ambiguity this module declines to guess through.
        return None
    result = extract_column_lineage(f"SELECT {entry.approved_expression} AS _metric")
    if not result.is_parseable or not result.column_lineage:
        return None
    only_table = entry.source_tables[0]
    pairs: list[tuple[str, str]] = []
    for lineage_entry in result.column_lineage:
        for table_hint, column_name in lineage_entry.source_columns:
            # An unqualified reference (table_hint is None) is safe to
            # resolve here specifically because exactly one source table
            # is declared -- same single-table disambiguation
            # `lineage.sql_lineage._resolve_source_columns` itself applies.
            pairs.append((table_hint or only_table, column_name))
    return pairs or None


def build_catalog_lineage(
    entries: Sequence[CatalogEntrySnapshot],
) -> tuple[list[LineageNode], list[LineageEdge]]:
    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []

    for entry in entries:
        node_type = _NODE_TYPE_BY_CONCEPT.get(entry.concept_type)
        if node_type is None:
            continue
        truth_level = status_to_truth_level(entry.status)
        node_id = concept_node_id(entry)
        nodes.append(
            LineageNode(
                id=node_id,
                type=node_type,
                label=entry.business_name,
                database_id=entry.database_id,
                concept_key=entry.concept_key,
                concept_version=entry.version,
                truth_level=truth_level,
                extra={"status": entry.status.value},
            )
        )

        column_pairs = (
            _try_column_level_edges(entry)
            if entry.concept_type == CatalogConceptType.METRIC
            else None
        )
        if column_pairs:
            for table, column in column_pairs:
                edges.append(
                    LineageEdge(
                        source_id=node_id,
                        target_id=column_node_id(entry.database_id, table, column),
                        type=LineageEdgeType.GOVERNED_BY,
                        truth_level=truth_level,
                        detail=f"governed metric reads {table}.{column} "
                        f"(parsed from approved_expression, v{entry.version})",
                        is_inferred=(truth_level.value == "ai_inference"),
                    )
                )
        else:
            for table in entry.source_tables:
                edges.append(
                    LineageEdge(
                        source_id=node_id,
                        target_id=table_node_id(entry.database_id, table),
                        type=LineageEdgeType.GOVERNED_BY,
                        truth_level=truth_level,
                        detail=f"governed concept declares {table} as a source table "
                        f"(table-level only, v{entry.version})",
                        is_inferred=(truth_level.value == "ai_inference"),
                    )
                )

    return nodes, edges


__all__ = ["build_catalog_lineage", "concept_node_id"]
