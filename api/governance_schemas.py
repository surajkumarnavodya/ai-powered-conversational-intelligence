"""Request and response models for `api/governance.py` (Prompt 44,
`44_AI_GOVERNANCE_CENTER_CONTRACT.md`).

Request models forbid unknown fields and strip whitespace -- the same
convention `api/onboarding_schemas.py`/`api/semantic_catalog_schemas.py`'s
own request models establish, so a client cannot slip in a `tenant_id`/
`caller_roles`/`created_by_user_id` override; every one of those is
always resolved server-side.

Response models mirror `identity.models.GovernanceAssetVersion`/
`GovernanceEvent` field-by-field with plain primitives (strings instead
of enums/UUIDs/datetimes) -- the identical "mirror the ORM row, don't
re-export it" convention `CatalogEntryOut` already establishes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateGovernanceAssetRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    asset_type: str = Field(..., min_length=1)
    asset_key: str = Field(..., min_length=1, max_length=200)
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    owner: str | None = None


class UpdateGovernanceDraftRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    config: dict[str, Any] | None = None
    owner: str | None = None


class RecordTestResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_results: dict[str, Any] = Field(..., min_length=1)


class RecordEvaluationResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_results: dict[str, Any] = Field(..., min_length=1)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    notes: str | None = None


class RejectRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str = Field(..., min_length=1)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    target_version_id: uuid.UUID
    reason: str = Field(..., min_length=1)


class RetireRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str | None = None


class RecordUsageRequest(BaseModel):
    """One caller/operator-reported usage/cost/latency sample -- see
    `identity.models.GovernanceEvent`'s own docstring for why this is
    reported, never auto-instrumented, in this pass. Every field is
    optional; at least one should be present for the sample to be
    meaningful, but this route never rejects an otherwise-empty sample
    (a caller reporting "zero requests this window" is itself valid
    data)."""

    model_config = ConfigDict(extra="forbid")

    request_count: int | None = Field(default=None, ge=0)
    total_cost: float | None = Field(default=None, ge=0)
    p50_latency_ms: float | None = Field(default=None, ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    window_start: str | None = None
    window_end: str | None = None


class GovernanceAssetVersionOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    asset_type: str
    asset_key: str
    display_name: str
    description: str
    owner: str | None
    version: int
    stage: str
    truth_level: str
    config: dict[str, Any]
    test_results: dict[str, Any] | None
    evaluation_results: dict[str, Any] | None
    approval_notes: str | None
    rejection_reason: str | None
    supersedes_id: str | None
    created_by_user_id: str | None
    tested_by_user_id: str | None
    tested_at: datetime | None
    evaluated_by_user_id: str | None
    evaluated_at: datetime | None
    approved_by_user_id: str | None
    approved_at: datetime | None
    deployed_by_user_id: str | None
    deployed_at: datetime | None
    retired_by_user_id: str | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GovernanceEventOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    asset_version_id: str
    event_type: str
    from_stage: str | None
    to_stage: str | None
    actor_user_id: str | None
    actor_label: str | None
    reason: str | None
    payload: dict[str, Any] | None
    created_at: datetime


class UsageSummaryOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    sample_count: int
    total_requests: float
    total_cost: float
    mean_p95_latency_ms: float | None


class RollbackResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    rolled_back: GovernanceAssetVersionOut
    redeployed: GovernanceAssetVersionOut


class DeployResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    deployed: GovernanceAssetVersionOut
    retired: GovernanceAssetVersionOut | None


# --------------------------------------------------------------------------
# Prompt 45 (`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`) -- the prompt/agent/
# policy registry's own inventory and validation surface, layered on the
# lifecycle above. Every model below is additive; none of Prompt 44's own
# shapes changed.
# --------------------------------------------------------------------------


class ArtifactViolationOut(BaseModel):
    """One problem found in a candidate artifact version."""

    model_config = ConfigDict(frozen=True)

    code: str
    severity: str
    subject: str
    message: str


class ArtifactValidationReportOut(BaseModel):
    """A candidate artifact version's validation result.

    `is_valid` is false whenever any `severity == "error"` violation is
    present -- the API never reports a partially-valid artifact (see
    `governance_center/validation.py`'s own "no partial trust" note).
    `truth_level` is always `"database_fact"`: a deterministic finding
    about the submitted payload's structure, never a judgment that the
    artifact is safe to deploy, which stays a human approval.
    """

    model_config = ConfigDict(frozen=True)

    asset_type: str
    asset_key: str
    is_valid: bool
    compared_against_live: bool
    truth_level: str
    violations: tuple[ArtifactViolationOut, ...] = ()


class ValidateArtifactRequest(BaseModel):
    """Validate a candidate artifact without persisting anything.

    Deliberately takes the same `(asset_type, asset_key, config)` triple
    a real version carries, so a reviewer can check a payload before
    creating a draft from it and get a byte-identical report.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    asset_type: str = Field(..., min_length=1)
    asset_key: str = Field(..., min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)


class InventoryEntryOut(BaseModel):
    """One live, in-code artifact as the registry sees it.

    `truth_level` is always `"database_fact"` -- an inventory entry is a
    deterministic observation about this repository's own source, not a
    claim that the artifact is approved (that is `stage`'s job on a real
    version).
    """

    model_config = ConfigDict(frozen=True)

    asset_type: str
    asset_key: str
    display_name: str
    source_module: str
    source_symbol: str
    fingerprint: str
    truth_level: str
    declared_invariants: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    max_truth_level: str | None = None
    policy_rules: tuple[str, ...] = ()
    notes: str = ""


class InventoryOut(BaseModel):
    """The full live inventory of prompts, agents and analytical policies.

    `known_tools`/`known_models`/`known_evaluation_suites` are the same
    sets validation checks a candidate artifact against, returned here so
    a reviewer authoring a version can see the valid names up front
    rather than discovering them from rejections.
    """

    model_config = ConfigDict(frozen=True)

    prompts: tuple[InventoryEntryOut, ...] = ()
    agents: tuple[InventoryEntryOut, ...] = ()
    policies: tuple[InventoryEntryOut, ...] = ()
    known_tools: tuple[str, ...] = ()
    known_models: tuple[str, ...] = ()
    known_evaluation_suites: tuple[str, ...] = ()
    truth_level: str = "database_fact"
