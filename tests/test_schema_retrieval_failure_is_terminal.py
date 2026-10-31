"""`retrieve_schema` must end the run it says it ended -- UAT-readiness Phase 11.

`OPS-025`. `retrieve_schema_node` composes a terminal `"failed"` state with
a `failure_explanation` whenever no schema is available: an unbuilt or
empty index, a tenant entitled to no configured database, or any other
`SchemaRetrievalError`. The edge out of it was **unconditional**, so that
state was written and then driven straight past -- every downstream node
ran without a `selected_database`, `generate_sql` spent a real model call
on a prompt containing no schema, and the run ended in
`_require_selected_database`'s `RuntimeError`, which is not an
`AgentError` and so became HTTP 500 "An unexpected error occurred."

Measured live before the fix (2026-10-31), with `CHROMA_PERSIST_DIR`
pointing at an empty directory on this machine's four-database
configuration:

    [retrieve_schema] failed: No configured database has a built schema
    index. Run `python scripts/build_embeddings.py` first.
    RAISED after 68.43s: RuntimeError: reached with no selected_database

68 seconds and one model call to turn an actionable message into an
opaque 500. This is the third instance of the shape Phase 6 named in
`LOP-001` and `LOP-004` -- a *designed* terminal condition escaping
`run_agent` as an unhandled exception -- which is why the whole class
gets a test here rather than only the one trigger.

Two triggers, deliberately both covered, because they fail differently:

1. **An unbuilt/empty index** raises `SchemaRetrievalError`, which the
   node *does* catch. It reaches every configuration, and it is the most
   likely state of a fresh UAT host, since `scripts/build_embeddings.py`
   is a documented setup step and `GET /health` already reports "Index is
   empty" per database. The defect was purely the unconditional edge.
2. **An unusable persist directory** raises `OSError` from
   `get_chroma_client`'s `mkdir`, which the node's
   `except SchemaRetrievalError` never matched. Only reachable with more
   than one configured database, because `select_database` returns before
   touching Chroma when exactly one is configured. Tested in
   `tests/test_db_router.py`, which already owns `select_database` and its
   two-database settings, rather than duplicated here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from retrieval.retriever import RetrievalResult

from agent.exceptions import SchemaRetrievalError
from agent.graph import run_agent
from agent.nodes import route_after_schema_retrieval
from agent.state import TableSchema
from config.settings import Settings
from security.secrets import SecretStr

_SETTINGS = Settings(
    ollama_host="http://localhost:11434",
    ollama_model="llama3.1:8b",
    db_type="postgresql",
    db_host="db.example.com",
    db_port=5432,
    db_name="mydb",
    db_user="reader",
    db_password=SecretStr("secret"),
    chroma_persist_dir=Path("/tmp/chroma"),
    cost_estimation_enabled=False,
)

_QUESTION = "List orders"

_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, order_total DECIMAL(10,2));"
_TABLES = [TableSchema(table_name="orders", ddl=_DDL, similarity_score=0.95)]

#: The exact message `embeddings.retriever.select_database` raises when no
#: configured database has an index, i.e. the forgotten-setup-step case.
_UNBUILT = (
    "No configured database has a built schema index. Run "
    "`python scripts/build_embeddings.py` first."
)


@pytest.fixture(autouse=True)
def _mock_boundary(monkeypatch):
    """Only the calls that would leave the process, as in the rest of this suite."""
    monkeypatch.setattr("agent.nodes.get_settings", lambda: _SETTINGS)
    monkeypatch.setattr("agent.graph.get_settings", lambda: _SETTINGS)
    monkeypatch.setattr("agent.nodes.retrieve_golden_examples", lambda *a, **k: [])
    monkeypatch.setattr("agent.nodes.retrieve_business_context", lambda *a, **k: RetrievalResult())
    monkeypatch.setattr("agent.nodes.generate_analytical_intent_from_llm", lambda *a, **k: None)
    monkeypatch.setattr("agent.nodes.generate_analytical_plan_from_llm", lambda *a, **k: None)
    monkeypatch.setattr("agent.nodes.generate_query_plan_from_llm", lambda *a, **k: [])
    monkeypatch.setattr("agent.nodes.generate_insight_from_llm", lambda *a, **k: "ok")


def _counting_generator(monkeypatch) -> dict[str, int]:
    """Stubs the generation model call and counts it.

    The count is the point of this file: the pre-`OPS-025` run reached
    `generate_sql` with no schema and spent a model call there, which on
    this hardware is the difference between a 0.1 s refusal and a 68 s
    one.
    """
    calls = {"n": 0}

    def _generate(*args, **kwargs):
        calls["n"] += 1
        return "SELECT 1"

    monkeypatch.setattr("agent.nodes.generate_sql_from_llm", _generate)
    return calls


class TestAnUnavailableSchemaIndexEndsTheRun:
    """Trigger 1: `SchemaRetrievalError`, which the node already catches."""

    def test_an_unbuilt_index_fails_terminally_with_no_model_call(self, monkeypatch):
        def _raise(*args, **kwargs):
            raise SchemaRetrievalError(_UNBUILT)

        monkeypatch.setattr("agent.nodes.retrieve_relevant_schema", _raise)
        generated = _counting_generator(monkeypatch)

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        # The node's own explanation survives to the caller -- this is the
        # message a UAT operator needs, naming the command to run.
        assert "build_embeddings" in state["failure_explanation"]
        # The whole finding in one assertion: no schema means nothing to
        # generate from, so no model call may be spent.
        assert generated["n"] == 0, "generate_sql ran with no schema in state"

    def test_the_run_stops_before_every_downstream_node(self, monkeypatch):
        """No node after retrieve_schema may run -- each would see no database."""

        def _raise(*args, **kwargs):
            raise SchemaRetrievalError(_UNBUILT)

        monkeypatch.setattr("agent.nodes.retrieve_relevant_schema", _raise)
        _counting_generator(monkeypatch)

        state = run_agent(_QUESTION)

        stages = [entry["stage"] for entry in state.get("stage_timings", [])]
        assert "retrieve_schema" in stages
        for downstream in (
            "retrieve_golden_examples",
            "retrieve_business_context",
            "plan_query",
            "generate_sql",
            "validate_sql",
            "execute_sql",
        ):
            assert downstream not in stages, f"{downstream} ran after a terminal failure"

    def test_a_tenant_with_no_queryable_database_is_also_terminal(self, monkeypatch):
        """The node's other `SchemaRetrievalError` source, same contract.

        Deliberately asserted separately: this one is a *authorization*
        outcome reached through the same path, and it must not be
        distinguishable from a missing index in what it costs or how it
        ends.
        """

        def _raise(*args, **kwargs):
            raise SchemaRetrievalError(
                "No configured database is available for this caller. Check "
                "DB_CONNECTIONS and any DB_<NAME>_TENANT_IDS restrictions in .env."
            )

        monkeypatch.setattr("agent.nodes.retrieve_relevant_schema", _raise)
        generated = _counting_generator(monkeypatch)

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert generated["n"] == 0

    def test_a_successful_retrieval_still_proceeds(self, monkeypatch):
        """Non-vacuity: the new edge must not terminate a healthy run."""
        monkeypatch.setattr("agent.nodes.retrieve_relevant_schema", lambda *a, **k: list(_TABLES))
        generated = _counting_generator(monkeypatch)

        state = run_agent(_QUESTION)

        assert generated["n"] >= 1, "the happy path no longer reaches generate_sql"
        stages = [entry["stage"] for entry in state.get("stage_timings", [])]
        assert "retrieve_golden_examples" in stages


class TestTheRoutingFunctionItself:
    """Unit-level, so a future edge change cannot pass by accident."""

    def test_a_failed_status_routes_to_the_terminal_branch(self):
        assert route_after_schema_retrieval({"status": "failed"}) == "failed"

    @pytest.mark.parametrize("status", ["running", "succeeded", None])
    def test_anything_else_proceeds(self, status):
        assert route_after_schema_retrieval({"status": status}) == "retrieve_golden_examples"

    def test_a_missing_status_key_proceeds(self):
        """Defaults to proceeding -- the node sets `status` only to stop."""
        assert route_after_schema_retrieval({}) == "retrieve_golden_examples"
