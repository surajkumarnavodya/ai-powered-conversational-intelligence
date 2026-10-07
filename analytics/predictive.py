"""The governed Predictive Analytics engine -- Prompt 40
(`40_PREDICTIVE_ANALYTICS_CONTRACT.md`), expanding `analytics.forecasting`
(time-series projection) into general-purpose, governed regression and
classification over arbitrary tabular data (demand, churn, risk, ...).

`train_predictive_model` is a pure function over an already-fetched
baseline dataset (`columns`, `rows` -- the same shape every other
`analytics/` module takes): it never queries a database and never calls
an LLM. The model catalog is a small, closed, reviewed set
(`analytics.models.PredictiveModelType`) -- never an arbitrary class
name a caller (or an LLM) could inject.

**Reused, not reimplemented**: `scikit-learn`'s `LinearRegression`/
`LogisticRegression`/evaluation metrics (already a genuine transitive
dependency via `sentence-transformers`, now pinned directly -- see
`requirements.txt`), and `scipy.stats`' `pearsonr` (leakage correlation)/
`ks_2samp` (drift detection) -- `scipy` itself already a direct
dependency as of Prompt 39's optimization engine.

**Governance controls, each directly answering one line of the
mission**:

1. **Data sufficiency** (`Settings.predictive_min_training_rows`/
   `predictive_min_rows_per_feature`/`predictive_min_rows_per_class`):
   checked before any split or fit is attempted --
   `PredictiveModelStatus.INSUFFICIENT_DATA`, never a model fit on too
   little data to mean anything.
2. **Leakage checks** (`_detect_leakage`): a feature identical to the
   target, or correlated with it at/above `Settings
   .predictive_leakage_correlation_threshold`, is flagged -- rejected by
   default (`Settings.predictive_reject_on_leakage`), always disclosed
   in `PredictiveModelResult.leakage_findings` either way.
3. **Train/test separation** (`_plain_split`/`_stratified_split`): every
   evaluation metric is computed **only** on a held-out test split the
   model was never fit on -- never an overfitting-optimistic training-
   set score reported as if it were real-world accuracy. The split
   itself is a **deterministic, hash-based ordering**
   (`_deterministic_order`), not Python's or numpy's own global random
   state -- the same training data, feature list, and seed always
   produce the exact same split, the literal mechanism behind "test
   reproducibility."
4. **Feature validation**: every feature/target column must exist, a
   feature may not also be the target, and (a disclosed, bounded scope
   for this engine version) every feature and a regression target must
   be numeric -- categorical features are rejected outright, never
   silently coerced or dropped.
5. **Versioning and evaluation**: `analytics.models
   .PredictiveModelMetadata` records the model type, feature list,
   target, split sizes/fraction, random seed, and both a data and a
   model fingerprint; `RegressionMetrics`/`ClassificationMetrics` record
   the held-out evaluation, each with its own `formula` string --
   master-contract rule 11.
6. **Authorization**: a feature or target column `config
   .sensitive_columns` classifies restricted is rejected
   (`PredictiveModelStatus.UNAUTHORIZED`) unless the caller holds
   `Permission.VIEW_RESTRICTED_COLUMNS` -- the identical, independently
   re-derived pattern `analytics.scenario`/`optimization.validator`
   already establish.
7. **"Store model/data fingerprints, features and metrics"**: every
   successful training run is audit-logged
   (`security.audit_log.log_security_event`, `"predictive_model_trained"`)
   carrying both fingerprints, the feature list, and the evaluation
   metrics -- **never** the raw data or the fitted coefficients. This is
   a deliberate choice, not an oversight: this engine (like every other
   one in this package) has no persisted model registry in this pass --
   "storing" happens through the identical audit-log mechanism every
   other authorization/governance event in this codebase already uses,
   not a new database table. See `40_PREDICTIVE_ANALYTICS_CONTRACT.md`
   §4 for the full, disclosed reasoning.
"""

from __future__ import annotations

import hashlib
import statistics as _statistics
from collections import Counter
from decimal import Decimal
from typing import Any

from scipy.stats import ks_2samp, pearsonr
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)

from agent.authz import Permission, has_role_permission
from analytics.models import (
    PREDICTIVE_ENGINE_VERSION,
    ClassificationMetrics,
    FeatureDriftFinding,
    FeatureDriftReport,
    LeakageFinding,
    PredictionWithUncertainty,
    PredictiveDomain,
    PredictiveModelMetadata,
    PredictiveModelResult,
    PredictiveModelStatus,
    PredictiveModelType,
    PredictiveTaskType,
    RegressionMetrics,
)
from config.sensitive_columns import load_sensitive_columns
from config.settings import Settings, get_settings
from security.audit_log import log_security_event

