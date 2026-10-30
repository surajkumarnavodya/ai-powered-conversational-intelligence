"""Classifies execution-time SQL failures so the retry loop can react
differently depending on *why* a query failed, instead of treating every
failure as "try again with the same generic feedback."

Mirrors the classification pattern in `db/connection.py`'s `_classify_error`
(keyword-matching on driver error text, since SQLAlchemy does not normalize
error causes consistently across psycopg2/pymysql/pyodbc/oracledb) but for a
different purpose: this one drives *retry routing* -- which node the graph
goes back to, and what hint the next generation attempt gets -- not just a
human-readable message for a connection test.
"""

from __future__ import annotations

import re
from enum import Enum


class ExecutionErrorCategory(str, Enum):
    """Why a validated, executing query failed.

    SYNTAX: the database rejected the query for a syntax/logic reason
        (bad keyword, ambiguous column, etc.) -- the schema context was
        fine, the generated SQL text wasn't. Worth retrying via
        `generate_sql` with the same schema context.
    MISSING_REFERENCE: the query referenced a table or column that does not
        exist in the target database. This can mean the LLM hallucinated a
        name, but it can just as easily mean schema retrieval surfaced the
        wrong tables in the first place -- so this category routes back to
        `retrieve_schema`, not straight to `generate_sql`.
    AGGREGATE_NESTING: the database rejected the query for calling one
        aggregate function inside another's arguments (e.g. `AVG(CASE WHEN
        ... THEN SUM(x) ELSE 0 END)`). `agent.sql_validator._find_nested_
        aggregate` already catches this shape statically before execution
        for the common case; this category is the execution-time backstop
        for whatever that AST check misses (e.g. a dialect-specific
        aggregate sqlglot doesn't classify as `exp.AggFunc`). Retried via
        `generate_sql` with a targeted rewrite hint (see
        `agent.llm_client._ERROR_CATEGORY_HINTS`), same budget as SYNTAX.
    TIMEOUT: the query exceeded `QUERY_TIMEOUT_SECONDS`. Retrying the same
        (or a similarly-shaped) expensive query is unlikely to help and
        wastes a retry budget slot -- this category fails immediately
        rather than looping.
    CONNECTIVITY: the database could not be reached at all, or the
        connection dropped. **Nothing about the generated SQL caused this
        and no rewrite of it can help**, so like TIMEOUT it fails
        immediately. Added 2026-10-30 by UAT-readiness Phase 11
        (`OPS-019`) after measuring the alternative: with the target
        database stopped mid-session, a `psycopg2.OperationalError:
        Connection refused` fell through to UNKNOWN, was retried the full
        budget, spent three `generate_sql` model calls re-deriving the
        same query, and then explained itself to the user as "the same
        query came back unchanged ... Try rephrasing the question, or
        naming the table or column you mean." On hardware where
        `generate_sql` runs at a p50 of ~119s that turns a 5-second
        outage into a multi-minute one and ends in a confident wrong
        diagnosis.
    UNKNOWN: driver error text didn't match a known pattern. Still retried
        like SYNTAX (the safest default), just without a more targeted
        prompt hint.
    """

    SYNTAX = "syntax"
    MISSING_REFERENCE = "missing_reference"
    AGGREGATE_NESTING = "aggregate_nesting"
    TIMEOUT = "timeout"
    CONNECTIVITY = "connectivity"
    UNKNOWN = "unknown"


# Keyword fragments observed across the four supported drivers' error text
# for "you referenced something that doesn't exist." Deliberately broad
# (lowercased substring match) rather than parsing driver-specific error
# codes, since sqlglot already guarantees the SQL *parses*; what's left to
# diagnose here is a semantic mismatch against the real schema.
_MISSING_REFERENCE_KEYWORDS = (
    "invalid object name",  # mssql: table/view doesn't exist
    "invalid column name",  # mssql: column doesn't exist
    "unknown column",  # mysql
    "unknown table",  # mysql
    "no such table",  # sqlite-style drivers, harmless to include
    "no such column",
    "does not exist",  # postgres: relation/column "x" does not exist
    "ora-00904",  # oracle: invalid identifier
    "ora-00942",  # oracle: table or view does not exist
)

