"""Tests for per-source query threading.

`RouterDecision.source_queries` has been parsed since the structured
router contract was written and consumed by nothing --
`docs/ROUTING.md` carried it as a known gap. These cover the validation
(it is model output reaching a retrieval call), the per-source fallback,
and the deliberate exclusion of `sql`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.orchestrator import nodes as orch
from agent.orchestrator.nodes import _validated_source_queries, source_query
from agent.orchestrator.router_contract import RouterDecision
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
    }
    base.update(overrides)
    return Settings(**base)


def decision(**queries: str) -> RouterDecision:
    return RouterDecision(
        primary="web_search",
        confidence=0.9,
        source_queries=dict(queries),
    )


class TestValidation:
    def test_a_rewrite_for_a_routed_source_is_accepted_and_translated(self):
        got = _validated_source_queries(
            decision(web_search="Google Pixel 10a price India 2026"),
            "what is the price of the Google Pixel 10a",
            ["web"],
        )
        # Keyed by the graph's internal name, not the prompt's.
        assert got == {"web": "Google Pixel 10a price India 2026"}

    def test_a_rewrite_for_a_source_that_is_not_running_is_dropped(self):
        got = _validated_source_queries(
            decision(policy_kb="sick leave entitlement eligibility"),
            "what is the sick leave policy",
            ["web"],
        )
        assert got == {}

    def test_a_rewrite_that_drops_the_question_is_discarded(self):
        # The model answered instead of rewriting -- measured behaviour on
        # small local models, and a different question rather than a
        # better query.
        got = _validated_source_queries(
            decision(web_search="It costs about 40000 rupees."),
            "what is the price of the Google Pixel 10a",
            ["web"],
        )
        assert got == {}

    def test_an_empty_rewrite_is_discarded(self):
        got = _validated_source_queries(decision(web_search="   "), "pixel 10a price", ["web"])
        assert got == {}

    def test_an_over_long_rewrite_is_capped_not_dropped(self):
        long_query = "Google Pixel 10a " + ("specification detail " * 200)
        got = _validated_source_queries(
            decision(web_search=long_query), "google pixel 10a", ["web"]
        )
        assert len(got["web"]) <= 600

    def test_whitespace_is_collapsed(self):
        got = _validated_source_queries(
            decision(web_search="Google   Pixel\n10a\tprice"), "google pixel 10a", ["web"]
        )
        assert got["web"] == "Google Pixel 10a price"

    def test_an_unknown_source_name_is_dropped(self):
        got = _validated_source_queries(decision(crm_database="anything"), "pixel 10a", ["web"])
        assert got == {}

    def test_sql_is_excluded_even_when_routed_and_valid(self):
        """Not an oversight -- see `_validated_source_queries`'s docstring.

        The SQL path's text is `resolved_question`, which
        `classify_followup_node` re-checks against the request-governance
        policy. Accepting a second, ungoverned rewrite there would mean
        the text that was governed is not the text that is used.
        """
        got = _validated_source_queries(
            decision(sql_database="Total revenue by month for 2013"),
            "revenue by month 2013",
            ["sql", "web"],
        )
        assert "sql" not in got

    def test_no_decision_yields_nothing(self):
        assert _validated_source_queries(None, "anything", ["web"]) == {}

    def test_a_decision_with_no_queries_yields_nothing(self):
        assert _validated_source_queries(decision(), "anything", ["web"]) == {}


class TestSourceQueryAccessor:
    def test_it_returns_the_rewrite_when_there_is_one(self):
        state = {
            "question": "Price?",
            "resolved_question": "What is the price of the Google Pixel 10a?",
            "route_decision": {"source_queries": {"web": "Google Pixel 10a price"}},
        }
        assert source_query(state, "web") == "Google Pixel 10a price"

    def test_it_falls_back_to_the_resolved_question(self):
        state = {
            "question": "Price?",
            "resolved_question": "What is the price of the Google Pixel 10a?",
            "route_decision": {"source_queries": {"web": "Google Pixel 10a price"}},
        }
        # A different source, with no rewrite of its own.
        assert source_query(state, "documents") == "What is the price of the Google Pixel 10a?"

    def test_it_falls_back_to_the_raw_question_with_no_resolution(self):
        assert source_query({"question": "Show total sales"}, "web") == "Show total sales"

    def test_no_route_decision_at_all_is_not_an_error(self):
        # The multi-source-router-off path never builds one.
        assert source_query({"question": "Show total sales"}, "web") == "Show total sales"


class TestSourceNodesUseIt:
    def test_web_search_runs_the_rewritten_query(self, monkeypatch):
        seen: dict[str, str] = {}
        monkeypatch.setattr(
            "search.web_search.web_search",
            lambda question, settings: (seen.update(q=question), [])[1],
        )
        orch.web_search_node(
            {
                "question": "Price?",
                "resolved_question": "What is the price of the Google Pixel 10a?",
                "route_decision": {"source_queries": {"web": "Google Pixel 10a price India"}},
                "caller_roles": ("analyst",),
            }
        )
        assert seen["q"] == "Google Pixel 10a price India"

    def test_media_search_runs_the_rewritten_query(self, monkeypatch):
        seen: dict[str, str] = {}
        # `media_search_node` imports the provider lazily (`from
        # media.search import search_media`) at call time, so the patch
        # goes on the owning module.
        monkeypatch.setattr(
            "media.search.search_media",
            lambda question, settings: (seen.update(q=question), [])[1],
        )
        orch.media_search_node(
            {
                "question": "photos?",
                "resolved_question": "Photos of the new airport terminal",
                "route_decision": {
                    "source_queries": {"media_search": "Navi Mumbai airport terminal photos"}
                },
                "caller_roles": ("analyst",),
            }
        )
        assert seen["q"] == "Navi Mumbai airport terminal photos"

    def test_the_policy_collection_looks_itself_up_by_source_name(self, monkeypatch):
        """`run_rag`'s collection is "policies"; the route's source name is
        "policy". The near-miss is why `_COLLECTION_SOURCE_NAMES` exists."""
        seen: dict[str, str] = {}

        def _run_rag(question, collection, caller_roles=()):
            seen["q"] = question
            return {"answer": "x", "citations": [], "status": "succeeded"}

        monkeypatch.setattr("rag.graph.run_rag", _run_rag)
        orch.policy_rag_node(
            {
                "question": "entitlement?",
                "resolved_question": "What is the sick leave entitlement?",
                "route_decision": {
                    "source_queries": {"policy": "sick leave entitlement eligibility"}
                },
                "caller_roles": ("analyst",),
            }
        )
        assert seen["q"] == "sick leave entitlement eligibility"

    def test_the_documents_collection_uses_its_own_rewrite(self, monkeypatch):
        seen: dict[str, str] = {}

        def _run_rag(question, collection, caller_roles=()):
            seen["q"] = question
            return {"answer": "x", "citations": [], "status": "succeeded"}

        monkeypatch.setattr("rag.graph.run_rag", _run_rag)
        orch.document_rag_node(
            {
                "question": "risks?",
                "resolved_question": "What risks does the report name?",
                "route_decision": {"source_queries": {"documents": "report risk register"}},
                "caller_roles": ("analyst",),
            }
        )
        assert seen["q"] == "report risk register"

    def test_a_source_with_no_rewrite_still_gets_the_resolved_question(self, monkeypatch):
        seen: dict[str, str] = {}

        def _run_rag(question, collection, caller_roles=()):
            seen["q"] = question
            return {"answer": "x", "citations": [], "status": "succeeded"}

        monkeypatch.setattr("rag.graph.run_rag", _run_rag)
        orch.document_rag_node(
            {
                "question": "risks?",
                "resolved_question": "What risks does the report name?",
                "route_decision": {"source_queries": {"web": "something else"}},
                "caller_roles": ("analyst",),
            }
        )
        assert seen["q"] == "What risks does the report name?"


class TestRouterPopulatesIt:
    def test_the_route_decision_carries_validated_queries(self, monkeypatch):
        settings = _settings()
        monkeypatch.setattr(orch, "get_settings", lambda: settings)
        monkeypatch.setattr(orch, "get_available_sources", lambda *a, **k: ["sql", "web"])

        def _classify(question, available, settings, **kwargs):
            return RouterDecision(
                primary="web_search",
                confidence=0.9,
                source_queries={
                    "web_search": "Google Pixel 10a price India",
                    # Dropped: sql is excluded by design.
                    "sql_database": "Total price of the Google Pixel 10a",
                },
            )

        monkeypatch.setattr(orch, "classify_sources_structured", _classify)
        result = orch.router_node(
            {
                "question": "price of the Google Pixel 10a",
                "resolved_question": "What is the price of the Google Pixel 10a?",
                "caller_roles": ("analyst",),
            }
        )
        queries = result["route_decision"]["source_queries"]
        assert queries == {"web": "Google Pixel 10a price India"}

    def test_a_short_circuited_route_carries_no_queries(self, monkeypatch):
        settings = _settings()
        monkeypatch.setattr(orch, "get_settings", lambda: settings)
        monkeypatch.setattr(orch, "get_available_sources", lambda *a, **k: ["sql"])

        def _must_not_run(*a: object, **k: object) -> object:
            raise AssertionError("classification must not run for a single source")

        monkeypatch.setattr(orch, "classify_sources_structured", _must_not_run)
        result = orch.router_node({"question": "how many orders?", "caller_roles": ("analyst",)})
        assert result["route_decision"]["source_queries"] == {}
