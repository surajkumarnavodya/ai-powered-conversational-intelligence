"""Request/response Pydantic models for `api/eval_platform.py` -- Prompt
46 (`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RunComponentBenchmarkRequest(BaseModel):
    """Everything optional -- recording a component-benchmark run needs
    no caller-supplied data at all, since the server executes
    `eval.component_benchmark.runner.run_all()` itself."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    governed_asset_type: str | None = None
    governed_asset_key: str | None = Field(default=None, max_length=200)
    governed_asset_version: int | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ReportEvalRunRequest(BaseModel):
    """An operator-reported run for one of the three harnesses this
    platform does not itself execute (needs live database/LLM
    infrastructure) -- see `eval_platform.models.EvalRunSource`'s own
    docstring for why."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: Literal["sql_benchmark", "security_benchmark", "tenant_isolation_suite"]
    dataset_version: str = Field(min_length=1, max_length=128)
    summary: dict[str, Any] = Field(default_factory=dict)
    total_cases: int | None = Field(default=None, ge=0)
    total_passed: int | None = Field(default=None, ge=0)
    quality: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_seconds: float | None = Field(default=None, ge=0.0)
    cost_estimate: float | None = Field(default=None, ge=0.0)
    governed_asset_type: str | None = None
    governed_asset_key: str | None = Field(default=None, max_length=200)
    governed_asset_version: int | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ComputeReleaseGateRequest(BaseModel):
    """Primitive, flattened release-gate inputs for domains this run
    itself doesn't already carry a comparison for -- see
    `api/eval_platform.py::_release_gate_inputs_for_run`'s own docstring
    for how a `component_benchmark` run's own stored regression outcome
    is folded in automatically, never requiring the caller to resupply
    it here."""

    model_config = ConfigDict(extra="forbid")

    sql_benchmark_has_regression: bool | None = None
    sql_benchmark_regression_summary: str | None = Field(default=None, max_length=2000)
    security_benchmark_critical_findings: int | None = Field(default=None, ge=0)
    tenant_isolation_passed: bool | None = None


class SubmitCaseReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain: str = Field(min_length=1, max_length=32)
    case_name: str = Field(min_length=1, max_length=300)
    original_verdict: bool
    reviewed_verdict: bool | None = None
    comment: str | None = Field(default=None, max_length=2000)


class EvaluationRunOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    source: str
    status: str
    dataset_version: str
    governed_asset_type: str | None
    governed_asset_key: str | None
    governed_asset_version: int | None
    summary: dict[str, Any]
    total_cases: int | None
    total_passed: int | None
    quality: float | None
    latency_seconds: float | None
    cost_estimate: float | None
    release_gate_verdict: str | None
    release_gate_reasons: dict[str, Any] | None
    notes: str | None
    triggered_by_user_id: str | None
    created_at: datetime


class EvaluationCaseReviewOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str
    domain: str
    case_name: str
    original_verdict: bool
    reviewed_verdict: bool | None
    comment: str | None
    reviewer_user_id: str | None
    created_at: datetime
