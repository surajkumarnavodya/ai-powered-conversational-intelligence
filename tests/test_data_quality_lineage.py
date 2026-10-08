"""Tests for `data_quality/lineage.py` (Prompt 41,
`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).
"""

from __future__ import annotations

from data_quality.lineage import (
    CatalogConceptRef,
    TableFindingsSummary,
    build_lineage_report,
)
from data_quality.models import DataQualitySeverity


def _concept(**overrides: object) -> CatalogConceptRef:
    base: dict = dict(
        concept_key="customer_ltv",
        name="Customer LTV",
        concept_type="metric",
        status="published",
        owner="analyst",
        database_id="default",
        source_tables=("customers",),
    )
    base.update(overrides)
    return CatalogConceptRef(**base)


class TestBasicMatching:
    def test_a_table_with_findings_is_linked_to_a_concept_declaring_it_as_a_source(self):
        summary = TableFindingsSummary(
            table="customers", severity_counts={"warning": 2, "critical": 1}
        )
        report = build_lineage_report([summary], [_concept()])
        assert len(report.impacts) == 1
        impact = report.impacts[0]
        assert impact.table == "customers"
        assert impact.finding_count == 3
        assert impact.severity_counts == {"warning": 2, "critical": 1}
        assert impact.affected_concepts[0].concept_key == "customer_ltv"
        assert report.unmapped_tables == ()

    def test_a_table_with_no_qualifying_findings_is_never_listed(self):
        summary = TableFindingsSummary(table="customers", severity_counts={"info": 5})
        report = build_lineage_report([summary], [_concept()])
        assert report.impacts == ()

    def test_a_table_with_findings_but_no_matching_concept_is_unmapped(self):
        summary = TableFindingsSummary(table="orders", severity_counts={"critical": 1})
        report = build_lineage_report([summary], [_concept()])
        assert report.unmapped_tables == ("orders",)
        assert report.impacts[0].affected_concepts == ()


class TestMinSeverityFilter:
    def test_info_only_findings_are_excluded_at_the_default_warning_floor(self):
        summary = TableFindingsSummary(
            table="customers", severity_counts={"info": 10, "warning": 1}
        )
        report = build_lineage_report([summary], [_concept()])
        assert report.impacts[0].finding_count == 1
        assert report.impacts[0].severity_counts == {"warning": 1}

    def test_a_lower_min_severity_includes_info(self):
        summary = TableFindingsSummary(table="customers", severity_counts={"info": 10})
        report = build_lineage_report(
            [summary], [_concept()], min_severity=DataQualitySeverity.INFO
        )
        assert report.impacts[0].finding_count == 10


class TestOnlyPublishedEntriesAreLinked:
    def test_a_draft_entry_is_never_linked_even_if_passed_in(self):
        summary = TableFindingsSummary(table="customers", severity_counts={"critical": 1})
        report = build_lineage_report([summary], [_concept(status="draft")])
        assert report.impacts[0].affected_concepts == ()
        assert report.unmapped_tables == ("customers",)


class TestHonestFraming:
    def test_limitations_disclose_table_level_scope_and_exposure_not_verdict(self):
        report = build_lineage_report([], [])
        assert any("table" in note.lower() for note in report.limitations)
        assert any("not a verdict" in note.lower() for note in report.limitations)
        for note in report.limitations:
            assert "is broken" not in note.lower()
            assert "is wrong" not in note.lower()
