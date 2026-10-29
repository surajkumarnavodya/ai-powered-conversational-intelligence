"""The wiring that feeds the AI usage rollup (catalogue row 45).

`observability.metrics.PerformanceMetrics.record_llm_call`'s own
aggregation is covered in `tests/test_observability_metrics.py
::TestLlmUsageRollup`; this file covers the half that is easy to get
silently wrong -- that `agent.llm_client.log_ollama_timing` is really
the single funnel every `client.chat` passes through, that the model name
and tenant are read from the right places, and that a response carrying
no counts contributes nothing rather than a phantom zero-token call.

Fully mocked: no Ollama, no HTTP. `log_ollama_timing` is called directly
with response shapes matching what the `ollama` client returns (a dict,
and an attribute-style object -- `_get_field` supports both).
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import llm_client
from observability.metrics import PerformanceMetrics
from rag import llm as rag_llm
from security.audit_log import reset_audit_tenant_id, set_audit_tenant_id


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> PerformanceMetrics:
    """A fresh rollup in place of the process-wide singleton.

    `log_ollama_timing` resolves `get_default_metrics()` at call time, so
    patching the name in `agent.llm_client` is enough -- and keeps these
    tests from accumulating into the real singleton other tests share.
    """
    fresh = PerformanceMetrics()
    monkeypatch.setattr(llm_client, "get_default_metrics", lambda: fresh)
    return fresh


def _response(**overrides: object) -> dict[str, object]:
    """A realistic Ollama chat response, token counters included."""
    base: dict[str, object] = {
        "model": "llama3.1:8b",
        "total_duration": 1_500_000_000,
        "load_duration": 10_000_000,
        "prompt_eval_duration": 400_000_000,
        "eval_duration": 1_000_000_000,
        "prompt_eval_count": 820,
        "eval_count": 64,
    }
    base.update(overrides)
    return base


class TestTheFunnelIsReallyTheOnlyPath:
    def test_every_chat_call_is_followed_by_the_timing_funnel(self):
        """Pins the claim both docstrings make -- that a *new* `client.chat`
        call site is counted for free because the funnel sits at every one.

        Structural rather than behavioural on purpose: the failure mode is
        someone adding another `client.chat` and forgetting the funnel
        line, which no behavioural test of the existing ones would catch.

        It happened. This test scanned `agent/llm_client.py` alone, and
        the eighth call site was in a *different* module --
        `rag/llm.py::call_ollama`, the shared helper behind the RAG
        subgraph, the orchestrator's router and web/media answer steps,
        the general-knowledge answer and the analyst planner. It had no
        funnel line at all, so its calls were missing from
        `GET /metrics/performance` and could open a breaker they could
        never close (UAT-readiness Phase 6, `LOP-004`). Both modules are
        scanned now, because "every `client.chat` in this application"
        is the claim worth pinning, not "every one in this file".
        """
        chat_calls: list[ast.Call] = []
        funnel_calls: list[ast.Call] = []
        for module in (llm_client, rag_llm):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            chat_calls += [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "chat"
            ]
            funnel_calls += [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "log_ollama_timing"
            ]

        assert len(chat_calls) > 0
        assert len(funnel_calls) == len(chat_calls), (
            f"{len(chat_calls)} client.chat call(s) but {len(funnel_calls)} "
            "log_ollama_timing call(s) -- a chat call with no funnel line is "
            "an LLM call whose tokens are never counted and whose circuit "
            "breaker never closes on success"
        )

    def test_no_module_builds_its_own_ollama_client(self):
        """The other half of `LOP-004`, and the more important half.

        A `client.chat` with no funnel line is an uncounted call. An
        `ollama.Client(...)` built outside `agent/llm_client.py` is
        worse: it also escapes the circuit breaker, the per-execution
        model-call budget and the deadline clamp, which are the three
        things `harness_ollama_client` exists to apply. Exactly one
        module may construct one.
        """
        repo_root = Path(llm_client.__file__).resolve().parent.parent
        # Application packages only: a test fixture, a benchmark harness
        # and a mock server all legitimately build their own client, and
        # none of them is on a request path.
        excluded = {"tests", "scripts", "eval", ".venv", "env", ".claude", "node_modules"}
        builders = set()
        for path in sorted(repo_root.glob("**/*.py")):
            relative = path.relative_to(repo_root)
            if excluded & set(relative.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Client"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "ollama"
                ):
                    builders.add(relative.as_posix())

        assert builders == {"agent/llm_client.py"}, (
            "an ollama.Client built outside agent.llm_client escapes the "
            "circuit breaker, the model-call budget and the deadline clamp. "
            f"Modules constructing one: {sorted(builders)}"
        )


class TestRecordingFromAResponse:
    def test_a_normal_response_is_recorded_under_the_model_it_reports(
        self, metrics: PerformanceMetrics
    ):
        llm_client.log_ollama_timing(_response())

        assert metrics.snapshot()["llm_usage"] == [
            {
                "model": "llama3.1:8b",
                "calls": 1,
                "prompt_tokens": 820,
                "completion_tokens": 64,
                "total_tokens": 884,
            }
        ]

    def test_an_attribute_style_response_object_works_too(self, metrics: PerformanceMetrics):
        # The `ollama` package returns an object, not a dict, on newer
        # versions -- `_get_field`/`_get_model_name` support both.
        llm_client.log_ollama_timing(SimpleNamespace(**_response()))

        assert metrics.snapshot()["llm_usage"][0]["total_tokens"] == 884

    def test_counts_are_recorded_even_when_the_duration_breakdown_is_absent(
        self, metrics: PerformanceMetrics
    ):
        # `total_duration` gates only the timing *log line*. The token
        # counters are separate response fields, so usage must not be
        # silently dependent on a field it has nothing to do with.
        llm_client.log_ollama_timing(_response(total_duration=None))

        assert metrics.snapshot()["llm_usage"][0]["calls"] == 1

    def test_a_response_with_no_token_counts_records_nothing(self, metrics: PerformanceMetrics):
        llm_client.log_ollama_timing(_response(prompt_eval_count=None, eval_count=None))

        assert metrics.snapshot()["llm_usage"] == []

    def test_a_response_with_no_model_name_records_nothing(self, metrics: PerformanceMetrics):
        llm_client.log_ollama_timing(_response(model=None))

        assert metrics.snapshot()["llm_usage"] == []

    def test_a_bare_mock_response_records_nothing(self, metrics: PerformanceMetrics):
        # What most of this repo's existing tests pass in. The rollup must
        # stay empty rather than inventing an "unknown" model bucket.
        llm_client.log_ollama_timing({"message": {"content": "SELECT 1"}})

        assert metrics.snapshot()["llm_usage"] == []


class TestTenantAttribution:
    def test_usage_lands_in_the_tenant_bound_for_this_request(self, metrics: PerformanceMetrics):
        # `agent.graph.run_agent` binds this contextvar on the worker side
        # of the /ask thread-pool boundary; the funnel reads it rather than
        # taking a tenant argument through seven call sites.
        token = set_audit_tenant_id("tenant-a")
        try:
            llm_client.log_ollama_timing(_response())
        finally:
            reset_audit_tenant_id(token)

        assert metrics.snapshot("tenant-a")["llm_usage"][0]["total_tokens"] == 884

    def test_another_tenants_slice_does_not_see_it(self, metrics: PerformanceMetrics):
        token = set_audit_tenant_id("tenant-a")
        try:
            llm_client.log_ollama_timing(_response())
        finally:
            reset_audit_tenant_id(token)

        assert metrics.snapshot("tenant-b")["llm_usage"] == []
