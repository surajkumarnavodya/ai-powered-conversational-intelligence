"""End-to-end tests: context resolution over the real HTTP surface.

What these add over `tests/test_context_resolution_integration.py` is the
parts a unit test cannot reach -- the request schema accepting the prior
answer, the response carrying the resolution, the multi-source router
being handed the *resolved* question, and the resolution being made once
per request rather than once per graph.

`run_orchestrated` is patched per test, so no LLM, database or vector
store is touched (CLAUDE.md 7).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def succeeded_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "question": "whatever",
        "status": "succeeded",
        "sql": "SELECT 1",
        "result_rows": [[1]],
        "result_columns": ["x"],
        "schema_tables": [],
        "error_history": [],
        "attempt_history": [],
        "stage_timings": [],
        "retry_count": 0,
    }
    state.update(overrides)
    return state


class TestRequestSchema:
    def test_a_prior_turn_may_carry_its_answer(self, client, monkeypatch):
        captured: dict[str, Any] = {}

        def _run(question, history, **kwargs):
            captured["history"] = history
            return succeeded_state()

        monkeypatch.setattr("api.main.run_orchestrated", _run)
        response = client.post(
            "/ask",
            json={
                "question": "Price?",
                "conversation_history": [
                    {
                        "question": "Tell me about the Google Pixel 10a",
                        "answer": "It is a mid-range phone.",
                        "status": "succeeded",
                    }
                ],
            },
        )

        assert response.status_code == 200
        assert captured["history"][0]["answer"] == "It is a mid-range phone."

    def test_omitting_the_answer_is_still_accepted(self, client, monkeypatch):
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "api.main.run_orchestrated",
            lambda question, history, **kw: (
                captured.update(history=history),
                succeeded_state(),
            )[1],
        )
        response = client.post(
            "/ask",
            json={
                "question": "Price?",
                "conversation_history": [{"question": "Tell me about the Pixel 10a"}],
            },
        )

        assert response.status_code == 200
        assert captured["history"][0].get("answer") is None

    def test_an_unknown_field_is_still_rejected(self, client):
        # `extra="forbid"` is unchanged by the additive field.
        response = client.post(
            "/ask",
            json={
                "question": "Price?",
                "conversation_history": [{"question": "x", "not_a_field": 1}],
            },
        )
        assert response.status_code == 422


class TestResponseSchema:
    def test_the_resolution_is_reported_when_one_was_made(self, client, monkeypatch):
        monkeypatch.setattr(
            "api.main.run_orchestrated",
            lambda *a, **k: succeeded_state(
                followup_classification="followup",
                resolved_question="Now by month (regarding: Show total sales by year)",
                context_resolution={
                    "relation": "followup",
                    "resolved_question": ("Now by month (regarding: Show total sales by year)"),
                    "rewritten": True,
                    "antecedent_question": "Show total sales by year",
                    "confidence": 0.9,
                    "source": "deterministic",
                    "carried_constraints": ["under $500"],
                    "replaced_constraints": [],
                    "candidate_topics": [],
                    "signals": ["referring"],
                },
            ),
        )
        body = client.post("/ask", json={"question": "Now by month"}).json()

        resolution = body["context_resolution"]
        assert resolution["relation"] == "followup"
        assert resolution["rewritten"] is True
        assert resolution["antecedent_question"] == "Show total sales by year"
        assert resolution["carried_constraints"] == ["under $500"]

    def test_the_field_is_null_when_resolution_is_switched_off(self, client, monkeypatch):
        monkeypatch.setattr("api.main.run_orchestrated", lambda *a, **k: succeeded_state())
        body = client.post("/ask", json={"question": "Show total sales"}).json()
        assert body["context_resolution"] is None

    def test_the_frozen_followup_field_still_carries_its_three_values(self, client, monkeypatch):
        monkeypatch.setattr(
            "api.main.run_orchestrated",
            lambda *a, **k: succeeded_state(
                followup_classification="followup",
                context_resolution={
                    # A relation outside the legacy Literal.
                    "relation": "refinement",
                    "resolved_question": "x",
                    "rewritten": False,
                    "antecedent_question": None,
                    "confidence": 0.9,
                    "source": "deterministic",
                    "carried_constraints": [],
                    "replaced_constraints": [],
                    "candidate_topics": [],
                    "signals": [],
                },
            ),
        )
        body = client.post("/ask", json={"question": "shorten it"}).json()

        # The old field keeps its old vocabulary; the new one carries the
        # richer value. A client switching on the old field is unaffected.
        assert body["followup_classification"] == "followup"
        assert body["context_resolution"]["relation"] == "refinement"

    def test_the_transcript_question_is_never_the_rewritten_one(self, client, monkeypatch):
        monkeypatch.setattr(
            "api.main.run_orchestrated",
            lambda *a, **k: succeeded_state(
                question="Price?",
                resolved_question="What is the price of the Google Pixel 10a?",
                context_resolution={
                    "relation": "followup",
                    "resolved_question": "What is the price of the Google Pixel 10a?",
                    "rewritten": True,
                    "antecedent_question": "Tell me about the Google Pixel 10a",
                    "confidence": 0.9,
                    "source": "deterministic",
                    "carried_constraints": [],
                    "replaced_constraints": [],
                    "candidate_topics": [],
                    "signals": [],
                },
            ),
        )
        body = client.post("/ask", json={"question": "Price?"}).json()

        # The rewrite is available for inspection, and is not presented as
        # anything the user said or as an assistant message.
        assert body["context_resolution"]["resolved_question"].startswith("What is the price")
        assert "Price?" not in body["context_resolution"]["relation"]


class TestClarification:
    def test_an_ambiguous_reference_is_answered_with_a_question(self, client, monkeypatch):
        monkeypatch.setattr(
            "api.main.run_orchestrated",
            lambda *a, **k: succeeded_state(
                status="needs_clarification",
                sql=None,
                result_rows=None,
                clarification_message=(
                    'This could refer to either "Tell me about the Google Pixel 10a" '
                    'or "Tell me about the Samsung Galaxy A56". Which one did you mean?'
                ),
                followup_classification="ambiguous",
            ),
        )
        body = client.post("/ask", json={"question": "What is its price?"}).json()

        assert body["status"] == "needs_clarification"
        assert "Which one did you mean?" in body["clarification_message"]
        assert body["sql"] is None


class TestOrchestratorThreading:
    """The resolved question has to reach the router, not just the SQL graph.

    `prompts/router.md` labels its own input "(standalone form)" and has
    done since it was written; before this feature nothing produced one.
    """

    def test_the_router_classifies_on_the_resolved_question(self, monkeypatch):
        from agent.orchestrator import nodes as orch

        seen: dict[str, Any] = {}

        def _classify(question, available, settings, **kwargs):
            seen["question"] = question
            raise RuntimeError("stop here -- the question is what this test is about")

        monkeypatch.setattr(orch, "classify_sources_structured", _classify)
        monkeypatch.setattr(orch, "get_available_sources", lambda *a, **k: ["sql", "web"])

        with pytest.raises(RuntimeError):
            orch.router_node(
                {
                    "question": "Price?",
                    "resolved_question": "What is the price of the Google Pixel 10a?",
                    "caller_roles": ("analyst",),
                }
            )

        assert seen["question"] == "What is the price of the Google Pixel 10a?"

    def test_web_search_runs_on_the_resolved_question(self, monkeypatch):
        from agent.orchestrator import nodes as orch

        seen: dict[str, Any] = {}
        # `web_search_node` imports the provider lazily from
        # `search.web_search`, so the patch goes on the owning module.
        monkeypatch.setattr(
            "search.web_search.web_search",
            lambda question, settings: (seen.update(q=question), [])[1],
        )
        orch.web_search_node(
            {
                "question": "Price?",
                "resolved_question": "What is the price of the Google Pixel 10a?",
                "caller_roles": ("analyst",),
            }
        )
        assert seen["q"] == "What is the price of the Google Pixel 10a?"

    def test_the_sql_subgraph_is_handed_the_existing_resolution(self, monkeypatch):
        from agent.orchestrator import nodes as orch

        seen: dict[str, Any] = {}

        def _run_agent(question, history, *args, **kwargs):
            seen["question"] = question
            seen["resolved_question"] = kwargs.get("resolved_question")
            seen["context_resolution"] = kwargs.get("context_resolution")
            return succeeded_state()

        monkeypatch.setattr(orch, "run_agent", _run_agent)
        record = {
            "relation": "followup",
            "resolved_question": "resolved form",
            "rewritten": True,
            "antecedent_question": "prior",
            "confidence": 0.9,
            "source": "deterministic",
            "carried_constraints": [],
            "replaced_constraints": [],
            "candidate_topics": [],
            "signals": [],
        }
        orch.sql_subgraph_node(
            {
                "question": "Price?",
                "resolved_question": "resolved form",
                "context_resolution": record,
            }
        )

        # The question as typed still goes in as the question -- the
        # transcript and the input guard's normalization own that -- while
        # the resolution travels beside it so the SQL graph reuses it.
        assert seen["question"] == "Price?"
        assert seen["resolved_question"] == "resolved form"
        assert seen["context_resolution"] is record
