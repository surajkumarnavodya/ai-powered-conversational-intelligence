"""Per-task model pins: precedence, allowlist enforcement, strategy ordering.

The security property under test is that a pin can never reach a model
outside `OLLAMA_ALLOWED_MODELS`, and that it fails at startup rather than
at the call site. The behavioural property is that the defaults change
nothing: a deployment that sets no pin and no strategy routes exactly as
it did before these settings existed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import ConfigurationError, Settings
from model_routing.tasks import (
    MODEL_ROUTING_STRATEGIES,
    STRATEGY_DESCRIPTIONS,
    ModelTask,
    describe_configuration,
    resolve_task_model,
    strategy_sort_key,
    validate_task_model_pins,
)

_ALLOWED = ("llama3.1:8b", "sqlcoder:7b", "llama3.2:3b", "qwen2.5:14b")


def _settings(**overrides) -> Settings:
    base = {"ollama_model": "llama3.1:8b", "ollama_allowed_models": _ALLOWED}
    base.update(overrides)
    return Settings(**base)


class TestPinPrecedence:
    """Caller pin, then task pin, then fallback, then OLLAMA_MODEL."""

    def test_no_pins_resolves_every_task_to_the_configured_default(self) -> None:
        """The default configuration must change nothing."""
        settings = _settings()
        for task in ModelTask:
            if task is ModelTask.EMBEDDING:
                continue
            assert resolve_task_model(task, settings) == "llama3.1:8b"

    def test_a_task_pin_is_used_for_that_task_only(self) -> None:
        settings = _settings(sql_model="sqlcoder:7b")
        assert resolve_task_model(ModelTask.SQL, settings) == "sqlcoder:7b"
        assert resolve_task_model(ModelTask.ROUTER, settings) == "llama3.1:8b"
        assert resolve_task_model(ModelTask.RAG_ANSWER, settings) == "llama3.1:8b"

    def test_a_caller_pin_beats_a_task_pin(self) -> None:
        """A caller's explicit choice always wins -- a per-task value is
        still only a default."""
        settings = _settings(sql_model="sqlcoder:7b")
        assert (
            resolve_task_model(ModelTask.SQL, settings, caller_pinned="qwen2.5:14b")
            == "qwen2.5:14b"
        )

    def test_the_fallback_model_covers_a_task_with_no_pin(self) -> None:
        settings = _settings(fallback_model="llama3.2:3b", sql_model="sqlcoder:7b")
        assert resolve_task_model(ModelTask.SQL, settings) == "sqlcoder:7b"
        assert resolve_task_model(ModelTask.ROUTER, settings) == "llama3.2:3b"

    def test_resolution_never_returns_an_empty_string(self) -> None:
        settings = _settings()
        for task in ModelTask:
            assert resolve_task_model(task, settings)

    def test_a_blank_pin_is_treated_as_unset(self) -> None:
        for blank in ("", "   "):
            settings = _settings(sql_model=blank)
            assert resolve_task_model(ModelTask.SQL, settings) == "llama3.1:8b"

    def test_the_vision_pin_reads_the_setting_that_already_owns_it(self) -> None:
        """One owner per fact: VISION_MODEL *is* MEDIA_VISION_MODEL."""
        settings = _settings(media_vision_model="llava:13b")
        assert resolve_task_model(ModelTask.VISION, settings) == "llava:13b"

    def test_the_embedding_task_reads_its_own_owner_and_never_falls_back(self) -> None:
        """A chat model is not an embedding model.

        Falling back from a missing embedding model to OLLAMA_MODEL would
        produce vectors from the wrong model and a corrupt index, rather
        than a clean failure.
        """
        settings = _settings(embedding_model_name="all-MiniLM-L6-v2", fallback_model="llama3.2:3b")
        assert resolve_task_model(ModelTask.EMBEDDING, settings) == "all-MiniLM-L6-v2"
        assert (
            resolve_task_model(ModelTask.EMBEDDING, settings, caller_pinned="llama3.1:8b")
            == "all-MiniLM-L6-v2"
        )


class TestAllowlistEnforcement:
    """A pin outside OLLAMA_ALLOWED_MODELS fails the app's boot."""

    @pytest.mark.parametrize(
        "field", ["router_model", "sql_model", "rag_answer_model", "fallback_model"]
    )
    def test_an_unapproved_pin_refuses_startup(self, field: str) -> None:
        with pytest.raises((ConfigurationError, ValidationError)) as excinfo:
            _settings(**{field: "smuggled-model:70b"})
        assert "OLLAMA_ALLOWED_MODELS" in str(excinfo.value)

    def test_the_error_names_every_offending_pin_at_once(self) -> None:
        """So an operator with two typos fixes both in one restart."""
        with pytest.raises((ConfigurationError, ValidationError)) as excinfo:
            _settings(sql_model="bad-one:7b", router_model="bad-two:7b")
        message = str(excinfo.value)
        assert "SQL_MODEL" in message
        assert "ROUTER_MODEL" in message

    def test_an_approved_pin_is_accepted(self) -> None:
        assert _settings(sql_model="sqlcoder:7b").sql_model == "sqlcoder:7b"

    def test_the_configured_default_is_always_implicitly_allowed(self) -> None:
        """`OLLAMA_MODEL` need not also be listed in the allowlist."""
        settings = Settings(
            ollama_model="llama3.1:8b",
            ollama_allowed_models=("sqlcoder:7b",),
            sql_model="llama3.1:8b",
        )
        assert resolve_task_model(ModelTask.SQL, settings) == "llama3.1:8b"

    def test_the_embedding_model_is_never_allowlist_checked(self) -> None:
        """It is a sentence-transformers model, not an Ollama chat model --
        checking it would reject every correct value."""
        settings = _settings(embedding_model_name="all-MiniLM-L6-v2")
        assert validate_task_model_pins(settings) == []

    def test_the_validator_is_not_vacuous(self) -> None:
        """Proving it actually reports something (CLAUDE.md 7)."""
        clean = _settings(sql_model="sqlcoder:7b")
        assert validate_task_model_pins(clean) == []
        # Bypass `Settings` validation to drive the function directly.
        dirty = clean.model_copy(update={"sql_model": "nope:1b"})
        assert validate_task_model_pins(dirty) == ["SQL_MODEL='nope:1b'"]


