"""Validates LLM-generated SQL before it is ever allowed to execute.

Security model: the LLM's output is treated as untrusted input, exactly like
a request from an untrusted client, regardless of the fact that "we" wrote
the prompt. Validation is allowlist-based on the *parsed statement type*
(via sqlglot), not a regex/keyword blocklist -- a blocklist can always be
bypassed by a syntax variant it didn't anticipate (comments, casing, string
tricks); an allowlist on the AST node type cannot, because there is no way
to construct an INSERT/DROP/ATTACH/etc. statement that parses to an
`exp.Select`/`exp.Union` root node.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, model_validator
from sqlglot import exp
from sqlglot.errors import SqlglotError

logger = logging.getLogger(__name__)

# Read-only, single-query statement shapes. exp.Union/Except/Intersect cover
# compound `SELECT ... UNION SELECT ...` queries, which are still read-only.
_ALLOWED_ROOT_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
)

# Write/DDL-shaped node types, checked *anywhere* in the parsed tree -- not
# just at the root (see _ALLOWED_ROOT_TYPES above). This closes a real
# bypass class: several engines (Postgres chief among them) support
# data-modifying CTEs -- `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM
# x` -- whose *root* node is an ordinary exp.Select even though it deletes
# real rows. A root-type-only check accepts that statement; this full-tree
# walk does not, regardless of how deeply the write is nested (a CTE, a
# subquery, ...). exp.Command is included because it's sqlglot's fallback
# for a fragment it couldn't parse into anything more specific (e.g. an
# engine-specific EXEC/CALL form) -- a legitimate read-only query never
# produces one anywhere in its tree, so its presence is only ever a sign the
# statement isn't what it appears to be.
_DISALLOWED_NESTED_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
)

# Known-dangerous function/procedure/table-valued-function names, callable
# from inside an otherwise ordinary, single-statement SELECT -- so the
# statement-type allowlist above (by design) never sees them as anything
# other than "a SELECT." Unlike that allowlist, this list is a **denylist**:
# it can only ever be as complete as what's enumerated here, and an
# engine-specific dangerous function not on this list is a real, standing
# residual risk (documented in SECURITY.md, the same honesty this project
# already applies to `agent/input_guard.py`'s own regex layer).
#   - postgresql: pg_sleep/pg_read_file/pg_read_binary_file/pg_ls_dir/
#     pg_stat_file (DoS / local file & directory disclosure); lo_import/
#     lo_export (large-object file I/O); dblink* (cross-database/network
#     connections).
#   - mysql: sleep/benchmark (DoS); load_file (local file disclosure).
#   - mssql: openrowset/openquery/opendatasource (remote/linked-server
#     query execution -- SSRF/lateral-pivot capable); xp_cmdshell/
#     xp_dirtree/xp_fileexist (OS command execution / filesystem probing);
#     sp_configure/sp_oacreate (server reconfiguration / OLE automation).
#   - oracle: utl_http/utl_tcp/utl_smtp (SSRF / network egress); utl_file
#     (filesystem I/O); utl_inaddr (DNS/hostname resolution, exfiltration-
#     capable); dbms_lock (sleep-based DoS); dbms_scheduler/dbms_java
#     (arbitrary job/code execution).
#
# 2026-10-25, UAT-readiness Phase 2 (SEC-004/SEC-005). Two further classes
# were found accepted by an adversarial sweep over this validator:
#   - **Nested-SQL evaluators.** `query_to_xml('<sql>', ...)` (postgres) and
#     `DBMS_XMLGEN.GETXML('<sql>')` (oracle) take a *query string* and
#     execute it. The inner string is never parsed by sqlglot, so every
#     check in `validate_sql` -- the statement-type allowlist, the
#     system-catalog gate, this very denylist -- applies to the outer call
#     and not to what actually runs. Measured: `SELECT query_to_xml('SELECT
#     * FROM pg_authid', true, true, '')` was accepted, while the plain
#     `SELECT ... FROM pg_authid` it wraps is refused. These are the one
#     entry class here that is a *gate bypass* rather than an extra
#     capability, which is why they are listed first.
#   - **Transaction-log, trace and audit readers.** mssql `fn_dblog` /
#     `fn_dump_dblog` read the transaction log and so can surface column
#     values from recent modifications -- including rows a restricted-column
#     gate protects in a normal SELECT; `fn_trace_gettable` and
#     `fn_get_audit_file` read trace/audit files from disk;
#     `fn_virtualfilestats` discloses storage paths. `sys.fn_dump_dblog` was
#     already refused, but only incidentally, by the `sys.` schema check --
#     the bare, unqualified `fn_dblog(...)` form was accepted.
#   - postgres `pg_logdir_ls` lists the log directory (filesystem
#     disclosure), completing the `pg_ls_dir`/`pg_stat_file` set above.
_DANGEROUS_FUNCTION_NAMES: frozenset[str] = frozenset(
    {
        # nested-SQL evaluators -- see the note above; these bypass the
        # statement allowlist and the system-catalog gate entirely
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "dbms_xmlgen",
        "dbms_sql",
        # transaction-log / trace / audit / log-directory readers
        "fn_dblog",
        "fn_dump_dblog",
        "fn_trace_gettable",
        "fn_get_audit_file",
        "fn_virtualfilestats",
        "pg_logdir_ls",
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_connect",
        "dblink_exec",
        "sleep",
        "benchmark",
        "load_file",
        "openrowset",
        "openquery",
        "opendatasource",
        "xp_cmdshell",
        "xp_dirtree",
        "xp_fileexist",
        "sp_configure",
        "sp_oacreate",
        "utl_http",
        "utl_tcp",
        "utl_smtp",
        "utl_file",
        "utl_inaddr",
        "dbms_lock",
        "dbms_scheduler",
        "dbms_java",
    }
)

# Raw-text fallback for the same names, requiring the name (as a whole word)
# to be immediately followed by either `(` or a `.qualifier(` chain -- e.g.
# `pg_sleep(` and `UTL_HTTP.REQUEST(` both match, but a column merely *named*
# "sleep_duration" or "utl_http_helper" cannot (no word boundary lands there).
# This exists only to catch a dialect-qualified call where sqlglot's AST
# stores the package qualifier (e.g. "UTL_HTTP") on a different node than the
# immediate call name ("REQUEST") the tree walk below inspects directly.
_DANGEROUS_FUNCTION_RE = re.compile(
    r"\b("
    + "|".join(re.escape(name) for name in sorted(_DANGEROUS_FUNCTION_NAMES))
    + r")\b(?:\.\w+)*\s*\(",
    re.IGNORECASE,
)

# 2026 Phase 3 security review: schema/database names that hold system
# catalog metadata (table/column definitions, user/role grants, server
# configuration) rather than application data, across every supported
# dialect. A SELECT naming one of these directly is never a legitimate
# answer to a business question this app exists to answer -- and unlike an
# ordinary application table, it can reveal internal schema structure or
# other users'/roles' grants even under a genuinely read-only DB role (see
# db.connection.check_write_privileges's docstring: DB-role read-only-ness
# bounds *writes*, it says nothing about what a SELECT is allowed to
# *read*). Matched against exp.Table's schema (`.db`) qualifier,
# case-insensitively -- this also catches an mssql cross-database
# reference like `master.sys.database_principals` (schema="sys").
_SYSTEM_CATALOG_SCHEMAS: frozenset[str] = frozenset(
    {
        "information_schema",  # ANSI-standard -- postgresql, mysql, mssql
        "pg_catalog",  # postgresql
        "mysql",  # mysql's own internal schema
        "performance_schema",  # mysql
        "sys",  # mysql 5.7+'s sys schema, and mssql's sys schema
    }
)

# Oracle exposes system catalog content as unqualified/pseudo-schema views
# rather than through a distinctly-named schema the way the other three
# dialects do -- ALL_*/DBA_*/USER_* data-dictionary views, conventionally
# queried bare (`SELECT * FROM DBA_USERS`), not schema-qualified.
#
# Deliberately matched as {scope prefix} + {a curated, real suffix}, never a
# blind prefix -- "user_"/"dba_"/"all_" are ordinary English words a real
# business schema legitimately prefixes tables with (`user_accounts`,
# `user_preferences`, `dba_notes`), so a bare `name.startswith("user_")`
# would false-positive on exactly the kind of everyday table this validator
# must not reject (see the "preserve existing functionality" principle this
# whole audit pass operates under). Every suffix below is a real, standard
# Oracle static data-dictionary view -- non-exhaustive (like
# `_DANGEROUS_FUNCTION_NAMES` above, honestly documented as such, not
# hidden), but chosen to cover the object types most useful for
# reconnaissance (tables/columns/privileges/users/roles/source code) while
# keeping the false-positive rate at effectively zero for ordinary schemas.
_ORACLE_CATALOG_SCOPE_PREFIXES: tuple[str, ...] = ("all_", "dba_", "user_")
_ORACLE_CATALOG_SUFFIXES: frozenset[str] = frozenset(
    {
        "tables",
        "tab_columns",
        "tab_cols",
        "tab_comments",
        "col_comments",
        "views",
        "indexes",
        "ind_columns",
        "constraints",
        "cons_columns",
        "objects",
        "source",
        "sequences",
        "triggers",
        "procedures",
        "arguments",
        "tab_privs",
        "col_privs",
        "role_privs",
        "sys_privs",
        "synonyms",
        "users",
        "roles",
        "segments",
        "tablespaces",
        "data_files",
        "directories",
    }
)
# Oracle's dynamic performance views (`V$SESSION`, `GV$SQL`, ...) -- the
# `$` makes this prefix unambiguous enough (unlike "user_"/"dba_"/"all_")
# that a plain prefix match is safe: no ordinary business table is
# realistically named starting with `v$`/`gv$`.
_ORACLE_DYNAMIC_VIEW_PREFIXES: tuple[str, ...] = ("v$", "gv$")


# SQL Server keeps a set of pre-2005 compatibility views that resolve
# *unqualified* from inside any database (`SELECT * FROM sysobjects`, not
# `sys.sysobjects`), so the `_SYSTEM_CATALOG_SCHEMAS` check above -- which
# matches on the schema qualifier -- never sees them. That is the same
# bare-name hole already closed for postgresql (`pg_`) and Oracle
# (`DBA_*`/`ALL_*`/`USER_*`) below and above respectively; mssql was the
# remaining dialect without it, which matters because these views expose
# exactly the reconnaissance surface the qualified `sys` block exists to
# deny -- object inventory, column inventory, logins, and, via
# `syscomments`, the *source text* of views and stored procedures.
#
# A curated set, never a blind `name.startswith("sys")`: that would reject
# ordinary application tables like `system_settings`, `sysadmin_audit` or
# `system_user_roles`, which is precisely the false-positive trap the
# Oracle comment above talks itself out of. Non-exhaustive by the same
# honest standard as the Oracle list -- these are the ones worth having.
_MSSQL_LEGACY_CATALOG_VIEWS: frozenset[str] = frozenset(
    {
        "sysobjects",
        "syscolumns",
        "syscomments",
        "sysindexes",
        "sysindexkeys",
        "systypes",
        "sysusers",
        "sysconstraints",
        "sysforeignkeys",
        "sysdatabases",
        "syslogins",
        "sysservers",
        "sysprocesses",
        "sysfiles",
        "sysaltfiles",
        "syspermissions",
        "sysmembers",
    }
)


def _is_oracle_catalog_view_name(name: str) -> bool:
    if any(name.startswith(prefix) for prefix in _ORACLE_DYNAMIC_VIEW_PREFIXES):
        return True
    for prefix in _ORACLE_CATALOG_SCOPE_PREFIXES:
        if name.startswith(prefix) and name[len(prefix) :] in _ORACLE_CATALOG_SUFFIXES:
            return True
    return False


def _find_system_catalog_reference(statement: exp.Expression) -> exp.Table | None:
    """Finds the first `exp.Table` reference (anywhere in the tree, same
    full-tree-walk posture as `_DISALLOWED_NESTED_TYPES` above) that names a
    system catalog/data-dictionary object rather than application data.
    """
    for table in statement.find_all(exp.Table):
        schema = (table.db or "").lower()
        if schema in _SYSTEM_CATALOG_SCHEMAS:
            return table
        name = (table.name or "").lower()
        if _is_oracle_catalog_view_name(name):
            return table
        # mssql's pre-2005 compatibility views, which resolve unqualified
        # from any database -- see _MSSQL_LEGACY_CATALOG_VIEWS.
        if name in _MSSQL_LEGACY_CATALOG_VIEWS:
            return table
        # postgresql's pg_catalog schema is always on the default
        # search_path, so its views are conventionally queried unqualified
        # (`SELECT * FROM pg_roles`, not `pg_catalog.pg_roles`) -- a bare-
        # name check is needed alongside the schema-qualifier check above.
        # Unlike Oracle's "user_"/"dba_"/"all_" (ordinary English words a
        # real schema might legitimately prefix a table with), "pg_" is a
        # reserved-by-convention prefix PostgreSQL itself warns application
        # schemas away from, so a plain prefix match here carries
        # negligible false-positive risk.
        if name.startswith("pg_"):
            return table
    return None


# None is sqlglot's own "generic/standard SQL" dialect -- used as the default
# here so this module has no dependency on which real database is configured.
# Callers on the actual query path (agent/nodes.py, api/main.py) resolve and
# pass the real dialect via `db.connection.get_sqlglot_dialect(settings.db_type)`.
DEFAULT_DIALECT: str | None = None


# Distinguishes *why* validation failed, so callers (agent/nodes.py) can
# react differently: a violation type in `SAFETY_VIOLATION_TYPES` means the
# LLM produced a statement this app must never execute (a non-SELECT, a
# stacked query, a SELECT that creates a table as a side effect, a write
# operation embedded inside an otherwise read-only query, or a call to a
# known-dangerous function) -- that is a security-gate failure, not a
# mistake worth coaching the model through, so the agent fails closed
# immediately rather than retrying. "empty" and "parse_error" are ordinary
# correctness mistakes (the LLM produced malformed or no text) and are safe
# to retry with error feedback.
ViolationType = Literal[
    "empty",
    "parse_error",
    "multiple_statements",
    "disallowed_statement",
    "select_into",
    "embedded_write",
    "dangerous_function",
    "system_catalog_access",
    "nested_aggregate",
    "unsafe_query_option",
    "unparseable_depth",
    "row_lock",
]

SAFETY_VIOLATION_TYPES: frozenset[str] = frozenset(
    {
        "multiple_statements",
        "disallowed_statement",
        "select_into",
        "embedded_write",
        "dangerous_function",
        "system_catalog_access",
        "unsafe_query_option",
        # "unparseable_depth" is a safety violation rather than a retryable
        # parse mistake on purpose: it means no check in this module was
        # able to run at all, so nothing is *known* about the statement.
        # Fail closed (CLAUDE.md §4.2), and never retry -- the same text
        # would exhaust the stack again.
        "unparseable_depth",
        "row_lock",
    }
)

# 2026-09-25: found via the "Resource exhaustion/agent loop" security
# benchmark category, whose entire purpose is to probe for exactly this
# shape of gap -- see docs/security/PROMPT_INJECTION_BENCHMARK_GAP_REPORT.md.
# A query-level session hint the model has no legitimate reason to ever
# set on this app's behalf: `OPTION (MAXRECURSION n)` overrides MSSQL's own
# default 100-level recursive-CTE safety limit (0 means *unlimited*). A
# prompt like "use recursive queries without a limit" reliably gets a local
# model to add this hint to an otherwise syntactically ordinary SELECT --
# sqlglot parses it as a ordinary `exp.QueryOption` on the `Select` node
# (still `exp.Select`-rooted, no disallowed nested type, no dangerous
# function, no system-catalog reference), so nothing above this check
# would have caught it. Rejected outright regardless of the requested
# value -- there is no legitimate business question this app answers that
# needs to override the engine's own recursion safety net, so this isn't a
# capped-allowlist the way `_DANGEROUS_FUNCTION_NAMES` is, it's a flat
# denial. Query-hint syntax is itself MSSQL-specific
# (`OPTION (...)`/`QueryOption`), but the check below matches by node type,
# not by dialect, so it applies uniformly regardless of which dialect
# `validate_sql` was called with.
#
# 2026-10-25, UAT-readiness Phase 2 (SEC-005): the adversarial sweep found
# two sibling options accepted by the same check. `QUERYTRACEON n` enables
# an undocumented engine trace flag for the query, and `MAXDOP 0` lifts the
# parallelism cap to every core on the server -- a resource-exhaustion
# lever reachable from a statement that is read-only in every other
# respect. Both are flat denials for the same reason MAXRECURSION is: no
# business question this app answers needs to override the engine's own
# limits. `RECOMPILE`, `FAST n` and the join/aggregation hints are left
# alone deliberately -- they change plan selection, not a safety bound.
_UNSAFE_QUERY_OPTION_NAMES: frozenset[str] = frozenset({"maxrecursion", "querytraceon", "maxdop"})

# 2026-10-25, UAT-readiness Phase 2 (SEC-003). Lock-taking and
# lock-escalating constructs, reachable from a statement whose root is an
# ordinary `exp.Select` and which therefore passes every check above:
#   - `SELECT ... FOR UPDATE` / `FOR SHARE` / `FOR NO KEY UPDATE` (all four
#     supported engines) parse to `exp.Lock` and take real row locks that
#     block other transactions for the life of this one.
#   - MSSQL table hints `WITH (TABLOCKX)`, `WITH (UPDLOCK, HOLDLOCK)` and
#     friends parse to `exp.WithTableHint` and escalate to table-level or
#     held locks -- an availability attack on a shared database issued from
#     a query this app calls read-only.
# Both are refused regardless of the configured dialect, by node type.
#
# `NOLOCK` / `READUNCOMMITTED` are deliberately **not** here: they *reduce*
# isolation rather than escalate locking, they are ordinary idiom in MSSQL
# reporting queries, and refusing them would reject correct SQL. They are a
# data-consistency trade-off, not a safety bound, which is the line this
# module draws everywhere else.
_LOCK_ESCALATING_TABLE_HINTS: frozenset[str] = frozenset(
    {
        "tablock",
        "tablockx",
        "updlock",
        "xlock",
        "holdlock",
        "paglock",
        "rowlock",
        "serializable",
        "repeatableread",
    }
)


def _find_unsafe_query_option(statement: exp.Expression) -> exp.Expression | None:
    for option in statement.find_all(exp.QueryOption):
        name_node = option.this
        name = (getattr(name_node, "this", None) or "").lower()
        if name in _UNSAFE_QUERY_OPTION_NAMES:
            return option
    return None


def _find_lock_construct(statement: exp.Expression) -> exp.Expression | None:
    """Returns the first lock-taking/escalating node anywhere in the tree.

    `find_all` rather than reading `statement.args["locks"]`, because a
    `FOR UPDATE` inside a subquery or a CTE sits on that inner `Select` and
    not on the root -- the same reason the write/DDL check above walks the
    whole tree.
    """
    lock = next(statement.find_all(exp.Lock), None)
    if lock is not None:
        return lock
    for hint in statement.find_all(exp.WithTableHint):
        for item in hint.expressions:
            name = (getattr(item, "name", "") or "").lower()
            if name in _LOCK_ESCALATING_TABLE_HINTS:
                return hint
    return None


class ValidationResult(BaseModel):
    """Outcome of validating a candidate SQL string.

    A `pydantic.BaseModel` rather than a plain dataclass specifically for
    the `model_validator` below: `is_valid`, `error`/`violation_type`, and
    `normalized_sql` are not three independent fields in practice -- they
    move together in exactly two fixed shapes (valid-with-SQL,
    invalid-with-a-reason) -- and this is a genuine invariant worth a
    machine-checked guarantee rather than just a docstring's word for it,
    since every caller (`agent.nodes.validate_sql_node`,
    `agent.llm_client`'s retry-hint lookup) branches on `is_valid` and then
    trusts the other fields' presence/absence without re-checking.

    Attributes:
        is_valid: Whether the SQL passed all checks.
        error: Human-readable reason for rejection (None if valid). This text
            is fed back to the LLM verbatim on retry, so it's written to be
            actionable ("X is not allowed" rather than a stack trace).
        normalized_sql: The re-serialized SQL from the parsed AST (None if
            invalid). Re-serializing (rather than passing the original
            string through) ensures what gets executed is exactly what was
            parsed and validated -- no gap between "what we checked" and
            "what we run".
        violation_type: Machine-readable reason for rejection (None if
            valid) -- see `SAFETY_VIOLATION_TYPES` for which values mean
            "fail closed, do not retry" versus "ordinary retry-able mistake."
    """

    model_config = ConfigDict(frozen=True)

    is_valid: bool
    error: str | None = None
    normalized_sql: str | None = None
    violation_type: ViolationType | None = None

    @model_validator(mode="after")
    def _check_shape_matches_validity(self) -> ValidationResult:
        if self.is_valid:
            if self.error is not None or self.violation_type is not None:
                raise ValueError("A valid ValidationResult must not carry error/violation_type.")
        else:
            if self.error is None or self.violation_type is None:
                raise ValueError(
                    "An invalid ValidationResult must carry both error and violation_type."
                )
            if self.normalized_sql is not None:
                raise ValueError("An invalid ValidationResult must not carry normalized_sql.")
        return self


def _cte_reference_table_ids(statement: exp.Expression) -> frozenset[int]:
    """Identifies which `exp.Table` nodes in `statement` are pure references
    to a CTE this same statement defines (`WITH x AS (...) SELECT ... FROM
    x`), as opposed to a real database table -- sqlglot represents both
    identically as `exp.Table`, since it does no semantic name resolution.

    2026-09-30 (`05_SQL_SERVER_DIALECT_VALIDATION_CONTRACT.md`): a real bug,
    found by actually running T-SQL CTEs through this module rather than
    assuming sqlglot support meant correctness. `qualify_table_schema`
    walked every `exp.Table` node and schema-qualified any without a `db`
    arg -- including a CTE reference in the main query, rewriting `FROM
    RecentOrders` into `employee.RecentOrders`, which doesn't exist. The
    same blind spot made `find_unexpected_table_references` flag the CTE
    name as "unexpected" and `references_multiple_tables` report `True`
    for a single-real-table CTE query. This function is the one shared fix
    all four call sites (this one plus `find_restricted_column_references`)
    now use instead of a bare `find_all(exp.Table)`.

    Processes CTEs in definition order: within CTE N's own body, a table
    reference is only ever a "virtual" CTE reference if its name matches an
    EARLIER CTE (T-SQL/ANSI SQL forbids forward/self references in a
    non-recursive CTE) -- this correctly handles one CTE building on
    another (`WITH a AS (...), b AS (SELECT * FROM a) SELECT * FROM b`).
    Outside every CTE body (the main query, or a later CTE's own body), a
    table reference matching *any* defined CTE alias is a virtual
    reference. A self-referential name collision inside a CTE's own body
    (the CTE alias happens to also be a real table's name) is deliberately
    left as a real-table reference -- the conservative direction, since
    treating it as a CTE reference risks hiding a real table access, while
    the reverse only risks an unnecessary schema-qualification/false-
    positive-detection on a vanishingly rare naming collision.

    Returns the `id()` of every `exp.Table` node identified this way -- an
    identity set, not a name set, since the same table *name* can validly
    appear at two different positions with two different meanings (a CTE
    alias outside its own body vs. a same-named real table referenced
    inside it).
    """
    with_clause = statement.args.get("with")
    if with_clause is None:
        return frozenset()
    ctes = with_clause.expressions
    seen_aliases: set[str] = set()
    excluded_ids: set[int] = set()
    cte_body_ids: set[int] = set()
    for cte in ctes:
        for table in cte.this.find_all(exp.Table):
            cte_body_ids.add(id(table))
            name = (table.name or "").lower()
            if name in seen_aliases:
                excluded_ids.add(id(table))
        if cte.alias:
            seen_aliases.add(cte.alias.lower())
    for table in statement.find_all(exp.Table):
        if id(table) in cte_body_ids:
            continue
        if (table.name or "").lower() in seen_aliases:
            excluded_ids.add(id(table))
    return frozenset(excluded_ids)


def _find_nested_aggregate(statement: exp.Expression) -> tuple[exp.AggFunc, exp.AggFunc] | None:
    """Finds an aggregate function called inside another aggregate function's own arguments.

    Every supported engine (mssql, postgres, mysql, oracle) rejects this
    shape at execution time -- e.g. `AVG(CASE WHEN ... THEN SUM(x) ELSE 0
    END)` -- but a weak local LLM asked for something like "average
    year-over-year growth" reaches for exactly this pattern often enough
    that it's worth catching before a DB round trip (see
    `agent.error_classification.ExecutionErrorCategory.AGGREGATE_NESTING`
    for the execution-time backstop when this static check has a false
    negative, e.g. a dialect-specific aggregate sqlglot doesn't classify as
    `exp.AggFunc`).

    Walks each aggregate's own argument subtree only (not the whole
    statement), and does not descend into a nested `exp.Select` -- a
    subquery is its own scope, so `SUM(x) FROM (SELECT AVG(y) AS x FROM t)`
    is legitimate and must not be flagged.

    Args:
        statement: A parsed, single-statement SQL AST (already confirmed
            SELECT-shaped by the caller).

    Returns:
        `(outer, inner)` -- the outer aggregate and the aggregate nested
        inside it -- for the first match found, or None if there is none.
    """
    for outer in statement.find_all(exp.AggFunc):
        nested = next(
            (
                node
                for node in outer.walk(prune=lambda n: isinstance(n, exp.Select))
                if node is not outer and isinstance(node, exp.AggFunc)
            ),
            None,
        )
        if nested is not None:
            return outer, nested
    return None


def validate_sql(sql: str, dialect: str | None = DEFAULT_DIALECT) -> ValidationResult:
    """Parses `sql` and checks it against the SELECT-only allowlist.

    Rejects:
        - Empty input.
        - SQL that fails to parse.
        - SQL nested deeply enough to exhaust the interpreter stack inside
          sqlglot's recursive-descent parser. Fails closed with
          `violation_type="unparseable_depth"` (a *safety* violation, see
          `SAFETY_VIOLATION_TYPES`) rather than letting the `RecursionError`
          escape, because when the parse cannot complete, none of the checks
          below have run and nothing at all is known about the statement.
        - A lock-taking or lock-escalating construct -- `FOR UPDATE`,
          `FOR SHARE`, or an MSSQL `WITH (TABLOCKX)`-style table hint --
          which is read-only in its projection but still blocks other
          transactions.
        - More than one statement (blocks `SELECT 1; DROP TABLE x`).
        - Any root statement type other than SELECT/UNION/EXCEPT/INTERSECT
          (blocks INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/ATTACH/COPY/etc.).
        - `SELECT ... INTO <table>`, which would create a table as a
          side effect despite being SELECT-shaped.
        - A write/DDL operation embedded *anywhere* in the tree, not just at
          the root -- e.g. a data-modifying CTE
          (`WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`), whose
          root is an ordinary SELECT even though it deletes real rows.
        - A reference to a system catalog/data-dictionary object (e.g.
          `information_schema.columns`, `sys.database_principals`,
          `pg_catalog.pg_roles`, Oracle's `DBA_USERS`/`V$SESSION`) rather
          than application data -- see `_SYSTEM_CATALOG_SCHEMAS`/
          `_ORACLE_CATALOG_PREFIXES` for the full list. This can reveal
          internal schema structure or other users'/roles' grants even
          under a genuinely read-only DB role, since DB-role read-only-ness
          bounds writes, not what a SELECT can read.
        - A call to a known-dangerous function/procedure (e.g. `pg_sleep`,
          `xp_cmdshell`, `OPENQUERY`, `UTL_HTTP.REQUEST`) -- see
          `_DANGEROUS_FUNCTION_NAMES` for the full list and why each is
          there. This is a denylist, not an allowlist, and is documented as
          such (not exhaustive) in SECURITY.md.
        - An aggregate function nested inside another aggregate function's
          own arguments (e.g. `AVG(CASE WHEN ... THEN SUM(x) ELSE 0 END)`)
          -- every supported engine rejects this at execution time; see
          `_find_nested_aggregate`. Retryable (`violation_type=
          "nested_aggregate"`, not in `SAFETY_VIOLATION_TYPES`), not a
          security-gate failure.

    Args:
        sql: Raw SQL text, as produced by the LLM (already stripped of any
            markdown code fences by the caller).
        dialect: sqlglot dialect to parse with. Defaults to sqlglot's
            generic/standard SQL dialect; pass the real one (via
            `db.connection.get_sqlglot_dialect(settings.db_type)`) on the
            actual query path.

    Returns:
        A `ValidationResult`.
    """
    try:
        return _validate_sql_checks(sql, dialect)
    except RecursionError:
        # 2026-10-25, UAT-readiness Phase 2 (SEC-002). sqlglot parses by
        # recursive descent, and walks/regenerates the tree the same way, so
        # nesting depth in the *input* maps directly onto interpreter stack
        # depth. `RecursionError` is a `RuntimeError`, not a `SqlglotError`,
        # so the inner handler never saw it and it escaped this module --
        # turning the security boundary into a crash instead of a refusal.
        # Measured before this guard existed: a **90-byte** payload,
        # `SELECT ` followed by 41 nested parentheses, raised; so did `ABS(`
        # x37, `NOT ` x81, and `(SELECT * FROM ` x89. Reachable from every
        # caller-supplied-SQL route, none of which caps the text length
        # (`api/schemas.py::ExecuteRequest.sql` is `min_length=1` with no
        # maximum).
        #
        # Wrapped around the whole check sequence, not just the parse: the
        # tree walks (`statement.walk()`, `find_all`) and the final
        # `statement.sql(dialect=...)` regeneration recurse over the same
        # depth, so a payload that parses just under the limit can still
        # raise further down.
        #
        # Caught rather than bounded by a pre-parse length/depth scan because
        # the raising shapes share no one measurable feature: `NOT NOT ...
        # 1=1` has no brackets at all, so a bracket-depth counter would miss
        # it, while a length cap would reject legitimate long queries. The
        # exception is the accurate signal, and catching it unwinds the
        # frames it consumed.
        #
        # Classified as a safety violation, not a retryable parse error:
        # when the analysis does not complete, the statement is *unanalyzed*
        # rather than merely malformed, and re-running the same text would
        # raise again.
        logger.warning(
            "[sql_validator] refused SQL that exhausted the parser's recursion "
            "limit (%d characters) -- failing closed, nothing about the "
            "statement could be checked",
            len(sql),
        )
        return ValidationResult(
            is_valid=False,
            error=(
                "The SQL is nested too deeply to be analyzed safely and is refused "
                "for that reason -- not because of anything it was found to contain. "
                "Rewrite it with less nesting (fewer parentheses, subqueries or "
                "chained NOTs)."
            ),
            violation_type="unparseable_depth",
        )


def _validate_sql_checks(sql: str, dialect: str | None) -> ValidationResult:
    """The check sequence itself. See `validate_sql`, which is its only caller.

    Split out purely so one `except RecursionError` in `validate_sql` can
    cover the parse, every tree walk, and the final regeneration -- all three
    recurse over the input's nesting depth.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return ValidationResult(
            is_valid=False, error="Generated SQL is empty.", violation_type="empty"
        )

    try:
        statements = [s for s in sqlglot.parse(stripped, read=dialect) if s is not None]
    except SqlglotError as exc:
        # Deliberately the broad `SqlglotError` base, not just `ParseError`:
        # sqlglot's *tokenizer* can fail before parsing even starts (e.g. an
        # unterminated quoted string in malformed LLM output), raising a
        # sibling `TokenError` that a narrower `except ParseError` would not
        # catch -- confirmed live during benchmark authoring, where a
        # malformed model response crashed the whole agent run with an
        # unhandled `TokenError` instead of failing closed here as an
        # ordinary "parse_error" (retryable, see SAFETY_VIOLATION_TYPES).
        # Untrusted LLM output failing to tokenize is exactly the same kind
        # of "ordinary correctness mistake" as failing to parse -- it must
        # never be the reason the whole request crashes instead of
        # retrying/failing cleanly.
        logger.debug("SQL failed to tokenize/parse: %s", exc)
        return ValidationResult(
            is_valid=False, error=f"SQL failed to parse: {exc}", violation_type="parse_error"
        )

    if len(statements) == 0:
        return ValidationResult(
            is_valid=False, error="Generated SQL is empty.", violation_type="empty"
        )
    if len(statements) > 1:
        return ValidationResult(
            is_valid=False,
            error="Only a single SQL statement is allowed; found multiple statements.",
            violation_type="multiple_statements",
        )

    statement = statements[0]
    if not isinstance(statement, _ALLOWED_ROOT_TYPES):
        return ValidationResult(
            is_valid=False,
            error=(
                f"Only SELECT statements are allowed; got a "
                f"{type(statement).__name__} statement instead."
            ),
            violation_type="disallowed_statement",
        )

    if statement.args.get("into") is not None:
        return ValidationResult(
            is_valid=False,
            error="'SELECT ... INTO <table>' is not allowed (it creates a table).",
            violation_type="select_into",
        )

    nested_write = next(
        (node for node in statement.walk() if isinstance(node, _DISALLOWED_NESTED_TYPES)), None
    )
    if nested_write is not None:
        return ValidationResult(
            is_valid=False,
            error=(
                f"The statement contains a {type(nested_write).__name__} operation "
                "embedded inside an otherwise read-only query (e.g. a data-modifying "
                "CTE). Write and DDL operations are never allowed, regardless of the "
                "outer statement shape."
            ),
            violation_type="embedded_write",
        )

    dangerous_name: str | None = next(
        (
            node.name.lower()
            for node in statement.walk()
            if isinstance(node, exp.Func) and (node.name or "").lower() in _DANGEROUS_FUNCTION_NAMES
        ),
        None,
    )
    if dangerous_name is None:
        text_match = _DANGEROUS_FUNCTION_RE.search(stripped)
        if text_match:
            dangerous_name = text_match.group(1).lower()
    if dangerous_name is not None:
        return ValidationResult(
            is_valid=False,
            error=(
                f"The function/procedure '{dangerous_name}' is not allowed -- it can "
                "access the filesystem, network, or server configuration rather than "
                "just querying data."
            ),
            violation_type="dangerous_function",
        )

    catalog_table = _find_system_catalog_reference(statement)
    if catalog_table is not None:
        return ValidationResult(
            is_valid=False,
            error=(
                f"'{catalog_table.sql(dialect=dialect)}' refers to a system catalog/"
                "data-dictionary object, not application data -- this is not allowed, "
                "regardless of what information it's used to look up."
            ),
            violation_type="system_catalog_access",
        )

    nested_aggregate = _find_nested_aggregate(statement)
    if nested_aggregate is not None:
        outer, inner = nested_aggregate
        return ValidationResult(
            is_valid=False,
            error=(
                f"{outer.sql_name().upper()}(...) contains a nested "
                f"{inner.sql_name().upper()}(...) call -- an aggregate function cannot "
                "be called inside another aggregate function's arguments. Rewrite this "
                "using a CTE that pre-aggregates first, or a window function "
                "(e.g. SUM(...) OVER (PARTITION BY ...), LAG(...) OVER (...)) instead "
                "of nesting aggregate calls."
            ),
            violation_type="nested_aggregate",
        )

    unsafe_option = _find_unsafe_query_option(statement)
    if unsafe_option is not None:
        return ValidationResult(
            is_valid=False,
            error=(
                f"'{unsafe_option.sql(dialect=dialect)}' is not allowed -- this app "
                "never overrides the database engine's own safety limits (e.g. a "
                "recursion cap), regardless of what the query is trying to compute."
            ),
            violation_type="unsafe_query_option",
        )

    lock_construct = _find_lock_construct(statement)
    if lock_construct is not None:
        return ValidationResult(
            is_valid=False,
            error=(
                f"'{lock_construct.sql(dialect=dialect)}' is not allowed -- it takes or "
                "escalates database locks, which blocks other transactions even though "
                "the query itself only reads. This app's queries never lock."
            ),
            violation_type="row_lock",
        )

    return ValidationResult(
        is_valid=True,
        normalized_sql=statement.sql(dialect=dialect),
    )


def find_unexpected_table_references(
    sql: str, known_tables: set[str], dialect: str | None = DEFAULT_DIALECT
) -> list[str]:
    """Flags tables `sql` references that were never part of the retrieved schema context.

    This is a *detection* signal, not a new gate -- the SELECT-only
    allowlist above plus the read-only database connection already bound
    what any generated SQL can actually do, regardless of which tables it
    names. What this catches is a different thing: one concrete symptom of
    a successful prompt injection via poisoned schema/sampled-value content
    (see `db/value_sampling.py`'s security note) is the model suddenly
    referencing a table or column it was never shown, because something in
    the data it was given told it to. A table appearing here doesn't by
    itself prove an injection happened -- it's also what a plain
    hallucinated table name looks like -- but either way it's worth logging
    distinctly so a pattern of it is inspectable (see `agent.nodes.
    validate_sql_node`, the caller, for how this is logged).

    Args:
        sql: Already-validated SQL (only called after `validate_sql`
            confirms it parses and is SELECT-shaped; a parse failure here
            is treated as "nothing to flag," not re-raised -- that
            diagnosis is `validate_sql`'s job).
        known_tables: Table names that were actually part of the retrieved
            schema context for this attempt (`AgentState["schema_tables"]`),
            compared case-insensitively.
        dialect: sqlglot dialect to parse with.

    Returns:
        Sorted list of table names referenced in `sql` that aren't in
        `known_tables` -- empty if none (the common case) or if `sql`
        doesn't parse.
    """
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return []

    cte_refs = _cte_reference_table_ids(statement)
    referenced = {
        table.name
        for table in statement.find_all(exp.Table)
        if table.name and id(table) not in cte_refs
    }
    known_lower = {name.lower() for name in known_tables}
    return sorted(name for name in referenced if name.lower() not in known_lower)


def summarize_sql(sql: str, dialect: str | None = DEFAULT_DIALECT) -> tuple[str, list[str]] | None:
    """The statement's shape and the real tables it references.

    Two presentation facts the dashboard's SQL panel needs and had no
    accurate source for: the *statement type*, and the tables the SQL
    actually references -- not the tables retrieval happened to offer.
    Those differ routinely: schema retrieval returns the top-k candidate
    tables, and a generated query commonly uses one of them, so labelling
    the retrieved set as "tables referenced" would be wrong.

    Derived from the same parse and the same CTE-aware filtering
    `find_unexpected_table_references` above already performs (a CTE's own
    name is a reference, not a base table), rather than a second
    extraction. Nothing here validates or gates: `validate_sql` remains
    the only thing that decides whether SQL may run.

    Args:
        sql: SQL to describe. Usually already validated, but a parse
            failure is a normal outcome here, not an error.
        dialect: sqlglot dialect to parse with.

    Returns:
        `(statement_type, referenced_tables)` -- the root node's SQL
        keyword (e.g. `"SELECT"`, `"UNION"`) and the sorted distinct base
        tables. `None` when `sql` does not parse, so a caller shows
        nothing rather than guessing.
    """
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return None
    if statement is None:
        return None

    cte_refs = _cte_reference_table_ids(statement)
    tables = sorted(
        {
            table.name
            for table in statement.find_all(exp.Table)
            if table.name and id(table) not in cte_refs
        }
    )
    # `key` is sqlglot's own lowercase node name ("select", "union",
    # "except", "intersect") -- read from the parsed AST rather than
    # matched against the SQL text, so a leading comment or a CTE prefix
    # cannot mislabel it.
    return statement.key.upper(), tables


#: Caps on what `describe_sql` returns. A description is a presentation
#: aid, not a dump: an adversarial or machine-generated statement can
#: carry hundreds of predicates, and neither the response model nor the
#: panel should grow without bound because of one query.
_MAX_SHAPE_ITEMS = 12
_MAX_SHAPE_ITEM_CHARS = 80


class SqlJoinFact(BaseModel):
    """One join, as the parser actually read it.

    `kind` is sqlglot's own side/kind for the node (`INNER`, `LEFT`,
    `RIGHT`, `FULL`, `CROSS`) -- never inferred from the SQL text, and
    never guessed when sqlglot reports neither (a bare `JOIN` is `INNER`
    by SQL's own default, which is a language fact rather than an
    assumption about this query).
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    table: str


class SqlShape(BaseModel):
    """Structural facts about a statement, read from its parsed AST.

    **What this is for.** `summarize_sql` above answers "what kind of
    statement is this, and which tables does it touch". This answers the
    next question a reader asks -- how are those tables joined, what is
    filtered, what is aggregated, at what grain -- so the dashboard can
    describe a query without anyone having to read SQL.

    **Why it is parsed, not generated.** Every field here is a fact
    already present in the AST `validate_sql` parsed. Asking a model to
    describe SQL instead would add a round trip, and could hallucinate a
    join that is not there -- which, rendered beside a real validator
    verdict, would read as authoritative. A parse cannot be wrong about
    the statement it parsed.

    **Three honesty boundaries this type deliberately keeps:**

    1. **No business meaning.** `group_by` is the grain the statement
       declares, not "sales by region". Naming what a column *means* is
       the semantic layer's job (`semantic/`), and inventing it here
       would be a second, unreviewed owner for it.
    2. **No execution outcome.** Nothing here says whether the query ran,
       returned rows, or was truncated. Those are owned by the status
       line and the result gate.
    3. **No row limit.** Deliberately absent even though the AST has one.
       `agent.nodes.validate_sql_node` writes `enforce_row_limit`'s
       output back to `state["sql"]`, so the `TOP`/`LIMIT` in the SQL a
       caller sees is usually **this platform's governance cap, not the
       query's own intent**. Reporting it as query behaviour would
       present a control as authorship. The cap is already disclosed by
       the result gate, which is its one owner (`CLAUDE.md` 4.8).

    Gates nothing. `validate_sql` remains the only thing that decides
    whether SQL may run.
    """

    model_config = ConfigDict(frozen=True)

    joins: tuple[SqlJoinFact, ...] = ()
    #: Columns appearing in a `WHERE` anywhere in the statement --
    #: including inside a CTE or subquery, because that is routinely
    #: where the real filter lives in generated SQL. Column names only:
    #: the compared *values* are deliberately not reported, since a
    #: predicate can carry data (`WHERE email = '...'`) and this string
    #: is rendered in a UI and may be logged.
    filter_columns: tuple[str, ...] = ()
    aggregates: tuple[str, ...] = ()
    #: Statement-level `GROUP BY` -- the grain of the result.
    group_by: tuple[str, ...] = ()
    #: Statement-level `ORDER BY` only. Read from the statement's own
    #: `order` argument rather than by searching the tree, because a
    #: window function carries its own `ORDER BY` inside `OVER (...)`
    #: and a search finds that one first on a statement that has no
    #: ordering of its own -- reporting a window's internal ordering as
    #: the result's ordering would be wrong.
    order_by: tuple[str, ...] = ()
    is_distinct: bool = False


def _render_shape_items(nodes: object) -> tuple[str, ...]:
    """Renders AST nodes back to SQL text, de-duplicated and bounded.

    De-duplication matters: an aggregate repeated in `SELECT` and
    `HAVING` is one aggregate to a reader but two nodes to the parser.
    Order is preserved so the rendering follows the statement.
    """
    seen: list[str] = []
    for node in nodes or ():  # type: ignore[union-attr]
        try:
            text = node.sql().strip()
        # nosec B112 - `describe_sql` is a presentation aid (CLAUDE.md 4.2: an
        # accuracy aid fails open). Skipping one unrenderable node degrades the
        # human-readable shape summary; it is not on the validation path, which
        # is `validate_sql` and refuses rather than continues.
        except Exception:  # noqa: BLE001 - a presentation aid never raises
            continue  # nosec B112
        if not text:
            continue
        text = text[:_MAX_SHAPE_ITEM_CHARS]
        if text not in seen:
            seen.append(text)
        if len(seen) >= _MAX_SHAPE_ITEMS:
            break
    return tuple(seen)


def describe_sql(sql: str, dialect: str | None = DEFAULT_DIALECT) -> SqlShape | None:
    """The statement's structural shape, for display beside the SQL.

    A companion to `summarize_sql`, deliberately a separate function
    rather than a wider return from it: that one has two live callers
    (`api.main._sql_summary_out` and `api.artifacts`) whose tuple
    unpacking would break on a changed signature, and one of them wants
    only the table list.

    Args:
        sql: SQL to describe. Usually already validated, but a parse
            failure is a normal outcome here, not an error.
        dialect: sqlglot dialect to parse with.

    Returns:
        A `SqlShape`, or `None` when `sql` is empty or does not parse --
        so a caller shows nothing rather than guessing.
    """
    if not sql or not sql.strip():
        return None
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return None
    if statement is None:
        return None

    try:
        joins: list[SqlJoinFact] = []
        for node in statement.find_all(exp.Join):
            target = node.this
            # A join onto a subquery or derived table has no table name;
            # its alias is the honest label, and an unnamed one is
            # skipped rather than labelled with a guess.
            name = getattr(target, "name", "") or getattr(target, "alias", "")
            if not name:
                continue
            # `side` is LEFT/RIGHT/FULL, `kind` is INNER/CROSS/OUTER;
            # sqlglot leaves both empty for a bare `JOIN`, which SQL
            # itself defines as INNER.
            kind = (node.side or node.kind or "INNER").upper()
            fact = SqlJoinFact(kind=kind, table=name)
            if fact not in joins:
                joins.append(fact)
            if len(joins) >= _MAX_SHAPE_ITEMS:
                break

        # Every WHERE in the statement, not just the top-level one: in
        # generated SQL the meaningful predicate is routinely inside a
        # CTE, and reporting "no filters" for such a query would be
        # actively misleading.
        filter_columns: list[str] = []
        for where in statement.find_all(exp.Where):
            for column in where.find_all(exp.Column):
                if column.name and column.name not in filter_columns:
                    filter_columns.append(column.name)
                if len(filter_columns) >= _MAX_SHAPE_ITEMS:
                    break

        aggregates = _render_shape_items(list(statement.find_all(exp.AggFunc)))

        group = statement.args.get("group")
        group_by = _render_shape_items(getattr(group, "expressions", ()) if group else ())

        order = statement.args.get("order")
        order_by = _render_shape_items(getattr(order, "expressions", ()) if order else ())

        return SqlShape(
            joins=tuple(joins),
            filter_columns=tuple(filter_columns[:_MAX_SHAPE_ITEMS]),
            aggregates=aggregates,
            group_by=group_by,
            order_by=order_by,
            # Only meaningful for a SELECT. sqlglot also sets `distinct`
            # on a set operation, where it encodes `UNION` rather than
            # `UNION ALL` -- a different fact about de-duplicating two
            # result sets, not a `SELECT DISTINCT`. Reporting that as
            # "distinct" would mislabel every plain UNION.
            is_distinct=isinstance(statement, exp.Select)
            and statement.args.get("distinct") is not None,
        )
    except Exception:  # noqa: BLE001 - a presentation aid never fails the answer
        # An AST shape this walk did not anticipate must degrade to "no
        # description", never to a partial one that a reader would take
        # as complete.
        return None


def references_multiple_tables(sql: str, dialect: str | None = DEFAULT_DIALECT) -> bool:
    """Whether `sql` references two or more distinct tables (i.e. involves a join).

    Used by `agent.nodes.execute_sql_node` to decide whether a successful
    but zero-row result is worth flagging as low-confidence: a single-table
    zero-row result (e.g. "how many orders in 2050") is almost always a
    legitimate answer, but a multi-table zero-row result is also the
    observable symptom of a join that matched columns from unrelated key
    spaces (see `agent.llm_client._system_prompt`'s join-correctness rules)
    -- a query that runs without error but is silently wrong.

    Args:
        sql: Already-validated SQL.
        dialect: sqlglot dialect to parse with.

    Returns:
        True if 2+ distinct table names are referenced. False if `sql`
        doesn't parse (diagnosing that is `validate_sql`'s job, not this
        function's).
    """
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return False

    cte_refs = _cte_reference_table_ids(statement)
    referenced = {
        table.name.lower()
        for table in statement.find_all(exp.Table)
        if table.name and id(table) not in cte_refs
    }
    return len(referenced) >= 2


def _has_wildcard_projection(statement: exp.Expression) -> bool:
    """True if any `SELECT` clause in `statement` projects a bare `*` or a
    qualified `table.*` wildcard, rather than only explicit named columns.

    `find_restricted_column_references` below can only recognize a
    restricted column by name -- it has no way to know which columns a
    wildcard actually expands to without a live schema lookup. Without this
    check, `SELECT * FROM DimCustomer` never mentions `EmailAddress` by
    name and silently bypassed the restricted-column gate entirely (2026
    Phase 1 security review finding AUTHZ-01). `COUNT(*)` is not a
    projection wildcard (it's a function argument, `exp.Count(this=exp.Star())`,
    never a direct member of `Select.expressions`) and correctly does not
    match here.
    """
    for select in statement.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                return True
            if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
                return True
    return False


def find_restricted_column_references(
    sql: str,
    restricted_columns: set[tuple[str, str]],
    known_tables: set[str],
    dialect: str | None = DEFAULT_DIALECT,
) -> list[tuple[str, str]]:
    """Flags (table, column) pairs `sql` selects that are classified "restricted".

    Enforces `config/sensitive_columns.yaml`'s policy (see
    `config.sensitive_columns`, and `docs/GOVERNANCE.md`'s "Data
    classification policy") -- unlike `find_unexpected_table_references`
    above (a detection signal only), a hit here **is** a new gate: the
    caller (`agent.nodes.validate_sql_node`) treats it exactly like a
    validation failure and does not let the query execute.

    Args:
        sql: Already-validated SQL.
        restricted_columns: (table_name, column_name) pairs classified
            "restricted" -- pre-filtered by the caller from
            `config.sensitive_columns.load_sensitive_columns()`'s full
            tier map (this function doesn't know about "internal", only
            "restricted").
        known_tables: Unused by the matching logic itself (see the "2026
            Phase 1" note below) -- kept as a parameter so
            `agent.nodes.validate_sql_node`'s existing call site (which
            also feeds the same set into `find_unexpected_table_references`
            for its own, separate detection-only purpose) doesn't need to
            change shape.
        dialect: sqlglot dialect to parse with.

    Returns:
        Sorted list of (table_name, column_name) pairs referenced in `sql`
        that are classified restricted -- empty if none (the overwhelming
        common case while the classification file is unpopulated), or if
        `sql` doesn't parse.

        Matching is name-based (a restricted column name appearing
        anywhere in the statement, whose owning table is actually
        referenced in the statement's own `FROM`/`JOIN` clauses), not full
        table-qualification resolution -- deliberately conservative in the
        safe direction: a false positive only costs a retry (the model
        gets a chance to drop the column and answer with what remains),
        while a false negative would let a restricted column through
        undetected. A wildcard projection (`SELECT *`/`SELECT t.*`) flags
        every restricted column of every table the statement references,
        since a named-column match can't see through a wildcard.

        2026 Phase 1 security review (finding AUTHZ-02): this used to
        additionally require the restricted table to be part of
        `known_tables` -- the caller's *attempt-scoped, RAG-retrieved*
        schema subset for this one question, not the full configured
        schema. A restricted table referenced through a join/subquery/
        golden-example-influenced generation that the retriever simply
        didn't surface for this particular attempt was therefore never
        checked at all. Matching is now grounded in the statement's own
        parsed `FROM`/`JOIN` tables instead, which is strictly more
        accurate (ground truth from the SQL actually being validated,
        not a proxy for "tables retrieval happened to consider") and
        closes that gap without changing behavior for any query that
        doesn't reference a restricted table at all.
    """
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return []

    cte_refs = _cte_reference_table_ids(statement)
    referenced_tables_lower = {
        table.name.lower()
        for table in statement.find_all(exp.Table)
        if table.name and id(table) not in cte_refs
    }
    referenced_columns_lower = {
        column.name.lower() for column in statement.find_all(exp.Column) if column.name
    }
    has_wildcard = _has_wildcard_projection(statement)

    flagged = {
        (table_name, column_name)
        for table_name, column_name in restricted_columns
        if table_name.lower() in referenced_tables_lower
        and (has_wildcard or column_name.lower() in referenced_columns_lower)
    }
    return sorted(flagged)


def enforce_row_limit(sql: str, max_rows: int, dialect: str | None = DEFAULT_DIALECT) -> str:
    """Clamps or adds a `LIMIT` clause so a query can never return more than `max_rows`.

    Must only be called on SQL that has already passed `validate_sql` --
    it assumes a single SELECT/UNION/EXCEPT/INTERSECT statement.

    Args:
        sql: Already-validated SQL text.
        max_rows: Row cap to enforce (e.g. `Settings.max_result_rows`).
        dialect: sqlglot dialect to parse/render with.

    Returns:
        SQL text with a LIMIT clause no greater than `max_rows`.
    """
    statement = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(statement, exp.Query):
        # Precondition violation: callers must only pass SQL that already
        # passed validate_sql, which guarantees a Select/Union/Except/Intersect
        # (all exp.Query subclasses, the ones with a .limit() method).
        raise TypeError(
            f"enforce_row_limit expects already-validated SELECT-shaped SQL, "
            f"got a {type(statement).__name__} statement instead."
        )

    current_limit: int | None = None
    limit_clause = statement.args.get("limit")
    literal: exp.Expression | None = None
    if isinstance(limit_clause, exp.Fetch):
        # ANSI SQL:2008 / T-SQL `OFFSET n ROWS FETCH NEXT m ROWS ONLY`
        # pagination -- sqlglot stores this shape under the same "limit" arg
        # key as an ordinary LIMIT/TOP clause, but as an `exp.Fetch` node
        # whose row count lives in `count`, not `expression`. Missing this
        # case (2026-09-30, 05_SQL_SERVER_DIALECT_VALIDATION_CONTRACT.md)
        # meant an explicit `FETCH NEXT 20 ROWS ONLY` was never recognized
        # as an existing cap, silently widening it to `max_rows` (e.g. 20 ->
        # 1000) every time -- not a security issue (never exceeds the
        # ceiling), but a real correctness regression this fixes.
        literal = limit_clause.args.get("count")
    elif limit_clause is not None:
        literal = limit_clause.expression
    if isinstance(literal, exp.Literal) and literal.is_number:
        try:
            current_limit = int(literal.this)
        except (TypeError, ValueError):
            current_limit = None

    if current_limit is None or current_limit > max_rows:
        statement = statement.limit(max_rows)

    return statement.sql(dialect=dialect)


def strip_row_limit(sql: str, dialect: str | None = DEFAULT_DIALECT) -> str:
    """Removes a `LIMIT`/`TOP` clause -- for cost *estimation* only, never for execution.

    A row cap dramatically changes what a query optimizer estimates: a
    `TOP 1000` (or `LIMIT 1000`) lets the engine stop scanning/joining as
    soon as 1000 rows are found, so a plan's reported row estimate collapses
    to ~1000 regardless of how expensive satisfying the query's actual
    WHERE/JOIN logic would be -- verified against a real accidental cross
    join on AdventureWorksDW2025, where adding `TOP 1000` made SQL Server
    report ~1,000 estimated rows for a query that, unlimited, estimates
    over 1.1 *billion*. `agent.nodes.estimate_query_cost_node` calls this
    to see the query's true shape before the cap is applied, but the SQL
    that's actually *executed* always keeps the real limit (`state["sql"]`
    is never replaced by this function's output) -- this exists purely to
    give the cost-estimation step an accurate picture, not to change what
    runs.

    Args:
        sql: Already row-limited SQL (the output of `enforce_row_limit`).
        dialect: sqlglot dialect to parse/render with.

    Returns:
        The same SQL with its `LIMIT`/`TOP` clause removed.
    """
    statement = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(statement, exp.Query):
        raise TypeError(
            f"strip_row_limit expects already-validated SELECT-shaped SQL, "
            f"got a {type(statement).__name__} statement instead."
        )
    statement.set("limit", None)
    return statement.sql(dialect=dialect)


def qualify_table_schema(
    sql: str, schema: str | None, dialect: str | None = DEFAULT_DIALECT
) -> str:
    """Schema-qualifies every unqualified table reference, for execution only.

    An engine resolves an unqualified table name (`FROM Employee`) against
    the *connecting user's own default schema* -- which has no guaranteed
    relationship to `Settings.databases[i].db_schema` (`DB_<NAME>_SCHEMA`),
    the schema introspection was restricted to. A database whose tables all
    live under a non-default schema (e.g. `employee.Employee`, with the
    connection's actual default schema being `dbo`) will otherwise fail
    every query with "Invalid object name", even though the table genuinely
    exists -- the model has no way to know to qualify it, because nothing
    it's shown (the DDL, `table_descriptions.yaml`) ever mentions a schema;
    both are deliberately keyed by bare table name (see CLAUDE.md's
    multi-database note on why).

    Applied only to a throwaway copy of the SQL text passed to
    `db.execution.execute_readonly_sql`, in `execute_sql_node`/`POST /execute`'s
    "Confirm and Run" -- `state["sql"]` itself (and everything that reads it:
    the schema-anomaly and restricted-column checks above, `table_descriptions`
    lookups, the UI's editable SQL box, insight generation) is never touched,
    since all of those are keyed by the same bare table names the model was
    shown and must keep matching them.

    A table reference that's already schema-qualified (by the model, or
    because it's genuinely in a different schema on purpose) is left alone --
    this only fills in a *missing* schema, never overrides one.

    A reference to a CTE this same statement defines (`WITH x AS (...)
    SELECT ... FROM x`) is also left alone -- see
    `_cte_reference_table_ids` for why: a CTE alias is not a schema object,
    and schema-qualifying it (`employee.x`) breaks the query outright.

    Args:
        sql: Already-validated, row-limited SQL text (the output of
            `enforce_row_limit`).
        schema: The selected database's configured schema
            (`DatabaseConnectionConfig.db_schema`), or None. A no-op when
            None -- the common case, since most configured databases either
            have no non-default schema at all or (dialects with a session
            search path, e.g. Postgres) resolve it without this.
        dialect: sqlglot dialect to parse/render with.

    Returns:
        SQL text with every previously-unqualified table reference
        prefixed with `schema`, or the input unchanged if `schema` is None.
    """
    if not schema:
        return sql
    statement = sqlglot.parse_one(sql, read=dialect)
    cte_refs = _cte_reference_table_ids(statement)
    for table in statement.find_all(exp.Table):
        if id(table) in cte_refs:
            continue
        if not table.args.get("db"):
            table.set("db", exp.to_identifier(schema))
    return statement.sql(dialect=dialect)
