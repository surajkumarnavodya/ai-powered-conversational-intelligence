"""An unreachable database fails immediately and says so honestly.

UAT-readiness Phase 11 (`OPS-019`).

Measured 2026-10-30 against the live app with the target PostgreSQL
container stopped mid-session. A `psycopg2.OperationalError: Connection
refused` matched no classifier keyword, so it fell to
`ExecutionErrorCategory.UNKNOWN`, whose documented behaviour is "retried
like SYNTAX (the safest default)". The run therefore:

* retried the full budget, recording the identical connection error three
  times in `error_history`;
* spent a `generate_sql` model call per attempt re-deriving the same
  query -- on this hardware `generate_sql` has a measured p50 of
  **119,016 ms**, so a 5-second outage became a multi-minute one; and
* ended on the no-progress path (`LOP-002`), whose explanation is
  *"the same query came back unchanged after being told what was wrong
  with it ... Try rephrasing the question, or naming the table or column
  you mean."*

The last point is the defect worth naming. The pipeline was not wrong
about what it observed -- the query genuinely was unchanged -- but it
reported a confident diagnosis of the **user's question** for a failure
that had nothing to do with it, in the one journey (`J13`, "What happens
on a bad day?") whose stated objective is "failures are clear,
recoverable and leak nothing internal".

`TIMEOUT` already established that a failure no SQL rewrite can fix is
terminal rather than retried. `CONNECTIVITY` completes that pattern, so
this is a missing case in an existing design rather than a new policy.

One ordering trap is covered below because getting it wrong is worse than
the original bug: several drivers phrase a connectivity failure with text
containing "does not exist" (postgres `database "x" does not exist`,
mssql `SQL Server does not exist or access denied`). Classified as
`MISSING_REFERENCE`, those route the run back to `retrieve_schema` to
re-introspect a database that is not answering.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from agent.error_classification import ExecutionErrorCategory, classify_execution_error
from agent.nodes import execute_sql_node, route_after_execution
from agent.state import AgentState
from config.settings import Settings


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    """A minimal `Settings` for `execute_sql_node`, matching
    `tests/test_agent_nodes.py`'s own autouse fixture -- `tests/conftest.py`
    strips every environment variable a `Settings` field could read, so a
    node that resolves a connection needs one supplied."""
    settings = Settings(
        db_type="postgresql",
        db_host="db.example.com",
        db_name="mydb",
        db_user="reader",
        db_password=SecretStr("secret"),
        chroma_persist_dir=Path("/tmp/chroma"),
        max_retries=3,
        query_timeout_seconds=15,
        max_result_rows=1000,
    )
    monkeypatch.setattr("agent.nodes.get_settings", lambda: settings)
    return settings


#: The exact text measured on 2026-10-30, trimmed. Kept verbatim rather
#: than paraphrased: the classifier matches on driver text, so a
#: paraphrase would test the paraphrase.
_MEASURED_PSYCOPG2_REFUSAL = (
    '(psycopg2.OperationalError) connection to server at "localhost" (::1), port 55432 '
    "failed: Connection refused (0x0000274D/10061)\n\tIs the server running on that host "
    'and accepting TCP/IP connections?\nconnection to server at "localhost" (127.0.0.1), '
    "port 55432 failed: Connection refused (0x0000274D/10061)"
)


def _operational(message: str) -> OperationalError:
    """An `OperationalError` shaped the way SQLAlchemy raises one."""
    return OperationalError(statement="SELECT 1", params=None, orig=Exception(message))


class TestTheClassifierRecognisesEveryDriversWording:
    def test_the_exact_text_measured_in_the_live_drill(self):
        assert (
            classify_execution_error(SQLAlchemyError(_MEASURED_PSYCOPG2_REFUSAL))
            is ExecutionErrorCategory.CONNECTIVITY
        )

    @pytest.mark.parametrize(
        "message",
        [
            # postgres / psycopg2
            "could not connect to server: Connection refused",
            "server closed the connection unexpectedly",
            # mssql / pyodbc
            "[08001] [Microsoft][ODBC Driver 17 for SQL Server]TCP Provider: "
            "No connection could be made because the target machine actively refused it.",
            "[08S01] Communication link failure",
            "Login timeout expired",
            "A network-related or instance-specific error occurred while establishing "
            "a connection to SQL Server",
            # mysql / pymysql
            "(2003, \"Can't connect to MySQL server on 'db' (111)\")",
            "(2013, 'Lost connection to MySQL server during query')",
            # oracle
            "ORA-12541: TNS:no listener",
            "DPY-6005: cannot connect to database",
        ],
    )
    def test_each_supported_drivers_connectivity_wording(self, message):
        assert (
            classify_execution_error(_operational(message)) is ExecutionErrorCategory.CONNECTIVITY
        )

    @pytest.mark.parametrize(
        "message",
        [
            'FATAL: database "analytics" does not exist',
            "[08001] SQL Server does not exist or access denied.",
        ],
    )
    def test_connectivity_wins_over_the_does_not_exist_keyword(self, message):
        """The ordering trap. These contain `does not exist`, which is a
        `MISSING_REFERENCE` keyword; classifying them that way sends the
        run back to re-introspect an unreachable database."""
        assert (
            classify_execution_error(_operational(message)) is ExecutionErrorCategory.CONNECTIVITY
        )

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            (
                'relation "dim_prodcut" does not exist',
                ExecutionErrorCategory.MISSING_REFERENCE,
            ),
            ("Invalid column name 'SalesAmnt'.", ExecutionErrorCategory.MISSING_REFERENCE),
            ('syntax error at or near "GROPU"', ExecutionErrorCategory.SYNTAX),
            (
                "aggregate function calls cannot be nested",
                ExecutionErrorCategory.AGGREGATE_NESTING,
            ),
            ("something nobody has seen before", ExecutionErrorCategory.UNKNOWN),
        ],
    )
    def test_the_other_categories_are_unchanged(self, message, expected):
        """Over-matching would be the dangerous direction: a genuine
        missing-column error classified as connectivity would stop the
        self-correction loop that exists to fix exactly that."""
        assert classify_execution_error(_operational(message)) is expected


class TestTheRunEndsRatherThanRetrying:
    def _run(self, monkeypatch, message: str, retry_count: int = 0) -> dict:
        def _raise(sql, timeout, max_rows, engine=None):
            raise _operational(message)

        monkeypatch.setattr("agent.nodes.execute_readonly_sql", _raise)
        state: AgentState = {
            "sql": "SELECT COUNT(*) FROM dim_product",
            "retry_count": retry_count,
            "selected_database": "default",
        }
        return execute_sql_node(state)

    def test_it_fails_immediately_with_the_whole_retry_budget_unspent(self, monkeypatch):
        result = self._run(monkeypatch, _MEASURED_PSYCOPG2_REFUSAL)

        assert result["status"] == "failed"
        assert result["last_error_category"] == "connectivity"
        assert result["attempt_history"][0]["outcome"] == "connectivity"
        assert result["attempt_history"][0]["will_retry"] is False
        assert route_after_execution(result) == "failed"
        # One attempt, not the budget.
        assert result["retry_count"] == 1

    def test_the_explanation_blames_the_database_and_not_the_question(self, monkeypatch):
        """The whole point of the finding. The old path told the user to
        rephrase; this asserts both halves of the replacement, so a later
        reword cannot quietly reintroduce the wrong advice."""
        explanation = self._run(monkeypatch, _MEASURED_PSYCOPG2_REFUSAL)["failure_explanation"]

        assert "could not reach the database" in explanation.lower()
        assert "not a problem with your question" in explanation.lower()
        assert "rephras" not in explanation.lower()
        assert "naming the table" not in explanation.lower()

    def test_it_is_terminal_even_on_a_later_attempt(self, monkeypatch):
        """A database that goes away mid-run, after a genuine SQL retry,
        must still stop here rather than spend what is left."""
        result = self._run(monkeypatch, _MEASURED_PSYCOPG2_REFUSAL, retry_count=1)

        assert result["status"] == "failed"
        assert route_after_execution(result) == "failed"

    def test_the_connection_detail_is_still_redacted(self, monkeypatch):
        """`redact_secrets` runs before this branch, and must keep doing
        so: some drivers render the full connection string, password
        included, on a connection failure (CLAUDE.md 5.6)."""
        result = self._run(
            monkeypatch,
            "could not connect to server: password=sUp3rS3cret Connection refused",
        )

        joined = " ".join(
            [
                result["execution_error"],
                *result["error_history"],
                result["attempt_history"][0]["error"],
            ]
        )
        assert "sUp3rS3cret" not in joined

    def test_a_missing_column_still_retries(self, monkeypatch):
        """Non-vacuity for the whole module: if this stopped retrying too,
        the new branch would be over-matching and the tests above would
        pass for the wrong reason."""
        result = self._run(monkeypatch, 'relation "dim_prodcut" does not exist')

        assert result["status"] == "retrieving_schema"
        assert result["attempt_history"][0]["will_retry"] is True
        assert route_after_execution(result) == "retrieve_schema"
