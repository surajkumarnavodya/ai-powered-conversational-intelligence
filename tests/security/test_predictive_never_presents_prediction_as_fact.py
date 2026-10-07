"""Regression test for a governance invariant that spans three modules
at once -- `analytics/models.py`, `analytics/predictive.py`, and
`agent/multiagent/agents.py`'s `predictive_handler` -- so per
`tests/security/README.md`'s own rule, it belongs here rather than being
split across module-specific test files.

Master-contract rule 10 ("never present inference, simulation or
prediction as observed fact"), applied specifically to the Predictive
Analytics engine (Prompt 40, `40_PREDICTIVE_ANALYTICS_CONTRACT.md`):

1. **Structural**: `analytics.models.PredictiveModelType` is a closed,
   reviewed, two-member enum -- there is no way to request an arbitrary
   model class name, let alone an LLM-authored one.
2. **Behavioral**: `PredictiveModelResult.truth_level` is always
   `DataTruthLevel.AI_INFERENCE`, across every representative outcome
   (trained regression, trained classification, insufficient data,
   invalid, unauthorized, leakage detected) -- never promoted to
   `DATABASE_FACT`/`CONFIRMED_BUSINESS_TRUTH`.
3. **Text-level, through the one live multi-agent handler**: every
   claim `predictive_handler` actually renders says "modeled estimate"
   and "not an observed fact" explicitly.
"""

from __future__ import annotations

from analytics.models import (
    PredictiveDomain,
    PredictiveModelStatus,
    PredictiveModelType,
    PredictiveTaskType,
)
from analytics.predictive import train_predictive_model

from agent.provenance import DataTruthLevel
from config.settings import Settings

_SETTINGS = Settings(predictive_min_training_rows=20, predictive_min_rows_per_feature=10)
_ANALYST = ("analyst",)


def _regression_rows() -> list[tuple]:
    rows = []
    for i in range(1, 41):
        x1 = float(i)
        x2 = float((i * 7) % 13)
        rows.append((x1, x2, 2 * x1 + 3 * x2 + 5.0))
    return rows


class TestStructuralClosedModelCatalog:
    def test_predictive_model_type_is_a_closed_two_member_enum(self):
        assert {m.value for m in PredictiveModelType} == {
            "linear_regression",
            "logistic_regression",
        }


class TestTruthLevelNeverPromoted:
    def test_a_trained_regression_result_is_ai_inference(self):
        result = train_predictive_model(
            "p1",
            "Test",
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
        assert result.truth_level == DataTruthLevel.AI_INFERENCE

    def test_a_trained_classification_result_is_ai_inference(self):
        rows = [(float(i), "yes" if i < 20 else "no") for i in range(1, 61)]
        result = train_predictive_model(
            "p2",
            "Test",
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
        assert result.status == PredictiveModelStatus.TRAINED
        assert result.truth_level == DataTruthLevel.AI_INFERENCE

    def test_an_insufficient_data_result_is_still_ai_inference(self):
        result = train_predictive_model(
            "p3",
            "Test",
            PredictiveDomain.OTHER,
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
        assert result.truth_level == DataTruthLevel.AI_INFERENCE

    def test_an_invalid_result_is_still_ai_inference(self):
        result = train_predictive_model(
            "p4",
            "Test",
            PredictiveDomain.OTHER,
            PredictiveTaskType.REGRESSION,
            PredictiveModelType.LINEAR_REGRESSION,
            ["x1", "x2", "y"],
            _regression_rows(),
            ["not_declared"],
            "y",
            caller_roles=_ANALYST,
            settings=_SETTINGS,
        )
        assert result.status == PredictiveModelStatus.INVALID
        assert result.truth_level == DataTruthLevel.AI_INFERENCE

    def test_an_unauthorized_result_is_still_ai_inference(self, monkeypatch):
        import analytics.predictive as predictive_module

        monkeypatch.setattr(
            predictive_module,
            "load_sensitive_columns",
            lambda path=None: {("T", "x1"): "restricted"},
        )
        result = train_predictive_model(
            "p5",
            "Test",
            PredictiveDomain.OTHER,
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
        assert result.truth_level == DataTruthLevel.AI_INFERENCE

    def test_a_leakage_detected_result_is_still_ai_inference(self):
        rows = [(float(i), float(i)) for i in range(1, 31)]
        result = train_predictive_model(
            "p6",
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
        assert result.truth_level == DataTruthLevel.AI_INFERENCE


class TestHandlerClaimTextAlwaysDisclosesEstimate:
    """Exercises the one live caller (`agent.multiagent.agents
    .predictive_handler`) end to end -- the actual text a user would
    see."""

    def test_every_claim_discloses_modeled_estimate_not_fact(self, monkeypatch):
        import agent.multiagent.supervisor as supervisor
        import agent.multiagent.tools as tools
        from agent.multiagent.policy import reset_breakers
        from agent.multiagent.supervisor import PredictiveRequest, run_supervised_analysis

        reset_breakers()
        settings = _SETTINGS

        def fake_sql(question, **kwargs):
            rows = _regression_rows()
            return {
                "status": "succeeded",
                "sql": "SELECT 1",
                "row_count": len(rows),
                "result_columns": ["x1", "x2", "y"],
                "result_rows": rows,
                "analytical_result": None,
                "analytical_intent": None,
                "recommendations": [],
            }

        monkeypatch.setattr(supervisor, "get_settings", lambda: settings)
        monkeypatch.setattr(tools, "run_agent", fake_sql)
        monkeypatch.setattr(supervisor, "_database_id", lambda s, t: "default")
        monkeypatch.setattr(
            tools, "plan_subquestions", lambda q, s, m, n: (["Revenue by region?"], "planner")
        )
        monkeypatch.setattr(
            tools, "retrieve_business_context", lambda *a, **k: type("R", (), {"items": []})()
        )

        try:
            result = run_supervised_analysis(
                "Predict demand",
                caller_roles=("analyst",),
                tenant_id="tenant-a",
                predictive_requests=[
                    PredictiveRequest(
                        data_task="Q1",
                        model_id="pm1",
                        name="Demand model",
                        domain=PredictiveDomain.DEMAND,
                        task_type=PredictiveTaskType.REGRESSION,
                        model_type=PredictiveModelType.LINEAR_REGRESSION,
                        feature_columns=("x1", "x2"),
                        target_column="y",
                        predict_rows=((50.0, 2.0),),
                    )
                ],
            )
        finally:
            reset_breakers()

        pred_claims = [c for c in result.claims if c.agent.value == "predictive"]
        assert pred_claims
        for claim in pred_claims:
            assert "modeled estimate" in claim.text
            assert "not an observed fact" in claim.text
            assert claim.truth_level.value == "ai_inference"
