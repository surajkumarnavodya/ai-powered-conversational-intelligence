"""Shared pytest fixtures.

`config/settings.py` calls `load_dotenv()` at import time, so this
machine's real `.env` is already sitting in `os.environ` by the time
pytest starts collecting. That was harmless before the Pydantic
migration -- the old `Settings` was a plain `@dataclass`, so a test
building `Settings(**partial_kwargs)` got the class's hardcoded
defaults for every field it didn't pass, never the real environment.

Now that `Settings` is a `pydantic_settings.BaseSettings`, an
unspecified field falls back to reading `os.environ` (that's the
entire point of `BaseSettings`), not a fixed default -- so the exact
same test code would silently pick up whatever this developer's own
`.env` happens to have set (e.g. `ENABLE_DOCUMENT_RAG=true`), making
the test suite's result depend on the machine it runs on. The fixture
below restores test isolation by stripping every env var one of
`Settings`'s own fields could read before each test runs; individual
tests that want to exercise real env-var parsing (e.g.
`DB_CONNECTIONS`) call `monkeypatch.setenv`/`setattr` themselves,
after this fixture has already cleared the slate.

**A second, narrower gap the per-test fixture alone cannot close, found
while enabling `LOCAL_AUTH_ENABLED=true` in this machine's own `.env` for
real end-to-end testing:** ~20 test files (`tests/test_api_*.py`,
`tests/test_connection.py`, others) build a **module-level**
`_BASE_SETTINGS = Settings(...)` constant, executed once at module import
time -- which, per pytest's own collection order, happens *before* any
test in that module has run, and therefore before the autouse fixture
below has ever fired even once. Any `Settings` field that module-level
call doesn't explicitly pass still reads the real, polluted `os.environ`
at that one import moment, and gets baked permanently into
`_BASE_SETTINGS.__dict__` -- every test in the file that rebuilds via
`Settings(**{**_BASE_SETTINGS.__dict__, **overrides})` (this codebase's
own documented pattern, see e.g. `tests/test_connection.py::_settings`'s
docstring) then inherits that one-time-polluted value on every call,
completely bypassing the per-test `monkeypatch.delenv` below, since the
value is being passed as an explicit constructor kwarg, not read from
`os.environ` again. Concretely: `LOCAL_AUTH_ENABLED=true` in this
developer's `.env` made `Settings.auth_mode` silently resolve to
`"local"` instead of the value ~77 tests across 11 files actually
expected (`"none"`/`"static_token"`/`"oidc"`), since `auth_mode` prioritizes
`local_auth_enabled` first (see that property's own docstring) --
discovered, not hypothetical.

Fixed the only way a *module-level* snapshot can be protected from a
*per-test* fixture: strip the same env vars here too, as plain top-level
code in this file, so it runs once at collection time -- pytest always
imports a directory's `conftest.py` before collecting sibling test
modules in that directory, so this runs before any file's own
module-level `Settings(...)` call does. The fixture below remains
necessary on top of this: it also undoes whatever a test's own
`monkeypatch.setenv` call did, between tests within a session, which a
one-time strip at collection can't.

**A third gap, found 2026-10-28 by the Phase 4 authorization audit
(finding `BP-003`).** Both strips above iterate `Settings.model_fields`,
so they only ever remove env vars whose names *are* field names. But
`config/settings.py` deliberately reads several **open-ended** families
straight from `os.environ`, because each names a dynamic set of variables
no Pydantic field can declare -- `DB_CONNECTIONS` plus a `DB_<NAME>_*`
block per entry (`_parse_named_connections`), and `MCP_SERVERS` plus
`MCP_<NAME>_*` likewise. None of those are field names, so none of them
were ever stripped, and `load_dotenv()` had already put them in
`os.environ`.

Measured with this fixture active: 24 `DB_*` variables visible inside a
test, two of them `DB_<NAME>_PASSWORD`, and `get_settings().databases`
returning this developer's **four real SQL Server connections**. That
makes CLAUDE.md 7's claim -- "Every pytest test is fully mocked -- no
real database" -- false in exactly the way it was already found false for
Ollama: silently. `tests/security/test_authorization_matrix.py` swept
`GET /health`, which connected to a real server and logged
`SAWarning: Unrecognized server version info '17.0.4025.3'` -- the only
visible trace.

Closed by stripping the prefix families too, and pinned by
`tests/test_settings_env_isolation.py` so it cannot regress silently a
second time. The prefixes are a superset of the scalar `DB_*` fields
already covered, which is harmless: a test that wants to exercise real
parsing sets what it needs with `monkeypatch.setenv` after the slate is
clear, exactly as the `DB_CONNECTIONS` tests already do.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.settings import Settings

#: Env-var name prefixes `config/settings.py` reads directly from
#: `os.environ` rather than through a declared `Settings` field, because
#: each names an open-ended family whose members depend on the values of
#: `DB_CONNECTIONS`/`MCP_SERVERS` themselves. Iterating
#: `Settings.model_fields` can never reach them -- see this module's
#: docstring, third gap (`BP-003`).
_DYNAMIC_ENV_PREFIXES = ("DB_", "MCP_")


def _env_names_to_strip() -> list[str]:
    """Every env var that could steer a `Settings` built inside a test:
    the declared field names, plus everything currently present in the
    dynamic families."""
    names = {field_name.upper() for field_name in Settings.model_fields}
    names.update(name for name in os.environ if name.upper().startswith(_DYNAMIC_ENV_PREFIXES))
    return sorted(names)


for _env_name in _env_names_to_strip():
    os.environ.pop(_env_name, None)
del _env_name


@pytest.fixture(autouse=True)
def _isolate_settings_from_real_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in _env_names_to_strip():
        monkeypatch.delenv(env_name, raising=False)


class RealOllamaCallInTest(AssertionError):
    """A test reached the real Ollama server.

    Its own class so the message is unmistakable in a failure report, and
    so a test that deliberately wants to assert this guard fires can catch
    it precisely.
    """


@pytest.fixture(autouse=True)
def _block_real_ollama_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes CLAUDE.md 7's "no real Ollama" rule load-bearing.

    That rule was documented but nothing enforced it, and the gap was
    real: `tests/test_orchestrator.py::TestRunOrchestrated
    ::test_flag_off_with_attachments_still_runs_the_orchestrator_graph`
    ran the orchestrator graph for real, so `router_node` called
    `agent.orchestrator.nodes.classify_sources_structured`, which submits
    `rag.llm.call_ollama` to a `ThreadPoolExecutor` it then abandons with
    `shutdown(wait=False)`. The test passed on the keyword fallback while
    the abandoned worker went on to make a genuine HTTP request to
    `localhost:11434` -- visible only as an `httpx` log line printed
    *after* pytest's own summary, which is why it survived so long.

    On a developer machine with Ollama running that makes the suite
    slower, non-deterministic, and dependent on which models happen to be
    installed. In CI, where no Ollama exists, the same call fails and
    falls back, so the suite stayed green either way and never reported
    the difference.

    Patching `ollama.Client.chat` rather than `rag.llm.call_ollama` or
    `agent.llm_client`'s helpers is deliberate: it is the single point
    every path reaches the network through, including from a thread a node
    has already stopped waiting for, so no new call site can route around
    it. A test that wants an LLM reply patches its own seam
    (`rag.llm.call_ollama`, `agent.nodes.generate_sql_from_llm`, ...) as
    tests here already do, and never sees this.
    """

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise RealOllamaCallInTest(
            "A test reached the real Ollama server. Patch the seam the code "
            "under test actually uses (rag.llm.call_ollama, "
            "agent.nodes.generate_sql_from_llm, ...) instead of relying on a "
            "timeout or a fallback. See tests/conftest.py's "
            "_block_real_ollama_calls."
        )

    import ollama

    monkeypatch.setattr(ollama.Client, "chat", _refuse, raising=False)
    monkeypatch.setattr(ollama.Client, "embed", _refuse, raising=False)
    monkeypatch.setattr(ollama.Client, "embeddings", _refuse, raising=False)


