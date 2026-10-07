"""Request and response models for `api/predictive.py` (Prompt 40,
`40_PREDICTIVE_ANALYTICS_CONTRACT.md`).

Request models forbid unknown fields and strip whitespace, the same
convention `api/optimization_schemas.py`'s own request models already
establish, so a client cannot slip in a `caller_roles` override -- roles
are resolved server-side from the authenticated identity.

Response models mirror `analytics.models`' own predictive types field-
by-field with plain primitives (strings instead of enums), the identical
"mirror the internal engine model, don't re-export it" convention
`OptimizationValidateResponse`/`ParameterValidationOut` already
establish.
"""

from __future__ import annotations

from typing import Any

from analytics.models import PredictiveDomain, PredictiveModelType, PredictiveTaskType
from pydantic import BaseModel, ConfigDict, Field


class PredictiveTrainRequest(BaseModel):
    """`POST /predictive/train`'s request body -- the baseline dataset
    directly in the body (the caller's own already-fetched, already-
    governed result), never fetched by this route itself."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    domain: PredictiveDomain
    task_type: PredictiveTaskType
    model_type: PredictiveModelType
    columns: list[str] = Field(..., min_length=1)
    rows: list[list[Any]] = Field(default_factory=list)
    feature_columns: list[str] = Field(..., min_length=1)
    target_column: str = Field(..., min_length=1)
    predict_rows: list[list[Any]] = Field(default_factory=list)
    test_fraction: float = Field(default=0.2, gt=0.0, lt=1.0)
    random_seed: int = 0


class LeakageFindingOut(BaseModel):
    """Mirrors `analytics.models.LeakageFinding`."""

    model_config = ConfigDict(frozen=True)

    feature: str
    reason: str
    correlation: float | None


class RegressionMetricsOut(BaseModel):
    """Mirrors `analytics.models.RegressionMetrics`."""

    model_config = ConfigDict(frozen=True)

    mae: float
    rmse: float
    r2: float
    formula: str


class ClassificationMetricsOut(BaseModel):
    """Mirrors `analytics.models.ClassificationMetrics`."""

    model_config = ConfigDict(frozen=True)

    accuracy: float
    precision: float
    recall: float
    f1: float
    test_class_counts: dict[str, int]
    formula: str


class PredictiveModelMetadataOut(BaseModel):
    """Mirrors `analytics.models.PredictiveModelMetadata`."""

    model_config = ConfigDict(frozen=True)

    model_type: str
    version: str
    feature_columns: tuple[str, ...]
    target_column: str
    training_row_count: int
    test_row_count: int
    test_fraction: float
    random_seed: int
    data_fingerprint: str
    model_fingerprint: str
    coefficients: dict[str, float]
    intercept: float


class PredictionOut(BaseModel):
    """Mirrors `analytics.models.PredictionWithUncertainty`."""

    model_config = ConfigDict(frozen=True)

    index: int
    value: float | str
    lower_bound: float | None
    upper_bound: float | None
    class_probabilities: dict[str, float] | None


class PredictiveTrainResponse(BaseModel):
    """Mirrors `analytics.models.PredictiveModelResult`."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    name: str
    domain: str
    task_type: str
    status: str
    metadata: PredictiveModelMetadataOut | None
    regression_metrics: RegressionMetricsOut | None
    classification_metrics: ClassificationMetricsOut | None
    predictions: tuple[PredictionOut, ...]
    leakage_findings: tuple[LeakageFindingOut, ...]
    rejection_reasons: tuple[str, ...]
    truth_level: str
    limitations: tuple[str, ...]
    engine_version: str


class FeatureDriftRequest(BaseModel):
    """`POST /predictive/drift`'s request body -- two same-shaped
    datasets directly in the body, never fetched by this route itself."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reference_columns: list[str] = Field(..., min_length=1)
    reference_rows: list[list[Any]] = Field(default_factory=list)
    new_columns: list[str] = Field(..., min_length=1)
    new_rows: list[list[Any]] = Field(default_factory=list)
    feature_columns: list[str] = Field(..., min_length=1)


class FeatureDriftFindingOut(BaseModel):
    """Mirrors `analytics.models.FeatureDriftFinding`."""

    model_config = ConfigDict(frozen=True)

    feature: str
    reference_count: int
    new_count: int
    ks_statistic: float
    p_value: float
    is_drifted: bool
    formula: str


class FeatureDriftResponse(BaseModel):
    """Mirrors `analytics.models.FeatureDriftReport`."""

    model_config = ConfigDict(frozen=True)

    threshold: float
    findings: tuple[FeatureDriftFindingOut, ...]
    any_drifted: bool
    notes: tuple[str, ...]
    truth_level: str
    engine_version: str
