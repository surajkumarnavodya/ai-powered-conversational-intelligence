"""POST /predictive/train and POST /predictive/drift -- the governed
Predictive Analytics engine's REST surface (Prompt 40,
`40_PREDICTIVE_ANALYTICS_CONTRACT.md`).

**Neither route ever queries a database or calls an LLM.** Both take
the dataset(s) directly in the request body -- the caller's own
already-fetched, already-governed result (e.g. from a prior `POST
/execute`). `analytics.predictive` is a pure function over whatever
rows it's handed.

Behind `Settings.enable_predictive_analytics` (default on, since this
is a pure, deterministic-given-a-seed, zero-I/O computation -- the same
posture `api/optimization.py`'s own `Settings.enable_optimization_engine`
gate establishes). When the flag is off the routes answer 404.

Authorization: `Permission.ASK` (the same base gate `api/scenarios.py`'s
own routes use) plus a restricted-column check inside
`analytics.predictive.train_predictive_model` itself for every feature
and the target column -- a feature/target `config.sensitive_columns`
classifies restricted is rejected unless the caller also holds
`Permission.VIEW_RESTRICTED_COLUMNS`. Every successful training run is
audit-logged (`security.audit_log.log_security_event`,
`"predictive_model_trained"`, metadata/fingerprints only, never raw
data or coefficients) -- see that module's own docstring.

Rate-limited and payload-bounded: `enforce_api_action_rate_limit` per
caller, and `rows`/`feature_columns`/`predict_rows` are each capped
(`Settings.max_result_rows`/`predictive_max_features`/
`predictive_max_predict_rows`).
"""

from __future__ import annotations

import logging

from analytics.predictive import detect_feature_drift, train_predictive_model
from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent.authz import Permission
from api.authz import require_permission
from api.predictive_schemas import (
    ClassificationMetricsOut,
    FeatureDriftFindingOut,
    FeatureDriftRequest,
    FeatureDriftResponse,
    LeakageFindingOut,
    PredictionOut,
    PredictiveModelMetadataOut,
    PredictiveTrainRequest,
    PredictiveTrainResponse,
    RegressionMetricsOut,
)
from api.rate_limit import enforce_api_action_rate_limit
from config.settings import get_settings
from security.oidc import AuthIdentity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictive", tags=["predictive"])

PREDICTIVE_DISABLED_DETAIL = "The Predictive Analytics engine is not enabled on this server."
TOO_MANY_ROWS_DETAIL = "Too many baseline rows for this endpoint."
TOO_MANY_FEATURES_DETAIL = "Too many feature columns for this endpoint."
TOO_MANY_PREDICT_ROWS_DETAIL = "Too many predict_rows for this endpoint."


def _require_enabled() -> None:
    if not get_settings().enable_predictive_analytics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PREDICTIVE_DISABLED_DETAIL
        )


@router.post("/train", response_model=PredictiveTrainResponse)
def train(
    payload: PredictiveTrainRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> PredictiveTrainResponse:
    """Trains (and, when `predict_rows` is given, scores) a governed
    regression/classification model. `status` is `"invalid"`/
    `"insufficient_data"`/`"leakage_detected"`/`"unauthorized"` (never a
    partial training run) when validation found any problem; otherwise
    the real evaluation outcome. Never touches a database or calls an
    LLM."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "predictive_train", settings, identity=identity)

    if len(payload.rows) > settings.max_result_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_ROWS_DETAIL
        )
    if len(payload.feature_columns) > settings.predictive_max_features:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_FEATURES_DETAIL
        )
    if len(payload.predict_rows) > settings.predictive_max_predict_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_PREDICT_ROWS_DETAIL
        )

    result = train_predictive_model(
        payload.model_id,
        payload.name,
        payload.domain,
        payload.task_type,
        payload.model_type,
        payload.columns,
        [tuple(row) for row in payload.rows],
        payload.feature_columns,
        payload.target_column,
        [tuple(row) for row in payload.predict_rows] or None,
        payload.test_fraction,
        payload.random_seed,
        tuple(identity.roles),
        settings,
    )

    return PredictiveTrainResponse(
        model_id=result.model_id,
        name=result.name,
        domain=result.domain.value,
        task_type=result.task_type.value,
        status=result.status.value,
        metadata=(
            PredictiveModelMetadataOut(
                model_type=result.metadata.model_type.value,
                version=result.metadata.version,
                feature_columns=result.metadata.feature_columns,
                target_column=result.metadata.target_column,
                training_row_count=result.metadata.training_row_count,
                test_row_count=result.metadata.test_row_count,
                test_fraction=result.metadata.test_fraction,
                random_seed=result.metadata.random_seed,
                data_fingerprint=result.metadata.data_fingerprint,
                model_fingerprint=result.metadata.model_fingerprint,
                coefficients=result.metadata.coefficients,
                intercept=result.metadata.intercept,
            )
            if result.metadata is not None
            else None
        ),
        regression_metrics=(
            RegressionMetricsOut(
                mae=result.regression_metrics.mae,
                rmse=result.regression_metrics.rmse,
                r2=result.regression_metrics.r2,
                formula=result.regression_metrics.formula,
            )
            if result.regression_metrics is not None
            else None
        ),
        classification_metrics=(
            ClassificationMetricsOut(
                accuracy=result.classification_metrics.accuracy,
                precision=result.classification_metrics.precision,
                recall=result.classification_metrics.recall,
                f1=result.classification_metrics.f1,
                test_class_counts=result.classification_metrics.test_class_counts,
                formula=result.classification_metrics.formula,
            )
            if result.classification_metrics is not None
            else None
        ),
        predictions=tuple(
            PredictionOut(
                index=p.index,
                value=p.value,
                lower_bound=p.lower_bound,
                upper_bound=p.upper_bound,
                class_probabilities=p.class_probabilities,
            )
            for p in result.predictions
        ),
        leakage_findings=tuple(
            LeakageFindingOut(feature=f.feature, reason=f.reason, correlation=f.correlation)
            for f in result.leakage_findings
        ),
        rejection_reasons=result.rejection_reasons,
        truth_level=result.truth_level.value,
        limitations=result.limitations,
        engine_version=result.engine_version,
    )


@router.post("/drift", response_model=FeatureDriftResponse)
def drift(
    payload: FeatureDriftRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> FeatureDriftResponse:
    """Compares two same-shaped datasets feature by feature (two-sample
    Kolmogorov-Smirnov test) -- never touches a database."""
    _require_enabled()
    settings = get_settings()
    enforce_api_action_rate_limit(request, "predictive_drift", settings, identity=identity)

    if (
        len(payload.reference_rows) > settings.max_result_rows
        or len(payload.new_rows) > settings.max_result_rows
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_ROWS_DETAIL
        )
    if len(payload.feature_columns) > settings.predictive_max_features:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TOO_MANY_FEATURES_DETAIL
        )

    report = detect_feature_drift(
        payload.reference_columns,
        [tuple(row) for row in payload.reference_rows],
        payload.new_columns,
        [tuple(row) for row in payload.new_rows],
        payload.feature_columns,
        settings,
    )

    return FeatureDriftResponse(
        threshold=report.threshold,
        findings=tuple(
            FeatureDriftFindingOut(
                feature=f.feature,
                reference_count=f.reference_count,
                new_count=f.new_count,
                ks_statistic=f.ks_statistic,
                p_value=f.p_value,
                is_drifted=f.is_drifted,
                formula=f.formula,
            )
            for f in report.findings
        ),
        any_drifted=report.any_drifted,
        notes=report.notes,
        truth_level=report.truth_level.value,
        engine_version=report.engine_version,
    )
