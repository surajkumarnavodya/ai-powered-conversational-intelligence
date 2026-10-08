"""Builds the `QUERY` -> `VISUALIZATION`/`INSIGHT`/`RECOMMENDATION` tail
of the lineage chain -- the part of the mission's own named chain this
platform never recomputes, only *describes*: a chart spec
(`analytics.visualization.ChartSpec`), an insight sentence
(`agent.llm_client.generate_insight_from_llm`), and a recommendation
(`recommendation.models.Recommendation`) are each already produced,
already tested, elsewhere in this codebase. This module only connects an
already-existing query node to an already-produced downstream object's
own lineage node -- it never builds or re-derives any of those objects
itself, and never calls an LLM.

Every function here takes plain, primitive-shaped input (not the real
`ChartSpec`/`Recommendation` types) deliberately: the caller already has
the real object from a prior `/execute`/`/ask` response and only needs to
hand this module the few descriptive fields lineage actually needs --
mirroring `data_quality/lineage.py`'s own `TableFindingsSummary` precedent
of "a lightweight summary, never the full object re-transmitted."
"""

from __future__ import annotations

from collections.abc import Sequence

from agent.provenance import DataTruthLevel
from lineage.models import LineageEdge, LineageEdgeType, LineageNode, LineageNodeType
from lineage.schema_lineage import column_node_id, table_node_id
from lineage.sql_lineage import SqlLineageResult, query_fingerprint


def query_node_id(fingerprint: str) -> str:
    return f"query:{fingerprint}"


def visualization_node_id(fingerprint: str) -> str:
    return f"visualization:{fingerprint}"


def insight_node_id(fingerprint: str) -> str:
    return f"insight:{fingerprint}"


def recommendation_node_id(fingerprint: str, index: int) -> str:
    return f"recommendation:{fingerprint}:{index}"


def build_query_node_and_reads(
    sql: str,
    database_id: str,
    lineage_result: SqlLineageResult,
    fingerprint: str | None = None,
) -> tuple[LineageNode, list[LineageEdge]]:
    """One `QUERY` node, plus one `READS` edge per real table/column the
    deterministic parse (`lineage.sql_lineage.extract_column_lineage`)
    found -- `DATABASE_FACT`, since this is a parse of the query's own
    text, never a guess about what it does.

    The query's own per-output-column derivation detail (which source
    column(s), via what transform) is carried on the node's own `extra`
    field rather than as separate pseudo-nodes per output column -- kept
    deliberately this way, as a disclosed, practical bound on graph size:
    a query's "columns" are a property of *this one query*, not a durable
    object a future lineage trace needs to walk through as its own node.
    """
    fp = fingerprint or query_fingerprint(sql)
    q_id = query_node_id(fp)
    node = LineageNode(
        id=q_id,
        type=LineageNodeType.QUERY,
        label=f"query:{fp[:8]}",
        database_id=database_id,
        truth_level=DataTruthLevel.DATABASE_FACT,
        extra={
            "fingerprint": fp,
            "is_parseable": lineage_result.is_parseable,
            "tables_read": list(lineage_result.tables_read),
            "column_lineage": [
                entry.model_dump(mode="json") for entry in lineage_result.column_lineage
            ],
        },
    )
    edges: list[LineageEdge] = []
    for table in lineage_result.tables_read:
        edges.append(
            LineageEdge(
                source_id=q_id,
                target_id=table_node_id(database_id, table),
                type=LineageEdgeType.READS,
                truth_level=DataTruthLevel.DATABASE_FACT,
                detail="deterministically parsed from the query's own FROM/JOIN clauses",
            )
        )
    seen_columns: set[tuple[str, str]] = set()
    for entry in lineage_result.column_lineage:
        for source_table, column in entry.source_columns:
            if source_table is None or (source_table, column) in seen_columns:
                continue
            seen_columns.add((source_table, column))
            edges.append(
                LineageEdge(
                    source_id=q_id,
                    target_id=column_node_id(database_id, source_table, column),
                    type=LineageEdgeType.READS,
                    truth_level=DataTruthLevel.DATABASE_FACT,
                    detail=f"derives output column '{entry.output_column}'"
                    + (f" via {entry.transform}" if entry.transform else ""),
                )
            )
    return node, edges


