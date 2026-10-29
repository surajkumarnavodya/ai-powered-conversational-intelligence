"""The request-level control plane -- UAT-readiness Phase 6.

`tests/test_agent_loop_bounds.py` covers the bounds *inside* the SQL
graph. This file covers the four controls that span a whole request, each
of which was found either unwired or unreachable on 2026-10-28:

- `LOP-003` -- the orchestrator graph registered its nodes bare, so on
  every non-SQL route there was no step budget, no deadline check and no
  cancellation checkpoint between the router and synthesis.
- `LOP-004` -- `rag.llm.call_ollama` built its own `ollama.Client`, so
  the model calls made by the router, both RAG subgraphs, web search,
  media search, the general-knowledge answer and the analyst planner
  counted against no per-execution budget; and `ProviderUnavailable`,
  raised when the Ollama circuit breaker opens, was caught nowhere and
  reached the caller as HTTP 500.
- `LOP-005` -- an SSE client disconnecting (the dashboard's Stop button)
  did not cancel the run it had started.
- `LOP-006` -- the database query timeout was not clamped to the
  request deadline, the one operation on the request path that was not.

Fully mocked throughout: no Ollama, no database, no HTTP.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent import graph as graph_module
from agent.harness import ExecutionBudgets, ProviderUnavailable, create_envelope
from agent.harness.breakers import record_outcome, reset_breakers
from agent.harness.context import (
    get_current_envelope,
    publish_envelope,
    reset_envelope_observer,
    set_envelope_observer,
)
from agent.harness.failures import FailureClass
from agent.orchestrator import graph as orchestrator_graph_module


def _envelope(**budget_overrides):
    return create_envelope(
        graph_name="test",
        tenant_id="tenant-a",
        user_id="user-a",
        budgets=ExecutionBudgets(**budget_overrides) if budget_overrides else ExecutionBudgets(),
    )


class TestEveryOrchestratorNodeIsGuarded:
    """`LOP-003`, pinned structurally.

    A behavioural test of one route would pass while another route stayed
    unguarded, which is precisely how this was missed: `agent/graph.py`
    wrapped all nineteen of its nodes and the orchestrator wrapped none
    of its ten, and the orchestrator is the graph that runs first.
    """

    def test_no_node_is_registered_without_the_guard(self):
        """Every `add_node` in the orchestrator passes `guard_node(...)`.

        Read from the source rather than the compiled graph, because a
        compiled node is an opaque callable -- `guard_node` deliberately
        does not set `__wrapped__` (see its own note on why), so there is
        nothing to introspect at runtime.
        """
        source = Path(orchestrator_graph_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        unguarded: list[str] = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_node"
            ):
                continue
            name = node.args[0].value if isinstance(node.args[0], ast.Constant) else "<dynamic>"
            second = node.args[1]
            guarded = (
                isinstance(second, ast.Call)
                and isinstance(second.func, ast.Name)
                and second.func.id == "guard_node"
            )
            if not guarded:
                unguarded.append(str(name))

        assert unguarded == [], (
            "these orchestrator nodes run with no step budget, no deadline "
            f"check and no cancellation checkpoint: {unguarded}"
        )

    def test_the_orchestrator_registers_the_nodes_this_test_thinks_it_does(self):
        """Keeps the test above honest: it asserts nothing if `add_node`
        were ever renamed or the node set emptied."""
        compiled = orchestrator_graph_module.build_orchestrator_graph()
        names = set(compiled.get_graph().nodes) - {"__start__", "__end__"}
        assert names == {
            "resolve_context",
            "router",
            "sql_subgraph",
            "document_rag",
            "policy_rag",
            "web_search",
            "generation",
            "media_search",
            "attachments",
            "synthesis",
        }


class TestTheOrchestratorBindsTheEnvelopeForItsOwnLlmCalls:
    """`LOP-004`'s first half.

    `rag.llm.call_ollama` reads the envelope from a `ContextVar`, and the
    `/ask` pool is a raw `ThreadPoolExecutor` that does not copy
    `contextvars` across `submit` -- so the binding has to happen on the
    worker side. Only `run_agent` did it, which left every non-SQL route
    unbound.
    """

    def test_run_orchestrated_binds_the_envelope_on_its_own_thread(self, monkeypatch):
        seen: list[object] = []

        def _fake_invoke(_state, **_kwargs):
            seen.append(get_current_envelope())
            return {"status": "succeeded"}

        monkeypatch.setattr(
            orchestrator_graph_module,
            "build_orchestrator_graph",
            lambda: type("_Compiled", (), {"invoke": staticmethod(_fake_invoke)})(),
        )
        envelope = _envelope()

        # `selected_sources` is what breaks `run_orchestrated`'s
        # short-circuit: with `ENABLE_MULTI_SOURCE_ROUTER` off (the
        # default) and no attachments or source restriction, it calls
        # `run_agent` directly and the orchestrator graph never runs.
        orchestrator_graph_module.run_orchestrated(
            "anything", selected_sources=["sql"], envelope=envelope
        )

        assert seen == [envelope]

    def test_the_binding_is_reset_even_when_the_graph_raises(self, monkeypatch):
        """A leaked binding would apply this request's budget and deadline
        to whatever reuses the pool thread."""

        def _boom(_state, **_kwargs):
            raise ValueError("node exploded")

        monkeypatch.setattr(
            orchestrator_graph_module,
            "build_orchestrator_graph",
            lambda: type("_Compiled", (), {"invoke": staticmethod(_boom)})(),
        )

        with pytest.raises(ValueError):
            orchestrator_graph_module.run_orchestrated(
                "anything", selected_sources=["sql"], envelope=_envelope()
            )

        assert get_current_envelope() is None


class TestSharedOllamaCallsGoThroughTheHarness:
    """`LOP-004`'s second half, at the call site."""

    def test_call_ollama_counts_against_the_execution_budget(self, monkeypatch):
        """One `call_ollama` is one model call on the envelope.

        Before the fix it was zero: the helper built its own client, so
        `harness_ollama_client` -- which is where the accounting, the
        deadline clamp and the breaker check live -- never ran.
        """
        import rag.llm as rag_llm
        from agent.harness.context import reset_current_envelope, set_current_envelope
        from config.settings import Settings

        reset_breakers()
        captured: dict[str, object] = {}

        class _FakeClient:
            def chat(self, **kwargs):
                captured.update(kwargs)
                return {"message": {"content": "hello"}}

        monkeypatch.setattr(rag_llm, "harness_ollama_client", lambda _settings: _FakeClient())
        envelope = _envelope()
        token = set_current_envelope(envelope)
        try:
            # The real `harness_ollama_client` is what charges the
            # budget, so it is charged explicitly here to keep this test
            # about the *call site* rather than about the client factory
            # (which `tests/test_harness_runtime.py` already covers).
            envelope.record_llm_call()
            answer = rag_llm.call_ollama("sys", "user", Settings(), max_tokens=10)
        finally:
            reset_current_envelope(token)

        assert answer == "hello"
        assert envelope.llm_call_count == 1
        assert captured["options"]["temperature"] == 0.0

    def test_a_failed_call_opens_the_breaker_and_a_success_closes_it(self, monkeypatch):
        """The pair that must exist together.

        A call site that can open a breaker but never close it leaves the
        breaker open forever after one blip, which is strictly worse than
        no breaker -- `agent.llm_client.log_ollama_timing`'s own
        docstring says so, and this call site had neither half.
        """
        import ollama

        import rag.llm as rag_llm
        from agent.exceptions import OllamaUnavailableError
        from agent.harness.breakers import breaker_states
        from config.settings import Settings

        reset_breakers()

        class _FailingClient:
            def chat(self, **_kwargs):
                raise ollama.ResponseError("model not found")

        monkeypatch.setattr(rag_llm, "harness_ollama_client", lambda _settings: _FailingClient())
        for _ in range(5):
            with pytest.raises(OllamaUnavailableError):
                rag_llm.call_ollama("sys", "user", Settings(), max_tokens=10)
        assert breaker_states()["ollama"] == "open"

        class _WorkingClient:
            def chat(self, **_kwargs):
                return {"message": {"content": "ok"}}

        monkeypatch.setattr(rag_llm, "harness_ollama_client", lambda _settings: _WorkingClient())
        rag_llm.call_ollama("sys", "user", Settings(), max_tokens=10)
        assert breaker_states()["ollama"] == "closed"


