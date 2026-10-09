"""Pydantic request/response models for `api/workflow.py`. Mirrors
`api/recommendation_governance_schemas.py`'s own conventions exactly:
request models get `str_strip_whitespace=True` + `extra="forbid"`,
response models get `frozen=True`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WorkflowStatusLiteral = Literal[
    "proposed",
    "approval_required",
    "approved",
    "rejected",
    "assigned",
    "in_progress",
    "completed",
    "expired",
]

WorkflowEventTypeLiteral = Literal[
    "status_change", "comment", "owner_assigned", "due_date_set", "external_reference_set"
]


class WorkflowEventOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    from_status: WorkflowStatusLiteral | None
    to_status: WorkflowStatusLiteral
    event_type: WorkflowEventTypeLiteral = "status_change"
    actor_user_id: uuid.UUID | None
    actor_label: str | None
    #: For `status_change`/`comment` this is the reason/comment text;
    #: `None` for the metadata-only event types, which carry their
    #: payload in `detail` instead.
    reason: str | None
    detail: dict[str, Any] | None = None
    created_at: datetime


class WorkflowOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    tenant_id: str
    recommendation_id: uuid.UUID
    title: str
    description: str | None
    status: WorkflowStatusLiteral
    owner_user_id: uuid.UUID | None
    owner_display_name: str | None = None
    due_date: datetime | None
    external_system: str | None
    external_reference_id: str | None
    external_reference_url: str | None
    outcome_summary: str | None
    outcome_measured_impact: dict[str, Any] | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class CreateWorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    recommendation_id: uuid.UUID
    #: Defaults to the recommendation's own `claim_text`/`action` when
    #: omitted -- see `identity/repositories/workflow.py::create_workflow`.
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=4000)


class SubmitForApprovalRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class ApproveWorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class RejectWorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class AssignWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: uuid.UUID
    due_date: datetime | None = None


class SetDueDateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: datetime | None = None


class StartWorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class CompleteWorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    outcome_summary: str = Field(min_length=1, max_length=4000)
    #: Caller-reported only -- never independently verified or computed.
    #: See `identity.models.Workflow`'s own docstring.
    outcome_measured_impact: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=4000)


class ExpireWorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class AddCommentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    comment: str = Field(min_length=1, max_length=4000)


class SetExternalReferenceRequest(BaseModel):
    """Plain, caller-supplied metadata -- a label and a link a human can
    click to open a ticket in whatever external system they already
    use. Setting every field to `None` clears the reference. See
    `workflow/models.py`'s own module docstring for why this never calls
    an external system."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    external_system: str | None = Field(default=None, max_length=100)
    external_reference_id: str | None = Field(default=None, max_length=200)
    external_reference_url: str | None = Field(default=None, max_length=2048)
