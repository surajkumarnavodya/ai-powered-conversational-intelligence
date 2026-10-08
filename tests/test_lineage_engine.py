"""Unit tests for the `lineage/` package (Prompt 42,
`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`): deterministic SQL/expression
parsing, schema/catalog/presentation-layer assembly, end-to-end trace,
restricted-column suppression, and staleness detection.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from semantic.catalog import CatalogConceptType, CatalogEntrySnapshot, CatalogStatus

from agent.provenance import DataTruthLevel
from db.relationship_inference import InferredRelationship, RelationshipEvidence
from db.schema_introspection import (
    ColumnInfo,
    ForeignKeyInfo,
    TableSchemaInfo,
    get_schema_fingerprint,
)
from lineage.catalog_lineage import build_catalog_lineage, concept_node_id
from lineage.graph import build_lineage_graph, trace_lineage
from lineage.models import LineageEdgeType, LineageGraph, LineageNodeType
from lineage.presentation_lineage import (
    build_insight_lineage,
    build_query_node_and_reads,
    build_recommendation_lineage,
    build_visualization_lineage,
    insight_node_id,
    query_node_id,
    recommendation_node_id,
    visualization_node_id,
)
from lineage.schema_lineage import build_schema_lineage, column_node_id, table_node_id
from lineage.sql_lineage import extract_column_lineage, query_fingerprint
from lineage.staleness import detect_staleness


def _fact_sales_tables() -> list[TableSchemaInfo]:
    return [
        TableSchemaInfo(
            table_name="FactSales",
            columns=(
                ColumnInfo("SalesAmount", "decimal", True, False),
                ColumnInfo("ProductKey", "int", False, False),
                ColumnInfo("OrderDate", "date", False, False),
            ),
            foreign_keys=(ForeignKeyInfo(("ProductKey",), "DimProduct", ("ProductKey",)),),
            ddl="CREATE TABLE FactSales (...)",
        ),
        TableSchemaInfo(
            table_name="DimProduct",
            columns=(
                ColumnInfo("ProductKey", "int", False, True),
                ColumnInfo("ProductName", "text", False, False),
            ),
            foreign_keys=(),
            ddl="CREATE TABLE DimProduct (...)",
        ),
    ]


def _revenue_metric(**overrides: object) -> CatalogEntrySnapshot:
    base = dict(
        id="e1",
        tenant_id="tenant-a",
        database_id="adventureworks",
        concept_type=CatalogConceptType.METRIC,
        concept_key="total_revenue",
        business_name="Total Revenue",
        status=CatalogStatus.PUBLISHED,
        version=2,
        approved_expression="SUM(SalesAmount)",
        source_tables=("FactSales",),
    )
    base.update(overrides)
    return CatalogEntrySnapshot(**base)


class TestSqlLineage:
    def test_a_simple_join_aggregate_query_resolves_column_level_lineage(self):
        sql = (
            "SELECT p.ProductName, SUM(f.SalesAmount) AS TotalRevenue "
            "FROM FactSales f JOIN DimProduct p ON f.ProductKey = p.ProductKey "
            "GROUP BY p.ProductName"
        )
        result = extract_column_lineage(sql)
        assert result.is_parseable
        assert set(result.tables_read) == {"FactSales", "DimProduct"}
        by_output = {e.output_column: e for e in result.column_lineage}
        assert by_output["ProductName"].source_columns == (("DimProduct", "ProductName"),)
        assert by_output["TotalRevenue"].source_columns == (("FactSales", "SalesAmount"),)
        assert by_output["TotalRevenue"].transform == "SUM"
        assert not by_output["TotalRevenue"].is_ambiguous

    def test_an_unqualified_column_with_one_table_resolves_unambiguously(self):
        result = extract_column_lineage("SELECT SUM(SalesAmount) AS Total FROM FactSales")
        assert result.is_parseable
        entry = result.column_lineage[0]
        assert entry.source_columns == (("FactSales", "SalesAmount"),)
        assert not entry.is_ambiguous

    def test_an_unqualified_column_with_two_tables_is_disclosed_as_ambiguous(self):
        result = extract_column_lineage("SELECT SUM(Amount) AS Total FROM FactSales, FactReturns")
        assert result.is_parseable
        entry = result.column_lineage[0]
        assert entry.is_ambiguous
        assert any(table is None for table, _ in entry.source_columns)

    def test_a_cte_name_is_excluded_from_tables_read_and_not_traced_further(self):
        sql = "WITH recent AS (SELECT * FROM FactSales) SELECT SUM(SalesAmount) FROM recent"
        result = extract_column_lineage(sql)
        assert result.is_parseable
        assert "recent" not in result.tables_read
        assert "recent" in result.cte_names

    def test_unparseable_sql_fails_open_never_raises(self):
        result = extract_column_lineage("SELEC FROM WHERE :::")
        assert result.is_parseable is False
        assert result.column_lineage == ()

    def test_fingerprint_is_deterministic(self):
        sql = "SELECT 1"
        assert query_fingerprint(sql) == query_fingerprint(sql)
        assert query_fingerprint(sql) != query_fingerprint("SELECT 2")


class TestSchemaLineage:
    def test_a_declared_fk_is_a_database_fact_joins_via_edge(self):
        nodes, edges = build_schema_lineage(_fact_sales_tables(), "adventureworks")
        fk_edges = [e for e in edges if e.type == LineageEdgeType.JOINS_VIA]
        assert len(fk_edges) == 1
        edge = fk_edges[0]
        assert edge.truth_level == DataTruthLevel.DATABASE_FACT
        assert edge.is_inferred is False
        assert edge.source_id == column_node_id("adventureworks", "FactSales", "ProductKey")
        assert edge.target_id == column_node_id("adventureworks", "DimProduct", "ProductKey")

    def test_an_inferred_candidate_is_ai_inference_and_flagged(self):
        candidate = InferredRelationship(
            source_table="FactSales",
            source_columns=("OrderDate",),
            target_table="DimDate",
            target_columns=("DateKey",),
            relationship_type="many_to_one",
            confidence=0.7,
            evidence=(RelationshipEvidence("name_similarity", 0.7, "..."),),
        )
        nodes, edges = build_schema_lineage(_fact_sales_tables(), "adventureworks", [candidate])
        inferred_edges = [e for e in edges if e.is_inferred]
        assert len(inferred_edges) == 1
        assert inferred_edges[0].truth_level == DataTruthLevel.AI_INFERENCE

    def test_every_table_and_column_gets_a_node(self):
        nodes, _ = build_schema_lineage(_fact_sales_tables(), "adventureworks")
        table_nodes = [n for n in nodes if n.type == LineageNodeType.TABLE]
        column_nodes = [n for n in nodes if n.type == LineageNodeType.COLUMN]
        assert len(table_nodes) == 2
        assert len(column_nodes) == 5  # 3 + 2
        assert table_node_id("adventureworks", "FactSales") in {n.id for n in table_nodes}


class TestCatalogLineage:
    def test_a_published_metric_with_one_source_table_resolves_column_level(self):
        nodes, edges = build_catalog_lineage([_revenue_metric()])
        metric_node = nodes[0]
        assert metric_node.type == LineageNodeType.METRIC
        assert metric_node.truth_level == DataTruthLevel.CONFIRMED_BUSINESS_TRUTH
        assert metric_node.concept_version == 2
        assert len(edges) == 1
        assert edges[0].target_id == column_node_id("adventureworks", "FactSales", "SalesAmount")
        assert edges[0].truth_level == DataTruthLevel.CONFIRMED_BUSINESS_TRUTH
        assert edges[0].is_inferred is False

    def test_a_draft_metric_is_ai_inference_and_falls_back_to_table_level_with_two_sources(self):
        entry = _revenue_metric(
            status=CatalogStatus.DRAFT, source_tables=("FactSales", "FactReturns")
        )
        nodes, edges = build_catalog_lineage([entry])
        assert nodes[0].truth_level == DataTruthLevel.AI_INFERENCE
        assert len(edges) == 2  # table-level, one per declared source table
        assert edges[0].is_inferred is True
        assert all(e.target_id.startswith("table:") for e in edges)

    def test_a_metric_with_no_approved_expression_falls_back_to_table_level(self):
        entry = _revenue_metric(approved_expression=None)
        _, edges = build_catalog_lineage([entry])
        assert len(edges) == 1
        assert edges[0].target_id == table_node_id("adventureworks", "FactSales")

    def test_an_entity_concept_type_produces_no_node(self):
        entry = _revenue_metric(concept_type=CatalogConceptType.ENTITY, concept_key="customer")
        nodes, edges = build_catalog_lineage([entry])
        assert nodes == []
        assert edges == []

    def test_concept_node_id_is_deterministic(self):
        entry = _revenue_metric()
        assert concept_node_id(entry) == concept_node_id(entry)


class TestPresentationLineage:
    def test_query_node_carries_reads_edges_for_every_real_table_and_column(self):
        sql = "SELECT SUM(SalesAmount) AS Total FROM FactSales"
        lineage_result = extract_column_lineage(sql)
        node, edges = build_query_node_and_reads(sql, "adventureworks", lineage_result)
        assert node.type == LineageNodeType.QUERY
        assert node.truth_level == DataTruthLevel.DATABASE_FACT
        targets = {e.target_id for e in edges}
        assert table_node_id("adventureworks", "FactSales") in targets
        assert column_node_id("adventureworks", "FactSales", "SalesAmount") in targets

    def test_visualization_insight_recommendation_each_point_back_to_the_query(self):
        fp = "abc123"
        v_node, v_edge = build_visualization_lineage(fp, "Revenue", "bar")
        i_node, i_edge = build_insight_lineage(fp, "Revenue grew.", ["Total=100"])
        r_node, r_edge = build_recommendation_lineage(fp, 0, "Investigate", 1)

        assert v_node.id == visualization_node_id(fp)
        assert v_edge.target_id == query_node_id(fp)
        assert v_edge.truth_level == DataTruthLevel.DATABASE_FACT

        assert i_node.id == insight_node_id(fp)
        assert i_edge.target_id == query_node_id(fp)
        assert i_node.truth_level == DataTruthLevel.AI_INFERENCE
        assert i_edge.truth_level == DataTruthLevel.AI_INFERENCE

        assert r_node.id == recommendation_node_id(fp, 0)
        assert r_edge.target_id == query_node_id(fp)
        assert r_node.truth_level == DataTruthLevel.AI_INFERENCE


class TestBuildLineageGraphAndTrace:
    def test_a_governed_kpi_traces_end_to_end_from_source_column_to_recommendation(self):
        """The mission's own acceptance criterion: a KPI traces end to
        end, upstream to its real source column and downstream to its own
        visualization/insight/recommendation."""
        sql = (
            "SELECT p.ProductName, SUM(f.SalesAmount) AS TotalRevenue "
            "FROM FactSales f JOIN DimProduct p ON f.ProductKey = p.ProductKey "
            "GROUP BY p.ProductName"
        )
        g = build_lineage_graph(
            "adventureworks",
            _fact_sales_tables(),
            [_revenue_metric()],
            queries=[
                {
                    "sql": sql,
                    "visualization": {"title": "Revenue by product", "type": "bar"},
                    "insight": {
                        "text": "Revenue grew.",
                        "grounded_fact_labels": ["TotalRevenue=100"],
                    },
                    "recommendations": [{"action": "Investigate top product", "evidence_count": 1}],
                }
            ],
            caller_roles=("admin",),
        )

        metric_id = next(n.id for n in g.nodes if n.type == LineageNodeType.METRIC)
        upstream = trace_lineage(g, metric_id, "upstream")
        assert any(step.node.type == LineageNodeType.COLUMN for step in upstream.steps)
        assert any(
            step.node.table == "FactSales" and step.node.column == "SalesAmount"
            for step in upstream.steps
        )

        query_id = next(n.id for n in g.nodes if n.type == LineageNodeType.QUERY)
        downstream = trace_lineage(g, query_id, "downstream")
        downstream_types = {step.node.type for step in downstream.steps}
        assert downstream_types == {
            LineageNodeType.VISUALIZATION,
            LineageNodeType.INSIGHT,
            LineageNodeType.RECOMMENDATION,
        }
        # Every recommendation/insight stays disclosed as an inference, never
        # promoted, even reached transitively through a trace.
        for step in downstream.steps:
            if step.node.type in (LineageNodeType.INSIGHT, LineageNodeType.RECOMMENDATION):
                assert step.node.truth_level == DataTruthLevel.AI_INFERENCE

    def test_trace_is_cycle_safe_and_bounded_by_max_depth(self):
        g = LineageGraph(
            database_id="d",
            nodes=(),
            edges=(),
        )
        # Build a graph with a cycle A->B->A plus real node objects.
        from lineage.models import LineageEdge, LineageNode

        a = LineageNode(
            id="a", type=LineageNodeType.TABLE, label="a", truth_level=DataTruthLevel.DATABASE_FACT
        )
        b = LineageNode(
            id="b", type=LineageNodeType.TABLE, label="b", truth_level=DataTruthLevel.DATABASE_FACT
        )
        edge_ab = LineageEdge(
            source_id="a",
            target_id="b",
            type=LineageEdgeType.READS,
            truth_level=DataTruthLevel.DATABASE_FACT,
        )
        edge_ba = LineageEdge(
            source_id="b",
            target_id="a",
            type=LineageEdgeType.READS,
            truth_level=DataTruthLevel.DATABASE_FACT,
        )
        g = LineageGraph(database_id="d", nodes=(a, b), edges=(edge_ab, edge_ba))

        result = trace_lineage(g, "a", "upstream", max_depth=10)
        # Must terminate (cycle-safe) and visit "b" exactly once.
        assert [s.node.id for s in result.steps] == ["b"]

    def test_trace_from_an_unknown_node_returns_no_steps(self):
        g = build_lineage_graph("adventureworks", _fact_sales_tables())
        result = trace_lineage(g, "does-not-exist", "upstream")
        assert result.steps == ()

    def test_restricted_column_is_suppressed_for_a_caller_without_permission(self):
        tables = [
            TableSchemaInfo(
                table_name="Employee",
                columns=(
                    ColumnInfo("Salary", "decimal", False, False),
                    ColumnInfo("Name", "text", False, False),
                ),
                foreign_keys=(),
                ddl="x",
            )
        ]
        with patch(
            "lineage.graph.load_sensitive_columns",
            return_value={("Employee", "Salary"): "restricted"},
        ):
            viewer_graph = build_lineage_graph("hr", tables, caller_roles=("user",))
            analyst_graph = build_lineage_graph("hr", tables, caller_roles=("analyst",))

        assert (
            column_node_id("hr", "Employee", "Salary") in viewer_graph.suppressed_restricted_nodes
        )
        assert not any(n.column == "Salary" for n in viewer_graph.nodes)
        assert analyst_graph.suppressed_restricted_nodes == ()
        assert any(n.column == "Salary" for n in analyst_graph.nodes)

    def test_graph_records_schema_fingerprint_and_catalog_versions(self):
        tables = _fact_sales_tables()
        g = build_lineage_graph("adventureworks", tables, [_revenue_metric()])
        assert g.schema_fingerprint == get_schema_fingerprint(tables)
        assert g.catalog_versions == {"total_revenue": 2}

    def test_invalid_direction_raises_value_error(self):
        g = build_lineage_graph("adventureworks", _fact_sales_tables())
        start = next(n.id for n in g.nodes if n.type == LineageNodeType.TABLE)
        with pytest.raises(ValueError):
            trace_lineage(g, start, "sideways")


class TestStaleness:
    def test_an_unchanged_schema_and_catalog_is_not_stale(self):
        tables = _fact_sales_tables()
        g = build_lineage_graph("adventureworks", tables, [_revenue_metric()])
        report = detect_staleness(g, get_schema_fingerprint(tables), {"total_revenue": 2})
        assert report.is_stale is False
        assert report.findings == ()

    def test_a_changed_schema_is_reported_as_stale(self):
        tables = _fact_sales_tables()
        g = build_lineage_graph("adventureworks", tables, [_revenue_metric()])
        report = detect_staleness(g, "a-different-fingerprint", {"total_revenue": 2})
        assert report.is_stale is True
        assert any(f.kind == "schema_changed" for f in report.findings)

    def test_a_republished_metric_version_is_reported_as_stale(self):
        tables = _fact_sales_tables()
        g = build_lineage_graph("adventureworks", tables, [_revenue_metric()])
        report = detect_staleness(g, get_schema_fingerprint(tables), {"total_revenue": 3})
        assert report.is_stale is True
        assert any(f.kind == "catalog_entry_version_changed" for f in report.findings)

    def test_a_removed_catalog_entry_is_reported_as_stale(self):
        tables = _fact_sales_tables()
        g = build_lineage_graph("adventureworks", tables, [_revenue_metric()])
        report = detect_staleness(g, get_schema_fingerprint(tables), {})
        assert report.is_stale is True
        assert any(f.kind == "catalog_entry_removed" for f in report.findings)
