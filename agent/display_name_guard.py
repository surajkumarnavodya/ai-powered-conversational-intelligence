"""Deterministic enforcement: a result must not be labelled by a bare key.

**Why this exists.** `agent/llm_client.py`'s generation prompt already
instructs the model to join in a readable column rather than return a
surrogate key. Local models follow that inconsistently, so "total sales
by product" comes back grouped by `ProductKey`. An instruction the model
may ignore is not a control -- CLAUDE.md 4.3: the model may propose, a
deterministic check decides. This module is that check.

**Three outcomes, and the order matters.**

1. **Repair.** For the shape this can prove safe -- a key column
   projected or grouped, reachable by exactly one *declared* foreign key
   to a dimension with a known display column -- the join is added and
   the key swapped for the name. The result is still handed to
   `agent.sql_validator`, so a repair can never widen what the SQL is
   allowed to do.
2. **Retry with precise feedback.** When the shape is more complex than
   repair can prove safe, the finding becomes error feedback for the
   next `generate_sql` attempt, naming the exact table, join column and
   display column. This reuses the graph's existing bounded retry budget
   rather than introducing a second one (CLAUDE.md 4.1).
3. **Say so.** When the key reaches a dimension that genuinely has no
   readable column, nothing is repaired and nothing is retried -- the
   limitation is reported. Inventing a label, or silently returning the
   key with no explanation, are both worse than saying "this database
   has no name for that" (the brief's rule 9; CLAUDE.md 4.4).

**Never guesses a join.** Every join this module writes comes from a
foreign key declared in live introspection (`db.display_names
.find_join_path`). Joining two columns because their names look alike is
the specific mistake that produces a query which runs, returns rows, and
is silently wrong -- the generation prompt warns about it, and this
module must not reintroduce it from the other side.

**Aggregates are left alone.** A key inside `COUNT(DISTINCT f.OrderKey)`
is a measure, not a label, and swapping it would change what the query
computes. Only key columns in a user-facing *label* position -- a bare
projection, or a GROUP BY term -- are candidates.

**The join a repair writes is a LEFT join, and that is a correctness
requirement rather than a preference.** An INNER join is the obvious
choice -- the join column is a *declared* foreign key, so "every fact row
has a matching dimension row" reads like something the constraint
guarantees. It does not. A foreign key permits NULL in the constrained
column, and a real warehouse also carries orphans (a staged load, a
removed dimension member, a constraint created `WITH NOCHECK` or
disabled). Those rows are dropped by an INNER join, and because the
repair is applied to a statement whose measure is usually an aggregate,
what the user then reads is a *smaller total* for the question they
asked, with nothing to indicate rows went missing.

Measured on this exact repair (UAT-readiness Phase 2026-10-28, `HAL-003`):
`SELECT ProductKey, SUM(SalesAmount) ... GROUP BY ProductKey` over four
fact rows, one with a NULL `ProductKey` and one orphan, returned
**2527.0 across 4 groups** before the repair and **1250.0 across 2
groups** after it. Half the measured total, presented as the answer. With
a LEFT join the total is 2527.0 again; the unmatched rows collect into
one group whose label is NULL, which is honest -- the database has no
name for them -- and is the same outcome `NO_DISPLAY_NAME_NOTICE` already
exists to describe.

A LEFT join cannot add rows either: a foreign key's target is always a
primary key or a unique constraint, so the dimension side matches at most
once and no fan-out is possible (`db.display_names.find_join_path` only
ever returns a single-column declared FK, and refuses a composite one).

**One semantic change is inherent to the feature and is not a defect:**
grouping moves from the key to the display name, so two keys sharing one
display name merge into a single group. That is what "by product name"
means, and it is the point of the repair. It changes the *grain of the
labels*, never the total.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from db.display_names import DisplayName, DisplayNameMap, find_join_path
from db.schema_introspection import TableSchemaInfo

logger = logging.getLogger(__name__)

GuardStatus = Literal["ok", "repaired", "needs_retry", "no_display_name"]

#: Caller-facing text for the "this database has no readable name" case.
#: A module constant, never built from the question, so it cannot be used
#: to probe what the schema contains (CLAUDE.md 5.5).
NO_DISPLAY_NAME_NOTICE = (
    "Some results are labelled by an identifier because the related table in this "
    "database does not have a human-readable name column for it. The identifier is "
    "shown as-is rather than guessing a label."
)


@dataclass(frozen=True)
class DisplayNameFinding:
    """What the guard decided about one generated statement.

    Attributes:
        status: `ok` (nothing to do), `repaired` (`sql` is rewritten),
            `needs_retry` (feedback for the next generation attempt), or
            `no_display_name` (an honest limitation to disclose).
        sql: The statement to carry forward -- rewritten only when
            `status == "repaired"`, otherwise the input unchanged.
        message: Retry feedback, or the caller-facing notice. Empty for
            `ok`.
        repaired_keys: Key columns that were swapped for a name.
        unresolved_keys: Key columns left as-is, for either reason.
    """

    status: GuardStatus
    sql: str
    message: str = ""
    repaired_keys: tuple[str, ...] = ()
    unresolved_keys: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.status == "repaired"


def _table_sources(select: exp.Select) -> dict[str, str]:
    """Alias (or bare name), lowercased -> real table name, for one SELECT.

    Covers the FROM entry and every JOIN. A subquery source is skipped: it
    has no schema table behind it to look a foreign key up on, so the
    guard treats it as unrepairable rather than guessing.
    """
    sources: dict[str, str] = {}
    for source in [select.args.get("from")] + list(select.args.get("joins") or []):
        if source is None:
            continue
        this = source.this if isinstance(source, exp.From | exp.Join) else None
        if not isinstance(this, exp.Table):
            continue
        real = this.name
        alias = this.alias_or_name
        sources[alias.lower()] = real
        sources.setdefault(real.lower(), real)
    return sources


def _is_inside_aggregate(node: exp.Expression) -> bool:
    """Whether `node` sits within an aggregate call.

    A key inside `COUNT(DISTINCT ...)` is a measure. Rewriting it would
    change the computation, not the label.
    """
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.AggFunc):
            return True
        parent = parent.parent
    return False


def _label_columns(select: exp.Select) -> list[exp.Column]:
    """Every column in a user-facing label position.

    Two positions qualify: a bare projection (or one that is only
    aliased), and a GROUP BY term. Both are what a reader sees as the row
    label. Anything inside an aggregate, a filter, or a join condition is
    excluded -- those are not labels.
    """
    found: list[exp.Column] = []

    for projection in select.selects:
        target = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(target, exp.Column) and not _is_inside_aggregate(target):
            found.append(target)

    group = select.args.get("group")
    if isinstance(group, exp.Group):
        for term in group.expressions:
            if isinstance(term, exp.Column):
                found.append(term)

    return found


def _find_table_info(tables: list[TableSchemaInfo], name: str) -> TableSchemaInfo | None:
    target = name.split(".")[-1].lower()
    for table in tables:
        if table.table_name.split(".")[-1].lower() == target:
            return table
    return None


def _unused_alias(select: exp.Select, preferred: str) -> str:
    """An alias for a newly joined dimension that collides with nothing."""
    taken = {a.lower() for a in _table_sources(select)}
    if preferred.lower() not in taken:
        return preferred
    for suffix in range(2, 50):
        candidate = f"{preferred}{suffix}"
        if candidate.lower() not in taken:
            return candidate
    return f"{preferred}_dn"


def _retry_feedback(key: str, display: DisplayName) -> str:
    """Precise, actionable feedback for the next generation attempt.

    Names the table, the join column and the display column explicitly,
    because "use a readable column" is the instruction the model already
    had and already ignored. Everything here is schema metadata the model
    was shown anyway, so this leaks nothing.
    """
    columns = ", ".join(display.columns)
    # Bandit's B608 heuristic false-positives here purely because the
    # words "Join"/"SELECT" appear nearby: this builds an instructional
    # sentence for the next generation attempt, not a database query.
    # Nothing in it is ever executed. Same annotation and same reasoning
    # as `agent.llm_client._system_prompt`, which builds the generation
    # prompt and trips the identical check.
    #
    # Every interpolated value is a schema identifier read from live
    # introspection (`db.display_names`), never caller text -- the model
    # was already shown all of them in this question's schema context, so
    # this discloses nothing new either.
    return (
        f"The result is labelled by the raw key '{key}', which is not readable. "  # nosec B608
        f"Join {display.table} on {key} = {display.table}.{display.key_column} and "
        f"SELECT {columns} from it instead of '{key}', grouping by that same "
        f"column (not by the key). Keep every measure unchanged."
    )


def enforce_display_names(
    sql: str,
    tables: list[TableSchemaInfo],
    display_map: DisplayNameMap,
    *,
    dialect: str = "",
) -> DisplayNameFinding:
    """Check, and where provably safe repair, the display names in `sql`.

    Args:
        sql: The generated statement.
        tables: Live introspected tables for the target database.
        display_map: `db.display_names.build_display_name_map`'s output.
        dialect: sqlglot dialect name for the target engine.

    Returns:
        A `DisplayNameFinding`. `status == "ok"` whenever there is nothing
        to do, which is the common case for a query that already selects
        names or returns no entity label at all.

    Never raises. A statement that does not parse is returned untouched
    with `ok` -- `agent.sql_validator` is the module that rejects
    unparseable SQL, and duplicating that verdict here would give one
    fact two owners.
    """
    if not sql or not sql.strip():
        return DisplayNameFinding(status="ok", sql=sql)

    try:
        statement = parse_one(sql, read=dialect or None)
    except (ParseError, Exception):  # noqa: BLE001 - see docstring
        return DisplayNameFinding(status="ok", sql=sql)

    if not isinstance(statement, exp.Select):
        # A UNION/CTE-rooted statement is legitimate but beyond what this
        # can rewrite safely. Left alone rather than half-repaired.
        return DisplayNameFinding(status="ok", sql=sql)

    sources = _table_sources(statement)
    if not sources:
        return DisplayNameFinding(status="ok", sql=sql)

    repaired: list[str] = []
    no_name: list[str] = []
    retry_messages: list[str] = []
    unresolved: list[str] = []

    for column in list(_label_columns(statement)):
        key_name = column.name
        display = display_map.for_key(key_name)

        if display is None:
            # Reaches a real dimension that has no readable column --
            # distinct from a key this map simply does not recognise,
            # which is silently left alone.
            if display_map.has_known_gap(key_name) and key_name not in no_name:
                no_name.append(key_name)
                unresolved.append(key_name)
            continue

        owner_alias = (column.table or "").lower()
        owner_real = sources.get(owner_alias) if owner_alias else None
        if owner_real is None and len(sources) == 1:
            # Unqualified column in a single-table query: unambiguous.
            owner_real = next(iter(sources.values()))
        if owner_real is None:
            unresolved.append(key_name)
            retry_messages.append(_retry_feedback(key_name, display))
            continue

        dimension_short = display.table.split(".")[-1].lower()

        if owner_real.split(".")[-1].lower() == dimension_short:
            # The dimension is already in the query -- only the selected
            # column is wrong. No join needed, which is the safest repair
            # there is.
            alias = column.table or owner_real
            _swap_column(statement, column, display, alias, dialect)
            repaired.append(key_name)
            continue

        fact = _find_table_info(tables, owner_real)
        if fact is None:
            unresolved.append(key_name)
            retry_messages.append(_retry_feedback(key_name, display))
            continue

        join_path = find_join_path(fact, key_name, display)
        if join_path is None:
            # No declared foreign key. Never improvised -- this is the
            # mistake that yields a query which runs and is silently
            # wrong.
            unresolved.append(key_name)
            retry_messages.append(_retry_feedback(key_name, display))
            continue

        existing_alias = _existing_alias_for(statement, sources, dimension_short)
        if existing_alias is None:
            alias = _unused_alias(statement, _default_alias(dimension_short))
            fact_col, dim_col = join_path
            on = f"{column.table or owner_real}.{fact_col} = {alias}.{dim_col}"
            statement = statement.join(
                f"{display.table} AS {alias}",
                on=on,
                # LEFT, never INNER -- a declared foreign key does not
                # guarantee a match (NULLs are permitted, orphans happen),
                # and an INNER join here silently drops those fact rows
                # from an aggregate. See this module's docstring for the
                # measurement. `HAL-003`.
                join_type="left",
                dialect=dialect or None,
            )
            sources[alias.lower()] = display.table
        else:
            alias = existing_alias

        _swap_column(statement, column, display, alias, dialect)
        repaired.append(key_name)

    if repaired:
        rewritten = statement.sql(dialect=dialect or None)
        logger.info(
            "[display_names] repaired %d key label(s) in generated SQL: %s",
            len(repaired),
            sorted(set(repaired)),
        )
        return DisplayNameFinding(
            status="repaired",
            sql=rewritten,
            repaired_keys=tuple(dict.fromkeys(repaired)),
            unresolved_keys=tuple(dict.fromkeys(unresolved)),
            message=(NO_DISPLAY_NAME_NOTICE if no_name else ""),
        )

    if retry_messages:
        logger.info(
            "[display_names] cannot safely repair key label(s) %s -- returning feedback",
            sorted(set(unresolved)),
        )
        return DisplayNameFinding(
            status="needs_retry",
            sql=sql,
            message=" ".join(dict.fromkeys(retry_messages)),
            unresolved_keys=tuple(dict.fromkeys(unresolved)),
        )

    if no_name:
        logger.info(
            "[display_names] key(s) %s reach a dimension with no readable name", sorted(no_name)
        )
        return DisplayNameFinding(
            status="no_display_name",
            sql=sql,
            message=NO_DISPLAY_NAME_NOTICE,
            unresolved_keys=tuple(no_name),
        )

    return DisplayNameFinding(status="ok", sql=sql)


def _default_alias(dimension_short: str) -> str:
    """A short, readable alias for a newly joined dimension."""
    core = dimension_short.removeprefix("dim").removeprefix("d_") or dimension_short
    return core[:3].lower() or "d"


def _existing_alias_for(
    select: exp.Select, sources: dict[str, str], dimension_short: str
) -> str | None:
    """The alias the dimension is already joined under, if it is.

    Re-using an existing join matters: adding a second copy of the same
    dimension would multiply rows and silently change every measure in
    the query.
    """
    for alias, real in sources.items():
        if real.split(".")[-1].lower() == dimension_short:
            return alias
    return None


def _swap_column(
    select: exp.Select,
    column: exp.Column,
    display: DisplayName,
    alias: str,
    dialect: str,
) -> None:
    """Replace every reference to one key column with the display expression.

    Replaces the key *everywhere it appears as a label* -- the projection
    and the GROUP BY -- in one pass, because replacing only the
    projection would leave the query grouped by the key and therefore
    still one row per key, which looks repaired and is not.

    The projection gains an alias (`AS Product`) so the result header
    reads as the business entity. ORDER BY is deliberately untouched: it
    usually references a measure alias, and when it does reference the
    key the engine resolves it against the new GROUP BY expression.
    """
    key_name = column.name.lower()
    expression_sql = display.select_expression(alias, dialect)
    output_alias = display.output_alias()

    for projection in list(select.selects):
        target = projection.this if isinstance(projection, exp.Alias) else projection
        if not isinstance(target, exp.Column) or target.name.lower() != key_name:
            continue
        if _is_inside_aggregate(target):
            continue
        replacement = parse_one(expression_sql, read=dialect or None)
        # Keep the model's own alias when it chose one; otherwise name the
        # column after the entity rather than leaving it unnamed.
        existing_alias = projection.alias if isinstance(projection, exp.Alias) else None
        projection.replace(exp.alias_(replacement, existing_alias or output_alias))

    group = select.args.get("group")
    if isinstance(group, exp.Group):
        for term in list(group.expressions):
            if isinstance(term, exp.Column) and term.name.lower() == key_name:
                term.replace(parse_one(expression_sql, read=dialect or None))


__all__ = [
    "NO_DISPLAY_NAME_NOTICE",
    "DisplayNameFinding",
    "GuardStatus",
    "enforce_display_names",
]
