"""Request and response models for `api/schema_drift.py` (Prompt 43).

Reuses `api.semantic_intelligence_schemas.FindingOut` verbatim for one
finding's shape -- a schema-drift finding is persisted as the exact same
`identity.models.SemanticFinding` row a catalog-detector finding is (see
`schema_drift/__init__.py`'s own docstring), so there is exactly one
response shape for "one governed finding," not two near-duplicates.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from api.semantic_intelligence_schemas import FindingOut


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_id: str = Field(..., min_length=1, max_length=200)


class ScanOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_id: str
    tables_compared: int
    is_first_scan: bool
    findings_detected: int
    findings_truncated: int
    created: int
    updated: int
    unchanged: int
    report_markdown: str
    top_findings: list[FindingOut]


__all__ = ["ScanRequest", "ScanOut", "FindingOut"]