class TestAnOpenBreakerIsARefusalNotAFiveHundred:
    """`LOP-004`'s third half, and the user-visible one.

    `ProviderUnavailable` is a plain `RuntimeError` raised inside
    `harness_ollama_client`, a layer below the
    `OllamaUnavailableError`/`MalformedLLMOutputError` that
    `generate_sql_node` handles. Nothing caught it, so with the breaker
    open -- i.e. with Ollama actually down, the moment graceful
    degradation matters most -- `POST /ask` answered HTTP 500 with "An
    unexpected error occurred".
    """

    @pytest.fixture(autouse=True)
    def _clean_breakers(self):
        reset_breakers()
        yield
        reset_breakers()

    def _trip_the_breaker(self) -> None:
        for _ in range(10):
            record_outcome("ollama", FailureClass.MODEL_UNAVAILABLE)

    def test_run_agent_reports_a_failed_run_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(
            graph_module,
            "build_graph",
            lambda: type(
                "_Compiled",
                (),
                {
                    "invoke": staticmethod(
                        lambda _s, **_k: (_ for _ in ()).throw(ProviderUnavailable("ollama"))
                    )
                },
            )(),
        )

        state = graph_module.run_agent("List orders", envelope=_envelope())

        assert state["status"] == "failed"
        assert state["failure_explanation"] == ProviderUnavailable("ollama").safe_message

    def test_run_orchestrated_reports_a_failed_run_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(
            orchestrator_graph_module,
            "build_orchestrator_graph",
            lambda: type(
                "_Compiled",
                (),
                {
                    "invoke": staticmethod(
                        lambda _s, **_k: (_ for _ in ()).throw(ProviderUnavailable("ollama"))
                    )
                },
            )(),
        )

        state = orchestrator_graph_module.run_orchestrated(
            "anything", selected_sources=["sql"], envelope=_envelope()
        )

        assert state["status"] == "failed"
        assert state["failure_explanation"] == ProviderUnavailable("ollama").safe_message

    def test_the_message_names_no_host_model_or_internal_error(self):
        """CLAUDE.md 5.6: a response body carries the safe message only."""
        message = ProviderUnavailable("ollama").safe_message
        assert "ollama" not in message.lower()
        assert "http" not in message.lower()


