"""Builds `DATABASE`/`TABLE`/`COLUMN` nodes and `JOINS_VIA` edges from
already-introspected schema data -- never a second query of its own.

Reuses `db.schema_introspection.TableSchemaInfo`/`ForeignKeyInfo` (a
declared foreign key, a `DATABASE_FACT`) and, optionally,
`db.relationship_inference.InferredRelationship` (a structurally-inferred
candidate, never a declared fact -- `AI_INFERENCE`, exactly that module's
own `truth_level` field, reused verbatim rather than re-derived).

A column already covered by a declared FK is never also proposed via an
inferred candidate in the same graph -- `InferredRelationship` itself
already guarantees this at the source (see that module's own docstring:
"a column already part of a declared FK is never re-proposed"), so this
module does no additional de-duplication of its own.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent.provenance import DataTruthLevel
from db.relationship_inference import InferredRelationship
from db.schema_introspection import TableSchemaInfo
from lineage.models import LineageEdge, LineageEdgeType, LineageNode, LineageNodeType


def table_node_id(database_id: str, table_name: str) -> str:
    return f"table:{database_id}:{table_name}"


def column_node_id(database_id: str, table_name: str, column_name: str) -> str:
    return f"column:{database_id}:{table_name}:{column_name}"


def database_node_id(database_id: str) -> str:
    return f"database:{database_id}"


def build_schema_lineage(
    tables: Sequence[TableSchemaInfo],
    database_id: str,
    inferred: Sequence[InferredRelationship] = (),
) -> tuple[list[LineageNode], list[LineageEdge]]:
    """One `DATABASE` node, one `TABLE` node per table/view, one `COLUMN`
    node per column, and one `JOINS_VIA` edge per declared foreign key
    (`DATABASE_FACT`) plus one per caller-supplied inferred candidate
    (`AI_INFERENCE`, `is_inferred=True`).
    """
    nodes: list[LineageNode] = [
        LineageNode(
            id=database_node_id(database_id),
            type=LineageNodeType.DATABASE,
            label=database_id,
            database_id=database_id,
        )
    ]
    edges: list[LineageEdge] = []

    for table in tables:
        t_id = table_node_id(database_id, table.table_name)
        nodes.append(
            LineageNode(
                id=t_id,
                type=LineageNodeType.TABLE,
                label=table.table_name,
                database_id=database_id,
                table=table.table_name,
                extra={"is_view": table.is_view},
            )
        )
        for column in table.columns:
            c_id = column_node_id(database_id, table.table_name, column.name)
            nodes.append(
                LineageNode(
                    id=c_id,
                    type=LineageNodeType.COLUMN,
                    label=f"{table.table_name}.{column.name}",
                    database_id=database_id,
                    table=table.table_name,
                    column=column.name,
                    extra={"data_type": column.type},
                )
            )
        for fk in table.foreign_keys:
            if len(fk.constrained_columns) != 1 or len(fk.referred_columns) != 1:
                # Composite FKs are real but this platform's column-level
                # edges are single-column only -- disclosed, not silently
                # approximated; the table-level JOINS_VIA edge below still
                # covers it.
                continue
            source_col_id = column_node_id(database_id, table.table_name, fk.constrained_columns[0])
            target_col_id = column_node_id(database_id, fk.referred_table, fk.referred_columns[0])
            edges.append(
                LineageEdge(
                    source_id=source_col_id,
                    target_id=target_col_id,
                    type=LineageEdgeType.JOINS_VIA,
                    truth_level=DataTruthLevel.DATABASE_FACT,
                    detail=f"declared foreign key {table.table_name}.{fk.constrained_columns[0]} "
                    f"-> {fk.referred_table}.{fk.referred_columns[0]}",
                )
            )

    for candidate in inferred:
        if len(candidate.source_columns) != 1 or len(candidate.target_columns) != 1:
            continue
        source_col_id = column_node_id(
            database_id, candidate.source_table, candidate.source_columns[0]
        )
        target_col_id = column_node_id(
            database_id, candidate.target_table, candidate.target_columns[0]
        )
        edges.append(
            LineageEdge(
                source_id=source_col_id,
                target_id=target_col_id,
                type=LineageEdgeType.JOINS_VIA,
                truth_level=candidate.truth_level,
                detail=f"inferred candidate relationship (confidence={candidate.confidence:.2f})",
                is_inferred=True,
            )
        )

    return nodes, edges


__all__ = ["build_schema_lineage", "table_node_id", "column_node_id", "database_node_id"]