class TestRoutingStrategy:
    """A strategy is a preference among eligible models, never a permission."""

    def test_the_accepted_strategies_are_exactly_the_documented_four(self) -> None:
        assert set(MODEL_ROUTING_STRATEGIES) == {"capability", "cost", "latency", "quality"}
        assert set(STRATEGY_DESCRIPTIONS) == set(MODEL_ROUTING_STRATEGIES)

    def test_the_default_strategy_is_the_pre_existing_behaviour(self) -> None:
        assert _settings().model_routing_strategy == "capability"

    @pytest.mark.parametrize("strategy", MODEL_ROUTING_STRATEGIES)
    def test_every_documented_strategy_is_accepted(self, strategy: str) -> None:
        assert _settings(model_routing_strategy=strategy).model_routing_strategy == strategy

    def test_an_unknown_strategy_is_rejected_by_settings(self) -> None:
        with pytest.raises((ConfigurationError, ValidationError)):
            _settings(model_routing_strategy="cheapest")

    def _candidates(self):
        from model_routing.models import ModelTier, RoutableModel

        return (
            RoutableModel(
                model_id="small:1b",
                tier=ModelTier.SMALL,
                tier_known=True,
                evaluation_quality_score=0.5,
            ),
            RoutableModel(
                model_id="large:70b",
                tier=ModelTier.LARGE,
                tier_known=True,
                evaluation_quality_score=0.9,
            ),
        )

    def test_cost_prefers_the_cheapest_tier(self) -> None:
        ordered = sorted(self._candidates(), key=strategy_sort_key("cost"))
        assert ordered[0].model_id == "small:1b"

    def test_quality_prefers_the_highest_recorded_score(self) -> None:
        ordered = sorted(self._candidates(), key=strategy_sort_key("quality"))
        assert ordered[0].model_id == "large:70b"

    def test_capability_matches_quality_ordering_today(self) -> None:
        """They are distinct accepted values that happen to share an
        ordering; `strategy_sort_key`'s own comment says why."""
        assert (
            sorted(self._candidates(), key=strategy_sort_key("capability"))[0].model_id
            == sorted(self._candidates(), key=strategy_sort_key("quality"))[0].model_id
        )

    def test_latency_prefers_the_fastest_measured_model(self) -> None:
        ordered = sorted(
            self._candidates(),
            key=strategy_sort_key("latency", {"large:70b": 100.0, "small:1b": 5000.0}),
        )
        assert ordered[0].model_id == "large:70b"

    def test_an_unmeasured_model_sorts_last_under_latency(self) -> None:
        """Unknown is not fast. Treating absence of evidence as evidence
        of speed would be inference presented as fact (CLAUDE.md 4.4)."""
        ordered = sorted(self._candidates(), key=strategy_sort_key("latency", {"small:1b": 10.0}))
        assert ordered[0].model_id == "small:1b"
        assert ordered[-1].model_id == "large:70b"

    def test_an_unrecognized_strategy_falls_back_to_the_previous_behaviour(self) -> None:
        assert (
            sorted(self._candidates(), key=strategy_sort_key("nonsense"))[0].model_id
            == sorted(self._candidates(), key=strategy_sort_key("capability"))[0].model_id
        )


class TestConfigurationDisclosure:
    """The startup summary is log-safe."""

    def test_the_summary_names_models_and_the_strategy_only(self) -> None:
        text = describe_configuration(_settings(sql_model="sqlcoder:7b"))
        assert "sqlcoder:7b" in text
        assert "capability" in text

    def test_the_summary_contains_no_host_or_credential(self) -> None:
        settings = _settings()
        text = describe_configuration(settings)
        assert settings.ollama_host not in text
        assert "11434" not in text
        for forbidden in ("password", "api_key", "secret", "token"):
            assert forbidden not in text.lower()
