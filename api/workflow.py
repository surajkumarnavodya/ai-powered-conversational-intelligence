"""The governed workflow lifecycle's REST surface -- the API for
`identity/repositories/workflow.py`. Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`), mirroring
`api/recommendation_governance.py`'s own structure and discipline
exactly.

**Every route here does exactly one authorization dance**: resolve the
`identity.models.Workflow` row by id (never trusting a query filter
alone to scope it), then call `workflow.policy.authorize_workflow_action`
and act only on an `allowed=True` decision -- mirrors `api
/recommendation_governance.py`'s own identical discipline.

**`POST /workflows` is the one route that resolves a *different*
resource first** -- the source `identity.models.RecommendationRecord` --
since a workflow doesn't exist yet to authorize against. A cross-tenant
or nonexistent recommendation id gets the same 404 either way (the same
anti-enumeration posture every other route here already uses for a
cross-tenant workflow).

**No route here ever executes anything.** Every write this module
performs lands only in `workflows`/`workflow_events`, and nothing here
runs SQL, calls an external system, or invokes another engine -- the
mission's own "never permit uncontrolled autonomous execution" control,
made structural (see `workflow/models.py`'s own module docstring and
`tests/security/test_workflow_never_autonomously_executes.py`).

**Every mutation is logged** through `security.audit_log.log_security_event`
(`event=workflow_action`, carrying the workflow id, action, actor id and
status transition -- never the title, description, comment, or outcome
text) and rate-limited through `api.rate_limit.enforce_api_action_rate_limit`
(per-IP, the identical disclosed limitation Prompt 31 already names for
its own recommendation-action routes).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from identity.models import User, Workflow, WorkflowEvent
from identity.repositories.recommendation_governance import (
    get_record_by_id as get_recommendation_by_id,
)
from identity.repositories.users import get_user_permissions
from identity.repositories.workflow import (
    DuplicateActiveWorkflowError,
    InvalidWorkflowOwnerError,
    InvalidWorkflowStatusTransitionError,
    RecommendationNotEligibleError,
    add_comment,
    approve_workflow,
    assign_workflow,
    complete_workflow,
    create_workflow,
    expire_workflow,
    get_workflow_by_id,
    list_workflow_events,
    list_workflows,
    owner_display_names,
    set_due_date,
    set_external_reference,
    start_workflow,
    submit_for_approval,
)
from identity.repositories.workflow import (
    reject_workflow as _reject_workflow,
)
from sqlalchemy.orm import Session

from api.identity_authz import require_local_user
from api.rate_limit import enforce_api_action_rate_limit
from api.workflow_schemas import (
    AddCommentRequest,
    ApproveWorkflowRequest,
    AssignWorkflowRequest,
    CompleteWorkflowRequest,
    CreateWorkflowRequest,
    ExpireWorkflowRequest,
    RejectWorkflowRequest,
    SetDueDateRequest,
    SetExternalReferenceRequest,
    StartWorkflowRequest,
    SubmitForApprovalRequest,
    WorkflowEventOut,
    WorkflowOut,
)
from config.settings import get_settings
from security.audit_log import log_security_event
from security.tenancy import resolve_actor_tenant_id
from workflow.policy import WorkflowAction, authorize_workflow_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workflows", tags=["workflow-automation"])

# Singleton defaults for a route whose every request-body field is
# optional -- a caller omitting the body entirely must still work (the
# real bug Prompt 44 found and fixed for `api/governance.py`'s own
# `approve`/`retire` routes; avoided here from the start).
_DEFAULT_SUBMIT_REQUEST = SubmitForApprovalRequest()
_DEFAULT_APPROVE_REQUEST = ApproveWorkflowRequest()
_DEFAULT_REJECT_REQUEST = RejectWorkflowRequest()
_DEFAULT_START_REQUEST = StartWorkflowRequest()
_DEFAULT_EXPIRE_REQUEST = ExpireWorkflowRequest()


def _not_found(detail: str = "Workflow not found.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")


def _require_workflow(session: Session, workflow_id: uuid.UUID) -> Workflow:
    workflow = get_workflow_by_id(session, workflow_id)
    if workflow is None:
        raise _not_found()
    return workflow


def _authorize(
    user: User,
    session: Session,
    action: WorkflowAction,
    workflow_tenant_id: str | None,
) -> None:
    permissions = get_user_permissions(session, user.id)
    actor_tenant_id = resolve_actor_tenant_id(user)
    decision = authorize_workflow_action(
        actor_permissions=permissions,
        actor_tenant_id=actor_tenant_id,
        workflow_tenant_id=workflow_tenant_id,
        action=action,
    )
    if not decision.allowed:
        # A cross-tenant workflow is denied the exact same way as a
        # genuinely nonexistent one -- see `identity.share_policy`'s own
        # anti-enumeration precedent, reused here deliberately.
        if decision.reason in ("cross_tenant", "workflow_not_found"):
            raise _not_found()
        raise _forbidden()


def _rate_limit(request: Request, action: str) -> None:
    enforce_api_action_rate_limit(request, action, get_settings())


def _log_action(
    user: User,
    workflow: Workflow,
    *,
    action: str,
    from_status: str | None = None,
    to_status: str | None = None,
) -> None:
    """One structured `security.audit` line per governed mutation. Carries
    ids, the action name, and the status transition only -- never the
    title/description/comment/outcome text (master rule 7's "never log
    ... full result rows" spirit, applied to workflow content)."""
    log_security_event(
        "workflow_action",
        "info",
        f"workflow {action}",
        action=action,
        workflow_id=str(workflow.id),
        recommendation_id=str(workflow.recommendation_id),
        actor_user_id=str(user.id),
        from_status=from_status,
        to_status=to_status,
    )


def _owner_names_for(session: Session, workflows: list[Workflow]) -> dict[uuid.UUID, str]:
    owner_ids = {wf.owner_user_id for wf in workflows if wf.owner_user_id is not None}
    return owner_display_names(session, owner_ids)


def _out(workflow: Workflow, owner_display_name: str | None = None) -> WorkflowOut:
    return WorkflowOut(
        id=workflow.id,
        tenant_id=workflow.tenant_id,
        recommendation_id=workflow.recommendation_id,
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,  # type: ignore[arg-type]
        owner_user_id=workflow.owner_user_id,
        owner_display_name=owner_display_name,
        due_date=workflow.due_date,
        external_system=workflow.external_system,
        external_reference_id=workflow.external_reference_id,
        external_reference_url=workflow.external_reference_url,
        outcome_summary=workflow.outcome_summary,
        outcome_measured_impact=workflow.outcome_measured_impact,
        created_by_user_id=workflow.created_by_user_id,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _event_out(event: WorkflowEvent) -> WorkflowEventOut:
    return WorkflowEventOut(
        id=event.id,
        workflow_id=event.workflow_id,
        from_status=event.from_status,  # type: ignore[arg-type]
        to_status=event.to_status,  # type: ignore[arg-type]
        event_type=event.event_type,  # type: ignore[arg-type]
        actor_user_id=event.actor_user_id,
        actor_label=event.actor_label,
        reason=event.reason,
        detail=event.detail,
        created_at=event.created_at,
    )


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
def create_workflow_route(
    payload: CreateWorkflowRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """Proposes a new workflow from an already-`accepted`/
    `partially_useful` recommendation. Refused (409) if that
    recommendation already has a non-terminal workflow -- see
    `identity/repositories/workflow.py::create_workflow`'s own
    docstring."""
    user, session = user_and_session
    tenant_id = resolve_actor_tenant_id(user)
    _authorize(user, session, WorkflowAction.CREATE, None)
    _rate_limit(request, "workflow_create")

    recommendation = get_recommendation_by_id(session, payload.recommendation_id)
    if recommendation is None or recommendation.tenant_id != tenant_id:
        raise _not_found("Recommendation not found.")

    try:
        workflow = create_workflow(
            session,
            recommendation=recommendation,
            created_by_user_id=user.id,
            title=payload.title,
            description=payload.description,
        )
    except RecommendationNotEligibleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DuplicateActiveWorkflowError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(user, workflow, action="create", to_status=workflow.status)
    return _out(workflow)


@router.get("", response_model=list[WorkflowOut])
def list_workflows_route(
    status_filter: str | None = Query(default=None, alias="status"),
    owner_user_id: str | None = Query(default=None),
    unassigned: bool = Query(default=False),
    recommendation_id: uuid.UUID | None = Query(default=None),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[WorkflowOut]:
    """Lists the caller's own tenant's workflows. `unassigned=true` takes
    precedence over an `owner_user_id` if both are given, mirroring
    `GET /recommendations`'s own identical filter precedence."""
    user, session = user_and_session
    tenant_id = resolve_actor_tenant_id(user)
    if tenant_id is None:
        return []
    _authorize(user, session, WorkflowAction.LIST, None)
    owner_id: uuid.UUID | None = None
    if owner_user_id and not unassigned:
        try:
            owner_id = uuid.UUID(owner_user_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="owner_user_id must be a UUID.",
            ) from exc
    workflows = list_workflows(
        session,
        tenant_id,
        status=status_filter,
        owner_user_id=owner_id,
        unassigned=unassigned,
        recommendation_id=recommendation_id,
    )
    names = _owner_names_for(session, workflows)
    return [_out(wf, names.get(wf.owner_user_id) if wf.owner_user_id else None) for wf in workflows]


@router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow_route(
    workflow_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.VIEW, workflow.tenant_id)
    names = _owner_names_for(session, [workflow])
    return _out(workflow, names.get(workflow.owner_user_id) if workflow.owner_user_id else None)


@router.get("/{workflow_id}/events", response_model=list[WorkflowEventOut])
def get_workflow_events_route(
    workflow_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[WorkflowEventOut]:
    """The full, append-only audit trail for one workflow -- status
    transitions, comments, owner/due-date changes, and external-reference
    changes, oldest first. The mission's own "test ... audit"
    requirement."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.VIEW, workflow.tenant_id)
    events = list_workflow_events(session, workflow.id)
    return [_event_out(event) for event in events]


@router.post("/{workflow_id}/submit-for-approval", response_model=WorkflowOut)
def submit_for_approval_route(
    workflow_id: uuid.UUID,
    request: Request,
    payload: SubmitForApprovalRequest = _DEFAULT_SUBMIT_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """`proposed` -> `approval_required`."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.SUBMIT_FOR_APPROVAL, workflow.tenant_id)
    _rate_limit(request, "workflow_submit_for_approval")
    from_status = workflow.status
    try:
        workflow = submit_for_approval(
            session, workflow, actor_user_id=user.id, reason=payload.reason
        )
    except InvalidWorkflowStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(
        user,
        workflow,
        action="submit_for_approval",
        from_status=from_status,
        to_status=workflow.status,
    )
    return _out(workflow)


@router.post("/{workflow_id}/approve", response_model=WorkflowOut)
def approve_workflow_route(
    workflow_id: uuid.UUID,
    request: Request,
    payload: ApproveWorkflowRequest = _DEFAULT_APPROVE_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """`approval_required` -> `approved` -- `Permission.WORKFLOW_REVIEW`
    required (see `workflow/policy.py`'s own docstring)."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.APPROVE, workflow.tenant_id)
    _rate_limit(request, "workflow_approve")
    from_status = workflow.status
    try:
        workflow = approve_workflow(session, workflow, actor_user_id=user.id, reason=payload.reason)
    except InvalidWorkflowStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(
        user, workflow, action="approve", from_status=from_status, to_status=workflow.status
    )
    return _out(workflow)


@router.post("/{workflow_id}/reject", response_model=WorkflowOut)
def reject_workflow_route(
    workflow_id: uuid.UUID,
    request: Request,
    payload: RejectWorkflowRequest = _DEFAULT_REJECT_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """`approval_required` -> `rejected` -- `Permission.WORKFLOW_REVIEW`
    required."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.REJECT, workflow.tenant_id)
    _rate_limit(request, "workflow_reject")
    from_status = workflow.status
    try:
        workflow = _reject_workflow(session, workflow, actor_user_id=user.id, reason=payload.reason)
    except InvalidWorkflowStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(user, workflow, action="reject", from_status=from_status, to_status=workflow.status)
    return _out(workflow)


@router.post("/{workflow_id}/assign", response_model=WorkflowOut)
def assign_workflow_route(
    workflow_id: uuid.UUID,
    payload: AssignWorkflowRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """Sets the owner (and, optionally, due date) of a workflow --
    `approved -> assigned` on first assignment, or a plain reassignment
    from `assigned`/`in_progress`. An owner who is missing, disabled, in
    another tenant, or unable to act on workflows gets the **same** 404
    -- so this route can't be used to probe whether a given user id
    exists in some other tenant."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.ASSIGN, workflow.tenant_id)
    _rate_limit(request, "workflow_assign")
    from_status = workflow.status
    try:
        workflow = assign_workflow(
            session,
            workflow,
            owner_user_id=payload.owner_user_id,
            actor_user_id=user.id,
            due_date=payload.due_date,
        )
    except InvalidWorkflowOwnerError as exc:
        log_security_event(
            "workflow_owner_rejected",
            "warning",
            "owner assignment refused",
            workflow_id=str(workflow.id),
            actor_user_id=str(user.id),
            requested_owner_user_id=str(payload.owner_user_id),
        )
        raise _not_found("Owner not found in this tenant.") from exc
    except InvalidWorkflowStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(user, workflow, action="assign", from_status=from_status, to_status=workflow.status)
    names = _owner_names_for(session, [workflow])
    return _out(workflow, names.get(workflow.owner_user_id) if workflow.owner_user_id else None)


@router.post("/{workflow_id}/due-date", response_model=WorkflowOut)
def set_due_date_route(
    workflow_id: uuid.UUID,
    payload: SetDueDateRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """Sets or clears a workflow's due date without touching owner or
    status."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.ASSIGN, workflow.tenant_id)
    _rate_limit(request, "workflow_due_date")
    workflow = set_due_date(session, workflow, actor_user_id=user.id, due_date=payload.due_date)
    _log_action(
        user,
        workflow,
        action="set_due_date",
        from_status=workflow.status,
        to_status=workflow.status,
    )
    names = _owner_names_for(session, [workflow])
    return _out(workflow, names.get(workflow.owner_user_id) if workflow.owner_user_id else None)


@router.post("/{workflow_id}/start", response_model=WorkflowOut)
def start_workflow_route(
    workflow_id: uuid.UUID,
    request: Request,
    payload: StartWorkflowRequest = _DEFAULT_START_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """`assigned` -> `in_progress`."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.START, workflow.tenant_id)
    _rate_limit(request, "workflow_start")
    from_status = workflow.status
    try:
        workflow = start_workflow(session, workflow, actor_user_id=user.id, reason=payload.reason)
    except InvalidWorkflowStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(user, workflow, action="start", from_status=from_status, to_status=workflow.status)
    return _out(workflow)


@router.post("/{workflow_id}/complete", response_model=WorkflowOut)
def complete_workflow_route(
    workflow_id: uuid.UUID,
    payload: CompleteWorkflowRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """`in_progress` -> `completed`, recording the caller's own report
    of the outcome -- never independently verified (see
    `identity.models.Workflow`'s own docstring)."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.COMPLETE, workflow.tenant_id)
    _rate_limit(request, "workflow_complete")
    from_status = workflow.status
    try:
        workflow = complete_workflow(
            session,
            workflow,
            actor_user_id=user.id,
            outcome_summary=payload.outcome_summary,
            outcome_measured_impact=payload.outcome_measured_impact,
            reason=payload.reason,
        )
    except InvalidWorkflowStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(
        user, workflow, action="complete", from_status=from_status, to_status=workflow.status
    )
    return _out(workflow)


@router.post("/{workflow_id}/expire", response_model=WorkflowOut)
def expire_workflow_route(
    workflow_id: uuid.UUID,
    request: Request,
    payload: ExpireWorkflowRequest = _DEFAULT_EXPIRE_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """Any non-terminal status -> `expired` -- administrative override,
    `Permission.WORKFLOW_MANAGE` only."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.EXPIRE, workflow.tenant_id)
    _rate_limit(request, "workflow_expire")
    from_status = workflow.status
    try:
        workflow = expire_workflow(session, workflow, actor_user_id=user.id, reason=payload.reason)
    except InvalidWorkflowStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_action(user, workflow, action="expire", from_status=from_status, to_status=workflow.status)
    return _out(workflow)


@router.post("/{workflow_id}/comments", response_model=WorkflowEventOut)
def add_comment_route(
    workflow_id: uuid.UUID,
    payload: AddCommentRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowEventOut:
    """Appends a comment to the audit trail. Never changes status."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.ADD_COMMENT, workflow.tenant_id)
    _rate_limit(request, "workflow_comment")
    event = add_comment(session, workflow, actor_user_id=user.id, comment=payload.comment)
    _log_action(
        user, workflow, action="comment", from_status=event.from_status, to_status=event.to_status
    )
    return _event_out(event)


@router.post("/{workflow_id}/external-reference", response_model=WorkflowOut)
def set_external_reference_route(
    workflow_id: uuid.UUID,
    payload: SetExternalReferenceRequest,
    request: Request,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> WorkflowOut:
    """Sets or clears the workflow's external task reference -- plain
    metadata only, never a live connector call (see `workflow/models.py`'s
    own module docstring)."""
    user, session = user_and_session
    workflow = _require_workflow(session, workflow_id)
    _authorize(user, session, WorkflowAction.SET_EXTERNAL_REFERENCE, workflow.tenant_id)
    _rate_limit(request, "workflow_external_reference")
    workflow = set_external_reference(
        session,
        workflow,
        actor_user_id=user.id,
        external_system=payload.external_system,
        external_reference_id=payload.external_reference_id,
        external_reference_url=payload.external_reference_url,
    )
    _log_action(
        user,
        workflow,
        action="set_external_reference",
        from_status=workflow.status,
        to_status=workflow.status,
    )
    return _out(workflow)