@pytest.fixture(autouse=True)
def _isolate_published_database_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A publish in any test must never write the real project `.env`.

    Points the registration at a temporary file and turns off the in-process
    `os.environ` update, so a publish test can't leak a `DB_CONNECTIONS` entry
    into the real configuration or into later tests.
    """
    import api.onboarding as onboarding_routes
    import onboarding.env_registration as env_registration

    monkeypatch.setattr(env_registration, "DEFAULT_ENV_PATH", tmp_path / "test.env")
    monkeypatch.setattr(onboarding_routes, "apply_to_runtime", lambda result: None)


@pytest.fixture(autouse=True)
def _clear_process_singleton_caches() -> None:
    """Clears the `functools.cache`/`lru_cache`-backed singletons built once
    per process (compiled LangGraph graphs, the cached Ollama client, the
    cached Chroma client) before each test.

    These exist for real request-latency reasons (see `agent.graph
    .build_graph`, `agent.orchestrator.graph.build_orchestrator_graph`,
    `agent.llm_client._get_ollama_client`'s docstrings) but a cache keyed
    only on call arguments (or, for `build_graph`, on nothing at all) means
    the *first* test in a session to populate a given cache slot pins
    whatever it built there for every later test that happens to share the
    same key -- concretely, a test that does `monkeypatch.setattr("ollama
    .Client", FakeClient)` and expects `_get_ollama_client(host, timeout)`
    to construct a fresh fake has no effect if an earlier test already
    cached a real client for that same `(host, timeout)` pair. Without this
    fixture that failure mode is silent and order-dependent -- it only
    shows up as a flaky, hard-to-explain failure depending on which tests
    ran first, not as a clear assertion mismatch.

    `_get_ask_executor` (added by the enterprise scalability assessment's
    graceful-shutdown work, `api/main.py`) is the same category of hazard
    with a sharper failure mode: a test that exercises real shutdown
    (`_shutdown_ask_executor`) leaves the cached `ThreadPoolExecutor`
    permanently unusable (`.shutdown()` is irreversible) -- without clearing
    it here, every *later* test in the process that happens to request the
    same `max_workers` key would get handed that already-shut-down instance
    and fail with `RuntimeError: cannot schedule new futures after
    shutdown`, not a clear assertion mismatch.
    """
    from identity.db import _cached_identity_engine, _cached_session_factory
    from moderation.store import _cached_moderation_engine

    from agent.graph import build_graph
    from agent.harness.breakers import reset_breakers as reset_harness_breakers
    from agent.llm_client import _get_ollama_client
    from agent.multiagent.policy import reset_breakers
    from agent.orchestrator.graph import build_orchestrator_graph
    from api.main import _get_ask_executor
    from db.query_store import clear_query_store_cache
    from embeddings.schema_indexer import clear_chroma_client_cache
    from jobs.queue import reset_question_queue
    from model_routing.budget import reset_escalation_limiters
    from model_routing.evidence import reset_evidence_cache
    from model_routing.metrics import get_route_metrics
    from observability.metrics import get_default_metrics

    build_graph.cache_clear()
    build_orchestrator_graph.cache_clear()
    _get_ollama_client.cache_clear()
    clear_chroma_client_cache()
    _cached_moderation_engine.cache_clear()
    _cached_identity_engine.cache_clear()
    _cached_session_factory.cache_clear()
    _get_ask_executor.cache_clear()
    # `observability.metrics.get_default_metrics` is the same
    # `functools.cache` singleton pattern, and leaving it out had a real,
    # long-unattributed consequence. CLAUDE.md 13 recorded five tests
    # that "fail only in file order" with the cause unknown: when
    # `tests/security/` ran first,
    # `TestGenerateRecommendationsNode`/`...QueryStore`'s
    # `recommendations == []` assertions failed. Root-caused 2026-10-24
    # with a two-test reproduction --
    # `test_run_agent_honors_an_explicit_selection_over_routing` ran the
    # real graph against a real Ollama server (the fixture had not mocked
    # the two planning seams), `build_analytical_plan` took **14
    # seconds**, that landed in this process-global rollup, and
    # `generate_recommendations_node` then correctly emitted "the
    # 'build_analytical_plan' stage is slow (p95 14039ms)".
    #
    # `_block_real_ollama_calls` removes the cause; clearing the rollup
    # removes the mechanism, so a genuinely slow stage in any future test
    # cannot leak a performance recommendation into an unrelated one.
    get_default_metrics.cache_clear()
    # The harness's provider circuit breakers are process-lifetime state
    # in the live LLM path, exactly like the caches above. Without this,
    # a test that drives enough Ollama failures leaves the circuit open
    # and the *next* test fails with ProviderUnavailable for no reason of
    # its own -- the hardest kind of cross-test contamination to find,
    # because it only appears in a particular test order.
    reset_harness_breakers()
    clear_query_store_cache()
    # The asynchronous question queue (`jobs/`) is a process-lifetime
    # singleton that also owns live threads, which makes it the sharpest
    # version of the `_get_ask_executor` hazard described above: a queue
    # built from one test's `Settings` (its per-user cap, its timeouts)
    # and its injected runner would otherwise be silently reused by every
    # later test, and a test that exercises real shutdown would leave the
    # pool permanently unusable. Resetting also joins the old queue's
    # dispatcher and monitor threads, so threads do not accumulate across
    # the suite.
    reset_question_queue()
    # Prompt 47 (model routing): the per-route metrics rollup, the
    # per-model circuit breakers and the per-tenant escalation windows are
    # all process-wide by design (a model being down is a fact about the
    # deployment, not about one question) -- which makes them exactly the
    # order-dependent hazard this fixture exists for. The breaker in
    # particular: one test that drives a model to its failure threshold
    # would otherwise leave that model circuit-open for every later test
    # in the process.
    get_route_metrics().reset()
    reset_breakers()
    reset_escalation_limiters()
    reset_evidence_cache()