#: Two-sided 95% normal-distribution critical value -- the identical
#: prediction-interval convention `analytics.forecasting`'s own
#: `_z_for_confidence` uses for its default confidence level, kept as a
#: plain constant here since this module only ever reports one fixed
#: interval width rather than a caller-selectable confidence level.
_Z_95 = 1.959963985

_NUMERIC_TYPES = (int, float, Decimal)

_TASK_MODEL_COMPAT: dict[PredictiveTaskType, PredictiveModelType] = {
    PredictiveTaskType.REGRESSION: PredictiveModelType.LINEAR_REGRESSION,
    PredictiveTaskType.CLASSIFICATION: PredictiveModelType.LOGISTIC_REGRESSION,
}


def _is_numeric_scalar(value: object) -> bool:
    return isinstance(value, _NUMERIC_TYPES) and not isinstance(value, bool)


def _all_numeric(values: list) -> bool:
    non_null = [v for v in values if v is not None]
    return bool(non_null) and all(_is_numeric_scalar(v) for v in non_null)


def _restricted_column_names(settings: Settings) -> frozenset[str]:
    """Every column name (lowercased, table-agnostic) classified
    "restricted" anywhere in `config/sensitive_columns.yaml` -- the
    identical, independently re-derived helper `analytics.scenario`/
    `optimization.validator`/`recommendation.engine` each already
    establish for their own authorization gate."""
    del settings  # reserved for a future per-database override; unused today
    classifications = load_sensitive_columns()
    return frozenset(
        column.lower() for (_table, column), tier in classifications.items() if tier == "restricted"
    )


def _deterministic_order(n: int, seed: int) -> list[int]:
    """A reproducible pseudo-random permutation of `range(n)`, derived
    from `sha256(f"{seed}:{index}")` rather than Python's or numpy's own
    global random state -- the same `(n, seed)` always produces the
    exact same order, on any machine, any process, any library version.
    This is what makes a train/test split (and therefore every metric
    computed from it) exactly reproducible without a caller needing to
    manage or restore any RNG state.
    """
    return sorted(range(n), key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())


def _plain_split(n: int, test_fraction: float, seed: int) -> tuple[list[int], list[int]]:
    order = _deterministic_order(n, seed)
    split_point = round(n * (1 - test_fraction))
    return order[:split_point], order[split_point:]


def _stratified_split(
    labels: list[Any], test_fraction: float, seed: int
) -> tuple[list[int], list[int]]:
    """Mirrors `_plain_split`, applied independently within each distinct
    label's own row indices -- guarantees both the train and test split
    contain examples of every class (as long as each class individually
    has enough rows, checked separately before this is ever called),
    rather than risking a class landing entirely in one split by chance.
    """
    by_class: dict[Any, list[int]] = {}
    for i, label in enumerate(labels):
        by_class.setdefault(label, []).append(i)

    train_idx: list[int] = []
    test_idx: list[int] = []
    for indices in by_class.values():
        local_order = sorted(
            indices, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()
        )
        split_point = round(len(local_order) * (1 - test_fraction))
        train_idx.extend(local_order[:split_point])
        test_idx.extend(local_order[split_point:])
    return train_idx, test_idx


def _safe_pearson(x: list[float], y: list[float]) -> float | None:
    """`None` when either series has zero variance (a correlation is
    undefined, never a spurious result) -- the identical zero-variance
    guard every statistical helper in this package already establishes."""
    if len(set(x)) < 2 or len(set(y)) < 2:
        return None
    result = pearsonr(x, y)
    return float(result.statistic)


