"""Loop-bound regression gate -- UAT-readiness Phase 6 (agent control flow).

Every cycle in the compiled SQL graph is driven to its ceiling here with
a stub LLM that never produces usable SQL, and each test asserts the run
**terminates**, with what status, after how many model calls, and with a
user-facing explanation. `tests/test_full_pipeline_integration.py`
already proves a retry loop *loops*; this file proves each one *stops*.

Three things it is deliberately built to catch, because each one has
actually happened in this repo:

1. **A cycle outgrowing the recursion limit.** `LOP-001`: the
   missing-reference cycle gained `enforce_display_names` and
   `agent.graph`'s limit arithmetic was never updated, so the worst-case
   retry sequence raised `GraphRecursionError` and `POST /ask` answered
   HTTP 500. `TestTheStructuralBoundMatchesTheGraph` recomputes all three
   structural constants from the compiled graph's own edges, so the next
   such addition fails a test that names the new number.
2. **A safety gate being retried.** A validator safety violation and an
   execution timeout must end the run with no further attempt, whatever
   budget is left.
3. **Spending the whole budget on no progress.** `LOP-002`: an identical
   statement regenerated verbatim is not an attempt, and burning four
   model calls and four database executions on it was measured before
   `agent.nodes` detected it.

Fully mocked, like the rest of this suite: the mock boundary is the same
narrow one `tests/test_full_pipeline_integration.py` uses -- only the
functions that would make a real network or database call are replaced,
so every node, every conditional edge and the real retry accounting run
unmodified.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from retrieval.retriever import RetrievalResult
from sqlalchemy.exc import SQLAlchemyError

from agent.graph import (
    LONGEST_RETRY_CYCLE_NODES,
    POST_EXECUTION_NODES,
    PRE_CYCLE_NODES,
    RECURSION_LIMIT_MESSAGE,
    build_graph,
    recursion_limit_for,
    run_agent,
)
from agent.state import TableSchema
from config.settings import Settings
from security.secrets import SecretStr

_BASE_MAX_RETRIES = 3

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
    max_retries=_BASE_MAX_RETRIES,
    # 0 so every test in this file has exactly `_BASE_MAX_RETRIES` to
    # spend whatever its question's wording happens to score -- the
    # adaptive bonus is `agent.complexity`'s own concern, covered by
    # `TestTheStructuralBoundMatchesTheGraph` instead.
    complex_query_max_retry_bonus=0,
    # A pure pass-through, so no SQLAlchemy-level EXPLAIN mock is needed.
    cost_estimation_enabled=False,
)

#: One attempt per retry, plus the first one.
_MAX_ATTEMPTS = _BASE_MAX_RETRIES + 1

_DDL = (
    "CREATE TABLE orders (\n"
    "    id INTEGER PRIMARY KEY,\n"
    "    customer_id INTEGER,\n"
    "    order_total DECIMAL(10,2)\n"
    ");"
)
_TABLES = [TableSchema(table_name="orders", ddl=_DDL, similarity_score=0.95)]

#: A plain question with no complexity signal, so the retry budget is
#: exactly `_BASE_MAX_RETRIES` and no planning model call is made.
_QUESTION = "List orders"

_MISSING_COLUMN_ERROR = '(psycopg2.errors.UndefinedColumn) column "nope" does not exist'
_SYNTAX_ERROR = "(psycopg2.errors.SyntaxError) syntax error at or near FROM"


@pytest.fixture(autouse=True)
def _mock_boundary(monkeypatch):
    """Settings plus every call that would otherwise leave the process."""
    monkeypatch.setattr("agent.nodes.get_settings", lambda: _SETTINGS)
    monkeypatch.setattr("agent.graph.get_settings", lambda: _SETTINGS)
    monkeypatch.setattr("agent.nodes.retrieve_relevant_schema", lambda *a, **k: list(_TABLES))
    monkeypatch.setattr("agent.nodes.retrieve_golden_examples", lambda *a, **k: [])
    monkeypatch.setattr("agent.nodes.retrieve_business_context", lambda *a, **k: RetrievalResult())
    monkeypatch.setattr("agent.nodes.generate_analytical_intent_from_llm", lambda *a, **k: None)
    monkeypatch.setattr("agent.nodes.generate_analytical_plan_from_llm", lambda *a, **k: None)
    monkeypatch.setattr("agent.nodes.generate_query_plan_from_llm", lambda *a, **k: [])
    monkeypatch.setattr("agent.nodes.generate_insight_from_llm", lambda *a, **k: "ok")


@pytest.fixture(autouse=True)
def _reset_process_wide_limiters():
    """Both limiters `agent.rate_limit` keeps as process-wide singletons.

    `_database_execution_limiters` for the same reason
    `tests/test_full_pipeline_integration.py` resets it: one test's
    in-flight count must not leak into another's.

    `_llm_call_limiter` because this file is the one that *needs* it
    reset. Every test here deliberately drives a retry loop to its
    ceiling, so the file makes far more generation calls than
    `llm_call_rate_limit_per_minute` (20) allows in a minute -- and the
    limiter is a sliding window over wall-clock time, not per-run state.
    Without this, a test that runs late in the file is denied by the
    limiter and ends at `status="rate_limited"`: a real control firing
    correctly, but not the one under test, and the symptom (a ceiling
    test passing or failing depending on how many tests ran before it)
    is exactly the file-order fragility CLAUDE.md 13 records having
    already cost this repo one debugging session.
    """
    import agent.rate_limit as rate_limit_module

    def _reset() -> None:
        rate_limit_module._database_execution_limiters.clear()
        rate_limit_module._llm_call_limiter = None

    _reset()
    yield
    _reset()


def _counting_generator(monkeypatch, sql: str | list[str]) -> dict[str, int]:
    """Stubs `generate_sql_from_llm` and counts its calls.

    `sql` as a list returns one entry per attempt, repeating the last --
    which is what makes "the model changed its answer" distinguishable
    from "the model repeated itself" in the no-progress tests below.
    """
    calls = {"n": 0}
    statements = [sql] if isinstance(sql, str) else list(sql)

    def _generate(*args, **kwargs):
        calls["n"] += 1
        return statements[min(calls["n"] - 1, len(statements) - 1)]

    monkeypatch.setattr("agent.nodes.generate_sql_from_llm", _generate)
    return calls


def _counting_executor(monkeypatch, raises: BaseException | None) -> dict[str, int]:
    """Stubs `execute_readonly_sql` and counts its calls."""
    calls = {"n": 0}

    def _execute(sql, timeout, max_rows, engine=None):
        calls["n"] += 1
        if raises is not None:
            raise raises
        return ["id"], [(1,)]

    monkeypatch.setattr("agent.nodes.execute_readonly_sql", _execute)
    return calls


def _never_called(monkeypatch, target: str) -> None:
    def _fail(*args, **kwargs):
        raise AssertionError(f"{target} must not be reached")

    monkeypatch.setattr(target, _fail)


def _node_executions(state) -> int:
    """How many nodes this run actually executed.

    `stage_timings` gets one entry per node entry (every node is wrapped
    by `agent.nodes._timed_node`), which makes it the honest count to
    compare against LangGraph's `recursion_limit`.
    """
    return len(state.get("stage_timings", []))


class TestTheStructuralBoundMatchesTheGraph:
    """`agent.graph`'s structural constants, recomputed from the compiled
    graph rather than trusted.

    This is the test `LOP-001` existed because nobody had written:
    `enforce_display_names` was added to the longest retry cycle, the
    constant describing that cycle stayed at its old value, and the only
    symptom was a 500 on the one retry path long enough to notice.
    """

    @staticmethod
    def _successors() -> dict[str, set[str]]:
        graph = build_graph().get_graph()
        successors: dict[str, set[str]] = {}
        for edge in graph.edges:
            if edge.source.startswith("__") or edge.target.startswith("__"):
                continue
            successors.setdefault(edge.source, set()).add(edge.target)
        return successors

    def test_the_longest_cycle_is_the_length_the_constant_claims(self):
        """Enumerates every simple cycle and takes the longest.

        The graph is 19 nodes with a handful of back edges, so an
        exhaustive depth-first enumeration is both fast and exact -- no
        need for an approximation that could under-report.
        """
        successors = self._successors()
        longest = 0

        def walk(start: str, node: str, path: list[str]) -> None:
            nonlocal longest
            for successor in sorted(successors.get(node, ())):
                if successor == start:
                    longest = max(longest, len(path))
                elif successor not in path:
                    walk(start, successor, [*path, successor])

        for node in sorted(successors):
            walk(node, node, [node])

        assert longest == LONGEST_RETRY_CYCLE_NODES, (
            f"the graph's longest retry cycle is now {longest} nodes, not "
            f"{LONGEST_RETRY_CYCLE_NODES}. Update "
            "agent.graph.LONGEST_RETRY_CYCLE_NODES -- an under-count "
            "silently shrinks the recursion limit until a worst-case retry "
            "sequence 500s (LOP-001)."
        )

    def test_the_pre_cycle_stretch_is_the_length_claimed(self):
        """The straight line from the entry point to the first cycle node."""
        successors = self._successors()
        pre_cycle, node = 0, "sanitize_input"
        while node != "retrieve_schema":
            pre_cycle += 1
            node = next(n for n in sorted(successors[node]))
        assert pre_cycle == PRE_CYCLE_NODES

    def test_the_post_execution_tail_is_the_length_claimed(self):
        """The chain a successful execution still has to run."""
        successors = self._successors()
        tail, node = 0, "compute_analytics"
        while True:
            tail += 1
            onward = sorted(successors.get(node, ()))
            if not onward:
                break
            node = onward[0]
        assert tail == POST_EXECUTION_NODES

    def test_the_limit_exceeds_the_worst_case_for_every_reachable_budget(self):
        """Including the adaptive bonus's own maximum.

        `agent.complexity.compute_max_retries` can raise the effective
        budget to `max_retries + complex_query_max_retry_bonus`, so the
        limit has to clear the worst case at *that* budget, not at the
        configured base.
        """
        for max_retries in range(1, 13):
            worst_case = (
                PRE_CYCLE_NODES
                + (max_retries + 1) * LONGEST_RETRY_CYCLE_NODES
                + POST_EXECUTION_NODES
            )
            assert recursion_limit_for(max_retries) > worst_case


class TestTheMissingReferenceCycleTerminates:
    """`execute_sql -> retrieve_schema`, the longest cycle in the graph
    and the one `LOP-001` broke.

    A missing-reference driver error re-routes all the way back to schema
    retrieval rather than straight to generation, because the wrong
    tables may have been retrieved in the first place.

    Both tests hand the stub a *different* non-existent column on each
    attempt. That is not incidental: a stub repeating one statement is
    stopped at attempt 2 by the no-progress guard below, which would
    leave the budget ceiling itself untested. "Wrong every time, but
    wrong in a new way every time" is the shape that actually reaches it.
    """

    #: A new non-existent column per attempt, so every attempt is a real
    #: attempt as far as the no-progress guard is concerned.
    _COLUMNS = [f"nope_{n}" for n in range(_MAX_ATTEMPTS + 2)]

    def test_an_always_missing_column_ends_in_a_clean_failure(self, monkeypatch):
        generated = _counting_generator(
            monkeypatch, [f"SELECT {column} FROM orders" for column in self._COLUMNS]
        )
        executed = _counting_executor(monkeypatch, SQLAlchemyError(_MISSING_COLUMN_ERROR))

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert generated["n"] == _MAX_ATTEMPTS
        assert executed["n"] == _MAX_ATTEMPTS
        assert state["failure_explanation"]
        outcomes = [record["outcome"] for record in state["attempt_history"]]
        assert outcomes == ["missing_reference"] * _MAX_ATTEMPTS
        assert state["attempt_history"][-1]["will_retry"] is False

    def test_the_worst_case_stays_inside_the_recursion_limit(self, monkeypatch):
        """The regression test for `LOP-001` itself.

        Before the fix this run raised `GraphRecursionError` out of
        `run_agent` -- so `POST /ask` returned HTTP 500 with no SQL, no
        explanation and no persisted turn. Measured 2026-10-28: 54 node
        executions against a limit of 68.
        """
        _counting_generator(
            monkeypatch, [f"SELECT {column} FROM orders" for column in self._COLUMNS]
        )
        _counting_executor(monkeypatch, SQLAlchemyError(_MISSING_COLUMN_ERROR))

        state = run_agent(_QUESTION)

        executions = _node_executions(state)
        assert executions == PRE_CYCLE_NODES + _MAX_ATTEMPTS * LONGEST_RETRY_CYCLE_NODES
        assert executions < recursion_limit_for(_BASE_MAX_RETRIES)
        assert state["status"] == "failed"


class TestTheValidationCycleTerminates:
    """`validate_sql -> generate_sql`, for an ordinary parse failure."""

    def test_unparseable_output_ends_after_the_budget_without_executing(self, monkeypatch):
        generated = _counting_generator(
            monkeypatch,
            # Unparseable, and differently unparseable each time -- see
            # `TestTheMissingReferenceCycleTerminates` on why a repeated
            # statement would not reach the ceiling.
            [f"this is not sql at all ((( {n}" for n in range(_MAX_ATTEMPTS + 2)],
        )
        _never_called(monkeypatch, "agent.nodes.execute_readonly_sql")

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert generated["n"] == _MAX_ATTEMPTS
        assert state["retry_count"] == _MAX_ATTEMPTS
        assert [r["outcome"] for r in state["attempt_history"]] == ["parse_error"] * _MAX_ATTEMPTS
        assert "Gave up after" in state["failure_explanation"]


class TestTheExecutionCycleTerminates:
    """`execute_sql -> generate_sql`, for a syntax error from the driver."""

    def test_an_always_failing_statement_ends_after_the_budget(self, monkeypatch):
        generated = _counting_generator(
            monkeypatch,
            [f"SELECT order_total + {n} FROM orders" for n in range(_MAX_ATTEMPTS + 2)],
        )
        executed = _counting_executor(monkeypatch, SQLAlchemyError(_SYNTAX_ERROR))

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert generated["n"] == _MAX_ATTEMPTS
        assert executed["n"] == _MAX_ATTEMPTS


class TestSafetyGatesFailClosedWithNoRetry:
    """A safety violation and a timeout are decisions, not mistakes.

    Neither is ever fed back to the model as error context to work
    around, and neither spends a second attempt -- whatever budget is
    left. CLAUDE.md 4.2 and 5.1.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM orders",
            "SELECT 1; DROP TABLE orders",
            "SELECT * INTO backup FROM orders",
            "SELECT * FROM orders FOR UPDATE",
            "SELECT * FROM information_schema.tables",
        ],
    )
    def test_a_validator_safety_violation_is_never_retried(self, monkeypatch, sql):
        generated = _counting_generator(monkeypatch, sql)
        _never_called(monkeypatch, "agent.nodes.execute_readonly_sql")

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert generated["n"] == 1, "a safety violation must not spend a second attempt"
        assert state["attempt_history"][-1]["outcome"] == "safety_violation"
        assert state["attempt_history"][-1]["will_retry"] is False
        assert "security gate" in state["failure_explanation"]

    def test_an_execution_timeout_is_never_retried(self, monkeypatch):
        generated = _counting_generator(monkeypatch, "SELECT order_total FROM orders")
        executed = _counting_executor(monkeypatch, TimeoutError("query exceeded 15s"))

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert generated["n"] == 1
        assert executed["n"] == 1, "retrying into the same timeout cannot help"
        assert state["attempt_history"][-1]["outcome"] == "timeout"
        assert state["attempt_history"][-1]["will_retry"] is False

    def test_a_safety_violation_is_not_fed_back_as_error_context(self, monkeypatch):
        """The narrower claim: nothing reaches a *second* generation call.

        Proven by the generator recording the `error_feedback` it was
        handed -- a safety violation that produced one would be the
        "security gate as retry hint" shape Phase 6 asks about.
        """
        feedback: list[str | None] = []

        def _generate(*args, **kwargs):
            feedback.append(kwargs.get("error_feedback"))
            return "DROP TABLE orders"

        monkeypatch.setattr("agent.nodes.generate_sql_from_llm", _generate)
        _never_called(monkeypatch, "agent.nodes.execute_readonly_sql")

        run_agent(_QUESTION)

        assert feedback == [None]