_SYNTAX_KEYWORDS = (
    "incorrect syntax",  # mssql
    "syntax error",  # postgres / mysql
    "ambiguous column name",  # mssql -- a real column, just unqualified; the
    # fix is in the SQL text (qualify it), not the schema, so treat as syntax
    "ora-00933",  # oracle: sql command not properly ended
)

# Keyword fragments for "one aggregate function was called inside another's
# arguments" -- the execution-time backstop for whatever
# `agent.sql_validator._find_nested_aggregate`'s static AST check misses
# (see AGGREGATE_NESTING's docstring above). Checked before _SYNTAX_KEYWORDS
# since some engines phrase this as a syntax-adjacent error.
_AGGREGATE_NESTING_KEYWORDS = (
    "cannot perform an aggregate function on an expression containing an aggregate",  # mssql
    "aggregate function calls cannot be nested",  # postgres
    "invalid use of group function",  # mysql
    "ora-00978",  # oracle: nested group function without group by
)


# Keyword fragments for "the server could not be reached, or went away."
# Checked **before** `_MISSING_REFERENCE_KEYWORDS` on purpose: several
# drivers render a connectivity failure with text that contains "does not
# exist" (postgres `database "x" does not exist`, mssql `SQL Server does
# not exist or access denied`), and classifying one of those as a missing
# reference routes the run back to `retrieve_schema` to re-introspect a
# database that is not answering -- the worst available choice.
_CONNECTIVITY_KEYWORDS = (
    # postgres / psycopg2
    "connection refused",
    "could not connect to server",
    "is the server running on that host",
    "server closed the connection unexpectedly",
    "terminating connection due to administrator command",
    # mssql / pyodbc
    "tcp provider",
    "communication link failure",
    "unable to complete login process",
    "sql server does not exist or access denied",
    "login timeout expired",
    "a network-related or instance-specific error",
    # mysql / pymysql
    "can't connect to mysql server",
    "lost connection to mysql server",
    # oracle / oracledb
    "ora-12541",  # TNS: no listener
    "ora-12170",  # TNS: connect timeout occurred
    "ora-12514",  # TNS: listener does not currently know of service
    "dpy-6005",  # python-oracledb: cannot connect to database
)

#: Postgres raises `FATAL: database "x" does not exist` when the configured
#: database *name* is wrong. That shares the "does not exist" fragment with
#: a missing table or column, but it is a connection-level failure: there
#: is no schema to re-retrieve, so `MISSING_REFERENCE`'s route back to
#: `retrieve_schema` would re-introspect a database that cannot be opened.
#: A regex rather than a substring because the bare phrase `database "`
#: appears in plenty of unrelated driver text.
_DATABASE_DOES_NOT_EXIST_RE = re.compile(r'database\s+"[^"]*"\s+does not exist')


def classify_execution_error(exc: BaseException) -> ExecutionErrorCategory:
    """Best-effort classification of an execution failure.

    Args:
        exc: The exception raised by `db.execution.execute_readonly_sql`
            (a `sqlalchemy.exc.SQLAlchemyError` or a `TimeoutError`).

    Returns:
        The best-matching `ExecutionErrorCategory`. `TIMEOUT` is checked by
        exception type first (unambiguous); the rest is keyword-matched on
        `str(exc)`, which for SQLAlchemy errors includes the underlying
        driver's original error text. `CONNECTIVITY` is matched before the
        others -- see `_CONNECTIVITY_KEYWORDS` for why the order matters.
    """
    if isinstance(exc, TimeoutError):
        return ExecutionErrorCategory.TIMEOUT

    message = str(exc).lower()
    if any(keyword in message for keyword in _CONNECTIVITY_KEYWORDS) or (
        _DATABASE_DOES_NOT_EXIST_RE.search(message)
    ):
        return ExecutionErrorCategory.CONNECTIVITY
    if any(keyword in message for keyword in _MISSING_REFERENCE_KEYWORDS):
        return ExecutionErrorCategory.MISSING_REFERENCE
    if any(keyword in message for keyword in _AGGREGATE_NESTING_KEYWORDS):
        return ExecutionErrorCategory.AGGREGATE_NESTING
    if any(keyword in message for keyword in _SYNTAX_KEYWORDS):
        return ExecutionErrorCategory.SYNTAX
    return ExecutionErrorCategory.UNKNOWN