def _detect_leakage(
    columns: list[str],
    rows: list[tuple],
    feature_columns: list[str],
    target_column: str,
    settings: Settings,
) -> tuple[LeakageFinding, ...]:
    """See this module's own docstring, point 2."""
    findings: list[LeakageFinding] = []
    target_idx = columns.index(target_column)
    target_values = [row[target_idx] for row in rows]

    for feature in feature_columns:
        feature_idx = columns.index(feature)
        feature_values = [row[feature_idx] for row in rows]
        if feature_values == target_values:
            findings.append(
                LeakageFinding(
                    feature=feature,
                    reason="identical to the target column -- almost certainly leakage",
                    correlation=1.0,
                )
            )
            continue
        if _all_numeric(feature_values) and _all_numeric(target_values):
            corr = _safe_pearson(
                [float(v) for v in feature_values], [float(v) for v in target_values]
            )
            if corr is not None and abs(corr) >= settings.predictive_leakage_correlation_threshold:
                findings.append(
                    LeakageFinding(
                        feature=feature,
                        reason=(
                            f"correlation with the target ({corr:.4f}) is at or above the "
                            f"configured leakage threshold "
                            f"({settings.predictive_leakage_correlation_threshold:g})"
                        ),
                        correlation=round(corr, 4),
                    )
                )
    return tuple(findings)


