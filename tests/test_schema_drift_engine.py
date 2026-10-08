"""Unit tests for `schema_drift.findings`, `schema_drift.impact`,
`schema_drift.engine`, and `schema_drift.reports` (Prompt 43). Pure
functions throughout -- `schema_drift.impact` is tested against a real
`lineage.graph.build_lineage_graph` result (Prompt 42's own machinery,
genuinely reused, not mocked)."""

from __future__ import annotations

from semantic.catalog import CatalogConceptType, CatalogEntrySnapshot, CatalogStatus

from agent.provenance import DataTruthLevel
from db.schema_introspection import ColumnInfo, ForeignKeyInfo, TableSchemaInfo
from lineage.graph import build_lineage_graph
from schema_drift.diff import DriftChangeKind, SchemaDriftFact
from schema_drift.engine import scan_for_drift
from schema_drift.findings import fact_to_finding, facts_to_findings, is_schema_drift_kind
from schema_drift.impact import assess_impact
from schema_drift.reports import render_markdown_report


def _tables() -> list[TableSchemaInfo]:
    return [
        TableSchemaInfo(
            table_name="FactSales",
            columns=(
                ColumnInfo("SalesAmount", "decimal", True, False),
                ColumnInfo("ProductKey", "int", False, False),
            ),
            foreign_keys=(ForeignKeyInfo(("ProductKey",), "DimProduct", ("ProductKey",)),),
            ddl="x",
        ),
        TableSchemaInfo(
            table_name="DimProduct",
            columns=(
                ColumnInfo("ProductKey", "int", False, True),
                ColumnInfo("ProductName", "text", False, False),
            ),
            foreign_keys=(),
            ddl="x",
        ),
    ]


def _published_metric_reading_sales_amount() -> CatalogEntrySnapshot:
    return CatalogEntrySnapshot(
        id="e1",
        tenant_id="tenant-a",
        database_id="default",
        concept_type=CatalogConceptType.METRIC,
        concept_key="total_revenue",
        business_name="Total Revenue",
        status=CatalogStatus.PUBLISHED,
        version=3,
        approved_expression="SUM(SalesAmount)",
        source_tables=("FactSales",),
    )


class TestFindingsConversion:
    def test_a_deterministic_fact_is_database_fact(self):
        fact = SchemaDriftFact(
            kind=DriftChangeKind.COLUMN_REMOVED,
            table="FactSales",
            column="SalesAmount",
            previous_value="decimal",
            current_value=None,
            detail="removed",
        )
        finding = fact_to_finding("default", fact)
        assert finding.truth_level == DataTruthLevel.DATABASE_FACT.value
        assert finding.kind == "schema_drift_column_removed"
        assert is_schema_drift_kind(finding.kind)

    def test_an_inferred_rename_is_ai_inference(self):
        fact = SchemaDriftFact(
            kind=DriftChangeKind.COLUMN_RENAMED,
            table="FactSales",
            column="new_name",
            previous_value="old_name",
            current_value="new_name",
            detail="renamed?",
            is_inferred=True,
            confidence=0.75,
        )
        finding = fact_to_finding("default", fact)
        assert finding.truth_level == DataTruthLevel.AI_INFERENCE.value
        assert finding.confidence == 0.75

    def test_repeated_conversion_of_the_same_fact_produces_the_same_key(self):
        fact = SchemaDriftFact(
            kind=DriftChangeKind.COLUMN_ADDED,
            table="FactSales",
            column="NewCol",
            previous_value=None,
            current_value="int",
            detail="added",
        )
        a = fact_to_finding("default", fact)
        b = fact_to_finding("default", fact)
        assert a.key == b.key

    def test_findings_for_two_different_databases_never_collide(self):
        fact = SchemaDriftFact(
            kind=DriftChangeKind.COLUMN_ADDED,
            table="FactSales",
            column="NewCol",
            previous_value=None,
            current_value="int",
            detail="added",
        )
        a = fact_to_finding("db1", fact)
        b = fact_to_finding("db2", fact)
        assert a.key != b.key