class TestNoProgressStopsTheLoopEarly:
    """`LOP-002`: an identical statement is not a new attempt.

    Measured before the fix (2026-10-28): a stub model returning the same
    statement every time spent all four generation calls *and* four
    database executions re-running a query already known to fail. With
    `temperature=0.0` -- which `agent.llm_client.decoding_options` sets
    for every call in this application -- that is the expected shape of a
    retry whose error feedback the model does not act on, not a corner
    case.
    """

    def test_a_repeated_statement_stops_after_the_second_attempt(self, monkeypatch):
        generated = _counting_generator(monkeypatch, "SELECT order_total FROM orders")
        executed = _counting_executor(monkeypatch, SQLAlchemyError(_SYNTAX_ERROR))

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert generated["n"] == 2, "the second identical statement ends the run"
        assert executed["n"] == 1, "and is never executed a second time"
        assert state["attempt_history"][-1]["outcome"] == "no_progress"
        assert state["attempt_history"][-1]["will_retry"] is False
        assert state["last_error_category"] == "no_progress"
        assert state["failure_explanation"]

    def test_a_changed_statement_still_gets_its_whole_budget(self, monkeypatch):
        """The guard must not shorten a run that is making progress.

        The mirror of the test above, and what makes it non-vacuous:
        different SQL on every attempt spends the full budget exactly as
        it did before the guard existed.
        """
        generated = _counting_generator(
            monkeypatch,
            [f"SELECT order_total + {n} FROM orders" for n in range(_MAX_ATTEMPTS + 2)],
        )
        executed = _counting_executor(monkeypatch, SQLAlchemyError(_SYNTAX_ERROR))

        state = run_agent(_QUESTION)

        assert generated["n"] == _MAX_ATTEMPTS
        assert executed["n"] == _MAX_ATTEMPTS
        assert [r["outcome"] for r in state["attempt_history"]] == ["syntax_error"] * _MAX_ATTEMPTS

    def test_whitespace_and_case_alone_do_not_count_as_progress(self, monkeypatch):
        """Reformatting is not a new attempt.

        A local model often returns the same statement with different
        indentation or keyword casing. Comparing raw text would read that
        as progress and spend the budget anyway, so the comparison is
        made on a normalized form.
        """
        generated = _counting_generator(
            monkeypatch,
            [
                "SELECT order_total FROM orders",
                "select   order_total\n  FROM   orders",
            ],
        )
        executed = _counting_executor(monkeypatch, SQLAlchemyError(_SYNTAX_ERROR))

        state = run_agent(_QUESTION)

        assert generated["n"] == 2
        assert executed["n"] == 1
        assert state["attempt_history"][-1]["outcome"] == "no_progress"


