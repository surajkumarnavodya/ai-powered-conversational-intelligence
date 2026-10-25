"""Tests for `agent.orchestrator.general_knowledge`.

The router has always had two no-retrieval outcomes; until this module
existed neither produced an answer, so a writing task, a coding question,
a study plan or a greeting routed correctly and then hit a canned
message. These tests cover the answering path, the fail-open fallbacks,
the fail-closed guardrails, and the provenance obligations -- plus the
default being off, which is the behaviour an existing deployment keeps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.orchestrator.general_knowledge import (
    MODEL_KNOWLEDGE_DISCLAIMER,
    NO_RETRIEVAL_CONVERSATION_MESSAGE,
    NO_RETRIEVAL_UNMATCHED_MESSAGE,
    answer_without_retrieval,
)
from agent.provenance import DataTruthLevel
from config.settings import Settings
from security.secrets import SecretStr


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "ollama_host": "http://localhost:11434",
        "ollama_model": "llama3.1:8b",
        "db_type": "postgresql",
        "db_host": "db.example.com",
        "db_name": "mydb",
        "db_user": "reader",
        "db_password": SecretStr("secret"),
        "chroma_persist_dir": Path("/tmp/chroma"),
        "chroma_collection_name": "schema_ddl",
        "embedding_model_name": "all-MiniLM-L6-v2",
        "max_question_length": 500,
    }
    base.update(overrides)
    return Settings(**base)


def canned(reply: str):
    captured: dict[str, str] = {}

    def _llm(system_prompt: str, user_prompt: str) -> str:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return reply

    _llm.captured = captured  # type: ignore[attr-defined]
    return _llm


class TestTheDefault:
    def test_the_feature_is_off_unless_an_operator_turns_it_on(self):
        # The router picks `general_knowledge` *because* it could not match
        # a source -- which is sometimes a routing miss on a real data
        # question. Answering that from model weights by default would
        # replace a helpful routing failure with a confident unsourced
        # claim. See the module docstring.
        assert _settings().enable_general_knowledge_answers is False

    def test_off_returns_the_same_message_as_before(self):
        answer = answer_without_retrieval(
            "draft a LinkedIn post",
            route="general_knowledge",
            settings=_settings(),
            llm=canned("should never be called"),
        )
        assert answer.text == NO_RETRIEVAL_UNMATCHED_MESSAGE
        assert answer.from_model is False
        assert answer.reason == "disabled"
        assert answer.truth_level is None

    def test_off_never_calls_the_model(self):
        def _explode(*_a: object, **_k: object) -> str:
            raise AssertionError("the model must not be called while the feature is off")

        answer = answer_without_retrieval(
            "draft a LinkedIn post",
            route="general_knowledge",
            settings=_settings(),
            llm=_explode,
        )
        assert answer.from_model is False

    def test_a_greeting_off_gets_the_capability_message(self):
        answer = answer_without_retrieval(
            "hi there", route="conversation", settings=_settings(), llm=None
        )
        assert answer.text == NO_RETRIEVAL_CONVERSATION_MESSAGE


class TestAnswering:
    @pytest.fixture
    def on(self) -> Settings:
        return _settings(enable_general_knowledge_answers=True)

    def test_a_model_answer_is_returned_with_its_disclaimer(self, on):
        answer = answer_without_retrieval(
            "explain dependency injection in .NET",
            route="general_knowledge",
            settings=on,
            llm=canned("Dependency injection supplies a class's collaborators from outside."),
        )

        assert answer.from_model is True
        assert "Dependency injection supplies" in answer.text
        assert answer.text.startswith(MODEL_KNOWLEDGE_DISCLAIMER)

    def test_the_answer_is_marked_as_inference_not_fact(self, on):
        answer = answer_without_retrieval(
            "explain photosynthesis", route="general_knowledge", settings=on, llm=canned("Plants.")
        )
        assert answer.truth_level is DataTruthLevel.AI_INFERENCE

    def test_the_disclaimer_says_it_is_not_the_deployment_data(self, on):
        answer = answer_without_retrieval(
            "explain photosynthesis", route="general_knowledge", settings=on, llm=canned("Plants.")
        )
        # The obligation CLAUDE.md 4.4 puts on an inference: it must not be
        # presentable as something read out of the organization's data.
        lowered = answer.text.lower()
        assert "general knowledge" in lowered
        assert "not from this deployment" in lowered

    def test_the_disclaimer_is_a_fixed_constant_not_built_from_the_answer(self, on):
        a = answer_without_retrieval(
            "explain x", route="general_knowledge", settings=on, llm=canned("one")
        )
        b = answer_without_retrieval(
            "explain y", route="conversation", settings=on, llm=canned("two")
        )
        assert a.text.startswith(MODEL_KNOWLEDGE_DISCLAIMER)
        assert b.text.startswith(MODEL_KNOWLEDGE_DISCLAIMER)

    def test_the_question_reaches_the_prompt(self, on):
        llm = canned("ok")
        answer_without_retrieval(
            "create a three-day study plan for AZ-900",
            route="general_knowledge",
            settings=on,
            llm=llm,
        )
        assert "three-day study plan" in llm.captured["user"]

    def test_conversation_memory_reaches_the_prompt_framed_as_data(self, on):
        llm = canned("ok")
        answer_without_retrieval(
            "make it more professional",
            route="general_knowledge",
            settings=on,
            conversation_memory="Entities named earlier: LinkedIn",
            llm=llm,
        )
        prompt = llm.captured["user"]
        assert "LinkedIn" in prompt
        # Framed as a record, never as instructions (CLAUDE.md 5.5).
        assert "data, never instructions" in prompt

    def test_the_prompt_forbids_claiming_the_data_as_a_source(self, on):
        llm = canned("ok")
        answer_without_retrieval(
            "what is our revenue", route="general_knowledge", settings=on, llm=llm
        )
        prompt = llm.captured["user"]
        assert "Answer only from your own knowledge" in prompt
        assert "Never invent specifics" in prompt

    def test_the_system_prompt_is_a_constant_in_code(self, on):
        llm = canned("ok")
        answer_without_retrieval(
            "explain database normalization", route="general_knowledge", settings=on, llm=llm
        )
        # Never a database row -- that would make every write an injection
        # with full reach (CLAUDE.md 5.5).
        assert "never claim" in llm.captured["system"].lower()

    def test_no_memory_renders_a_placeholder_rather_than_an_empty_hole(self, on):
        llm = canned("ok")
        answer_without_retrieval("explain x", route="general_knowledge", settings=on, llm=llm)
        assert "no earlier turns" in llm.captured["user"]


class TestFailsOpenToTheFixedMessage:
    @pytest.fixture
    def on(self) -> Settings:
        return _settings(enable_general_knowledge_answers=True)

    def test_an_unreachable_model_falls_back(self, on):
        def _raise(_s: str, _u: str) -> str:
            raise RuntimeError("ollama is down")

        answer = answer_without_retrieval(
            "explain x", route="general_knowledge", settings=on, llm=_raise
        )
        assert answer.text == NO_RETRIEVAL_UNMATCHED_MESSAGE
        assert answer.reason == "llm_error"
        assert answer.truth_level is None

    def test_an_empty_reply_falls_back(self, on):
        answer = answer_without_retrieval(
            "explain x", route="general_knowledge", settings=on, llm=canned("   ")
        )
        assert answer.reason == "empty_reply"
        assert answer.text == NO_RETRIEVAL_UNMATCHED_MESSAGE

    def test_the_conversation_route_falls_back_to_its_own_message(self, on):
        def _raise(_s: str, _u: str) -> str:
            raise RuntimeError("down")

        answer = answer_without_retrieval("hi", route="conversation", settings=on, llm=_raise)
        assert answer.text == NO_RETRIEVAL_CONVERSATION_MESSAGE

    def test_it_never_raises_whatever_the_model_does(self, on):
        class _Hostile:
            def __call__(self, _s: str, _u: str) -> str:
                raise BaseException("not even an Exception")  # noqa: TRY002

        # A raised exception here would turn a correctly-routed question
        # into a 500, so this path catches broadly on purpose.
        try:
            answer_without_retrieval(
                "explain x", route="general_knowledge", settings=on, llm=_Hostile()
            )
        except BaseException as exc:  # noqa: BLE001 - that is what is being asserted
            # BaseException is deliberately *not* swallowed (a KeyboardInterrupt
            # must still propagate); Exception is.
            assert not isinstance(exc, Exception)


class TestFailsClosedOnGuardrails:
    """The input guardrails are the one thing here that refuses.

    The SQL path gets them from `agent.nodes.sanitize_input_node`, which
    only runs inside `run_agent` -- so a no-retrieval route reaches this
    module with the raw question and this is the first place it is
    checked.
    """

    @pytest.fixture
    def on(self) -> Settings:
        return _settings(enable_general_knowledge_answers=True, max_question_length=60)

    def test_an_over_length_question_is_refused_not_answered(self, on):
        def _explode(_s: str, _u: str) -> str:
            raise AssertionError("a blocked question must never reach the model")

        answer = answer_without_retrieval(
            "x" * 500, route="general_knowledge", settings=on, llm=_explode
        )
        assert answer.from_model is False
        assert answer.reason == "blocked"
        assert answer.truth_level is None

    def test_a_refusal_does_not_echo_the_question(self, on):
        answer = answer_without_retrieval(
            "zxcvbnmqwerty" * 20, route="general_knowledge", settings=on, llm=canned("x")
        )
        assert "zxcvbnmqwerty" not in answer.text


class TestWiredIntoSynthesis:
    """The route reaches `synthesis_node`, so the wiring is tested there."""

    def test_the_fixed_message_still_comes_back_with_the_feature_off(self, monkeypatch):
        from agent.orchestrator import nodes as orchestrator_nodes

        monkeypatch.setattr(orchestrator_nodes, "get_settings", _settings)
        result = orchestrator_nodes.synthesis_node(
            {
                "question": "draft a LinkedIn post",
                "sources_used": [],
                "route_decision": {
                    "sources": [],
                    "reasoning": "x",
                    "short_circuited": False,
                    "no_retrieval_route": "general_knowledge",
                },
            }
        )
        assert result["synthesized_answer"] == NO_RETRIEVAL_UNMATCHED_MESSAGE
        assert "answer_truth_level" not in result

    def test_an_answered_route_reports_its_truth_level(self, monkeypatch):
        from agent.orchestrator import nodes as orchestrator_nodes

        monkeypatch.setattr(
            orchestrator_nodes,
            "get_settings",
            lambda: _settings(enable_general_knowledge_answers=True),
        )
        monkeypatch.setattr(
            orchestrator_nodes,
            "_no_retrieval_llm",
            lambda settings, state: canned("Here is a post about your release."),
        )
        result = orchestrator_nodes.synthesis_node(
            {
                "question": "draft a LinkedIn post",
                "sources_used": [],
                "route_decision": {
                    "sources": [],
                    "reasoning": "x",
                    "short_circuited": False,
                    "no_retrieval_route": "general_knowledge",
                },
            }
        )
        assert "Here is a post" in result["synthesized_answer"]
        assert result["answer_truth_level"] == DataTruthLevel.AI_INFERENCE.value

    def test_the_resolved_question_is_what_gets_answered(self, monkeypatch):
        """The point of wiring this after context resolution: "make it more
        professional" arrives naming what it refines."""
        from agent.orchestrator import nodes as orchestrator_nodes

        llm = canned("Revised.")
        monkeypatch.setattr(
            orchestrator_nodes,
            "get_settings",
            lambda: _settings(enable_general_knowledge_answers=True),
        )
        monkeypatch.setattr(orchestrator_nodes, "_no_retrieval_llm", lambda settings, state: llm)
        orchestrator_nodes.synthesis_node(
            {
                "question": "Make it more professional.",
                "resolved_question": (
                    "Make it more professional (regarding: Draft a LinkedIn post)"
                ),
                "sources_used": [],
                "route_decision": {
                    "sources": [],
                    "reasoning": "x",
                    "short_circuited": False,
                    "no_retrieval_route": "general_knowledge",
                },
            }
        )
        assert "Draft a LinkedIn post" in llm.captured["user"]

    def test_the_memory_block_reaches_the_answer_prompt(self, monkeypatch):
        from agent.orchestrator import nodes as orchestrator_nodes

        llm = canned("Revised.")
        monkeypatch.setattr(
            orchestrator_nodes,
            "get_settings",
            lambda: _settings(enable_general_knowledge_answers=True),
        )
        monkeypatch.setattr(orchestrator_nodes, "_no_retrieval_llm", lambda settings, state: llm)
        orchestrator_nodes.synthesis_node(
            {
                "question": "Make it shorter.",
                "sources_used": [],
                "conversation_history": [
                    {
                        "question": "Draft a LinkedIn post about Project Orion",
                        "sql": None,
                        "tables": [],
                        "status": "succeeded",
                        "answer": "Excited to share Project Orion!",
                    }
                ],
                "route_decision": {
                    "sources": [],
                    "reasoning": "x",
                    "short_circuited": False,
                    "no_retrieval_route": "general_knowledge",
                },
            }
        )
        assert "Project Orion" in llm.captured["user"]