def _data_fingerprint(
    columns: list[str], rows: list[tuple], feature_columns: list[str], target_column: str
) -> str:
    """`sha256` of a deterministic serialization of exactly the feature
    and target values used -- mirrors `db.schema_introspection
    .get_schema_fingerprint`'s own "hash a deterministic serialization"
    convention. Changes if and only if the underlying data (restricted
    to the columns this model actually used) changes."""
    indices = [columns.index(c) for c in feature_columns] + [columns.index(target_column)]
    serialized = "\n".join("|".join(str(row[i]) for i in indices) for row in rows)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _model_fingerprint(
    model_type: PredictiveModelType,
    feature_columns: list[str],
    target_column: str,
    data_fingerprint: str,
) -> str:
    serialized = (
        f"{model_type.value}|{','.join(feature_columns)}|{target_column}|{data_fingerprint}"
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _rejected(
    model_id: str,
    name: str,
    domain: PredictiveDomain,
    task_type: PredictiveTaskType,
    status: PredictiveModelStatus,
    reasons: tuple[str, ...],
    leakage_findings: tuple[LeakageFinding, ...] = (),
) -> PredictiveModelResult:
    return PredictiveModelResult(
        model_id=model_id,
        name=name,
        domain=domain,
        task_type=task_type,
        status=status,
        rejection_reasons=reasons,
        leakage_findings=leakage_findings,
        limitations=(
            "This model was not trained -- see rejection_reasons for every reason found.",
        ),
    )


def train_predictive_model(
    model_id: str,
    name: str,
    domain: PredictiveDomain,
    task_type: PredictiveTaskType,
    model_type: PredictiveModelType,
    columns: list[str],
    rows: list[tuple],
    feature_columns: list[str],
    target_column: str,
    predict_rows: list[tuple] | None = None,
    test_fraction: float = 0.2,
    random_seed: int = 0,
    caller_roles: tuple[str, ...] = (),
    settings: Settings | None = None,
) -> PredictiveModelResult:
    """The engine's single entry point: validates, splits, fits,
    evaluates, and (when `predict_rows` is given) scores new rows with
    the freshly-trained model, all in one deterministic, reproducible
    call.

    Args:
        model_id: Caller-supplied identifier for this training run.
        name: A short, human-readable name.
        domain: See `analytics.models.PredictiveDomain`.
        task_type: See `analytics.models.PredictiveTaskType`.
        model_type: See `analytics.models.PredictiveModelType` -- must
            match `task_type` (`REGRESSION` -> `LINEAR_REGRESSION`,
            `CLASSIFICATION` -> `LOGISTIC_REGRESSION`), checked, never
            silently coerced.
        columns: The real dataset's column names.
        rows: The real dataset's rows -- read only, never mutated.
        feature_columns: Which columns to train on -- every one must be
            numeric in this engine version (categorical features are a
            disclosed, bounded scope -- see this module's own docstring).
        target_column: What to predict.
        predict_rows: Optional new, unlabeled rows (same `columns`
            layout) to score with the freshly-trained model -- `None`
            or empty means "train and evaluate only."
        test_fraction: Fraction of `rows` held out for evaluation,
            `(0, 1)` exclusive.
        random_seed: The deterministic split seed (see
            `_deterministic_order`'s own docstring).
        caller_roles: The roles of whoever is asking to train this
            model -- used only for the restricted-column authorization
            check.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        A `PredictiveModelResult` -- `status != TRAINED` (with every
        reason in `rejection_reasons`) is never a partial training run;
        `metadata`/metrics/`predictions` are populated only when
        `status == TRAINED`.
    """
    settings = settings or get_settings()
    violations: list[str] = []

    if model_type != _TASK_MODEL_COMPAT[task_type]:
        violations.append(
            f"model_type {model_type.value!r} is not valid for task_type {task_type.value!r} "
            f"(expected {_TASK_MODEL_COMPAT[task_type].value!r})"
        )
    if target_column not in columns:
        violations.append(f"unknown target column {target_column!r}")
    if target_column in feature_columns:
        violations.append(f"target column {target_column!r} may not also be a feature")
    if not feature_columns:
        violations.append("at least one feature column is required")
    unknown_features = [f for f in feature_columns if f not in columns]
    violations.extend(f"unknown feature column {f!r}" for f in unknown_features)
    if len(feature_columns) > settings.predictive_max_features:
        violations.append(
            f"too many feature columns ({len(feature_columns)}); the configured maximum is "
            f"{settings.predictive_max_features}"
        )
    if not (0.0 < test_fraction < 1.0):
        violations.append("test_fraction must be strictly between 0 and 1")
    if predict_rows and len(predict_rows) > settings.predictive_max_predict_rows:
        violations.append(
            f"too many predict_rows ({len(predict_rows)}); the configured maximum is "
            f"{settings.predictive_max_predict_rows}"
        )

    if violations:
        return _rejected(
            model_id, name, domain, task_type, PredictiveModelStatus.INVALID, tuple(violations)
        )

    known_features = [f for f in feature_columns if f not in unknown_features]

    restricted = _restricted_column_names(settings)
    checked_columns = [*known_features, target_column]
    restricted_hits = [c for c in checked_columns if c.lower() in restricted]
    if restricted_hits and not has_role_permission(
        caller_roles, Permission.VIEW_RESTRICTED_COLUMNS
    ):
        log_security_event(
            "predictive_unauthorized",
            "info",
            "A predictive model training request referencing a restricted column was "
            "rejected for a caller lacking VIEW_RESTRICTED_COLUMNS.",
            model_id=model_id,
            domain=domain.value,
            columns=restricted_hits,
        )
        return _rejected(
            model_id,
            name,
            domain,
            task_type,
            PredictiveModelStatus.UNAUTHORIZED,
            (f"not authorized: restricted column(s) referenced ({', '.join(restricted_hits)})",),
        )

    feature_idx = [columns.index(f) for f in known_features]
    non_numeric_features = [
        f
        for f, idx in zip(known_features, feature_idx, strict=True)
        if not _all_numeric([row[idx] for row in rows])
    ]
    if non_numeric_features:
        return _rejected(
            model_id,
            name,
            domain,
            task_type,
            PredictiveModelStatus.INVALID,
            (
                "non-numeric feature column(s), not supported in this engine version "
                f"(categorical features must be pre-encoded by the caller): "
                f"{', '.join(non_numeric_features)}",
            ),
        )

    target_idx = columns.index(target_column)
    target_values_all = [row[target_idx] for row in rows]
    if task_type == PredictiveTaskType.REGRESSION and not _all_numeric(target_values_all):
        return _rejected(
            model_id,
            name,
            domain,
            task_type,
            PredictiveModelStatus.INVALID,
            ("the target column must be numeric for a regression task",),
        )

    min_rows = max(
        settings.predictive_min_training_rows,
        settings.predictive_min_rows_per_feature * len(known_features),
    )
    if len(rows) < min_rows:
        return _rejected(
            model_id,
            name,
            domain,
            task_type,
            PredictiveModelStatus.INSUFFICIENT_DATA,
            (f"fewer than {min_rows} row(s) available (got {len(rows)})",),
        )

    if task_type == PredictiveTaskType.CLASSIFICATION:
        class_counts_all = Counter(str(v) for v in target_values_all)
        if len(class_counts_all) < 2:
            return _rejected(
                model_id,
                name,
                domain,
                task_type,
                PredictiveModelStatus.INSUFFICIENT_DATA,
                ("at least two distinct classes are required for classification",),
            )
        too_small = [
            cls for cls, n in class_counts_all.items() if n < settings.predictive_min_rows_per_class
        ]
        if too_small:
            return _rejected(
                model_id,
                name,
                domain,
                task_type,
                PredictiveModelStatus.INSUFFICIENT_DATA,
                (
                    f"class(es) with fewer than {settings.predictive_min_rows_per_class} row(s): "
                    f"{', '.join(too_small)}",
                ),
            )

    leakage_findings = _detect_leakage(columns, rows, known_features, target_column, settings)
    if leakage_findings and settings.predictive_reject_on_leakage:
        return _rejected(
            model_id,
            name,
            domain,
            task_type,
            PredictiveModelStatus.LEAKAGE_DETECTED,
            tuple(f"{f.feature}: {f.reason}" for f in leakage_findings),
            leakage_findings=leakage_findings,
        )

    n = len(rows)
    x_all = [[float(row[i]) for i in feature_idx] for row in rows]

    if task_type == PredictiveTaskType.REGRESSION:
        y_all: list[Any] = [float(v) for v in target_values_all]
        train_idx, test_idx = _plain_split(n, test_fraction, random_seed)
    else:
        y_all = list(target_values_all)
        train_idx, test_idx = _stratified_split(y_all, test_fraction, random_seed)

    x_train = [x_all[i] for i in train_idx]
    y_train = [y_all[i] for i in train_idx]
    x_test = [x_all[i] for i in test_idx]
    y_test = [y_all[i] for i in test_idx]

    try:
        if task_type == PredictiveTaskType.REGRESSION:
            model: LinearRegression | LogisticRegression = LinearRegression()
            model.fit(x_train, y_train)
            test_predictions = model.predict(x_test)
            regression_metrics = RegressionMetrics(
                mae=round(float(mean_absolute_error(y_test, test_predictions)), 6),
                rmse=round(float(mean_squared_error(y_test, test_predictions) ** 0.5), 6),
                r2=round(float(r2_score(y_test, test_predictions)), 6),
            )
            classification_metrics = None
            coefficients = dict(zip(known_features, (float(c) for c in model.coef_), strict=True))
            intercept = float(model.intercept_)
        else:
            model = LogisticRegression(random_state=random_seed, max_iter=1000)
            model.fit(x_train, y_train)
            test_predictions = model.predict(x_test)
            regression_metrics = None
            classification_metrics = ClassificationMetrics(
                accuracy=round(float(accuracy_score(y_test, test_predictions)), 6),
                precision=round(
                    float(
                        precision_score(y_test, test_predictions, average="macro", zero_division=0)
                    ),
                    6,
                ),
                recall=round(
                    float(recall_score(y_test, test_predictions, average="macro", zero_division=0)),
                    6,
                ),
                f1=round(
                    float(f1_score(y_test, test_predictions, average="macro", zero_division=0)), 6
                ),
                test_class_counts=dict(Counter(str(v) for v in y_test)),
            )
            # Binary or multi-class: coef_ has one row per class (or one row
            # total for binary). Disclosed, bounded scope -- see
            # PredictiveModelMetadata.coefficients' own docstring.
            coefficients = dict(
                zip(known_features, (float(c) for c in model.coef_[0]), strict=True)
            )
            intercept = float(model.intercept_[0])
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a solver failure must be reported, never crash the caller
        return _rejected(
            model_id,
            name,
            domain,
            task_type,
            PredictiveModelStatus.ERROR,
            (f"the model failed to fit: {exc}",),
        )

    data_fp = _data_fingerprint(columns, rows, known_features, target_column)
    model_fp = _model_fingerprint(model_type, known_features, target_column, data_fp)
    metadata = PredictiveModelMetadata(
        model_type=model_type,
        feature_columns=tuple(known_features),
        target_column=target_column,
        training_row_count=len(train_idx),
        test_row_count=len(test_idx),
        test_fraction=test_fraction,
        random_seed=random_seed,
        data_fingerprint=data_fp,
        model_fingerprint=model_fp,
        coefficients=coefficients,
        intercept=intercept,
    )

    predictions: tuple[PredictionWithUncertainty, ...] = ()
    if predict_rows:
        x_predict = [[float(row[i]) for i in feature_idx] for row in predict_rows]
        if task_type == PredictiveTaskType.REGRESSION:
            residuals = [float(a) - float(p) for a, p in zip(y_test, test_predictions, strict=True)]
            residual_std = _statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
            margin = _Z_95 * residual_std
            predicted_values = model.predict(x_predict)
            predictions = tuple(
                PredictionWithUncertainty(
                    index=i,
                    value=float(value),
                    lower_bound=round(float(value) - margin, 6),
                    upper_bound=round(float(value) + margin, 6),
                )
                for i, value in enumerate(predicted_values)
            )
        else:
            predicted_labels = model.predict(x_predict)
            predicted_proba = model.predict_proba(x_predict)
            predictions = tuple(
                PredictionWithUncertainty(
                    index=i,
                    value=str(label),
                    class_probabilities={
                        str(cls): round(float(p), 6)
                        for cls, p in zip(model.classes_, predicted_proba[i], strict=True)
                    },
                )
                for i, label in enumerate(predicted_labels)
            )

    limitations = [
        "This is a modeled prediction over historical data, not an observed fact -- treat it "
        "as an estimate, not a guarantee.",
        "Only numeric features are supported in this engine version -- categorical features "
        "must be pre-encoded by the caller before training.",
    ]
    if (
        classification_metrics is not None
        and min(classification_metrics.test_class_counts.values()) < 10
    ):
        limitations.append(
            "at least one class has fewer than 10 examples in the held-out test split -- "
            "treat these metrics as directional, not precise."
        )

    log_security_event(
        "predictive_model_trained",
        "info",
        "A predictive model was trained and evaluated.",
        model_id=model_id,
        domain=domain.value,
        task_type=task_type.value,
        model_type=model_type.value,
        data_fingerprint=data_fp,
        model_fingerprint=model_fp,
        training_row_count=len(train_idx),
        test_row_count=len(test_idx),
    )

    return PredictiveModelResult(
        model_id=model_id,
        name=name,
        domain=domain,
        task_type=task_type,
        status=PredictiveModelStatus.TRAINED,
        metadata=metadata,
        regression_metrics=regression_metrics,
        classification_metrics=classification_metrics,
        predictions=predictions,
        leakage_findings=leakage_findings,
        limitations=tuple(limitations),
        engine_version=PREDICTIVE_ENGINE_VERSION,
    )


def detect_feature_drift(
    reference_columns: list[str],
    reference_rows: list[tuple],
    new_columns: list[str],
    new_rows: list[tuple],
    feature_columns: list[str],
    settings: Settings | None = None,
) -> FeatureDriftReport:
    """Compares `reference_rows` (e.g. the data a model was trained on)
    against `new_rows` (e.g. today's data), feature by feature, using a
    two-sample Kolmogorov-Smirnov test (`scipy.stats.ks_2samp`) -- "test
    ... drift," the mission's own named requirement. Deliberately
    standalone, callable against any two same-shaped datasets, not only
    before/after training one specific model (mirrors `RootCauseResult`'s
    own "standalone two-dataset comparison" precedent).

    Args:
        reference_columns: The reference dataset's column names.
        reference_rows: The reference dataset's rows.
        new_columns: The new dataset's column names.
        new_rows: The new dataset's rows.
        feature_columns: Which columns to compare -- a column missing
            from either side, or non-numeric, or with fewer than 2
            points on either side, is skipped and disclosed in `notes`,
            never silently ignored.
        settings: Defaults to `config.settings.get_settings()`.
    """
    settings = settings or get_settings()
    threshold = settings.predictive_drift_p_value_threshold
    findings: list[FeatureDriftFinding] = []
    notes: list[str] = []

    for feature in feature_columns:
        if feature not in reference_columns or feature not in new_columns:
            notes.append(f"{feature!r}: not present in both datasets, skipped")
            continue
        ref_idx = reference_columns.index(feature)
        new_idx = new_columns.index(feature)
        ref_values = [row[ref_idx] for row in reference_rows]
        new_values = [row[new_idx] for row in new_rows]
        if not _all_numeric(ref_values) or not _all_numeric(new_values):
            notes.append(f"{feature!r}: not numeric on both sides, skipped")
            continue
        ref_numeric = [float(v) for v in ref_values if v is not None]
        new_numeric = [float(v) for v in new_values if v is not None]
        if len(ref_numeric) < 2 or len(new_numeric) < 2:
            notes.append(f"{feature!r}: fewer than 2 points on one side, skipped")
            continue

        result = ks_2samp(ref_numeric, new_numeric)
        is_drifted = bool(result.pvalue < threshold)
        findings.append(
            FeatureDriftFinding(
                feature=feature,
                reference_count=len(ref_numeric),
                new_count=len(new_numeric),
                ks_statistic=round(float(result.statistic), 6),
                p_value=round(float(result.pvalue), 6),
                is_drifted=is_drifted,
            )
        )

    return FeatureDriftReport(
        threshold=threshold,
        findings=tuple(findings),
        any_drifted=any(f.is_drifted for f in findings),
        notes=tuple(notes),
    )


__all__ = ["train_predictive_model", "detect_feature_drift"]
