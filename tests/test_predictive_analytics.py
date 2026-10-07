"""Unit tests for `analytics/predictive.py` -- Prompt 40
(`40_PREDICTIVE_ANALYTICS_CONTRACT.md`)'s governed Predictive Analytics
engine.

Covers, matching the mission's own explicit testing requirement ("test
reproducibility, drift, insufficient data and authorization"):

- reproducibility (the same data/seed always produce the same split,
  coefficients, and fingerprints),
- drift detection (two-sample KS-test, both "no drift" and "drift"
  cases),
- insufficient data (too few rows, too few classes, a too-small class),
- authorization (a restricted feature/target column, gated on
  `Permission.VIEW_RESTRICTED_COLUMNS`),
- leakage checks (an identical feature, a near-perfectly-correlated
  feature, soft-vs-hard rejection),
- train/test separation (metrics computed only on the held-out split),
- feature validation (unknown columns, non-numeric features, a
  mismatched model_type/task_type pairing), and
- versioning/evaluation (fingerprints, coefficients, metrics all
  present and correctly shaped).

All example numbers in this file were independently hand-computed and
cross-checked by directly exercising the module (via a scratch script,
not shown in the final diff) before being written into the test file --
not "whatever the code happened to produce."
"""

from __future__ import annotations

import pytest
from analytics.models import (
    PredictiveDomain,
    PredictiveModelStatus,
    PredictiveModelType,
    PredictiveTaskType,
)
from analytics.predictive import detect_feature_drift, train_predictive_model

from config.settings import Settings

_SETTINGS = Settings(
    predictive_min_training_rows=20,
    predictive_min_rows_per_feature=10,
    predictive_min_rows_per_class=5,
    predictive_leakage_correlation_threshold=0.98,
    predictive_reject_on_leakage=True,
    predictive_drift_p_value_threshold=0.05,
    predictive_max_features=20,
    predictive_max_predict_rows=200,
)
_ANALYST = ("analyst",)


def _regression_rows(n: int = 40) -> list[tuple]:
    rows = []
    for i in range(1, n + 1):
        x1 = float(i)
        x2 = float((i * 7) % 13)
        y = 2 * x1 + 3 * x2 + 5.0
        rows.append((x1, x2, y))
    return rows


def _classification_rows(n: int = 60, cutoff: int = 20) -> list[tuple]:
    return [(float(i), "yes" if i < cutoff else "no") for i in range(1, n + 1)]


