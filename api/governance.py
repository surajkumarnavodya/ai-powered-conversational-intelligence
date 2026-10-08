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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from governance_center.artifacts import (
    AgentArtifactSpec,
    AnalyticalPolicyArtifactSpec,
    ArtifactSpecError,
    PromptArtifactSpec,
    parse_artifact_spec,
)
from governance_center.inventory import (
    InventoryEntry,
    full_inventory,
    known_evaluation_suites,
    known_model_names,
    known_tool_names,
)
from governance_center.models import GovernedAssetType
from governance_center.policy import GovernanceAction, authorize_governance_action
from governance_center.safety import SafetyInvariant, invariants_present
from governance_center.validation import (
    ArtifactValidationReport,
    ValidationContext,
    requires_validation,
    validate_artifact_config,
)
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
    ArtifactValidationReportOut,
    ArtifactViolationOut,
    CreateGovernanceAssetRequest,
    DeployResponse,
    GovernanceAssetVersionOut,
    GovernanceEventOut,
    InventoryEntryOut,
    InventoryOut,
    RecordEvaluationResultsRequest,
    RecordTestResultsRequest,
    RecordUsageRequest,
    RejectRequest,
    RetireRequest,
    RollbackRequest,
    RollbackResponse,
    UpdateGovernanceDraftRequest,
    UsageSummaryOut,
    ValidateArtifactRequest,
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


# --------------------------------------------------------------------------
# Prompt 45 (`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`) -- artifact-spec
# validation, enforced at four lifecycle gates below. Everything here is
# additive: an asset type with no typed spec (`model`/`provider`/`tool`/
# `retrieval_source`) takes exactly the Prompt 44 path it always did,
# because `requires_validation` is false for it.
# --------------------------------------------------------------------------


def _validation_context() -> ValidationContext:
    """Builds the validator's context from the live registries.

    Rebuilt per request rather than cached: it reads `Settings` (whose
    own accessor is already process-cached) plus two in-process module
    registries, so the cost is a handful of dict comprehensions, and a
    cache here would be one more thing to invalidate for no measurable
    gain.
    """
    inventory = full_inventory()
    return ValidationContext(
        known_tools=known_tool_names(),
        known_models=known_model_names(),
        known_evaluation_suites=known_evaluation_suites(),
        live_prompt_invariants={
            entry.asset_key: frozenset(entry.declared_invariants) for entry in inventory.prompts
        },
        live_agent_tools={
            entry.asset_key: frozenset(entry.spec.allowed_tools)
            for entry in inventory.agents
            if isinstance(entry.spec, AgentArtifactSpec)
        },
        live_agent_truth_levels={
            entry.asset_key: entry.spec.max_truth_level
            for entry in inventory.agents
            if isinstance(entry.spec, AgentArtifactSpec)
        },
    )


def _report_out(report: ArtifactValidationReport) -> ArtifactValidationReportOut:
    return ArtifactValidationReportOut(
        asset_type=report.asset_type.value,
        asset_key=report.asset_key,
        is_valid=report.is_valid,
        compared_against_live=report.compared_against_live,
        truth_level=report.truth_level.value,
        violations=tuple(
            ArtifactViolationOut(
                code=violation.code.value,
                severity=violation.severity.value,
                subject=violation.subject,
                message=violation.message,
            )
            for violation in report.violations
        ),
    )


def _deployed_predecessor_config(
    session: Session, version: GovernanceAssetVersion
) -> dict[str, Any] | None:
    """The currently-deployed config for this version's own asset key.

    Used as the inherited baseline at the submit-for-testing and deploy
    gates. Without it, those two gates would catch a config that was
    tampered into containing a *subversive* instruction but not one that
    merely had its framing deleted -- there would be nothing to compare
    the absence against. Returns `None` when this version is itself the
    currently-deployed one, or when nothing is deployed for that key.
    """
    deployed = get_current_deployed(
        session, version.tenant_id, version.asset_type, version.asset_key
    )
    if deployed is None or deployed.id == version.id:
        return None
    return dict(deployed.config or {})


def _prompt_invariants_of(config: dict[str, Any]) -> frozenset[SafetyInvariant]:
    """The invariants a stored prompt config declares or carries.

    Both, unioned: a predecessor that carried the untrusted-data framing
    in its template without declaring it still constrains its successor,
    because the control was genuinely there. A config that does not parse
    as a prompt spec contributes nothing -- it cannot have established a
    baseline, and refusing the action over an already-stored malformed
    row would block a reviewer from fixing it.
    """
    try:
        spec = parse_artifact_spec(GovernedAssetType.PROMPT, config)
    except ArtifactSpecError:
        return frozenset()
    if not isinstance(spec, PromptArtifactSpec):  # pragma: no cover - type narrowing
        return frozenset()
    return frozenset(spec.safety_invariants) | invariants_present(spec.template)