class TestTheRecursionLimitNoLongerEscapes:
    """The `LOP-001` catch, proven non-vacuous.

    The arithmetic in `agent.graph` is a number that can drift again. The
    catch is the guarantee that drift costs a refusal rather than an HTTP
    500, so it is tested by forcing the limit down to a value no run can
    satisfy -- the only way to reach it on a correctly-sized graph.
    """

    def test_an_exhausted_recursion_limit_becomes_a_clean_failure(self, monkeypatch):
        monkeypatch.setattr("agent.graph.recursion_limit_for", lambda _max_retries: 3)
        _counting_generator(monkeypatch, "SELECT order_total FROM orders")
        _counting_executor(monkeypatch, None)

        state = run_agent(_QUESTION)

        assert state["status"] == "failed"
        assert state["failure_explanation"] == RECURSION_LIMIT_MESSAGE
        # Nothing partial is presented: the run is reported as failed and
        # carries neither SQL nor rows.
        assert state.get("sql") is None
        assert state.get("result_rows") is None

    def test_the_message_is_an_oracle_for_nothing(self):
        """A refusal is never built from the question or the cause
        (CLAUDE.md 5.5), and naming the internal limit would only be
        noise to the reader."""
        assert "recursion" not in RECURSION_LIMIT_MESSAGE.lower()
        assert "limit" not in RECURSION_LIMIT_MESSAGE.lower()


