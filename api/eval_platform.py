"""The AI Evaluation Benchmarking platform's REST surface -- Prompt 46
(`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`).

**Every route here does exactly one authorization dance**: resolve the
`EvaluationRun` row by id when one is needed (never trusting a query
filter alone to scope it), then call `eval_platform.policy
.authorize_eval_action` and act only on an `allowed=True` decision --
mirrors `api/governance.py`/`api/semantic_catalog.py`/`api/onboarding.py`'s
own identical discipline, including the cross-tenant-denial-is-a-404
anti-enumeration precedent.

**No `Settings.enable_*` feature flag** -- the identical reasoning
`api/governance.py`'s own docstring already gives: an administrative/
evaluation surface gated purely by RBAC is the correct shape here, not
an optional analytics engine with a cost/latency tradeoff a flag would
exist to let an operator decline.

**`POST /eval-platform/runs/component-benchmark` is the one route that
actually executes anything** -- it calls `eval.component_benchmark
.runner.run_all()` directly (the real production function, never a
mock) and `eval.component_benchmark.regression.compare_against_baseline`
against the currently-committed `eval/baselines
/component_benchmark_latest.json`, then persists both the raw result and
the regression outcome. Every other run-creation route
(`POST /eval-platform/runs/report`) is operator-reported, since the SQL
benchmark, the security benchmark, and the tenant-isolation suite all
need live database/LLM/pytest infrastructure this platform does not
itself run -- see `eval_platform.models.EvalRunSource`'s own docstring.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from identity.models import EvaluationCaseReview, EvaluationRun, User
from identity.repositories.eval_platform import create_case_review as _create_case_review
from identity.repositories.eval_platform import create_run as _create_run
from identity.repositories.eval_platform import get_run_by_id
from identity.repositories.eval_platform import list_case_reviews_for_run as _list_reviews
from identity.repositories.eval_platform import list_runs as _list_runs
from identity.repositories.eval_platform import record_release_gate_verdict as _record_verdict
from identity.repositories.users import get_user_permissions
from sqlalchemy.orm import Session

from api.eval_platform_schemas import (
    ComputeReleaseGateRequest,
    EvaluationCaseReviewOut,
    EvaluationRunOut,
    ReportEvalRunRequest,
    RunComponentBenchmarkRequest,
    SubmitCaseReviewRequest,
)
from api.identity_authz import require_local_user
from eval.component_benchmark.regression import (
    ComponentBenchmarkComparison,
    ComponentRegression,
    compare_against_baseline,
    load_baseline,
    report_to_baseline_dict,
)
from eval.component_benchmark.runner import run_all
from eval.regression import RegressionReport
from eval_platform.dataset_registry import fingerprint_baseline
from eval_platform.models import EvalRunSource
from eval_platform.policy import EvalAction, authorize_eval_action
from eval_platform.release_gate import ReleaseGateInputs, evaluate_release_gate
from security.audit_log import log_security_event
from security.tenancy import resolve_actor_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/eval-platform", tags=["eval-platform"])

#: Mirrors `scripts/run_component_benchmark.py`'s own `_BASELINE_PATH`
#: (that script's own module docstring explains why a second CLI entry
#: point exists alongside this route at all).
_COMPONENT_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "eval"
    / "baselines"
    / "component_benchmark_latest.json"
)

_DEFAULT_RUN_COMPONENT_REQUEST = RunComponentBenchmarkRequest()
_DEFAULT_RELEASE_GATE_REQUEST = ComputeReleaseGateRequest()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation run not found.")


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")


def _require_run(session: Session, run_id: uuid.UUID) -> EvaluationRun:
    run = get_run_by_id(session, run_id)
    if run is None:
        raise _not_found()
    return run


def _authorize(user: User, session: Session, action: EvalAction, run: EvaluationRun | None) -> str:
    """Returns the actor's own resolved tenant id on success (every
    caller needs it right after anyway); raises on denial."""
    permissions = get_user_permissions(session, user.id)
    actor_tenant_id = resolve_actor_tenant_id(user)
    decision = authorize_eval_action(
        actor_permissions=permissions,
        actor_tenant_id=actor_tenant_id,
        resource_tenant_id=run.tenant_id if run is not None else None,
        action=action,
    )
    if not decision.allowed:
        if run is not None and decision.reason == "resource belongs to a different tenant":
            raise _not_found()
        raise _forbidden()
    if actor_tenant_id is None:
        # `authorize_eval_action` only returns `allowed=True` when
        # `actor_tenant_id` is not None -- this branch is unreachable in
        # practice, but raising explicitly (never `assert`, which is
        # stripped under `python -O`) keeps that guarantee enforced at
        # runtime rather than only documented.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not resolve a tenant for this account.",
        )
    return actor_tenant_id


def _run_out(run: EvaluationRun) -> EvaluationRunOut:
    return EvaluationRunOut(
        id=str(run.id),
        tenant_id=run.tenant_id,
        source=run.source,
        status=run.status,
        dataset_version=run.dataset_version,
        governed_asset_type=run.governed_asset_type,
        governed_asset_key=run.governed_asset_key,
        governed_asset_version=run.governed_asset_version,
        summary=run.summary,
        total_cases=run.total_cases,
        total_passed=run.total_passed,
        quality=run.quality,
        latency_seconds=run.latency_seconds,
        cost_estimate=run.cost_estimate,
        release_gate_verdict=run.release_gate_verdict,
        release_gate_reasons=run.release_gate_reasons,
        notes=run.notes,
        triggered_by_user_id=str(run.triggered_by_user_id) if run.triggered_by_user_id else None,
        created_at=run.created_at,
    )


def _review_out(review: EvaluationCaseReview) -> EvaluationCaseReviewOut:
    return EvaluationCaseReviewOut(
        id=str(review.id),
        run_id=str(review.run_id),
        domain=review.domain,
        case_name=review.case_name,
        original_verdict=review.original_verdict,
        reviewed_verdict=review.reviewed_verdict,
        comment=review.comment,
        reviewer_user_id=str(review.reviewer_user_id) if review.reviewer_user_id else None,
        created_at=review.created_at,
    )


@router.post("/runs/component-benchmark", response_model=EvaluationRunOut)
def run_component_benchmark(
    payload: RunComponentBenchmarkRequest = _DEFAULT_RUN_COMPONENT_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> EvaluationRunOut:
    """Executes `eval.component_benchmark.runner.run_all()` (9 real
    domains, zero live infrastructure needed), compares it against the
    currently-committed baseline, and persists both the raw result and
    the regression outcome."""
    user, session = user_and_session
    tenant_id = _authorize(user, session, EvalAction.CREATE_RUN, None)

    report = run_all()
    baseline = load_baseline(_COMPONENT_BASELINE_PATH)
    comparison = compare_against_baseline(report, baseline)

    summary = {
        "cases": report_to_baseline_dict(report),
        "by_domain": {
            d.domain: {"passed": d.cases_passed, "total": d.cases_run} for d in report.domains
        },
        "regressions": [
            {"domain": r.domain, "case_name": r.case_name, "detail": r.current_detail}
            for r in comparison.regressions
        ],
        "new_cases": comparison.new_cases,
        "removed_cases": comparison.removed_cases,
    }
    quality = (report.total_passed / report.total_cases) if report.total_cases else None

    run = _create_run(
        session,
        tenant_id=tenant_id,
        source=EvalRunSource.COMPONENT_BENCHMARK.value,
        dataset_version=fingerprint_baseline(baseline),
        summary=summary,
        total_cases=report.total_cases,
        total_passed=report.total_passed,
        quality=quality,
        governed_asset_type=payload.governed_asset_type,
        governed_asset_key=payload.governed_asset_key,
        governed_asset_version=payload.governed_asset_version,
        notes=payload.notes,
        triggered_by_user_id=user.id,
    )
    log_security_event(
        "eval_run_recorded",
        "info",
        "A component-benchmark evaluation run was recorded.",
        run_id=str(run.id),
        source=run.source,
        total_cases=run.total_cases,
        total_passed=run.total_passed,
        has_regressions=bool(comparison.regressions),
        actor_user_id=str(user.id),
    )
    return _run_out(run)


@router.post("/runs/report", response_model=EvaluationRunOut)
def report_eval_run(
    payload: ReportEvalRunRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> EvaluationRunOut:
    """Records an operator-reported run for a harness this platform does
    not itself execute (SQL benchmark, security benchmark, or the
    tenant-isolation suite -- all three need live database/LLM/pytest
    infrastructure)."""
    user, session = user_and_session
    tenant_id = _authorize(user, session, EvalAction.CREATE_RUN, None)

    run = _create_run(
        session,
        tenant_id=tenant_id,
        source=payload.source,
        dataset_version=payload.dataset_version,
        summary=payload.summary,
        total_cases=payload.total_cases,
        total_passed=payload.total_passed,
        quality=payload.quality,
        latency_seconds=payload.latency_seconds,
        cost_estimate=payload.cost_estimate,
        governed_asset_type=payload.governed_asset_type,
        governed_asset_key=payload.governed_asset_key,
        governed_asset_version=payload.governed_asset_version,
        notes=payload.notes,
        triggered_by_user_id=user.id,
    )
    log_security_event(
        "eval_run_recorded",
        "info",
        "An operator-reported evaluation run was recorded.",
        run_id=str(run.id),
        source=run.source,
        actor_user_id=str(user.id),
    )
    return _run_out(run)


@router.get("/runs", response_model=list[EvaluationRunOut])
def list_eval_runs(
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[EvaluationRunOut]:
    user, session = user_and_session
    tenant_id = _authorize(user, session, EvalAction.LIST, None)
    runs = _list_runs(session, tenant_id=tenant_id, source=source, limit=limit)
    return [_run_out(run) for run in runs]


@router.get("/runs/{run_id}", response_model=EvaluationRunOut)
def get_eval_run(
    run_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> EvaluationRunOut:
    user, session = user_and_session
    run = _require_run(session, run_id)
    _authorize(user, session, EvalAction.GET_RUN, run)
    return _run_out(run)


def _release_gate_inputs_for_run(
    run: EvaluationRun, payload: ComputeReleaseGateRequest
) -> ReleaseGateInputs:
    """Folds a run's own already-stored, genuinely-computed regression
    outcome in automatically for a `component_benchmark` run (no need
    for the caller to resupply what this platform already knows) --
    reconstructing the real `ComponentBenchmarkComparison` dataclass
    from the flat `summary["regressions"]` list stored at creation time,
    never a second recomputation. Every other field comes directly from
    the caller's own primitive, flattened report (see
    `ComputeReleaseGateRequest`'s own docstring for why the REST layer
    takes primitives rather than the richer dataclasses
    `eval_platform.release_gate`'s own engine-level function accepts)."""
    component_comparison: ComponentBenchmarkComparison | None = None
    if run.source == EvalRunSource.COMPONENT_BENCHMARK.value:
        regressions_raw = run.summary.get("regressions", []) if run.summary else []
        component_comparison = ComponentBenchmarkComparison(
            regressions=[
                ComponentRegression(
                    domain=r["domain"],
                    case_name=r["case_name"],
                    baseline_passed=True,
                    current_passed=False,
                    current_detail=r.get("detail", ""),
                )
                for r in regressions_raw
            ],
            new_cases=list(run.summary.get("new_cases", []) if run.summary else []),
            removed_cases=list(run.summary.get("removed_cases", []) if run.summary else []),
        )

    sql_regression: RegressionReport | None = None
    if payload.sql_benchmark_has_regression is not None:
        sql_regression = RegressionReport(
            newly_failing_cases=(
                ["operator_reported_sql_regression"] if payload.sql_benchmark_has_regression else []
            )
        )

    return ReleaseGateInputs(
        component_benchmark_comparison=component_comparison,
        sql_benchmark_regression=sql_regression,
        security_benchmark_critical_findings=payload.security_benchmark_critical_findings,
        tenant_isolation_passed=payload.tenant_isolation_passed,
    )


