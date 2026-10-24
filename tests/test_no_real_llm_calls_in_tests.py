"""Proves `tests/conftest.py::_block_real_ollama_calls` is load-bearing.

CLAUDE.md §7 states that every pytest test is fully mocked -- no real
database, no Ollama. That was documented and unenforced, and the gap was
real: `tests/test_orchestrator.py::TestRunOrchestrated
::test_flag_off_with_attachments_still_runs_the_orchestrator_graph` ran
the orchestrator graph for real, so `router_node` submitted
`rag.llm.call_ollama` to a thread pool it then abandoned
(`shutdown(wait=False)`), and the abandoned worker went on to make a
genuine HTTP request to `localhost:11434`. The test passed on the keyword
fallback; the only trace was an `httpx` log line printed *after* pytest's
own summary.

A guard nothing exercises is not a guard (CLAUDE.md §7's
"prove it is not vacuous"), so these tests fail if the fixture stops
blocking.
"""

from __future__ import annotations

import threading

import pytest

from tests.conftest import RealOllamaCallInTest


class TestTheGuardFires:
    def test_a_direct_chat_call_is_refused(self):
        import ollama

        with pytest.raises(RealOllamaCallInTest):
            ollama.Client(host="http://localhost:11434").chat(
                model="llama3.1:8b", messages=[{"role": "user", "content": "hello"}]
            )

    def test_an_embedding_call_is_refused_too(self):
        # `embeddings/` and `retrieval/` reach the same server by a
        # different method, so blocking only `chat` would leave a hole.
        import ollama

        with pytest.raises(RealOllamaCallInTest):
            ollama.Client(host="http://localhost:11434").embed(
                model="all-MiniLM-L6-v2", input="hello"
            )

    def test_the_guard_reaches_a_worker_thread(self):
        """The case the leak actually took: a thread the caller abandoned.

        `classify_sources_structured` bounds how long the *question* waits
        by abandoning its worker, not by cancelling the HTTP request --
        there is no cancellation to be had. So the block has to hold
        inside that thread, which is why it is applied to
        `ollama.Client.chat` itself rather than to any one call site.
        """
        import ollama

        outcome: dict[str, object] = {}

        def _call() -> None:
            try:
                ollama.Client(host="http://localhost:11434").chat(
                    model="llama3.1:8b", messages=[{"role": "user", "content": "hi"}]
                )
            except BaseException as exc:  # noqa: BLE001 - recording it is the test
                outcome["error"] = type(exc).__name__

        worker = threading.Thread(target=_call, name="router_like_worker")
        worker.start()
        worker.join(timeout=10)

        assert outcome.get("error") == "RealOllamaCallInTest"

    def test_the_message_names_the_fix(self):
        # The failure a future session sees should say what to do, not just
        # that something was blocked.
        import ollama

        with pytest.raises(RealOllamaCallInTest) as caught:
            ollama.Client(host="http://localhost:11434").chat(model="m", messages=[])

        message = str(caught.value)
        assert "rag.llm.call_ollama" in message
        assert "_block_real_ollama_calls" in message


class TestTheRouterNoLongerLeaks:
    def test_the_router_falls_back_without_reaching_the_network(self, monkeypatch):
        """The original leak, as a direct regression test.

        `classify_sources_structured` must degrade to the keyword fallback
        when the model is unreachable -- that behaviour is deliberate and
        unchanged. What this pins is that it does so without a real HTTP
        request: the guard raises inside the worker, the node treats it as
        any other failure, and routing still produces a decision.
        """
        from agent.orchestrator import nodes as orchestrator_nodes

        calls: list[str] = []
        # Bound before patching: reading it back off the module inside the
        # wrapper would call the wrapper.
        original_fallback = orchestrator_nodes.keyword_fallback

        def _tracked_fallback(question, keywords):
            calls.append(question)
            return original_fallback(question, keywords)

        monkeypatch.setattr(orchestrator_nodes, "keyword_fallback", _tracked_fallback)

        decision = orchestrator_nodes.classify_sources_structured(
            "what does the file say?",
            ["sql", "documents"],
            _router_settings(),
        )

        # A decision, not an exception -- routing never fails a question.
        assert decision.primary
        # And it came from the fallback, which needs no model at all.
        assert calls == ["what does the file say?"]


def _router_settings():
    """Minimal `Settings` for the router path, built the way the existing
    orchestrator tests build theirs."""
    from pathlib import Path

    from config.settings import Settings
    from security.secrets import SecretStr

    return Settings(
        ollama_host="http://localhost:11434",
        ollama_model="llama3.1:8b",
        db_type="postgresql",
        db_host="db.example.com",
        db_name="mydb",
        db_user="reader",
        db_password=SecretStr("secret"),
        chroma_persist_dir=Path("/tmp/chroma"),
        chroma_collection_name="schema_ddl",
        embedding_model_name="all-MiniLM-L6-v2",
        router_timeout_seconds=5,
    )


class TestThePerformanceRollupDoesNotLeak:
    """The mechanism behind CLAUDE.md 13's "fail only in file order" entry.

    Root-caused 2026-10-24. `tests/security/test_model_routing_never
    _weakens_deterministic_controls.py::TestACallerPinnedModelIsNever
    Overridden::test_run_agent_honors_an_explicit_selection_over_routing`
    ran the real graph against a real Ollama server -- its fixture mocked
    four LLM seams but not the two planning ones -- and
    `build_analytical_plan` took **14 seconds**. That landed in
    `observability.metrics`' process-global rollup, and
    `generate_recommendations_node` then correctly reported "the
    'build_analytical_plan' stage is slow (p95 14039ms over 1 recent
    request(s))". So five unrelated tests asserting `recommendations == []`
    failed, and only when `tests/security/` happened to run first.

    Two fixes, because the cause and the mechanism are different things:
    `_block_real_ollama_calls` stops the slow call, and
    `_clear_process_singleton_caches` now clears the rollup so a
    genuinely slow stage in any future test cannot leak a performance
    recommendation into an unrelated one.
    """

    def test_the_rollup_starts_empty_for_every_test(self):
        from observability.metrics import get_default_metrics

        snapshot = get_default_metrics().snapshot()
        assert snapshot["stages"] == []

    def test_a_slow_stage_recorded_here_does_not_reach_the_next_test(self):
        # Writes a deliberately slow stage. The companion test below
        # asserts it is gone -- which only holds because the fixture
        # clears the singleton between them. Pinned as a pair so removing
        # that clear fails rather than silently re-opening the leak.
        from observability.metrics import get_default_metrics

        get_default_metrics().record_agent_run(
            [
                {
                    "stage": "build_analytical_plan",
                    "attempt": 1,
                    "duration_ms": 14_039.0,
                    "outcome": "ok",
                    "error_category": None,
                }
            ],
            total_duration_ms=14_039.0,
            status="succeeded",
        )
        assert get_default_metrics().snapshot()["stages"]

    def test_the_slow_stage_from_the_previous_test_is_gone(self):
        from observability.metrics import get_default_metrics

        assert get_default_metrics().snapshot()["stages"] == []

    def test_recommendations_stay_empty_after_a_slow_stage_elsewhere(self):
        """The assertion the five failing tests actually made."""
        from agent.nodes import generate_recommendations_node

        assert generate_recommendations_node({})["recommendations"] == []