def _enforce_artifact_validity(
    asset_type: GovernedAssetType,
    asset_key: str,
    config: dict[str, Any],
    *,
    gate: str,
    actor_user_id: str,
    predecessor_config: dict[str, Any] | None = None,
) -> None:
    """Refuses a lifecycle action whose artifact has a blocking violation.

    Applied at four gates -- create-draft, edit-draft, submit-for-testing
    and deploy (see each route). Re-validating at **deploy** and not only
    at authoring time is the point: an artifact's `config` is mutable
    while it is a draft, so a check that ran only at creation could be
    satisfied by a first version and then edited away before release.

    `predecessor_config` closes a real hole found while writing this
    feature's own regression suite: without it, an author could escape
    the dropped-invariant check by deleting the *declaration* along with
    the control. Version N declares `untrusted_data_framing`; version
    N+1 declares nothing and carries nothing, so nothing was "dropped"
    relative to its own (now empty) declaration, and it passed. Passing
    the predecessor makes the required set monotonic: a successor
    inherits whatever its predecessor declared or carried, and can only
    ever add to it. That is what makes "a prompt change cannot weaken
    security" true across versions rather than only within one.

    Args:
        asset_type: The version's asset type. A type with no typed spec
            returns immediately, leaving Prompt 44's behavior untouched.
        asset_key: The version's asset key.
        config: The raw `config` payload to validate.
        gate: Which lifecycle gate is being enforced, for the audit event.
        actor_user_id: The acting account, for the audit event.
        predecessor_config: The config this one supersedes -- the stored
            draft being edited, or the currently-deployed version of the
            same asset key. Its invariants are folded into the required
            set. `None` when there is genuinely no predecessor.

    Raises:
        HTTPException: 422 with the full report when a blocking violation
            is present. Every violation is included, not just the first,
            so one round-trip surfaces every real problem.
    """
    if not requires_validation(asset_type):
        return
    context = _validation_context()
    if predecessor_config and asset_type is GovernedAssetType.PROMPT:
        inherited = _prompt_invariants_of(predecessor_config)
        if inherited:
            merged = dict(context.live_prompt_invariants)
            merged[asset_key] = merged.get(asset_key, frozenset()) | inherited
            context = context.model_copy(update={"live_prompt_invariants": merged})
    report = validate_artifact_config(asset_type, asset_key, config, context)
    if report.is_valid:
        return
    # Metadata only -- violation codes and subjects, never the `config`
    # payload or template text (a template is caller-supplied content and
    # has no business in a log line).
    log_security_event(
        "governance_artifact_validation_failed",
        "warning",
        "A governed AI-asset version was refused by artifact validation.",
        asset_type=asset_type.value,
        asset_key=asset_key,
        gate=gate,
        violation_codes=sorted({violation.code.value for violation in report.errors}),
        actor_user_id=actor_user_id,
    )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=_report_out(report).model_dump(),
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
    caller_tenant_id = resolve_actor_tenant_id(user)
    deployed_predecessor = (
        get_current_deployed(session, caller_tenant_id, asset_type.value, payload.asset_key)
        if caller_tenant_id
        else None
    )
    _enforce_artifact_validity(
        asset_type,
        payload.asset_key,
        payload.config,
        gate="create_draft",
        actor_user_id=str(user.id),
        predecessor_config=(
            dict(deployed_predecessor.config or {}) if deployed_predecessor else None
        ),
    )
    tenant_id = caller_tenant_id
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
    _enforce_artifact_validity(
        GovernedAssetType(version.asset_type),
        version.asset_key,
        payload.config if payload.config is not None else dict(version.config or {}),
        gate="edit_draft",
        actor_user_id=str(user.id),
        predecessor_config=dict(version.config or {}),
    )
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
    _enforce_artifact_validity(
        GovernedAssetType(version.asset_type),
        version.asset_key,
        dict(version.config or {}),
        gate="submit_for_testing",
        actor_user_id=str(user.id),
        predecessor_config=_deployed_predecessor_config(session, version),
    )
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
    _enforce_artifact_validity(
        GovernedAssetType(version.asset_type),
        version.asset_key,
        dict(version.config or {}),
        gate="deploy",
        actor_user_id=str(user.id),
        predecessor_config=_deployed_predecessor_config(session, version),
    )
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