def build_visualization_lineage(
    fingerprint: str, chart_title: str, chart_type: str
) -> tuple[LineageNode, LineageEdge]:
    """A chart specification is deterministically computed from a query's
    own real result (`analytics.visualization.build_chart_spec`, no LLM
    involved) -- `DATABASE_FACT`."""
    v_id = visualization_node_id(fingerprint)
    node = LineageNode(
        id=v_id,
        type=LineageNodeType.VISUALIZATION,
        label=chart_title or chart_type,
        truth_level=DataTruthLevel.DATABASE_FACT,
        extra={"chart_type": chart_type},
    )
    edge = LineageEdge(
        source_id=v_id,
        target_id=query_node_id(fingerprint),
        type=LineageEdgeType.VISUALIZES,
        truth_level=DataTruthLevel.DATABASE_FACT,
        detail="chart spec computed deterministically from this query's own result",
    )
    return node, edge


def build_insight_lineage(
    fingerprint: str, insight_text: str, grounded_fact_labels: Sequence[str] = ()
) -> tuple[LineageNode, LineageEdge]:
    """An LLM-narrated insight sentence -- always `AI_INFERENCE`, never
    presented as fact, per master rule 10. `grounded_fact_labels` names
    the specific `DATABASE_FACT` numbers it cites (mirroring
    `agent.provenance.ProvenancedClaim.grounded_in`), disclosed on the
    edge rather than asserted as if the insight itself were a fact."""
    i_id = insight_node_id(fingerprint)
    node = LineageNode(
        id=i_id,
        type=LineageNodeType.INSIGHT,
        label=insight_text[:120],
        truth_level=DataTruthLevel.AI_INFERENCE,
        extra={"grounded_in": list(grounded_fact_labels)},
    )
    edge = LineageEdge(
        source_id=i_id,
        target_id=query_node_id(fingerprint),
        type=LineageEdgeType.EXPLAINS,
        truth_level=DataTruthLevel.AI_INFERENCE,
        detail="LLM-narrated explanation of this query's own result -- an inference, not a fact",
    )
    return node, edge


def build_recommendation_lineage(
    fingerprint: str, index: int, action: str, evidence_count: int
) -> tuple[LineageNode, LineageEdge]:
    """A recommendation is always `AI_INFERENCE`
    (`recommendation.models.Recommendation`'s own invariant) even though
    its `evidence` tuple must itself be `DATABASE_FACT`/
    `CONFIRMED_BUSINESS_TRUTH` -- that asymmetry (an inference grounded in
    facts, never the reverse) is exactly master rule 10, carried through
    to this lineage edge rather than collapsed."""
    r_id = recommendation_node_id(fingerprint, index)
    node = LineageNode(
        id=r_id,
        type=LineageNodeType.RECOMMENDATION,
        label=action[:120],
        truth_level=DataTruthLevel.AI_INFERENCE,
        extra={"evidence_count": evidence_count},
    )
    edge = LineageEdge(
        source_id=r_id,
        target_id=query_node_id(fingerprint),
        type=LineageEdgeType.SUPPORTED_BY,
        truth_level=DataTruthLevel.AI_INFERENCE,
        detail=(
            f"recommendation grounded in {evidence_count} fact(s) from this query's own result"
            if evidence_count
            else "recommendation with no specific numeric grounding"
        ),
    )
    return node, edge


__all__ = [
    "query_node_id",
    "visualization_node_id",
    "insight_node_id",
    "recommendation_node_id",
    "build_query_node_and_reads",
    "build_visualization_lineage",
    "build_insight_lineage",
    "build_recommendation_lineage",
]