class TestTheRetryErrorContextIsSizeBounded:
    """`LOP-007`: a retry's error context cannot grow without limit.

    The retry block is `SectionPriority.CRITICAL`, which
    `agent.context_budget` never drops and never truncates. An oversized
    CRITICAL section is reported `over_budget` and sent anyway, and
    Ollama truncates an oversized prompt from the **start** -- where the
    system prompt lives. So before this cap, one very verbose database
    error could silently take the safety and dialect rules out of the
    generation prompt: the exact failure `agent/context_budget.py` was
    written to stop, reintroduced through a channel it does not govern.
    """

    def test_an_ordinary_error_is_passed_through_byte_for_byte(self):
        """The compatibility claim, and why the benchmark is unaffected.

        Every error this pipeline has been measured producing is far
        under the cap, so a realistic retry prompt is identical to the
        unbounded one (CLAUDE.md 12 pins the generation prompt's
        accuracy numbers to its exact wording).
        """
        from agent.llm_client import _bounded_error_feedback

        error = (
            '(psycopg2.errors.UndefinedColumn) column "custname" does not exist\n'
            "The schema does not have that column, but table 'customers' has a "
            "column named 'customer_name'."
        )
        assert _bounded_error_feedback(error) == error

    def test_an_oversized_error_is_capped_at_both_ends(self):
        from agent.llm_client import MAX_ERROR_FEEDBACK_CHARS, _bounded_error_feedback

        error = "ERRORCLASS" + ("x" * 20_000) + "SUGGESTEDCOLUMN"
        bounded = _bounded_error_feedback(error)

        assert len(bounded) < MAX_ERROR_FEEDBACK_CHARS + 200
        # The head names the error class, the tail carries the hint
        # appended after it -- a plain `[:n]` would lose the second.
        assert bounded.startswith("ERRORCLASS")
        assert bounded.endswith("SUGGESTEDCOLUMN")
        assert "omitted" in bounded, "the elision must be stated, not silent"

    def test_the_assembled_retry_prompt_stays_inside_the_budget(self):
        """End to end: a 20,000-character driver error no longer pushes
        the prompt past the configured character budget.

        Asserted on the real prompt builder, because the cap is only
        worth anything at the point the prompt is assembled.
        """
        from agent.llm_client import _build_user_prompt
        from config.settings import Settings

        settings = Settings(context_budget_max_chars=32_000)
        prompt = _build_user_prompt(
            question="List orders",
            schema_context=_DDL,
            previous_sql="SELECT nope FROM orders",
            error_feedback="(psycopg2.errors.SyntaxError) " + ("y" * 20_000),
            error_category="syntax_error",
            settings=settings,
        )

        assert len(prompt) < settings.context_budget_max_chars
        assert "SELECT nope FROM orders" in prompt, "the previous SQL must survive the cap"