# --------------------------------------------------------------------------
# Prompt 45 -- the registry's own inventory and validation routes.
# --------------------------------------------------------------------------


def _inventory_entry_out(entry: InventoryEntry) -> InventoryEntryOut:
    """Flattens one inventory entry into its wire shape.

    The spec's own type-specific fields are projected into a single flat
    shape (`variables`/`allowed_tools`/`max_truth_level`/`policy_rules`),
    each empty for an entry it does not apply to -- deliberately one
    response model rather than three, so a client renders one table.
    **A prompt's own template text is not included**: the inventory is
    about which artifacts exist and whether they still carry their
    controls, and shipping every live system prompt over HTTP would hand
    any reader of this route the full prompt set, which is exactly what
    every one of those prompts' own `NO_PROMPT_DISCLOSURE` rule exists to
    prevent. `fingerprint` is what a caller compares instead.
    """
    spec = entry.spec
    variables: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    max_truth_level: str | None = None
    policy_rules: tuple[str, ...] = ()
    if isinstance(spec, PromptArtifactSpec):
        variables = tuple(variable.name for variable in spec.variables)
    elif isinstance(spec, AgentArtifactSpec):
        allowed_tools = spec.allowed_tools
        max_truth_level = spec.max_truth_level.value
    elif isinstance(spec, AnalyticalPolicyArtifactSpec):
        policy_rules = tuple(f"{rule.subject} {rule.operator} {rule.value}" for rule in spec.rules)
    return InventoryEntryOut(
        asset_type=entry.asset_type.value,
        asset_key=entry.asset_key,
        display_name=entry.display_name,
        source_module=entry.source_module,
        source_symbol=entry.source_symbol,
        fingerprint=entry.fingerprint,
        truth_level=entry.truth_level.value,
        declared_invariants=tuple(invariant.value for invariant in entry.declared_invariants),
        variables=variables,
        allowed_tools=allowed_tools,
        max_truth_level=max_truth_level,
        policy_rules=policy_rules,
        notes=entry.notes,
    )


@router.get("/inventory", response_model=InventoryOut)
def get_registry_inventory(
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> InventoryOut:
    """The live inventory of this platform's prompts, agents and policies.

    Gated on `GovernanceAction.LIST` -- the action for a route with no
    one specific target version, authorized by permission alone (either
    governance permission grants it), exactly as `GET /governance/assets`
    already is. `VIEW` would be wrong here: it always tenant-matches
    against a resolved version's own `tenant_id`, so with no version it
    correctly denies. Not public, because the
    inventory names every prompt/agent/policy this platform runs, their
    source locations, and their safety posture, which is internal
    architectural detail even though it carries no template text and no
    secret.

    Reads only in-process module state and `Settings` -- no database
    query, no LLM call.
    """
    user, session = user_and_session
    _authorize(user, session, GovernanceAction.LIST, None)
    inventory = full_inventory()
    return InventoryOut(
        prompts=tuple(_inventory_entry_out(entry) for entry in inventory.prompts),
        agents=tuple(_inventory_entry_out(entry) for entry in inventory.agents),
        policies=tuple(_inventory_entry_out(entry) for entry in inventory.policies),
        known_tools=tuple(sorted(known_tool_names())),
        known_models=tuple(sorted(known_model_names())),
        known_evaluation_suites=tuple(sorted(known_evaluation_suites())),
        truth_level=inventory.truth_level.value,
    )


@router.post("/assets/validate", response_model=ArtifactValidationReportOut)
def validate_artifact(
    payload: ValidateArtifactRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ArtifactValidationReportOut:
    """Validates a candidate artifact without persisting anything.

    Returns `200` with the report either way -- a validation *finding* is
    this route's successful result, unlike the lifecycle routes, where a
    blocking violation refuses the action with a `422`. A reviewer uses
    this to check a payload before creating a draft from it.

    Deliberately routed at `/assets/validate` rather than
    `/assets/{version_id}/validate`: there is no version yet.
    FastAPI matches this literal path ahead of the
    `/assets/{version_id}` parameterised route because it is declared
    with a different method (`POST` vs `GET`/`PATCH`), so the two cannot
    collide.
    """
    user, session = user_and_session
    # `LIST` for the same reason `GET /governance/inventory` uses it:
    # there is no specific version to tenant-match against.
    _authorize(user, session, GovernanceAction.LIST, None)
    try:
        asset_type = GovernedAssetType(payload.asset_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unrecognized asset_type {payload.asset_type!r}.",
        ) from None
    report = validate_artifact_config(
        asset_type, payload.asset_key, payload.config, _validation_context()
    )
    return _report_out(report)