class TestImpact:
    def test_a_removed_column_feeding_a_published_metric_is_high_impact(self):
        previous_graph = build_lineage_graph(
            "default", _tables(), [_published_metric_reading_sales_amount()]
        )
        fact = SchemaDriftFact(
            kind=DriftChangeKind.COLUMN_REMOVED,
            table="FactSales",
            column="SalesAmount",
            previous_value="decimal",
            current_value=None,
            detail="removed",
        )
        finding = fact_to_finding("default", fact)
        [updated] = assess_impact([finding], previous_graph, "default", max_depth=6)
        assert updated.dependents >= 1
        assert updated.published_count >= 1
        impacted_types = {item["type"] for item in updated.payload["impacted"]}
        assert "metric" in impacted_types

    def test_a_newly_added_column_has_no_impact_yet(self):
        previous_graph = build_lineage_graph("default", _tables(), [])
        fact = SchemaDriftFact(
            kind=DriftChangeKind.COLUMN_ADDED,
            table="FactSales",
            column="BrandNewColumn",
            previous_value=None,
            current_value="int",
            detail="added",
        )
        finding = fact_to_finding("default", fact)
        [updated] = assess_impact([finding], previous_graph, "default", max_depth=6)
        assert updated.dependents == 0
        assert "impacted" not in updated.payload

    def test_a_table_level_finding_resolves_to_the_table_node(self):
        # Two declared source tables forces lineage.catalog_lineage's own
        # table-level GOVERNED_BY fallback (single-column resolution needs
        # exactly one declared source table to disambiguate against) --
        # exactly the shape that targets a TABLE node directly, which is
        # what this test needs to exercise `_start_node_id`'s table branch.
        multi_table_metric = CatalogEntrySnapshot(
            id="e2",
            tenant_id="tenant-a",
            database_id="default",
            concept_type=CatalogConceptType.METRIC,
            concept_key="combined_metric",
            business_name="Combined Metric",
            status=CatalogStatus.PUBLISHED,
            version=1,
            approved_expression="SUM(SalesAmount) / COUNT(ProductName)",
            source_tables=("FactSales", "DimProduct"),
        )
        previous_graph = build_lineage_graph("default", _tables(), [multi_table_metric])
        fact = SchemaDriftFact(
            kind=DriftChangeKind.TABLE_REMOVED,
            table="FactSales",
            column=None,
            previous_value="table",
            current_value=None,
            detail="removed",
        )
        finding = fact_to_finding("default", fact)
        [updated] = assess_impact([finding], previous_graph, "default", max_depth=6)
        assert updated.dependents >= 1


class TestEngineOrchestration:
    def test_scan_for_drift_runs_diff_findings_impact_and_risk_in_one_call(self):
        previous = [
            TableSchemaInfo(
                table_name="FactSales",
                columns=(
                    ColumnInfo("SalesAmount", "decimal", True, False),
                    ColumnInfo("ProductKey", "int", False, False),
                ),
                foreign_keys=(),
                ddl="x",
            )
        ]
        current: list[TableSchemaInfo] = []  # the table was dropped entirely
        previous_graph = build_lineage_graph(
            "default", previous, [_published_metric_reading_sales_amount()]
        )
        result = scan_for_drift(
            "default",
            previous,
            current,
            previous_graph,
            rename_similarity_threshold=0.6,
            impact_max_depth=6,
        )
        assert result.tables_compared == 0
        [table_removed] = [f for f in result.findings if f.kind == "schema_drift_table_removed"]
        assert table_removed.risk_tier in {"high", "medium"}
        assert table_removed.risk_score > 0
        # riskiest first
        assert result.findings == tuple(
            sorted(result.findings, key=lambda f: (-f.risk_score, f.kind, f.key))
        )

    def test_a_first_scan_with_no_graph_still_produces_findings_with_zero_impact(self):
        current = [
            TableSchemaInfo(
                table_name="NewTable",
                columns=(ColumnInfo("id", "int", False, True),),
                foreign_keys=(),
                ddl="x",
            )
        ]
        result = scan_for_drift(
            "default", None, current, None, rename_similarity_threshold=0.6, impact_max_depth=6
        )
        assert len(result.findings) == 1
        assert result.findings[0].dependents == 0


class TestReports:
    def test_no_findings_produces_a_clean_report(self):
        report = render_markdown_report("default", [], tables_compared=5)
        assert "No drift detected" in report

    def test_findings_are_grouped_by_risk_tier(self):
        facts = [
            SchemaDriftFact(
                kind=DriftChangeKind.TABLE_REMOVED,
                table="A",
                column=None,
                previous_value="table",
                current_value=None,
                detail="A was removed",
            ),
            SchemaDriftFact(
                kind=DriftChangeKind.COLUMN_ADDED,
                table="B",
                column="x",
                previous_value=None,
                current_value="int",
                detail="B.x was added",
            ),
        ]
        from semantic.intelligence.risk import prioritize

        findings = prioritize(facts_to_findings("default", facts))
        report = render_markdown_report("default", findings, tables_compared=2)
        assert "## High risk" in report or "## Medium risk" in report
        assert "A was removed" in report
        assert "B.x was added" in report