class TestTheNoProgressCheckFailsOpen:
    """It is an efficiency aid, so it must never be the reason a question
    cannot be answered (CLAUDE.md 4.2).

    `_reproduces_previous_attempt` resolves the sqlglot dialect, which
    reads the selected connection. A missing `selected_database` or an
    unconfigured connection must cost this one optimisation, not the
    answer -- so the check returns False and the run keeps its full
    budget, exactly as it behaved before the check existed.
    """

    def test_an_unresolvable_dialect_leaves_the_run_untouched(self, monkeypatch):
        from agent.nodes import _reproduces_previous_attempt

        monkeypatch.setattr(
            "agent.nodes.get_connection",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such connection")),
        )

        assert (
            _reproduces_previous_attempt(
                {
                    "last_generated_sql": "SELECT order_total FROM orders",
                    "selected_database": "default",
                },
                "SELECT order_total FROM orders",
                _SETTINGS,
            )
            is False
        )

    def test_a_missing_selected_database_leaves_the_run_untouched(self):
        """`_selected_db_name` raises on a `None` selection by design."""
        from agent.nodes import _reproduces_previous_attempt

        assert (
            _reproduces_previous_attempt(
                {
                    "last_generated_sql": "SELECT order_total FROM orders",
                    "selected_database": None,
                },
                "SELECT order_total FROM orders",
                _SETTINGS,
            )
            is False
        )

    def test_the_first_attempt_is_never_a_repeat(self):
        from agent.nodes import _reproduces_previous_attempt

        assert (
            _reproduces_previous_attempt(
                {"selected_database": "default"}, "SELECT order_total FROM orders", _SETTINGS
            )
            is False
        )

    def test_unparseable_text_still_compares(self, monkeypatch):
        """The `parse_error` retry path, which is where sqlglot cannot help.

        `canonical_sql_form` falls back to whitespace-collapsed text
        there, so an exact repeat is still caught -- which is the case
        that matters, since the model produced something that is not SQL
        at all.
        """
        from agent.nodes import _reproduces_previous_attempt

        monkeypatch.setattr("agent.nodes.get_connection", lambda *a, **k: _FakeConnection())
        monkeypatch.setattr("agent.nodes.get_sqlglot_dialect", lambda _db_type: "postgres")
        state = {"last_generated_sql": "not sql (((", "selected_database": "default"}

        assert _reproduces_previous_attempt(state, "not   SQL   (((", _SETTINGS) is True
        assert _reproduces_previous_attempt(state, "something else entirely", _SETTINGS) is False


class _FakeConnection:
    """Just enough of a `db.connection` config for the dialect lookup."""

    db_type = "postgresql"
