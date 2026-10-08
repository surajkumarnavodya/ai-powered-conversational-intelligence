"""Typed contracts for the Data Lineage Platform -- Prompt 42
(`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`).

A lineage graph is assembled from several independently-reused sources,
each contributing nodes/edges at its own, honestly-disclosed truth level
(`agent.provenance.DataTruthLevel`) -- never one uniform confidence:

- A declared foreign key (`db.schema_introspection.ForeignKeyInfo`) is a
  `DATABASE_FACT` edge -- the database itself enforces it.
- A deterministically sqlglot-parsed column reference inside an executed
  (or about-to-be-executed) query, or inside a *published* governed
  metric's own `approved_expression`, is also `DATABASE_FACT` -- it is a
  measurement of the SQL/expression text's own structure, never a guess.
- A candidate relationship from `db.relationship_inference` (never a
  declared FK), or a lineage edge derived from a `draft`/`reviewed`
  (not-yet-published) catalog entry, is `AI_INFERENCE` -- exactly
  `semantic.catalog.status_to_truth_level`'s own mapping, reused here
  rather than re-derived.
- A lineage edge derived from a *published* catalog entry is
  `CONFIRMED_BUSINESS_TRUTH` -- a human has reviewed and approved it.

**Never one node type conflated with another.** `LineageNodeType` is a
closed, ordered vocabulary spanning exactly the mission's own named
chain (database -> table -> column -> metric/dimension -> query ->
visualization -> insight -> recommendation) -- deliberately distinct
node types even where this codebase's other engines use one shared
shape, because a lineage consumer needs to know *what kind* of thing a
node is to render or filter it sensibly.

**Deterministic parsing preferred over LLM guesses, per the mission's
own instruction.** Every edge this package's own extraction modules
build comes from a real foreign key, a real sqlglot parse of real SQL/
expression text, or a real governed-catalog field -- never an LLM call.
An LLM-authored node (an insight sentence, a recommendation) is only ever
a *leaf* this graph connects *to* (via `lineage.presentation_lineage`),
never a node this package infers structure *from*.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel

#: Bumped when this module's typed shapes or an extraction module's own
#: parsing/matching logic changes in a way a consumer should be able to
#: detect -- the identical convention every other engine module in this
#: codebase already follows (`DATA_QUALITY_ENGINE_VERSION`,
#: `ANALYTICS_ENGINE_VERSION`, ...).
LINEAGE_ENGINE_VERSION = "1.0.0"


class LineageNodeType(str, Enum):
    """The mission's own named chain, as a closed set."""

    DATABASE = "database"
    TABLE = "table"
    COLUMN = "column"
    METRIC = "metric"
    DIMENSION = "dimension"
    QUERY = "query"
    VISUALIZATION = "visualization"
    INSIGHT = "insight"
    RECOMMENDATION = "recommendation"


class LineageEdgeType(str, Enum):
    """How one node relates to another -- a closed set, never a free-text
    relationship label an LLM could author."""

    #: A table/column read by a query, or a metric's own source table.
    READS = "reads"
    #: A declared or candidate foreign key between two tables.
    JOINS_VIA = "joins_via"
    #: An output column/metric computed from a source column via an
    #: aggregate/transform (`detail` on the edge names the function,
    #: e.g. "SUM").
    DERIVES_FROM = "derives_from"
    #: A metric/dimension declared by (and only ever linked from) a
    #: governed semantic-catalog entry.
    GOVERNED_BY = "governed_by"
    #: A chart specification built from a query's own real result.
    VISUALIZES = "visualizes"
    #: An LLM-narrated insight sentence explaining a query's result.
    EXPLAINS = "explains"
    #: A recommendation whose evidence cites a query/table/column.
    SUPPORTED_BY = "supported_by"