class TestTheEnvelopeObserver:
    """`LOP-005`'s mechanism.

    The envelope is built inside the request handler, on the worker
    thread, from values only that handler has resolved. A wrapping caller
    that needs to cancel the run therefore cannot build it or reach it --
    which is why `/ask/stream` noticed a disconnect and did nothing.
    """

    def test_an_observer_receives_the_envelope_published_on_its_thread(self):
        received: list[object] = []
        token = set_envelope_observer(received.append)
        envelope = _envelope()
        try:
            publish_envelope(envelope)
        finally:
            reset_envelope_observer(token)

        assert received == [envelope]

    def test_publishing_with_no_observer_is_a_no_op(self):
        publish_envelope(_envelope())  # must not raise

    def test_an_observer_that_raises_never_fails_the_execution(self):
        """A lost cancellation handle is not a lost answer."""

        def _boom(_envelope):
            raise RuntimeError("observer exploded")

        token = set_envelope_observer(_boom)
        try:
            publish_envelope(_envelope())
        finally:
            reset_envelope_observer(token)

    def test_the_observer_does_not_outlive_its_token(self):
        """A pool thread is reused, so a leaked observer would hand one
        request's cancellation hook the next request's envelope."""
        received: list[object] = []
        token = set_envelope_observer(received.append)
        reset_envelope_observer(token)

        publish_envelope(_envelope())

        assert received == []


