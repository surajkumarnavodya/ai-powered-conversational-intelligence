"""Every `Settings` field should be discoverable from the documented
configuration surface -- `.env.example` (the template an operator copies) or
[`docs/CONFIGURATION.md`](../docs/CONFIGURATION.md) (the browsable reference).

**Why this file exists.** UAT-readiness Phase 10 (`DOC-013`, 2026-10-30)
measured the gap for the first time and found it large and silent:
`config/settings.py` declared 447 fields, of which 205 appeared nowhere in
`docs/CONFIGURATION.md` and 115 nowhere in `.env.example` -- **110 in
neither**, so they were not reachable from any documented surface at all.
Twenty-six of those were `ENABLE_*` feature flags, several on by default,
and `docs/DEPLOYMENT.md`'s rollback procedure tells an operator to "set the
flag back" for a misbehaving feature. Naming flags an operator cannot find
is not a rollback procedure.

The 26 flags were documented in that pass, taking the gap to 77. The rest
are numeric tuning constants for the analytics/forecasting/root-cause/
predictive/query-store/data-quality engines.

**This is a no-growth baseline, not an allowlist.** Both directions fail:

* a *new* undocumented setting fails `test_no_new_undocumented_settings`,
  so the gap cannot grow one convenient field at a time -- which is exactly
  how it reached 110;
* a setting that *gets* documented while still listed below fails
  `test_the_baseline_does_not_outlive_the_gap`, so the list cannot decay
  into a permanent exemption the way a plain allowlist would.

Either failure is fixed by editing `_UNDOCUMENTED_BASELINE`, never by
widening the comparison.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from config.settings import Settings

_REPO = Path(__file__).resolve().parent.parent
_ENV_EXAMPLE = _REPO / ".env.example"
_CONFIG_DOC = _REPO / "docs" / "CONFIGURATION.md"

# Computed at construction from other inputs rather than read from an
# environment variable of their own, so there is nothing for an operator to
# set and nothing to document as settable. `databases` is built by
# `_parse_named_connections()` out of `DB_CONNECTIONS` + the `DB_<NAME>_*`
# blocks (all documented); `project_root` is the repository path.
_DERIVED_NOT_SETTABLE = frozenset({"DATABASES", "PROJECT_ROOT"})

# Measured 2026-10-30 at `9e50b6f` + this phase's documentation commits.
# Settings that appear in neither `.env.example` nor `docs/CONFIGURATION.md`.
# Shrink this list as entries are documented; never add to it for a setting
# you are introducing.
_UNDOCUMENTED_BASELINE = frozenset(
    {
        "ANALYTICAL_PLAN_MAX_TOKENS",
        "ANALYTICS_RANKING_MAX_ENTRIES",
        "ANOMALY_ABSOLUTE_THRESHOLD",
        "ANOMALY_IQR_MULTIPLIER",
        "ANOMALY_PERCENT_CHANGE_THRESHOLD",
        "ANOMALY_ROLLING_WINDOW",
        "ANOMALY_SEASONAL_PERIOD",
        "ANOMALY_ZSCORE_THRESHOLD",
        "ARTIFACT_AGING_AFTER_HOURS",
        "ARTIFACT_MAX_PER_USER",
        "ARTIFACT_STALE_AFTER_HOURS",
        "CONTEXT_BUDGET_MAX_CHARS",
        "DATA_QUALITY_DEFAULT_MAX_STALENESS_SECONDS",
        "DATA_QUALITY_MAX_REPORTED_SAMPLES",
        "DATA_QUALITY_MAX_ROWS_PER_TABLE",
        "DATA_QUALITY_MAX_TABLES",
        "DATA_QUALITY_NULL_SPIKE_THRESHOLD",
        "FOLLOW_UP_TOP_N",
        "FORECAST_BACKTEST_HOLDOUT",
        "FORECAST_CONFIDENCE_LEVEL",
        "FORECAST_DEFAULT_HORIZON",
        "FORECAST_MAX_HORIZON",
        "FORECAST_MIN_DATA_POINTS",
        "FORECAST_MOVING_AVERAGE_WINDOW",
        "FORECAST_SEASONAL_PERIOD",
        "FORECAST_SES_ALPHA",
        "GOVERN_NARRATIVE_WITHOUT_EVIDENCE",
        "HF_IMAGE_MODEL",
        "HF_REQUEST_TIMEOUT_SECONDS",
        "INTENT_CLASSIFICATION_MAX_TOKENS",
        "LINEAGE_MAX_QUERIES_PER_REQUEST",
        "LINEAGE_MAX_TRACE_DEPTH",
        "LINEAGE_MAX_TRACE_GRAPH_NODES",
        "MAX_FOLLOW_UP_SUGGESTIONS",
        "OLLAMA_SEED",
        "OLLAMA_TOP_P",
        "OPTIMIZATION_MAX_CONSTRAINTS",
        "OPTIMIZATION_MAX_VARIABLES",
        "PII_DATA_VERIFICATION_SAMPLE_SIZE",
        "PLAN_REVIEW_BLOCKING",
        "PREDICTIVE_DRIFT_P_VALUE_THRESHOLD",
        "PREDICTIVE_LEAKAGE_CORRELATION_THRESHOLD",
        "PREDICTIVE_MAX_FEATURES",
        "PREDICTIVE_MAX_PREDICT_ROWS",
        "PREDICTIVE_MIN_ROWS_PER_CLASS",
        "PREDICTIVE_MIN_ROWS_PER_FEATURE",
        "PREDICTIVE_MIN_TRAINING_ROWS",
        "PREDICTIVE_REJECT_ON_LEAKAGE",
        "QUERY_STORE_HIGH_DURATION_MS_THRESHOLD",
        "QUERY_STORE_LOOKBACK_HOURS",
        "QUERY_STORE_MIN_EXECUTION_COUNT",
        "QUERY_STORE_PREVIEW_MAX_CHARS",
        "QUERY_STORE_REFRESH_INTERVAL_SECONDS",
        "QUERY_STORE_REGRESSION_FACTOR",
        "QUERY_STORE_REGRESSION_RECENT_HOURS",
        "QUERY_STORE_TIMEOUT_SECONDS",
        "QUERY_STORE_TOP_N_QUERIES",
        "RECOMMENDATION_CONCENTRATION_SHARE_THRESHOLD",
        "RECOMMENDATION_MIN_CONFIDENCE",
        "RECOMMENDATION_NULL_RATE_THRESHOLD",
        "RECOMMENDATION_SLOW_STAGE_MS_THRESHOLD",
        "RELATIONSHIP_DATA_VERIFICATION_SAMPLE_SIZE",
        "RELATIONSHIP_INFERENCE_MIN_CONFIDENCE",
        "REQUEST_TIMEOUT_SECONDS",
        "RETRIEVAL_TOP_K_BUSINESS_CONCEPTS",
        "ROOT_CAUSE_INTERACTION_MIN_PERCENT",
        "ROOT_CAUSE_MAX_HIERARCHY_DEPTH",
        "ROOT_CAUSE_MIN_CONTRIBUTION_PERCENT",
        "ROOT_CAUSE_MIN_SAMPLE_SIZE",
        "ROOT_CAUSE_SIGNIFICANCE_ALPHA",
        "SCENARIO_MAX_PARAMETERS",
        "SCENARIO_REJECT_UNREALISTIC",
        "SCENARIO_UNREALISTIC_CHANGE_PERCENT_THRESHOLD",
        "SCHEMA_DRIFT_IMPACT_MAX_TRACE_DEPTH",
        "SCHEMA_DRIFT_MAX_FINDINGS_PER_SCAN",
        "SCHEMA_DRIFT_RENAME_SIMILARITY_THRESHOLD",
        "VISUALIZATION_DEFAULT_TOP_N",
    }
)


def _env_name(field_name: str, field_info: object) -> str:
    """The environment variable a `Settings` field is populated from."""
    alias = getattr(field_info, "validation_alias", None) or getattr(field_info, "alias", None)
    if not isinstance(alias, str):
        alias = field_name
    return alias.upper()


def _settings_env_names() -> set[str]:
    return {_env_name(name, info) for name, info in Settings.model_fields.items()} - set(
        _DERIVED_NOT_SETTABLE
    )


def _mentions(text: str, name: str) -> bool:
    """Whole-word match, so `OLLAMA_MODEL` is not satisfied by
    `OLLAMA_MODEL_SELECTION_ENABLED`."""
    return re.search(r"\b" + re.escape(name) + r"\b", text) is not None


@pytest.fixture(scope="module")
def undocumented() -> set[str]:
    env_text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    doc_text = _CONFIG_DOC.read_text(encoding="utf-8")
    return {
        name
        for name in _settings_env_names()
        if not _mentions(env_text, name) and not _mentions(doc_text, name)
    }


class TestTheDocumentedConfigurationSurface:
    def test_no_new_undocumented_settings(self, undocumented: set[str]) -> None:
        """A setting documented in neither place must be documented, not added
        to the baseline. This is the direction that let the gap reach 110."""
        new = sorted(undocumented - _UNDOCUMENTED_BASELINE)
        assert not new, (
            f"{len(new)} setting(s) appear in neither .env.example nor "
            f"docs/CONFIGURATION.md and are not in the measured baseline: "
            f"{new}. Document them (a `.env.example` entry -- commented out "
            f"is fine -- and a row in docs/CONFIGURATION.md) rather than "
            f"extending _UNDOCUMENTED_BASELINE."
        )

    def test_the_baseline_does_not_outlive_the_gap(self, undocumented: set[str]) -> None:
        """A baseline entry that has since been documented must be removed, so
        this list can never quietly become a permanent exemption."""
        now_documented = sorted(_UNDOCUMENTED_BASELINE - undocumented)
        assert not now_documented, (
            f"{len(now_documented)} baseline entr(ies) are now documented and "
            f"must be deleted from _UNDOCUMENTED_BASELINE: {now_documented}"
        )

    def test_the_baseline_only_names_real_settings(self) -> None:
        """Guards against a renamed or deleted setting leaving a dead entry
        that silently excuses nothing."""
        real = _settings_env_names()
        stale = sorted(_UNDOCUMENTED_BASELINE - real)
        assert not stale, (
            f"_UNDOCUMENTED_BASELINE names setting(s) that no longer exist on " f"Settings: {stale}"
        )

    def test_derived_fields_are_really_derived(self) -> None:
        """`DATABASES`/`PROJECT_ROOT` are excluded because nothing reads an
        environment variable of that name. If that stops being true the
        exclusion is wrong, and so is this test."""
        import config.settings as settings_module

        source = Path(settings_module.__file__).read_text(encoding="utf-8")
        for name in _DERIVED_NOT_SETTABLE:
            assert f'os.environ.get("{name}"' not in source
            assert f'os.getenv("{name}"' not in source


class TestEveryDocumentedFlagIsRealAndItsDefaultIsRight:
    """The engine flags documented in Phase 10 were written from
    `config/settings.py`; this pins both halves so the table cannot drift
    from the code it describes."""

    FLAG_DEFAULTS = {
        "enable_analytics_engine": True,
        "enable_anomaly_detection": True,
        "enable_advanced_root_cause": True,
        "enable_forecasting": True,
        "enable_predictive_analytics": True,
        "enable_what_if_scenarios": True,
        "enable_optimization_engine": True,
        "enable_data_quality_engine": True,
        "enable_lineage_platform": True,
        "enable_schema_drift_detection": True,
        "enable_recommendation_engine": True,
        "enable_recommendation_persistence": True,
        "enable_follow_up_suggestions": True,
        "enable_intent_classification": True,
        "enable_analytical_planning": True,
        "enable_metric_conformance_review": True,
        "enable_query_store_insights": True,
        "enable_relationship_inference": True,
        "enable_relationship_data_verification": False,
        "enable_pii_data_verification": False,
        "enable_agent_harness": True,
        "enable_box_plot_charts": False,
        "enable_map_charts": False,
        "enable_free_generation": False,
        "enable_svg_generation": True,
        "enable_hf_generation": True,
    }

    HARNESS_DEFAULTS = {
        "harness_max_steps": 120,
        "harness_max_llm_calls": 40,
        "harness_max_context_chars": 400_000,
        "harness_max_result_bytes": 8_000_000,
    }

    @pytest.mark.parametrize("field,expected", sorted(FLAG_DEFAULTS.items()))
    def test_flag_default_matches_the_documented_one(self, field: str, expected: bool) -> None:
        assert Settings.model_fields[field].default is expected, (
            f"docs/CONFIGURATION.md documents {field.upper()} as " f"{str(expected).lower()}"
        )

    @pytest.mark.parametrize("field,expected", sorted(HARNESS_DEFAULTS.items()))
    def test_harness_ceiling_matches_the_documented_one(self, field: str, expected: int) -> None:
        assert (
            Settings.model_fields[field].default == expected
        ), f"docs/CONFIGURATION.md documents {field.upper()} as {expected}"

    def test_every_documented_flag_appears_in_both_surfaces(self) -> None:
        env_text = _ENV_EXAMPLE.read_text(encoding="utf-8")
        doc_text = _CONFIG_DOC.read_text(encoding="utf-8")
        for field in list(self.FLAG_DEFAULTS) + list(self.HARNESS_DEFAULTS):
            name = field.upper()
            assert _mentions(env_text, name), f"{name} missing from .env.example"
            assert _mentions(doc_text, name), f"{name} missing from CONFIGURATION.md"


class TestTheDocumentedDefaultsThatWereWrong:
    """`DOC-014`: `docs/CONFIGURATION.md` documented
    `MAX_QUESTION_LENGTH` as `500` while the code and `.env.example` both
    said `5000` -- a 10x wrong boundary for anyone testing it."""

    def test_max_question_length_default_is_consistent_everywhere(self) -> None:
        assert Settings.model_fields["max_question_length"].default == 5000
        doc_text = _CONFIG_DOC.read_text(encoding="utf-8")
        assert "| `MAX_QUESTION_LENGTH` | `5000` |" in doc_text
        env_text = _ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "MAX_QUESTION_LENGTH=5000" in env_text