class LineageNode(BaseModel):
    """One node in a lineage graph.

    `id` is a stable, deterministic string (never a random UUID) so the
    same underlying object always gets the same node id across repeated
    graph builds -- table/column nodes are keyed by name, a query node by
    its own fingerprint (see `lineage.sql_lineage`), a catalog-derived
    node by `concept_key`. This determinism is what makes two separately-
    built graphs comparable at all (`lineage.staleness` relies on it).

    `concept_version` is set only for a `METRIC`/`DIMENSION` node sourced
    from a governed catalog entry -- the literal "versioning" requirement:
    a lineage consumer can tell *which* version of a metric definition a
    given edge was computed from.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    type: LineageNodeType
    label: str
    database_id: str | None = None
    table: str | None = None
    column: str | None = None
    concept_key: str | None = None
    concept_version: int | None = None
    truth_level: DataTruthLevel = DataTruthLevel.DATABASE_FACT
    extra: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    """One directed edge, `source_id` -> `target_id`.

    `is_inferred` is `True` only for a structurally-inferred candidate
    relationship (`db.relationship_inference.InferredRelationship`) --
    kept as its own explicit boolean (not just inferrable from
    `truth_level`) so a UI can visually distinguish "inferred" from
    "governed-but-not-yet-published" without having to remember which
    `DataTruthLevel` value means which, matching `InferredRelationship
    .truth_level`'s own precedent of making this an explicit field.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str
    target_id: str
    type: LineageEdgeType
    truth_level: DataTruthLevel
    detail: str = ""
    is_inferred: bool = False


class LineageGraph(BaseModel):
    """The full, assembled lineage graph `lineage.graph.build_lineage_graph`
    returns.

    `schema_fingerprint` (reused verbatim from `db.schema_introspection
    .get_schema_fingerprint`) and `catalog_versions` (`concept_key ->
    version` for every catalog-derived node) are exactly what
    `lineage.staleness.detect_staleness` compares a graph's own recorded
    state against the live state to decide whether this graph is stale --
    see that module's own docstring.
    """

    model_config = ConfigDict(frozen=True)

    database_id: str | None
    nodes: tuple[LineageNode, ...]
    edges: tuple[LineageEdge, ...]
    schema_fingerprint: str | None = None
    catalog_versions: dict[str, int] = Field(default_factory=dict)
    suppressed_restricted_nodes: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    engine_version: str = LINEAGE_ENGINE_VERSION


class LineageTraceStep(BaseModel):
    """One hop in a `lineage.graph.trace_lineage` path -- the edge
    traversed to reach `node`, and at what depth."""

    model_config = ConfigDict(frozen=True)

    node: LineageNode
    via_edge: LineageEdge | None
    depth: int


class LineageTraceResult(BaseModel):
    """`lineage.graph.trace_lineage`'s full result -- an upstream (what
    feeds this node) or downstream (what this node feeds -- impact
    analysis) walk from one starting node, breadth-first, cycle-safe, and
    bounded by `max_depth`.

    `truncated` is `True` when the walk stopped only because `max_depth`
    was reached while more reachable nodes remained -- disclosed rather
    than silently returning a partial trace that looks complete.
    """

    model_config = ConfigDict(frozen=True)

    start_node_id: str
    direction: str
    max_depth: int
    steps: tuple[LineageTraceStep, ...]
    truncated: bool = False


class LineageStalenessFinding(BaseModel):
    """One reason a previously-built lineage graph may no longer reflect
    reality."""

    model_config = ConfigDict(frozen=True)

    kind: str
    detail: str
    node_id: str | None = None


class LineageStalenessReport(BaseModel):
    """`lineage.staleness.detect_staleness`'s full result."""

    model_config = ConfigDict(frozen=True)

    is_stale: bool
    findings: tuple[LineageStalenessFinding, ...]
    engine_version: str = LINEAGE_ENGINE_VERSION


__all__ = [
    "LINEAGE_ENGINE_VERSION",
    "LineageNodeType",
    "LineageEdgeType",
    "LineageNode",
    "LineageEdge",
    "LineageGraph",
    "LineageTraceStep",
    "LineageTraceResult",
    "LineageStalenessFinding",
    "LineageStalenessReport",
]