class TestTheStreamCancelsOnDisconnect:
    """`LOP-005` at the route.

    Asserted on the source, deliberately. Driving a real SSE disconnect
    through `TestClient` does not reproduce the race the fix is about
    (the disconnect is noticed on the event loop while the run holds a
    worker thread), and a test that mocked that away would assert only
    its own mock. What is worth pinning is that the handler registers for
    the envelope and cancels it on disconnect at all -- the old code did
    neither, with a comment explaining why it chose not to.
    """

    def test_the_handler_registers_for_the_envelope_and_cancels_it(self):
        import api.streaming as streaming

        source = Path(streaming.__file__).read_text(encoding="utf-8")
        assert "set_envelope_observer" in source
        assert "reset_envelope_observer" in source
        assert 'cancel("client_disconnected")' in source

    def test_ask_publishes_its_envelope_so_the_handler_can_find_it(self):
        import api.main as main

        source = Path(main.__file__).read_text(encoding="utf-8")
        assert "publish_envelope(harness_envelope)" in source


class TestTheQueryTimeoutIsClampedToTheDeadline:
    """`LOP-006`.

    `ExecutionEnvelope.timeout_for` is the rule that turns a pile of
    independent per-call timeouts into one end-to-end bound. It was
    applied to the Ollama calls from the start and never to the database
    query, so the deadline bounded the model but not the data access.
    """

    def test_a_short_remaining_deadline_shortens_the_query_timeout(self):
        from agent.harness.control import resolve_timeout

        envelope = _envelope(max_duration_seconds=2.0)
        assert resolve_timeout(envelope, 15.0) <= 2.0

    def test_an_unguarded_run_keeps_the_configured_timeout_exactly(self):
        from agent.harness.control import resolve_timeout

        assert resolve_timeout(None, 15.0) == 15.0

    def test_execute_sql_passes_the_clamped_value_to_the_database(self, monkeypatch):
        """The call site, not just the helper.

        `resolve_timeout` had no production caller at all before this
        fix, so testing the helper alone would have passed on the broken
        code.
        """
        from pathlib import Path as _Path

        from config.settings import Settings
        from security.secrets import SecretStr

        settings = Settings(
            db_type="postgresql",
            db_host="h",
            db_port=5432,
            db_name="d",
            db_user="u",
            db_password=SecretStr("p"),
            chroma_persist_dir=_Path("/tmp/chroma"),
            query_timeout_seconds=15,
        )
        monkeypatch.setattr("agent.nodes.get_settings", lambda: settings)
        monkeypatch.setattr("agent.nodes.get_read_only_engine", lambda _config: object())

        timeouts: list[int] = []

        def _execute(sql, timeout, max_rows, engine=None):
            timeouts.append(timeout)
            return ["id"], [(1,)]

        monkeypatch.setattr("agent.nodes.execute_readonly_sql", _execute)

        from agent.nodes import execute_sql_node

        envelope = _envelope(max_duration_seconds=3.0)
        execute_sql_node(
            {
                "sql": "SELECT id FROM orders",
                "selected_database": "default",
                "caller_roles": ("admin",),
                "harness_envelope": envelope,
            }
        )

        assert timeouts and timeouts[0] <= 3, (
            "the query was handed more time than the request had left: "
            f"{timeouts[0]}s against a 3s deadline"
        )

    def test_an_unguarded_execute_sql_still_gets_the_configured_timeout(self, monkeypatch):
        """The compatibility half: no envelope, no change."""
        from pathlib import Path as _Path

        from config.settings import Settings
        from security.secrets import SecretStr

        settings = Settings(
            db_type="postgresql",
            db_host="h",
            db_port=5432,
            db_name="d",
            db_user="u",
            db_password=SecretStr("p"),
            chroma_persist_dir=_Path("/tmp/chroma"),
            query_timeout_seconds=15,
        )
        monkeypatch.setattr("agent.nodes.get_settings", lambda: settings)
        monkeypatch.setattr("agent.nodes.get_read_only_engine", lambda _config: object())

        timeouts: list[int] = []

        def _execute(sql, timeout, max_rows, engine=None):
            timeouts.append(timeout)
            return ["id"], [(1,)]

        monkeypatch.setattr("agent.nodes.execute_readonly_sql", _execute)

        from agent.nodes import execute_sql_node

        execute_sql_node(
            {
                "sql": "SELECT id FROM orders",
                "selected_database": "default",
                "caller_roles": ("admin",),
            }
        )

        assert timeouts == [15]
