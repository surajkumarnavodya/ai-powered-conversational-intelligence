"""Deterministic, sqlglot-based column-lineage extraction from one SQL
statement's text -- the mission's own "prefer deterministic parsing over
LLM guesses" instruction, applied to the finest-grained lineage fact this
platform produces.

**Scope, disclosed rather than silently approximated.** This module
parses exactly one level of a single top-level `SELECT`: its own
`FROM`/`JOIN` table references and its own output projections. It does
**not** recurse into a subquery nested inside `FROM`, and a column read
from a CTE is attributed to the CTE's own name (never traced further into
the CTE's underlying base tables) -- the identical "table-level-only
where full precision isn't backed" honesty `data_quality/lineage.py`'s
own module docstring already established for a different boundary. An
unqualified column reference with more than one candidate table in scope
is reported with `table=None` (genuinely ambiguous) rather than guessing
one. A statement sqlglot cannot parse, or that has no top-level `SELECT`
at all, returns `is_parseable=False` with everything else empty -- never
raises, matching this codebase's "fail open, never a reason an accuracy
aid blocks anything" posture (this module is never a security boundary;
`agent/sql_validator.py` remains the only one of those).

This is intentionally a *separate* module from `agent/sql_validator.py`:
that module's job is allow/deny (an AST-shape allowlist); this module's
job is description (what does this already-validated-or-about-to-be-
validated query actually read and compute) -- conflating the two would
blur a deterministic security gate with a descriptive accuracy aid, the
same separation `agent.plan_validator` keeps from `agent.sql_validator`
for an analogous reason.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse_one

#: Matches `agent.sql_validator.DEFAULT_DIALECT` -- `None` means sqlglot's
#: own generic dialect. A caller with a real `DB_TYPE` should pass
#: `db.connection.get_sqlglot_dialect(settings.db_type)` through, exactly
#: like every other sqlglot-parsing call site in this codebase.
DEFAULT_DIALECT: str | None = None


class ColumnLineageEntry(BaseModel):
    """One output column's own derivation -- which real source
    table/column(s) it was computed from, and via what transform, if any.

    `table` on a `source_columns` entry is `None` only when the source
    column reference was unqualified *and* more than one table was in
    scope -- a genuine, disclosed ambiguity, never a silent guess.
    """

    model_config = ConfigDict(frozen=True)

    output_column: str
    source_tables: tuple[str, ...]
    source_columns: tuple[tuple[str | None, str], ...]
    transform: str | None = None
    is_ambiguous: bool = False


class SqlLineageResult(BaseModel):
    """`extract_column_lineage`'s full result."""

    model_config = ConfigDict(frozen=True)

    is_parseable: bool
    tables_read: tuple[str, ...] = ()
    cte_names: tuple[str, ...] = ()
    column_lineage: tuple[ColumnLineageEntry, ...] = ()
    parse_error: str | None = None


def query_fingerprint(sql: str) -> str:
    """A short, stable, non-reversible identifier for one query's text --
    the identical SHA-256-prefix convention `agent.nodes._query_fingerprint`
    already establishes for trace logs, reused here (not re-derived) as
    the deterministic id a `LineageNode` for this query is keyed by, so
    the same query text always maps to the same lineage node across
    separate graph builds.
    """
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def _alias_map(select: exp.Select) -> dict[str, str]:
    """Maps every table alias (or bare name, aliased to itself) appearing
    in `FROM`/`JOIN` to its real table name."""
    mapping: dict[str, str] = {}
    for table in select.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        alias = table.alias_or_name or name
        mapping[alias] = name
    return mapping


def _cte_names(statement: exp.Expression) -> frozenset[str]:
    with_clause = statement.find(exp.With)
    if with_clause is None:
        return frozenset()
    return frozenset(cte.alias for cte in with_clause.expressions if cte.alias)


def _transform_description(projection: exp.Expression) -> str | None:
    """A short, human-readable description of the aggregate/function
    calls inside one projection, e.g. `"SUM"` or `"SUM, COALESCE"` --
    deterministically derived from the real function names sqlglot
    parsed, never an LLM's own characterization."""
    names: list[str] = []
    for func in projection.find_all(exp.Func):
        name = func.sql_name() if hasattr(func, "sql_name") else func.key.upper()
        if name and name not in names:
            names.append(name)
    return ", ".join(names) if names else None


def _resolve_source_columns(
    projection: exp.Expression, alias_map: dict[str, str], real_table_count: int
) -> tuple[tuple[str | None, str], ...]:
    resolved: list[tuple[str | None, str]] = []
    for column in projection.find_all(exp.Column):
        col_name = column.name
        if not col_name:
            continue
        table_hint = column.table
        resolved_table: str | None
        if table_hint:
            resolved_table = alias_map.get(table_hint, table_hint)
        elif real_table_count == 1:
            resolved_table = next(iter(alias_map.values()), None)
        else:
            resolved_table = None
        pair = (resolved_table, col_name)
        if pair not in resolved:
            resolved.append(pair)
    return tuple(resolved)


def extract_column_lineage(sql: str, dialect: str | None = DEFAULT_DIALECT) -> SqlLineageResult:
    """Deterministically parses `sql` and returns which real tables it
    reads and, for the outermost `SELECT`'s own output projections, which
    source table/column(s) each one derives from.

    Never raises -- an unparseable statement, or one with no top-level
    `SELECT` (e.g. a bare `VALUES` list), returns
    `SqlLineageResult(is_parseable=False, ...)`.
    """
    try:
        statement = parse_one(sql, dialect=dialect)
    except Exception as exc:  # noqa: BLE001 - never block on a parse failure
        return SqlLineageResult(is_parseable=False, parse_error=str(exc)[:200])

    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        return SqlLineageResult(is_parseable=False, parse_error="no SELECT statement found")

    cte_names = _cte_names(statement)
    alias_map = _alias_map(select)
    real_tables = tuple(name for name in dict.fromkeys(alias_map.values()) if name not in cte_names)
    real_table_count = len(real_tables)

    column_lineage: list[ColumnLineageEntry] = []
    for projection in select.expressions:
        output_name = projection.alias_or_name
        if not output_name:
            continue
        source_columns = _resolve_source_columns(projection, alias_map, real_table_count)
        source_tables = tuple(
            dict.fromkeys(table for table, _ in source_columns if table is not None)
        )
        is_ambiguous = any(table is None for table, _ in source_columns)
        column_lineage.append(
            ColumnLineageEntry(
                output_column=output_name,
                source_tables=source_tables,
                source_columns=source_columns,
                transform=_transform_description(projection),
                is_ambiguous=is_ambiguous,
            )
        )

    return SqlLineageResult(
        is_parseable=True,
        tables_read=real_tables,
        cte_names=tuple(cte_names),
        column_lineage=tuple(column_lineage),
    )


__all__ = [
    "DEFAULT_DIALECT",
    "ColumnLineageEntry",
    "SqlLineageResult",
    "query_fingerprint",
    "extract_column_lineage",
]