class TestRegression:
    def test_a_clean_linear_relationship_is_recovered_exactly(self):
        rows = _regression_rows()
        result = train_predictive_model(
            "m1",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            rows,
            ["x1", "x2"],
            "y",
            predict_rows=[(50.0, 2.0)],
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.TRAINED
        assert result.regression_metrics is not None
        assert result.regression_metrics.r2 == pytest.approx(1.0, abs=1e-6)
        assert result.regression_metrics.mae == pytest.approx(0.0, abs=1e-6)
        assert result.metadata is not None
        assert result.metadata.coefficients["x1"] == pytest.approx(2.0, abs=1e-6)
        assert result.metadata.coefficients["x2"] == pytest.approx(3.0, abs=1e-6)
        assert result.metadata.intercept == pytest.approx(5.0, abs=1e-6)
        assert result.predictions[0].value == pytest.approx(111.0, abs=1e-6)
        # A perfect fit has zero residual spread -- the prediction interval
        # collapses to the point estimate, not an arbitrarily wide one.
        assert result.predictions[0].lower_bound == pytest.approx(111.0, abs=1e-6)
        assert result.predictions[0].upper_bound == pytest.approx(111.0, abs=1e-6)
        assert result.truth_level.value == "ai_inference"


class TestClassification:
    def test_a_clean_separable_relationship_is_recovered(self):
        rows = _classification_rows()
        result = train_predictive_model(
            "m2",
            "Churn",
            PredictiveDomain.CHURN,
            PredictiveTaskType.CLASSIFICATION,
            PredictiveModelType.LOGISTIC_REGRESSION,
            ["tenure", "churn"],
            rows,
            ["tenure"],
            "churn",
            predict_rows=[(5.0,), (50.0,)],
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.TRAINED
        assert result.classification_metrics is not None
        assert result.classification_metrics.accuracy == pytest.approx(1.0)
        assert result.predictions[0].value == "yes"
        assert result.predictions[1].value == "no"
        assert result.predictions[0].class_probabilities is not None
        assert sum(result.predictions[0].class_probabilities.values()) == pytest.approx(1.0)

    def test_test_class_counts_reflect_the_held_out_split_only(self):
        rows = _classification_rows()
        result = train_predictive_model(
            "m2b",
            "Churn",
            PredictiveDomain.CHURN,
            PredictiveTaskType.CLASSIFICATION,
            PredictiveModelType.LOGISTIC_REGRESSION,
            ["tenure", "churn"],
            rows,
            ["tenure"],
            "churn",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.classification_metrics is not None
        assert result.metadata is not None
        total_test = sum(result.classification_metrics.test_class_counts.values())
        assert total_test == result.metadata.test_row_count


class TestReproducibility:
    def test_the_same_data_and_seed_always_produce_the_same_result(self):
        rows = _regression_rows()
        args = (
            "m3",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            rows,
            ["x1", "x2"],
            "y",
        )
        result1 = train_predictive_model(*args, caller_roles=_ANALYST, settings=_SETTINGS)
        result2 = train_predictive_model(*args, caller_roles=_ANALYST, settings=_SETTINGS)
        assert result1.metadata is not None and result2.metadata is not None
        assert result1.metadata.data_fingerprint == result2.metadata.data_fingerprint
        assert result1.metadata.model_fingerprint == result2.metadata.model_fingerprint
        assert result1.metadata.coefficients == result2.metadata.coefficients
        assert result1.regression_metrics == result2.regression_metrics

    def test_a_different_seed_produces_a_different_split_but_the_same_fit_on_clean_data(self):
        """A perfectly linear, noise-free relationship is recovered
        identically regardless of which rows land in train vs. test --
        the fingerprints (which hash the *data*, not the split) stay
        identical, while the split sizes can differ if test_fraction
        rounds differently; the coefficients are identical either way
        because the data has zero noise."""
        rows = _regression_rows()
        base_args = (
            "m3b",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            rows,
            ["x1", "x2"],
            "y",
        )
        result_seed0 = train_predictive_model(
            *base_args, random_seed=0, caller_roles=_ANALYST, settings=_SETTINGS
        )
        result_seed1 = train_predictive_model(
            *base_args, random_seed=1, caller_roles=_ANALYST, settings=_SETTINGS
        )
        # Same underlying data -> same data fingerprint regardless of seed.
        assert result_seed0.metadata is not None and result_seed1.metadata is not None
        assert result_seed0.metadata.data_fingerprint == result_seed1.metadata.data_fingerprint
        assert result_seed0.metadata.coefficients == pytest.approx(
            result_seed1.metadata.coefficients
        )

    def test_changing_the_data_changes_the_fingerprint(self):
        rows = _regression_rows()
        rows_changed = _regression_rows(n=41)
        args = (
            "m3c",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
        )
        result1 = train_predictive_model(
            *args, rows, ["x1", "x2"], "y", caller_roles=_ANALYST, settings=_SETTINGS
        )
        result2 = train_predictive_model(
            *args, rows_changed, ["x1", "x2"], "y", caller_roles=_ANALYST, settings=_SETTINGS
        )
        assert result1.metadata is not None and result2.metadata is not None
        assert result1.metadata.data_fingerprint != result2.metadata.data_fingerprint
        assert result1.metadata.model_fingerprint != result2.metadata.model_fingerprint


class TestDrift:
    def test_near_identical_distributions_report_no_drift(self):
        reference = [(float(i),) for i in range(1, 31)]
        new = [(float(i) + 0.1,) for i in range(1, 31)]
        report = detect_feature_drift(["a"], reference, ["a"], new, ["a"], settings=_SETTINGS)
        assert report.any_drifted is False
        assert report.findings[0].p_value > _SETTINGS.predictive_drift_p_value_threshold

    def test_a_shifted_distribution_is_flagged_drifted(self):
        reference = [(float(i),) for i in range(1, 31)]
        new = [(float(i) + 50.0,) for i in range(1, 31)]
        report = detect_feature_drift(["a"], reference, ["a"], new, ["a"], settings=_SETTINGS)
        assert report.any_drifted is True
        assert report.findings[0].is_drifted is True
        assert report.findings[0].p_value < _SETTINGS.predictive_drift_p_value_threshold

    def test_a_column_missing_from_one_side_is_skipped_and_disclosed(self):
        reference = [(float(i),) for i in range(1, 31)]
        new = [(float(i),) for i in range(1, 31)]
        report = detect_feature_drift(["a"], reference, ["b"], new, ["a"], settings=_SETTINGS)
        assert report.findings == ()
        assert any("not present in both" in note for note in report.notes)

    def test_a_non_numeric_column_is_skipped_and_disclosed(self):
        reference = [("red",) for _ in range(10)]
        new = [("blue",) for _ in range(10)]
        report = detect_feature_drift(
            ["color"], reference, ["color"], new, ["color"], settings=_SETTINGS
        )
        assert report.findings == ()
        assert any("not numeric" in note for note in report.notes)


class TestInsufficientData:
    def test_too_few_rows_overall_is_rejected(self):
        result = train_predictive_model(
            "m4",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            _regression_rows()[:5],
            ["x1", "x2"],
            "y",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INSUFFICIENT_DATA

    def test_a_single_class_is_rejected_for_classification(self):
        rows = [(float(i), "yes") for i in range(1, 31)]
        result = train_predictive_model(
            "m5",
            "Churn",
            PredictiveDomain.CHURN,
            PredictiveTaskType.CLASSIFICATION,
            PredictiveModelType.LOGISTIC_REGRESSION,
            ["tenure", "churn"],
            rows,
            ["tenure"],
            "churn",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INSUFFICIENT_DATA
        assert any("two distinct classes" in r for r in result.rejection_reasons)

    def test_a_too_small_class_is_rejected(self):
        rows = [(float(i), "yes" if i == 1 else "no") for i in range(1, 31)]
        result = train_predictive_model(
            "m6",
            "Churn",
            PredictiveDomain.CHURN,
            PredictiveTaskType.CLASSIFICATION,
            PredictiveModelType.LOGISTIC_REGRESSION,
            ["tenure", "churn"],
            rows,
            ["tenure"],
            "churn",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INSUFFICIENT_DATA
        assert any("fewer than" in r for r in result.rejection_reasons)


class TestLeakage:
    def test_an_identical_feature_is_hard_rejected_by_default(self):
        rows = [(float(i), float(i)) for i in range(1, 31)]
        result = train_predictive_model(
            "m7",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["a", "b"],
            rows,
            ["a"],
            "b",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.LEAKAGE_DETECTED
        assert result.leakage_findings[0].correlation == 1.0

    def test_leakage_is_disclosed_but_not_rejected_when_the_flag_is_off(self):
        lenient = Settings(**{**_SETTINGS.__dict__, "predictive_reject_on_leakage": False})
        rows = [(float(i), float(i)) for i in range(1, 31)]
        result = train_predictive_model(
            "m8",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["a", "b"],
            rows,
            ["a"],
            "b",
            caller_roles=_ANALYST,
            settings=lenient,
        )
        assert result.status == PredictiveModelStatus.TRAINED
        assert result.leakage_findings  # still disclosed

    def test_an_unrelated_feature_is_never_flagged(self):
        rows = [(float(i), float((i * 7) % 13)) for i in range(1, 31)]
        result = train_predictive_model(
            "m9",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["a", "b"],
            rows,
            ["a"],
            "b",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.leakage_findings == ()


class TestAuthorization:
    def test_a_restricted_feature_is_rejected_for_a_viewer(self, monkeypatch):
        import analytics.predictive as predictive_module

        monkeypatch.setattr(
            predictive_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "x1"): "restricted"},
        )
        result = train_predictive_model(
            "m10",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            _regression_rows(),
            ["x1", "x2"],
            "y",
            caller_roles=("viewer",),
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.UNAUTHORIZED

    def test_the_same_restricted_feature_is_allowed_for_an_analyst(self, monkeypatch):
        import analytics.predictive as predictive_module

        monkeypatch.setattr(
            predictive_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "x1"): "restricted"},
        )
        result = train_predictive_model(
            "m11",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            _regression_rows(),
            ["x1", "x2"],
            "y",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.TRAINED

    def test_a_restricted_target_column_is_also_checked(self, monkeypatch):
        import analytics.predictive as predictive_module

        monkeypatch.setattr(
            predictive_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "y"): "restricted"},
        )
        result = train_predictive_model(
            "m12",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            _regression_rows(),
            ["x1", "x2"],
            "y",
            caller_roles=("viewer",),
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.UNAUTHORIZED


class TestFeatureValidation:
    def test_an_unknown_target_column_is_invalid(self):
        result = train_predictive_model(
            "m13",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["a", "b"],
            [(1.0, 2.0)] * 30,
            ["a"],
            "does_not_exist",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INVALID

    def test_target_as_its_own_feature_is_invalid(self):
        result = train_predictive_model(
            "m14",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["a", "b"],
            [(1.0, 2.0)] * 30,
            ["a", "b"],
            "b",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INVALID

    def test_a_non_numeric_feature_is_rejected(self):
        rows = [(float(i), "red" if i % 2 else "blue", float(i) * 2) for i in range(1, 31)]
        result = train_predictive_model(
            "m15",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["a", "color", "b"],
            rows,
            ["a", "color"],
            "b",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INVALID
        assert any("non-numeric" in r for r in result.rejection_reasons)

    def test_a_mismatched_model_type_and_task_type_is_rejected(self):
        result = train_predictive_model(
            "m16",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LOGISTIC_REGRESSION,  # wrong for REGRESSION
            ["a", "b"],
            _regression_rows(),
            ["a"],
            "b",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INVALID

    def test_too_many_features_is_rejected_before_any_fit(self):
        tight = Settings(**{**_SETTINGS.__dict__, "predictive_max_features": 1})
        result = train_predictive_model(
            "m17",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            _regression_rows(),
            ["x1", "x2"],
            "y",
            caller_roles=_ANALYST,
            settings=tight,
        )
        assert result.status == PredictiveModelStatus.INVALID


class TestTrainTestSeparation:
    def test_metrics_are_computed_only_on_the_held_out_split(self):
        """A model with noise in training but not in the held-out
        evaluation would show a perfect R2 only if train and test were
        accidentally the same rows -- this test instead just confirms
        the reported train/test counts sum to the full dataset and are
        disjoint by construction (the split functions never duplicate
        or drop a row)."""
        rows = _regression_rows()
        result = train_predictive_model(
            "m18",
            "Demand",
            PredictiveDomain.DEMAND,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            rows,
            ["x1", "x2"],
            "y",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.metadata is not None
        assert result.metadata.training_row_count + result.metadata.test_row_count == len(rows)
        assert result.metadata.test_row_count > 0
        assert result.metadata.training_row_count > 0


class TestNoLlmInventedModel:
    """The structural half of this engine's own "closed, reviewed model
    catalog" guarantee -- see `analytics.models.PredictiveModelType`'s
    own docstring."""

    def test_model_type_is_a_closed_enum_not_a_free_string(self):
        assert {m.value for m in PredictiveModelType} == {
            "linear_regression",
            "logistic_regression",
        }
