"""The AI Governance Center's REST surface -- Prompt 44
(`44_AI_GOVERNANCE_CENTER_CONTRACT.md`).

**Every route here does exactly one authorization dance**: resolve the
`GovernanceAssetVersion` row by id (never trusting a query filter alone
to scope it), then call `governance_center.policy
.authorize_governance_action` and act only on an `allowed=True` decision
-- mirrors `api/semantic_catalog.py`/`api/onboarding.py`'s own identical
discipline.

**No `Settings.enable_*` feature flag** -- this is an administrative/
compliance surface gated purely by RBAC, the identical posture
`api/onboarding.py`/`api/semantic_catalog.py`/`api/platform_admin.py`
already establish for their own domains (none of them has a feature
flag either; "always present, gated by permission" is the correct shape
for a governance surface, unlike an optional analytics engine).

**Rollback is a release/operations action (`GOVERNANCE_MANAGE`), not a
reviewer one** -- the identical tier `deploy` itself sits at, since both
change what is actually live in production.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from governance_center.models import GovernedAssetType
from governance_center.policy import GovernanceAction, authorize_governance_action
from identity.models import GovernanceAssetVersion, GovernanceEvent, User
from identity.repositories.governance import (
    InvalidGovernanceStageTransitionError,
    RollbackTargetIneligibleError,
    get_current_deployed,
    get_version_by_id,
    version_to_snapshot,
)
from identity.repositories.governance import approve as _approve
from identity.repositories.governance import create_draft as _create_draft
from identity.repositories.governance import deploy as _deploy
from identity.repositories.governance import list_events as _list_events
from identity.repositories.governance import list_versions as _list_versions
from identity.repositories.governance import record_evaluation_results as _record_evaluation
from identity.repositories.governance import record_test_results as _record_test_results
from identity.repositories.governance import record_usage as _record_usage
from identity.repositories.governance import reject as _reject
from identity.repositories.governance import retire as _retire
from identity.repositories.governance import rollback as _rollback
from identity.repositories.governance import submit_for_evaluation as _submit_for_evaluation
from identity.repositories.governance import submit_for_testing as _submit_for_testing
from identity.repositories.governance import update_draft as _update_draft
from identity.repositories.governance import usage_summary as _usage_summary
from identity.repositories.users import get_user_permissions
from sqlalchemy.orm import Session

from api.governance_schemas import (
    ApproveRequest,
    CreateGovernanceAssetRequest,
    DeployResponse,
    GovernanceAssetVersionOut,
    GovernanceEventOut,
    RecordEvaluationResultsRequest,
    RecordTestResultsRequest,
    RecordUsageRequest,
    RejectRequest,
    RetireRequest,
    RollbackRequest,
    RollbackResponse,
    UpdateGovernanceDraftRequest,
    UsageSummaryOut,
)
from api.identity_authz import require_local_user
from security.audit_log import log_security_event
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/governance", tags=["governance"])


#: Default bodies for the two routes whose request model has every field
#: optional (`approve`/`retire`) -- module-level singletons rather than
#: calling the model constructor directly in the route's own default-
#: argument position (both are frozen-shaped, read-only here, so sharing
#: one instance across every call is safe).
_DEFAULT_APPROVE_REQUEST = ApproveRequest()
_DEFAULT_RETIRE_REQUEST = RetireRequest()


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Governance asset version not found."
    )


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")


def _require_version(session: Session, version_id: uuid.UUID) -> GovernanceAssetVersion:
    version = get_version_by_id(session, version_id)
    if version is None:
        raise _not_found()
    return version


def _authorize(
    user: User, session: Session, action: GovernanceAction, version: GovernanceAssetVersion | None
) -> None:
    permissions = get_user_permissions(session, user.id)
    actor_tenant_id = resolve_actor_tenant_id(user)
    decision = authorize_governance_action(
        actor_permissions=permissions,
        actor_tenant_id=actor_tenant_id,
        asset_tenant_id=version.tenant_id if version is not None else None,
        action=action,
    )
    if not decision.allowed:
        # A cross-tenant version is denied the exact same way as a
        # genuinely nonexistent one -- see `identity.share_policy`'s own
        # anti-enumeration precedent, reused here deliberately (identical
        # to `api/onboarding.py`/`api/semantic_catalog.py`'s own choice).
        if decision.reason == "cross_tenant":
            raise _not_found()
        raise _forbidden()


def _out(version: GovernanceAssetVersion) -> GovernanceAssetVersionOut:
    snapshot = version_to_snapshot(version)
    return GovernanceAssetVersionOut(
        id=snapshot.id,
        tenant_id=snapshot.tenant_id,
        asset_type=snapshot.asset_type.value,
        asset_key=snapshot.asset_key,
        display_name=snapshot.display_name,
        description=snapshot.description,
        owner=snapshot.owner,
        version=snapshot.version,
        stage=snapshot.stage.value,
        truth_level=snapshot.truth_level.value,
        config=snapshot.config,
        test_results=snapshot.test_results,
        evaluation_results=snapshot.evaluation_results,
        approval_notes=snapshot.approval_notes,
        rejection_reason=snapshot.rejection_reason,
        supersedes_id=snapshot.supersedes_id,
        created_by_user_id=str(version.created_by_user_id) if version.created_by_user_id else None,
        tested_by_user_id=str(version.tested_by_user_id) if version.tested_by_user_id else None,
        tested_at=version.tested_at,
        evaluated_by_user_id=(
            str(version.evaluated_by_user_id) if version.evaluated_by_user_id else None
        ),
        evaluated_at=version.evaluated_at,
        approved_by_user_id=(
            str(version.approved_by_user_id) if version.approved_by_user_id else None
        ),
        approved_at=version.approved_at,
        deployed_by_user_id=(
            str(version.deployed_by_user_id) if version.deployed_by_user_id else None
        ),
        deployed_at=version.deployed_at,
        retired_by_user_id=str(version.retired_by_user_id) if version.retired_by_user_id else None,
        retired_at=version.retired_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def _event_out(event: GovernanceEvent) -> GovernanceEventOut:
    return GovernanceEventOut(
        id=str(event.id),
        asset_version_id=str(event.asset_version_id),
        event_type=event.event_type,
        from_stage=event.from_stage,
        to_stage=event.to_stage,
        actor_user_id=str(event.actor_user_id) if event.actor_user_id else None,
        actor_label=event.actor_label,
        reason=event.reason,
        payload=event.payload,
        created_at=event.created_at,
    )


@router.post("/assets", response_model=GovernanceAssetVersionOut)
def create_asset_draft(
    payload: CreateGovernanceAssetRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    """Creates a brand-new `"draft"` version of one governed asset --
    version `1` for a never-before-seen `asset_key`, or the next version
    otherwise."""
    user, session = user_and_session
    try:
        asset_type = GovernedAssetType(payload.asset_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unrecognized asset_type {payload.asset_type!r}.",
        ) from None
    _authorize(user, session, GovernanceAction.CREATE_DRAFT, None)
    tenant_id = resolve_actor_tenant_id(user)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not resolve a tenant for this account.",
        )
    version = _create_draft(
        session,
        tenant_id=tenant_id,
        asset_type=asset_type.value,
        asset_key=payload.asset_key,
        display_name=payload.display_name,
        description=payload.description,
        config=payload.config,
        owner=payload.owner,
        created_by_user_id=user.id,
    )
    log_security_event(
        "governance_asset_draft_created",
        "info",
        "A new governance asset draft version was created.",
        asset_version_id=str(version.id),
        asset_type=asset_type.value,
        asset_key=payload.asset_key,
        actor_user_id=str(user.id),
    )
    return _out(version)


@router.get("/assets", response_model=list[GovernanceAssetVersionOut])
def list_asset_versions(
    asset_type: str | None = Query(default=None),
    asset_key: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[GovernanceAssetVersionOut]:
    user, session = user_and_session
    _authorize(user, session, GovernanceAction.LIST, None)
    tenant_id = resolve_actor_tenant_id(user)
    if tenant_id is None:
        return []
    versions = _list_versions(
        session, tenant_id, asset_type=asset_type, asset_key=asset_key, stage=stage
    )
    return [_out(v) for v in versions]


@router.get("/assets/current", response_model=GovernanceAssetVersionOut | None)
def get_current_deployed_version(
    asset_type: str = Query(...),
    asset_key: str = Query(...),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut | None:
    user, session = user_and_session
    _authorize(user, session, GovernanceAction.LIST, None)
    tenant_id = resolve_actor_tenant_id(user)
    if tenant_id is None:
        return None
    version = get_current_deployed(session, tenant_id, asset_type, asset_key)
    return _out(version) if version is not None else None


@router.get("/assets/{version_id}", response_model=GovernanceAssetVersionOut)
def get_asset_version(
    version_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.VIEW, version)
    return _out(version)


@router.get("/assets/{version_id}/events", response_model=list[GovernanceEventOut])
def get_asset_version_events(
    version_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[GovernanceEventOut]:
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.VIEW, version)
    return [_event_out(e) for e in _list_events(session, version.id)]


@router.get("/assets/{version_id}/usage-summary", response_model=UsageSummaryOut)
def get_asset_version_usage_summary(
    version_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> UsageSummaryOut:
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.VIEW, version)
    return UsageSummaryOut(**_usage_summary(session, version.id))


@router.patch("/assets/{version_id}", response_model=GovernanceAssetVersionOut)
def update_asset_draft(
    version_id: uuid.UUID,
    payload: UpdateGovernanceDraftRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.EDIT_DRAFT, version)
    try:
        updated = _update_draft(
            session,
            version,
            display_name=payload.display_name,
            description=payload.description,
            config=payload.config,
            owner=payload.owner,
        )
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(updated)


@router.post("/assets/{version_id}/submit-for-testing", response_model=GovernanceAssetVersionOut)
def submit_for_testing(
    version_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    """`"draft"` -> `"testing"`."""
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.SUBMIT_FOR_TESTING, version)
    try:
        updated = _submit_for_testing(session, version, actor_user_id=user.id)
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(updated)


@router.post("/assets/{version_id}/test-results", response_model=GovernanceAssetVersionOut)
def record_test_results(
    version_id: uuid.UUID,
    payload: RecordTestResultsRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    """Records test results on a `"testing"` version -- does not itself
    change its stage."""
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.RECORD_TEST_RESULTS, version)
    try:
        updated = _record_test_results(
            session, version, test_results=payload.test_results, actor_user_id=user.id
        )
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(updated)


@router.post("/assets/{version_id}/submit-for-evaluation", response_model=GovernanceAssetVersionOut)
def submit_for_evaluation(
    version_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    """`"testing"` -> `"evaluating"`."""
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.SUBMIT_FOR_EVALUATION, version)
    try:
        updated = _submit_for_evaluation(session, version, actor_user_id=user.id)
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(updated)


@router.post("/assets/{version_id}/evaluation-results", response_model=GovernanceAssetVersionOut)
def record_evaluation_results(
    version_id: uuid.UUID,
    payload: RecordEvaluationResultsRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    """Records evaluation results on an `"evaluating"` version -- does
    not itself change its stage."""
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.RECORD_EVALUATION_RESULTS, version)
    try:
        updated = _record_evaluation(
            session, version, evaluation_results=payload.evaluation_results, actor_user_id=user.id
        )
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(updated)


@router.post("/assets/{version_id}/approve", response_model=GovernanceAssetVersionOut)
def approve_asset_version(
    version_id: uuid.UUID,
    payload: ApproveRequest = _DEFAULT_APPROVE_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    """`"evaluating"` -> `"approved"` -- the moment this version becomes
    `CONFIRMED_BUSINESS_TRUTH`."""
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.APPROVE, version)
    try:
        updated = _approve(session, version, actor_user_id=user.id, notes=payload.notes)
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    log_security_event(
        "governance_asset_approved",
        "info",
        "A governed AI-asset version was approved for deployment.",
        asset_version_id=str(updated.id),
        actor_user_id=str(user.id),
    )
    return _out(updated)


@router.post("/assets/{version_id}/reject", response_model=GovernanceAssetVersionOut)
def reject_asset_version(
    version_id: uuid.UUID,
    payload: RejectRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.REJECT, version)
    try:
        updated = _reject(session, version, actor_user_id=user.id, reason=payload.reason)
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(updated)


@router.post("/assets/{version_id}/deploy", response_model=DeployResponse)
def deploy_asset_version(
    version_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> DeployResponse:
    """`"approved"` -> `"deployed"` -- the mission's own release action.
    Atomically retires whichever version of the same asset was
    previously deployed, if any."""
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.DEPLOY, version)
    try:
        deployed, retired = _deploy(session, version, actor_user_id=user.id)
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    log_security_event(
        "governance_asset_deployed",
        "info",
        "A governed AI-asset version was deployed to production.",
        asset_version_id=str(deployed.id),
        asset_type=deployed.asset_type,
        asset_key=deployed.asset_key,
        retired_version_id=str(retired.id) if retired is not None else None,
        actor_user_id=str(user.id),
    )
    return DeployResponse(deployed=_out(deployed), retired=_out(retired) if retired else None)


@router.post("/assets/{version_id}/rollback", response_model=RollbackResponse)
def rollback_asset_version(
    version_id: uuid.UUID,
    payload: RollbackRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> RollbackResponse:
    """Rolls back `version_id`'s own asset from its *current* deployed
    version to `payload.target_version_id` -- an explicit, operator-tier
    release action (see `governance_center.policy`'s own docstring for
    why this sits at the same tier as `deploy`).

    `version_id` must itself be the asset's *currently deployed* version
    -- the caller names what it is rolling back *from*, matching how an
    operator actually thinks about this action ("roll back the live
    version"), not an arbitrary id.
    """
    user, session = user_and_session
    current_deployed = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.ROLLBACK, current_deployed)
    target_version = _require_version(session, payload.target_version_id)
    _authorize(user, session, GovernanceAction.ROLLBACK, target_version)
    try:
        rolled_back, redeployed = _rollback(
            session,
            current_deployed,
            target_version,
            actor_user_id=user.id,
            reason=payload.reason,
        )
    except (InvalidGovernanceStageTransitionError, RollbackTargetIneligibleError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    log_security_event(
        "governance_asset_rolled_back",
        "warning",
        "A governed AI-asset deployment was rolled back.",
        rolled_back_version_id=str(rolled_back.id),
        redeployed_version_id=str(redeployed.id),
        asset_type=rolled_back.asset_type,
        asset_key=rolled_back.asset_key,
        reason=payload.reason,
        actor_user_id=str(user.id),
    )
    return RollbackResponse(rolled_back=_out(rolled_back), redeployed=_out(redeployed))


@router.post("/assets/{version_id}/retire", response_model=GovernanceAssetVersionOut)
def retire_asset_version(
    version_id: uuid.UUID,
    payload: RetireRequest = _DEFAULT_RETIRE_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceAssetVersionOut:
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.RETIRE, version)
    try:
        updated = _retire(session, version, actor_user_id=user.id, reason=payload.reason)
    except InvalidGovernanceStageTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(updated)


@router.post("/assets/{version_id}/usage", response_model=GovernanceEventOut)
def record_asset_usage(
    version_id: uuid.UUID,
    payload: RecordUsageRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> GovernanceEventOut:
    user, session = user_and_session
    version = _require_version(session, version_id)
    _authorize(user, session, GovernanceAction.RECORD_USAGE, version)
    event = _record_usage(
        session,
        version,
        actor_user_id=user.id,
        payload=payload.model_dump(exclude_none=True),
    )
    return _event_out(event)
