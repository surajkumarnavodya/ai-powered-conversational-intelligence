"""Request and response models for `api/lineage.py` (Prompt 42,
`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`).

Request models forbid unknown fields and strip whitespace -- the same
convention `api/data_quality_schemas.py`/`api/predictive_schemas.py`'s
own request models establish, so a client cannot slip in a `tenant_id`/
`caller_roles` override; both are always resolved server-side.

Response models mirror `lineage.models`'s own typed shapes field-by-field
with plain primitives (strings instead of enums) -- the identical "mirror
the internal engine model, don't re-export it" convention
`DataQualityFindingOut`/`OptimizationValidateResponse` already establish.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --- /graph request ----------------------------------------------------


class VisualizationSpecIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = ""
    type: str = "chart"


class InsightSpecIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = ""
    grounded_fact_labels: list[str] = Field(default_factory=list)


class RecommendationSpecIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = ""
    evidence_count: int = Field(default=0, ge=0)


class QuerySpecIn(BaseModel):
    """One already-executed (or about-to-be-executed) query's own SQL
    text, plus optional already-produced downstream objects -- a chart
    spec summary, an insight sentence, and/or recommendations -- the
    caller's own already-fetched result, never re-fetched by this route.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sql: str = Field(..., min_length=1)
    fingerprint: str | None = None
    visualization: VisualizationSpecIn | None = None
    insight: InsightSpecIn | None = None
    recommendations: list[RecommendationSpecIn] = Field(default_factory=list)


class LineageGraphRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    database_id: str = Field(..., min_length=1)
    include_inferred_relationships: bool = False
    queries: list[QuerySpecIn] = Field(default_factory=list)


# --- Shared graph output shapes -----------------------------------------


class LineageNodeOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: str
    label: str
    database_id: str | None
    table: str | None
    column: str | None
    concept_key: str | None
    concept_version: int | None
    truth_level: str
    extra: dict[str, Any]


class LineageEdgeOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    target_id: str
    type: str
    truth_level: str
    detail: str
    is_inferred: bool


class LineageGraphResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_id: str | None
    nodes: list[LineageNodeOut]
    edges: list[LineageEdgeOut]
    schema_fingerprint: str | None
    catalog_versions: dict[str, int]
    suppressed_restricted_nodes: list[str]
    limitations: list[str]
    engine_version: str


# --- /trace request/response --------------------------------------------


class LineageTraceRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    graph: LineageGraphResponse
    start_node_id: str = Field(..., min_length=1)
    direction: str = Field(default="upstream")
    max_depth: int | None = Field(default=None, gt=0)


class LineageTraceStepOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    node: LineageNodeOut
    via_edge: LineageEdgeOut | None
    depth: int


class LineageTraceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_node_id: str
    direction: str
    max_depth: int
    steps: list[LineageTraceStepOut]
    truncated: bool


# --- /stale-check request/response --------------------------------------


class LineageStaleCheckRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    database_id: str = Field(..., min_length=1)
    recorded_schema_fingerprint: str | None = None
    recorded_catalog_versions: dict[str, int] = Field(default_factory=dict)


class LineageStalenessFindingOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    detail: str
    node_id: str | None


class LineageStaleCheckResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_stale: bool
    findings: list[LineageStalenessFindingOut]
    engine_version: str
