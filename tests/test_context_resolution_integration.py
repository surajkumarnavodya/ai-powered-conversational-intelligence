"""Integration tests for context resolution inside the real request path.

`tests/test_context_resolution.py` tests the resolver as a pure function.
This file tests the wiring: that the node writes the state the graph
expects, that the *resolved* question is what reaches retrieval and the
generation prompt, that the resolution is made once per request rather
than once per graph, that the rollback switch restores the old behaviour,
and that widening a question cannot widen what it is allowed to ask.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.context_resolution import ContextRelation
from agent.nodes import (
    classify_followup_node,
    generate_sql_node,
    retrieval_question,
    retrieve_schema_node,
)
from agent.state import AgentState, ConversationExchange
from config.settings import Settings
from security.secrets import SecretStr


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ollama_host="http://localhost:11434",
        ollama_model="qwen2.5-coder:7b",
        db_type="postgresql",
        db_host="db.example.com",
        db_name="mydb",
        db_user="reader",
        db_password=SecretStr("secret"),
        chroma_persist_dir=Path("/tmp/chroma"),
        chroma_collection_name="schema_ddl",
        embedding_model_name="all-MiniLM-L6-v2",
        schema_top_k=4,
        max_question_length=500,
    )


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch, settings):
    monkeypatch.setattr("agent.nodes.get_settings", lambda: settings)
    return settings


def with_settings(base: Settings, **overrides: Any) -> Settings:
    return Settings(**{**base.__dict__, **overrides})


def turn(
    question: str,
    *,
    answer: str | None = None,
    sql: str | None = None,
    tables: list[str] | None = None,
) -> ConversationExchange:
    exchange: ConversationExchange = {
        "question": question,
        "sql": sql,
        "tables": tables or [],
        "status": "succeeded",
    }
    if answer is not None:
        exchange["answer"] = answer
    return exchange


PRIOR = turn(
    "Show total sales by year",
    sql="SELECT year, SUM(amount) FROM orders GROUP BY year",
    tables=["orders"],
    answer="Sales grew from 2011 to 2013.",
)


class TestNodeState:
    def test_a_followup_records_the_resolution_and_the_legacy_fields(self):
        result = classify_followup_node(
            {
                "question": "Now break that down by month",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )

        assert result["status"] == "retrieving_schema"
        # The pre-existing contract still holds.
        assert result["followup_classification"] == "followup"
        assert result["followup_resolved_against"] == PRIOR
        # And the richer record is there alongside it.
        record = result["context_resolution"]
        assert record["relation"] == ContextRelation.FOLLOWUP.value
        assert record["antecedent_question"] == PRIOR["question"]
        assert record["source"] == "deterministic"
        assert result["resolved_question"]

    def test_a_new_topic_carries_no_resolved_question_rewrite(self):
        result = classify_followup_node(
            {
                "question": "How many distinct products do we sell?",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )
        assert result["followup_classification"] == "standalone"
        assert result["context_resolution"]["rewritten"] is False
        assert result["resolved_question"] == "How many distinct products do we sell?"

    def test_a_refinement_maps_onto_the_legacy_followup_value(self):
        result = classify_followup_node(
            {
                "question": "Exclude cancelled orders.",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )
        assert result["context_resolution"]["relation"] == "refinement"
        # A client switching on the frozen three-value field is unaffected.
        assert result["followup_classification"] == "followup"

    def test_a_correction_reports_what_it_replaced(self):
        history = [turn("Show orders under $500")]
        result = classify_followup_node(
            {
                "question": "I meant $300.",
                "conversation_history": history,
                "retry_count": 0,
            }
        )
        record = result["context_resolution"]
        assert record["relation"] == "correction"
        assert any("300" in text for text in record["carried_constraints"])
        assert any("500" in text for text in record["replaced_constraints"])

    def test_an_ambiguous_reference_stops_and_asks(self):
        history = [
            turn("Tell me about the Google Pixel 10a"),
            turn("Tell me about the Samsung Galaxy A56"),
        ]
        result = classify_followup_node(
            {"question": "What is its price?", "conversation_history": history, "retry_count": 0}
        )

        assert result["status"] == "needs_clarification"
        assert result["followup_classification"] == "ambiguous"
        assert result["resolved_question"] is None
        assert "Pixel 10a" in result["clarification_message"]

    def test_the_question_as_typed_is_never_overwritten(self):
        result = classify_followup_node(
            {
                "question": "Now break that down by month",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )
        # The node returns no `question` key at all, so the transcript's
        # copy cannot be replaced by a rewrite.
        assert "question" not in result


class TestResolutionReuse:
    def test_a_resolution_already_in_state_is_not_recomputed(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            "agent.nodes.resolve_context",
            lambda *a, **k: calls.append("resolved"),
        )
        existing = {
            "relation": "followup",
            "resolved_question": "resolved form",
            "rewritten": True,
            "antecedent_question": PRIOR["question"],
            "confidence": 0.9,
            "source": "deterministic",
            "carried_constraints": [],
            "replaced_constraints": [],
            "candidate_topics": [],
            "signals": [],
        }
        result = classify_followup_node(
            {
                "question": "Now break that down by month",
                "conversation_history": [PRIOR],
                "retry_count": 0,
                "context_resolution": existing,
                "resolved_question": "resolved form",
                "followup_classification": "followup",
                "followup_resolved_against": PRIOR,
            }
        )

        assert calls == []
        assert result["context_resolution"] is existing
        assert result["resolved_question"] == "resolved form"
        assert result["status"] == "retrieving_schema"

    def test_the_legacy_fields_survive_the_reuse_path(self):
        """Regression: they were lost on the whole multi-source route.

        `run_agent` builds a *fresh* `AgentState`, seeding only
        `resolved_question`, `context_resolution` and
        `followup_resolved_against`. The reuse branch originally read
        `followup_classification` back out of state, which is `None` in
        that fresh state -- so every orchestrated question silently lost
        the follow-up reference block in `generate_sql_node`, the
        prior-question fold in `retrieve_schema_node`, and reported
        `followup_classification: null`. Reproduced exactly, then fixed by
        deriving the value from the record instead.
        """
        existing = {
            "relation": "refinement",
            "resolved_question": "resolved form",
            "rewritten": True,
            "antecedent_question": PRIOR["question"],
            "confidence": 0.9,
            "source": "deterministic",
            "carried_constraints": [],
            "replaced_constraints": [],
            "candidate_topics": [],
            "signals": [],
        }
        result = classify_followup_node(
            {
                "question": "shorten it",
                "conversation_history": [PRIOR],
                "retry_count": 0,
                # Exactly what `run_agent` seeds -- and nothing else.
                "resolved_question": "resolved form",
                "context_resolution": existing,
                "followup_resolved_against": PRIOR,
            }
        )

        # Derived from the record's relation, not read back from a state
        # that never carried it.
        assert result["followup_classification"] == "followup"
        assert result["followup_resolved_against"] == PRIOR

    def test_the_sql_graph_can_rebuild_the_prompt_block_after_reuse(self, monkeypatch):
        # The reason the exchange is threaded rather than looked up by its
        # question text: the block `generate_sql_node` shows the model
        # needs the prior SQL and tables, which the record does not carry.
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "agent.nodes.generate_sql_from_llm",
            lambda **kw: (captured.update(kw), "SELECT 1")[1],
        )
        generate_sql_node(
            {
                "question": "Now by month",
                "resolved_question": "resolved form",
                "followup_resolved_against": PRIOR,
                "schema_context_text": "CREATE TABLE orders (x int)",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )
        assert captured["followup_context"] == PRIOR


class TestRollbackSwitch:
    def test_disabling_resolution_restores_the_previous_behaviour(self, monkeypatch, settings):
        monkeypatch.setattr(
            "agent.nodes.get_settings",
            lambda: with_settings(settings, enable_context_resolution=False),
        )
        result = classify_followup_node(
            {
                "question": "Now break that down by month",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )

        assert result["followup_classification"] == "followup"
        assert result["followup_resolved_against"] == PRIOR
        # Nothing is rewritten, so every downstream reader falls back to
        # the raw question exactly as it did before this feature.
        assert result["resolved_question"] is None
        assert result["context_resolution"] is None

    def test_disabling_resolution_keeps_the_old_clarification_path(self, monkeypatch, settings):
        monkeypatch.setattr(
            "agent.nodes.get_settings",
            lambda: with_settings(settings, enable_context_resolution=False),
        )
        result = classify_followup_node(
            {"question": "why", "conversation_history": [PRIOR], "retry_count": 0}
        )
        assert result["status"] == "needs_clarification"
        assert result["followup_classification"] == "ambiguous"

    def test_disabling_the_llm_tier_makes_no_model_call(self, monkeypatch, settings):
        monkeypatch.setattr(
            "agent.nodes.get_settings",
            lambda: with_settings(settings, enable_context_resolution_llm=False),
        )
        from agent.nodes import _context_resolution_llm

        assert _context_resolution_llm() is None

    def test_the_llm_tier_is_off_unless_an_operator_turns_it_on(self, settings):
        # Not a preference: defaulting it on put an 11s network call on the
        # latency path and inside this very test suite. See
        # `Settings.enable_context_resolution_llm`'s comment for the
        # measurement.
        assert settings.enable_context_resolution_llm is False

    def test_no_resolution_path_calls_ollama_by_default(self, monkeypatch):
        # Non-vacuity guard for the above: if the default ever flips back,
        # this fails loudly rather than silently making the suite slow and
        # network-dependent. A two-candidate reference is the lowest-
        # confidence case there is, so if anything escalates, this does.
        def _explode(*args, **kwargs):
            raise AssertionError("the resolver reached Ollama with the default settings")

        monkeypatch.setattr("rag.llm.call_ollama", _explode)
        result = classify_followup_node(
            {
                "question": "What is its price?",
                "conversation_history": [
                    turn("Tell me about the Google Pixel 10a"),
                    turn("Tell me about the Samsung Galaxy A56"),
                ],
                "retry_count": 0,
            }
        )
        assert result["status"] == "needs_clarification"

    def test_an_enabled_llm_tier_builds_a_callable(self, monkeypatch, settings):
        monkeypatch.setattr(
            "agent.nodes.get_settings",
            lambda: with_settings(settings, enable_context_resolution_llm=True),
        )
        from agent.nodes import _context_resolution_llm

        assert callable(_context_resolution_llm())


class TestRetrievalQuestion:
    def test_falls_back_to_the_raw_question_when_nothing_was_resolved(self):
        assert retrieval_question({"question": "Show total sales"}) == "Show total sales"

    def test_prefers_the_resolved_form_when_there_is_one(self):
        state: AgentState = {
            "question": "Price?",
            "resolved_question": "What is the price of the Google Pixel 10a?",
        }
        assert retrieval_question(state) == "What is the price of the Google Pixel 10a?"

    def test_an_empty_resolved_question_is_not_preferred_over_the_raw_one(self):
        # Defensive: an empty string must not silently become the query.
        assert retrieval_question({"question": "Show sales", "resolved_question": ""}) == (
            "Show sales"
        )


class TestResolvedQuestionReachesRetrieval:
    def test_schema_retrieval_embeds_the_resolved_form(self, monkeypatch):
        seen: list[str] = []

        def _retrieve(question, db_name, top_k):
            seen.append(question)
            return [
                {
                    "table_name": "orders",
                    "ddl": "CREATE TABLE orders (x int)",
                    "similarity_score": 0.9,
                }
            ]

        monkeypatch.setattr("agent.nodes.retrieve_relevant_schema", _retrieve)
        retrieve_schema_node(
            {
                "question": "Price?",
                "resolved_question": "What is the price of the Google Pixel 10a?",
                "selected_database": "mydb",
            }
        )

        assert seen
        assert "Google Pixel 10a" in seen[0]

    def test_schema_retrieval_still_folds_in_the_prior_question(self, monkeypatch):
        seen: list[str] = []

        def _retrieve(question, db_name, top_k):
            seen.append(question)
            return []

        monkeypatch.setattr("agent.nodes.retrieve_relevant_schema", _retrieve)
        retrieve_schema_node(
            {
                "question": "Now by month",
                "resolved_question": "Now by month (regarding: Show total sales by year)",
                "followup_resolved_against": PRIOR,
                "selected_database": "mydb",
            }
        )
        assert "Show total sales by year" in seen[0]

    def test_generation_is_prompted_with_the_resolved_form(self, monkeypatch):
        captured: dict[str, Any] = {}

        def _generate(**kwargs):
            captured.update(kwargs)
            return "SELECT 1"

        monkeypatch.setattr("agent.nodes.generate_sql_from_llm", lambda **kw: _generate(**kw))
        generate_sql_node(
            {
                "question": "Price?",
                "resolved_question": "What is the price of the Google Pixel 10a?",
                "schema_context_text": "CREATE TABLE products (price int)",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )

        assert captured["question"] == "What is the price of the Google Pixel 10a?"

    def test_generation_receives_a_bounded_memory_block(self, monkeypatch):
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "agent.nodes.generate_sql_from_llm",
            lambda **kw: (captured.update(kw), "SELECT 1")[1],
        )
        generate_sql_node(
            {
                "question": "Now by month",
                "resolved_question": "Now by month (regarding: Show total sales by year)",
                "schema_context_text": "CREATE TABLE orders (x int)",
                "conversation_history": [turn("Show sales under $500", tables=["orders"])],
                "retry_count": 0,
            }
        )

        memory = captured["conversation_memory"]
        assert memory
        # Framed as data, never as instructions (CLAUDE.md 5.5).
        assert "DATA" in memory
        assert "under $500" in memory

    def test_a_retry_is_not_given_the_memory_block_again(self, monkeypatch):
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "agent.nodes.generate_sql_from_llm",
            lambda **kw: (captured.update(kw), "SELECT 1")[1],
        )
        generate_sql_node(
            {
                "question": "Now by month",
                "schema_context_text": "CREATE TABLE orders (x int)",
                "conversation_history": [turn("Show sales under $500")],
                "retry_count": 1,
                "error_history": ["syntax error"],
            }
        )
        # A retry's own error feedback is the relevant reference, and the
        # follow-up block follows the same first-attempt-only rule.
        assert captured["conversation_memory"] is None
        assert captured["followup_context"] is None


class TestGovernanceOnTheResolvedQuestion:
    """A rewrite widens the ask, so it is governed on its own terms.

    The direction matters: the rewrite itself fails *open* (an
    unproducible rewrite leaves today's behaviour), while this check fails
    *closed*. Without it, a question whose resolved meaning is prohibited
    would be answered because its typed form looked innocuous.
    """

    def test_a_resolved_question_that_becomes_prohibited_is_refused(self, monkeypatch):
        class _Verdict:
            is_blocking = True
            domain = "human_resources"
            category = "individual_compensation"
            message = "I can't help with decisions about an individual."

            class decision:  # noqa: N801 - mirrors the real enum's `.value`
                value = "refuse"

        seen: list[str] = []

        def _evaluate(question, roles):
            seen.append(question)
            # Innocuous as typed; prohibited once resolved.
            return _Verdict() if "Jane Smith" in question else _Blank()

        class _Blank:
            is_blocking = False
            domain = "human_resources"
            category = None
            message = ""

        monkeypatch.setattr("agent.nodes.evaluate_request", _evaluate)
        result = classify_followup_node(
            {
                "question": "And their salary?",
                "conversation_history": [turn("Show the review history for Jane Smith")],
                "retry_count": 0,
                "caller_roles": ("analyst",),
            }
        )

        assert result["status"] == "rejected"
        assert result["rejection_reason"] == "policy_refused"
        assert result["resolved_question"] is None
        # It was the resolved text that was judged, not the typed text.
        assert any("Jane Smith" in question for question in seen)

    def test_a_question_that_was_not_rewritten_is_not_re_governed(self, monkeypatch):
        calls: list[str] = []

        class _Blank:
            is_blocking = False
            domain = "general"
            category = None
            message = ""

        monkeypatch.setattr(
            "agent.nodes.evaluate_request",
            lambda question, roles: (calls.append(question), _Blank())[1],
        )
        classify_followup_node(
            {
                "question": "How many distinct products do we sell?",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )
        # Nothing was widened, so there is nothing new to judge -- and no
        # second policy evaluation to pay for on the common path.
        assert calls == []

    def test_governance_is_skipped_when_the_feature_flag_is_off(self, monkeypatch, settings):
        calls: list[str] = []
        monkeypatch.setattr(
            "agent.nodes.get_settings",
            lambda: with_settings(settings, enable_request_governance=False),
        )
        monkeypatch.setattr(
            "agent.nodes.evaluate_request",
            lambda question, roles: calls.append(question),
        )
        result = classify_followup_node(
            {
                "question": "Now break that down by month",
                "conversation_history": [PRIOR],
                "retry_count": 0,
            }
        )
        assert calls == []
        assert result["status"] == "retrieving_schema"


class TestGraphShape:
    def test_the_sql_graph_still_has_exactly_nineteen_nodes(self):
        from agent.graph import build_graph

        graph = build_graph().get_graph()
        real = [n for n in graph.nodes if n not in ("__start__", "__end__")]
        assert len(real) == 19

    def test_resolution_is_the_orchestrator_entry_point(self):
        from agent.orchestrator.graph import build_orchestrator_graph

        graph = build_orchestrator_graph().get_graph()
        assert "resolve_context" in graph.nodes
        # The router must not be reachable before the question has been
        # resolved, or it routes on the raw text.
        starts = [edge.target for edge in graph.edges if edge.source == "__start__"]
        assert starts == ["resolve_context"]

    def test_an_unresolvable_reference_never_reaches_the_router(self):
        from agent.orchestrator.nodes import route_after_context

        assert route_after_context({"status": "needs_clarification"}) == "needs_clarification"
        assert route_after_context({"status": "rejected"}) == "rejected"
        assert route_after_context({"status": "retrieving_schema"}) == "router"
