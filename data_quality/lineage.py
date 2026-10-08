"""Connects Data Quality Command Center findings to affected governed
metrics/dashboards via lineage -- Prompt 41
(`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

A pure function, no database/LLM I/O of its own -- the identical "pure
function over caller-supplied, already-fetched data" posture
`data_quality/engine.py` already establishes. It takes a lightweight
per-table severity-count summary (`TableFindingsSummary`), not a full
`data_quality.models.DataQualityReport` -- a caller only needs to
disclose *how many findings of what severity* one table carries to
compute lineage, never the findings themselves, so this module (and the
REST request body behind it, `api/data_quality_schemas
.DataQualityLineageRequest`) never needs to re-transmit raw finding
detail already returned by a prior scan. The caller (`api
/data_quality.py`'s own `/lineage` route) is the one piece of this
feature that does touch a database: it reads the tenant's own
`identity.models.SemanticCatalogEntry` rows (via `identity.repositories
.semantic_catalog.list_entries`) and converts each into a `CatalogConceptRef`
before calling `build_lineage_report` here.

**Table-level lineage only, never column-level.** `SemanticCatalogEntry
.source_tables` is a real, persisted field; the catalog schema has no
`source_columns` field at all (confirmed by inspection -- see
`data_quality/models.py`'s own module docstring for the full citation).
A finding is therefore connected to a governed concept by its own
`DataQualityFinding.table` matching one of that concept's
`CatalogConceptRef.source_tables` -- never by column, which would imply a
precision this codebase's own catalog schema cannot back.

**Never asserts a governed metric IS wrong.** A table having a quality
finding does not mean every metric reading that table is incorrect --
this module reports *exposure* ("this governed concept reads a table with
an open data-quality finding"), not a verdict about the concept's own
correctness. Every `DataQualityLineageReport`/`TableLineageImpact`'s own
text says "potentially affected," never "is broken"/"is wrong" -- see
`tests/security/test_data_quality_findings_stay_observed_fact.py` for the
text-level regression proof.

**Only a `PUBLISHED` catalog entry is ever linked.** A `draft`/`reviewed`/
`superseded` entry passed in by mistake is silently excluded (defense in
depth -- the real enforcement is the caller only ever querying published
rows, the identical discipline `retrieval.chunking
.business_concept_chunk_from_catalog_entry` already establishes for the
same reason).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from data_quality.models import DATA_QUALITY_ENGINE_VERSION, DataQualitySeverity

_SEVERITY_RANK: dict[DataQualitySeverity, int] = {
    DataQualitySeverity.INFO: 0,
    DataQualitySeverity.WARNING: 1,
    DataQualitySeverity.CRITICAL: 2,
}


class TableFindingsSummary(BaseModel):
    """One table's own per-severity finding counts -- the minimal input
    `build_lineage_report` needs, built from a prior `assess_data_quality`
    result's own `TableQualityScore.findings` (e.g.
    `Counter(f.severity.value for f in table_score.findings)`), never the
    findings themselves."""

    model_config = ConfigDict(frozen=True)

    table: str
    severity_counts: dict[str, int] = Field(default_factory=dict)


class CatalogConceptRef(BaseModel):
    """A minimal, ORM-independent read shape of one *published*
    `identity.models.SemanticCatalogEntry` row -- just enough for table-
    level lineage matching. See this module's own docstring for why this
    is table-level only."""

    model_config = ConfigDict(frozen=True)

    concept_key: str
    name: str
    concept_type: str
    status: str
    owner: str | None = None
    database_id: str
    source_tables: tuple[str, ...] = ()


class TableLineageImpact(BaseModel):
    """One table's own lineage exposure -- how many findings (at/above the
    requested severity) it carries, and which governed concepts declare it
    as a source table."""

    model_config = ConfigDict(frozen=True)

    table: str
    finding_count: int
    severity_counts: dict[str, int]
    affected_concepts: tuple[CatalogConceptRef, ...]


class DataQualityLineageReport(BaseModel):
    """`data_quality.lineage.build_lineage_report`'s full result.

    Attributes:
        impacts: One `TableLineageImpact` per table that has at least one
            finding at/above the requested `min_severity` -- a clean
            table is never listed.
        unmapped_tables: Tables with qualifying findings but no published
            catalog concept declaring them as a source table -- disclosed
            rather than silently dropped, since "no governed metric reads
            this table (that we know of)" is itself useful information.
        limitations: Always non-empty -- the table-level-only scope
            boundary and the "exposure, not a verdict" framing, both from
            this module's own docstring, rendered as plain text for a
            caller that doesn't read source code.
        engine_version: `DATA_QUALITY_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    impacts: tuple[TableLineageImpact, ...]
    unmapped_tables: tuple[str, ...]
    limitations: tuple[str, ...]
    engine_version: str = DATA_QUALITY_ENGINE_VERSION


_LIMITATIONS: tuple[str, ...] = (
    "Lineage is table-level only -- the semantic catalog does not record which specific "
    "column(s) a governed metric reads, so a finding is linked by table, not column.",
    "A listed concept is 'potentially affected' because it reads a table with an open "
    "finding -- this is not a verdict that the concept's own value is currently wrong.",
    "Only published catalog concepts are considered -- a draft or reviewed definition is "
    "never linked, matching this codebase's own 'never silently promote an inference to "
    "confirmed business truth' rule.",
)


def build_lineage_report(
    table_summaries: Sequence[TableFindingsSummary],
    catalog_entries: Sequence[CatalogConceptRef],
    min_severity: DataQualitySeverity = DataQualitySeverity.WARNING,
) -> DataQualityLineageReport:
    """Connects each table's own finding-severity counts to
    `catalog_entries` by table name.

    Args:
        table_summaries: Per-table severity-count summaries, built from an
            already-computed `data_quality.engine.assess_data_quality`
            result (see `TableFindingsSummary`'s own docstring) -- this
            function never queries a database itself.
        catalog_entries: Already-fetched, real `SemanticCatalogEntry` rows
            (converted to `CatalogConceptRef`) -- likewise never fetched
            here. A non-`"published"` entry is silently excluded (defense
            in depth, see this module's own docstring).
        min_severity: Only severities at/above this count toward
            `TableLineageImpact.finding_count`/`.severity_counts` -- a
            table whose only counted severity is below this bar is never
            listed in `impacts`.
    """
    published = [entry for entry in catalog_entries if entry.status == "published"]
    min_rank = _SEVERITY_RANK[min_severity]
    qualifying_severities = {s.value for s in DataQualitySeverity if _SEVERITY_RANK[s] >= min_rank}

    impacts: list[TableLineageImpact] = []
    unmapped: list[str] = []
    for summary in table_summaries:
        severity_counts = {
            severity: count
            for severity, count in summary.severity_counts.items()
            if severity in qualifying_severities and count > 0
        }
        finding_count = sum(severity_counts.values())
        if finding_count == 0:
            continue
        concepts = tuple(entry for entry in published if summary.table in entry.source_tables)
        if not concepts:
            unmapped.append(summary.table)
        impacts.append(
            TableLineageImpact(
                table=summary.table,
                finding_count=finding_count,
                severity_counts=severity_counts,
                affected_concepts=concepts,
            )
        )

    return DataQualityLineageReport(
        impacts=tuple(impacts), unmapped_tables=tuple(unmapped), limitations=_LIMITATIONS
    )


__all__ = [
    "CatalogConceptRef",
    "TableFindingsSummary",
    "TableLineageImpact",
    "DataQualityLineageReport",
    "build_lineage_report",
]
