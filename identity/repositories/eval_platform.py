"""Repository layer for the AI Evaluation Benchmarking platform --
`EvaluationRun`/`EvaluationCaseReview`, Prompt 46
(`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`).

**This module never makes an authorization decision itself** --
`eval_platform.policy.authorize_eval_action` is the only place a caller
may or may not do something is decided, mirroring `identity/repositories
/onboarding.py`/`identity/repositories/governance.py`'s own identical
split: this repository returns a row (or `None`) regardless of tenant,
and the caller (`api/eval_platform.py`) is responsible for the
tenant-match check before acting on what it got back.

**`summary`'s shape is harness-specific, by `source`** (plain `dict`,
never a second typed schema this module would have to keep in sync with
four different harnesses' own result shapes):

- `"component_benchmark"`: `eval.component_benchmark.regression
  .report_to_baseline_dict`'s own flat `{"domain/case_name": passed}`
  shape, plus the full per-case `detail` strings
  (`{"cases": {...}, "by_domain": {domain: {passed, total}}}`).
- `"sql_benchmark"`: whatever `eval.reporting.report_to_dict` already
  produces for a `BenchmarkReport` -- unmodified.
- `"security_benchmark"`: whatever `eval.security_benchmark.reporting`
  already produces -- unmodified.
- `"tenant_isolation_suite"`: a plain operator-reported summary (e.g.
  `{"pytest_output": "...", "exit_code": 0}`) -- this codebase has no
  existing typed report object for a plain pytest run.

No validation is applied to `summary`'s internal shape -- an operator-
reported run is exactly as trusted as this codebase's other operator-
reported numbers (`GovernanceEvent.event_type == "usage_recorded"`'s
own `payload`), never re-derived or re-verified by this platform.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from identity.models import EvaluationCaseReview, EvaluationRun


def create_run(
    session: Session,
    *,
    tenant_id: str,
    source: str,
    dataset_version: str,
    summary: dict[str, Any],
    status: str = "completed",
    total_cases: int | None = None,
    total_passed: int | None = None,
    quality: float | None = None,
    latency_seconds: float | None = None,
    cost_estimate: float | None = None,
    governed_asset_type: str | None = None,
    governed_asset_key: str | None = None,
    governed_asset_version: int | None = None,
    notes: str | None = None,
    triggered_by_user_id: uuid.UUID | None = None,
) -> EvaluationRun:
    """Persists one already-complete evaluation run. See this module's
    own docstring for why a run is always created already-complete
    (no background worker exists in this codebase)."""
    run = EvaluationRun(
        tenant_id=tenant_id,
        source=source,
        status=status,
        dataset_version=dataset_version,
        summary=summary,
        total_cases=total_cases,
        total_passed=total_passed,
        quality=quality,
        latency_seconds=latency_seconds,
        cost_estimate=cost_estimate,
        governed_asset_type=governed_asset_type,
        governed_asset_key=governed_asset_key,
        governed_asset_version=governed_asset_version,
        notes=notes,
        triggered_by_user_id=triggered_by_user_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def get_run_by_id(session: Session, run_id: uuid.UUID) -> EvaluationRun | None:
    return session.get(EvaluationRun, run_id)


def list_runs(
    session: Session,
    *,
    tenant_id: str,
    source: str | None = None,
    limit: int = 50,
) -> list[EvaluationRun]:
    """Always tenant-scoped at the query itself (never a post-hoc
    filter) -- a caller of this function can never accidentally see
    another tenant's runs just by forgetting a filter downstream."""
    stmt = (
        select(EvaluationRun)
        .where(EvaluationRun.tenant_id == tenant_id)
        .order_by(EvaluationRun.created_at.desc())
        .limit(limit)
    )
    if source is not None:
        stmt = stmt.where(EvaluationRun.source == source)
    return list(session.execute(stmt).scalars().all())


def record_release_gate_verdict(
    session: Session,
    run: EvaluationRun,
    *,
    verdict: str,
    reasons: dict[str, Any],
) -> EvaluationRun:
    """Stores a just-computed `eval_platform.release_gate
    .ReleaseGateResult` onto its own run -- idempotent (re-running the
    gate against the same run simply overwrites the previous verdict),
    since a release-gate verdict is a derived judgment about the run's
    own already-fixed summary, not a second, independently-mutable fact
    that needs the atomic-conditional-update race protection
    `identity.repositories.governance._apply_transition`'s own docstring
    documents for a genuinely concurrent multi-writer lifecycle (this
    run's own `summary` never changes after creation, so there is
    nothing to race against)."""
    run.release_gate_verdict = verdict
    run.release_gate_reasons = reasons
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def create_case_review(
    session: Session,
    *,
    run_id: uuid.UUID,
    tenant_id: str,
    domain: str,
    case_name: str,
    original_verdict: bool,
    reviewed_verdict: bool | None = None,
    comment: str | None = None,
    reviewer_user_id: uuid.UUID | None = None,
) -> EvaluationCaseReview:
    review = EvaluationCaseReview(
        run_id=run_id,
        tenant_id=tenant_id,
        domain=domain,
        case_name=case_name,
        original_verdict=original_verdict,
        reviewed_verdict=reviewed_verdict,
        comment=comment,
        reviewer_user_id=reviewer_user_id,
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def list_case_reviews_for_run(
    session: Session, run_id: uuid.UUID, *, tenant_id: str
) -> list[EvaluationCaseReview]:
    """Tenant-scoped at the query itself, the identical reasoning
    `list_runs` above already gives -- `run_id` alone could belong to
    another tenant's run, so `tenant_id` is a real `WHERE` clause, never
    just a parameter name implying a check that isn't actually there."""
    stmt = (
        select(EvaluationCaseReview)
        .where(
            EvaluationCaseReview.run_id == run_id,
            EvaluationCaseReview.tenant_id == tenant_id,
        )
        .order_by(EvaluationCaseReview.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())