@router.post("/runs/{run_id}/release-gate", response_model=EvaluationRunOut)
def compute_release_gate(
    run_id: uuid.UUID,
    payload: ComputeReleaseGateRequest = _DEFAULT_RELEASE_GATE_REQUEST,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> EvaluationRunOut:
    """Computes and persists a PASS/BLOCK verdict for one run -- see
    `eval_platform.release_gate.evaluate_release_gate`'s own docstring
    for the full decision logic (two non-negotiable floors: security
    critical findings and tenant isolation; every other domain blocks
    only on a genuine regression)."""
    user, session = user_and_session
    run = _require_run(session, run_id)
    _authorize(user, session, EvalAction.COMPUTE_RELEASE_GATE, run)

    inputs = _release_gate_inputs_for_run(run, payload)
    result = evaluate_release_gate(inputs)
    updated = _record_verdict(
        session,
        run,
        verdict=result.verdict.value,
        reasons={
            "blocking_reasons": list(result.blocking_reasons),
            "warnings": list(result.warnings),
        },
    )
    log_security_event(
        "eval_release_gate_computed",
        "info" if result.passed else "warning",
        "A release-gate verdict was computed for an evaluation run.",
        run_id=str(run.id),
        verdict=result.verdict.value,
        blocking_reason_count=len(result.blocking_reasons),
        actor_user_id=str(user.id),
    )
    return _run_out(updated)


@router.post("/runs/{run_id}/case-reviews", response_model=EvaluationCaseReviewOut)
def submit_case_review(
    run_id: uuid.UUID,
    payload: SubmitCaseReviewRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> EvaluationCaseReviewOut:
    """Records a human review verdict on one case's outcome -- never
    mutates the parent run's own `summary`/`total_passed` (see
    `identity.models.EvaluationCaseReview`'s own docstring)."""
    user, session = user_and_session
    run = _require_run(session, run_id)
    _authorize(user, session, EvalAction.SUBMIT_CASE_REVIEW, run)

    review = _create_case_review(
        session,
        run_id=run.id,
        tenant_id=run.tenant_id,
        domain=payload.domain,
        case_name=payload.case_name,
        original_verdict=payload.original_verdict,
        reviewed_verdict=payload.reviewed_verdict,
        comment=payload.comment,
        reviewer_user_id=user.id,
    )
    log_security_event(
        "eval_case_review_submitted",
        "info",
        "A human review verdict was submitted for an evaluation case.",
        run_id=str(run.id),
        domain=payload.domain,
        case_name=payload.case_name,
        actor_user_id=str(user.id),
    )
    return _review_out(review)


@router.get("/runs/{run_id}/case-reviews", response_model=list[EvaluationCaseReviewOut])
def list_case_reviews(
    run_id: uuid.UUID,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> list[EvaluationCaseReviewOut]:
    user, session = user_and_session
    run = _require_run(session, run_id)
    _authorize(user, session, EvalAction.LIST_CASE_REVIEWS, run)
    reviews = _list_reviews(session, run.id, tenant_id=run.tenant_id)
    return [_review_out(review) for review in reviews]
