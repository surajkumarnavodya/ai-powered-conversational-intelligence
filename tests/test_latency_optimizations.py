"""Latency optimizations: fewer model calls, smaller prompts.

Measured on the reference machine (see `docs/PERFORMANCE.md`): ~96-99%
of a question's wall-clock is LLM inference, because the configured 7B
model loads only 41% into a 4GB GPU and runs the rest on CPU -- ~185
tok/s prompt processing, ~9.6 tok/s generation. Database execution
measured 0.05s.

Latency is therefore `prompt_tokens / 185 + generated_tokens / 9.6` per
call, and the only levers that move it are **how many model calls a
question makes** and **how big each prompt is**. These tests pin both.

Fully mocked -- no Ollama, no database. They assert call *counts* and
prompt *composition*, which is what the measurements showed matters,
rather than wall-clock, which is machine-dependent and would be flaky.
"""

from __future__ import annotations

import pytest

from agent.complexity import detect_complexity_signals
from agent.nodes import classify_analytical_intent_node
from config.settings import Settings


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


class TestIntentClassificationIsSkippedWhenItCannotMatter:
    """The single largest measured saving: one whole model call.

    `classify_analytical_intent` measured 12-25s per question. Its only
    consumer is `plan_query_node`'s gate, and that node's own docstring
    states that when `analytical_intent` is None the gate "reduces to
    exactly `agent.complexity`'s signals alone, byte-identical to this
    node's pre-Prompt-11 behavior".

    So for a question with no complexity signal, the classification
    cannot change any downstream decision -- paying for it buys nothing.
    These tests pin that it is skipped there and still runs where it
    earns its cost.
    """

    def test_a_simple_question_makes_no_model_call(self, monkeypatch) -> None:
        import agent.nodes as nodes

        calls: list[str] = []
        monkeypatch.setattr(nodes, "get_settings", lambda: _settings())
        monkeypatch.setattr(
            nodes,
            "_routed_llm_call",
            lambda *a, **k: calls.append("called"),
        )

        result = classify_analytical_intent_node(
            {"question": "Total sales by product", "complexity_signals": []}
        )

        assert calls == [], "a question with no complexity signal must not classify"
        assert result["analytical_intent"] is None
        assert result["status"] == "generating"

    @pytest.mark.parametrize(
        "question",
        [
            "Show top 5 products per region",
            "year over year growth in sales",
        ],
    )
    def test_a_complex_question_still_classifies(self, monkeypatch, question: str) -> None:
        """Where the classification can change the plan, it still runs."""
        import agent.nodes as nodes

        calls: list[str] = []
        monkeypatch.setattr(nodes, "get_settings", lambda: _settings())

        def _fake(*_args, **_kwargs):
            calls.append("called")
            return None

        monkeypatch.setattr(nodes, "_routed_llm_call", _fake)

        assert detect_complexity_signals(question), "test question must be complex"
        classify_analytical_intent_node(
            {"question": question, "complexity_signals": detect_complexity_signals(question)}
        )
        assert calls == ["called"]

    def test_the_skip_can_be_turned_off(self, monkeypatch) -> None:
        """An operator who wants the previous always-classify behaviour
        gets it back, byte for byte."""
        import agent.nodes as nodes

        calls: list[str] = []
        monkeypatch.setattr(
            nodes,
            "get_settings",
            lambda: _settings(skip_intent_classification_for_simple_questions=False),
        )

        def _fake(*_args, **_kwargs):
            calls.append("called")
            return None

        monkeypatch.setattr(nodes, "_routed_llm_call", _fake)

        classify_analytical_intent_node(
            {"question": "Total sales by product", "complexity_signals": []}
        )
        assert calls == ["called"]

    def test_the_feature_flag_still_wins(self, monkeypatch) -> None:
        """`ENABLE_INTENT_CLASSIFICATION=false` short-circuits first."""
        import agent.nodes as nodes

        calls: list[str] = []
        monkeypatch.setattr(
            nodes, "get_settings", lambda: _settings(enable_intent_classification=False)
        )
        monkeypatch.setattr(nodes, "_routed_llm_call", lambda *a, **k: calls.append("x"))

        result = classify_analytical_intent_node(
            {
                "question": "Show top 5 products per region",
                "complexity_signals": ["top_n_per_group"],
            }
        )
        assert calls == []
        assert result["analytical_intent"] is None

    def test_signals_are_derived_when_absent_from_state(self, monkeypatch) -> None:
        """A state built by a test or a direct node call still gates
        correctly rather than always classifying."""
        import agent.nodes as nodes

        calls: list[str] = []
        monkeypatch.setattr(nodes, "get_settings", lambda: _settings())
        monkeypatch.setattr(nodes, "_routed_llm_call", lambda *a, **k: calls.append("x"))

        result = classify_analytical_intent_node({"question": "Total sales by product"})
        assert calls == []
        assert result["analytical_intent"] is None


class TestSchemaPromptCeiling:
    """Schema measured 2,968 of 6,632 prompt tokens -- the largest single
    section, and larger than `SCHEMA_TOP_K` implies because relationship
    expansion adds tables. At ~185 tok/s each surplus table costs ~2s."""

    def test_the_ceiling_is_off_by_default(self) -> None:
        """Dropping a table the question needs turns a slow correct
        answer into a fast wrong one, so an operator opts in."""
        assert _settings().max_schema_tables_in_prompt == 0

    def test_a_configured_ceiling_is_accepted(self) -> None:
        assert _settings(max_schema_tables_in_prompt=4).max_schema_tables_in_prompt == 4

    @pytest.mark.parametrize("bad", [-1, 51, "abc"])
    def test_an_unsafe_ceiling_is_rejected_at_startup(self, bad) -> None:
        from pydantic import ValidationError

        from config.settings import ConfigurationError

        with pytest.raises((ConfigurationError, ValidationError)):
            _settings(max_schema_tables_in_prompt=bad)


class TestLatencyBudgetSettings:
    """The budgets are validated, so a typo cannot disable a guard."""

    def test_the_documented_default_target(self) -> None:
        assert _settings().target_first_response_seconds == 5.0

    @pytest.mark.parametrize("bad", [0, -1, 1000, "soon"])
    def test_an_invalid_target_is_rejected(self, bad) -> None:
        from pydantic import ValidationError

        from config.settings import ConfigurationError

        with pytest.raises((ConfigurationError, ValidationError)):
            _settings(target_first_response_seconds=bad)

    def test_the_skip_flag_defaults_on(self) -> None:
        assert _settings().skip_intent_classification_for_simple_questions is True


class TestNoQualityOrSafetyRegression:
    """Latency work must not weaken a control (brief 12)."""

    def test_skipping_the_intent_call_does_not_touch_authorization(self) -> None:
        """The node never read caller_roles; skipping it reads even less.

        Asserted structurally: beyond the timing `_timed_node` adds to
        every node, the update carries only the intent and the status --
        so it cannot widen a permission, a retry budget, or anything
        `validate_sql_node` reads.
        """
        result = classify_analytical_intent_node(
            {"question": "Total sales by product", "complexity_signals": []}
        )
        assert set(result) - {"stage_timings"} == {"analytical_intent", "status"}

    def test_the_sql_validator_is_untouched_by_latency_settings(self) -> None:
        """`validate_sql` takes no Settings at all, so no latency knob can
        reach it."""
        from agent.sql_validator import validate_sql

        for statement in ("DROP TABLE t", "DELETE FROM t", "UPDATE t SET a=1"):
            assert not validate_sql(statement).is_valid
