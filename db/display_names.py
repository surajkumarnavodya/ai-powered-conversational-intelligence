"""Which column is an entity's human-readable name, and how to reach it.

**The gap this closes.** `agent/llm_client.py`'s generation prompt already
tells the model to join in a readable column rather than return a bare
surrogate key. That instruction is advisory, and local models follow it
inconsistently -- so "total sales by product" comes back grouped by
`ProductKey`. Nothing downstream could repair that, because nothing in
this codebase knew which column *is* the readable name for `ProductKey`:
`db.schema_introspection.TableSchemaInfo` records columns, primary keys
and foreign keys, but not fact-vs-dimension classification and not a
canonical display column.

This module supplies that missing fact, so a deterministic check can act
on it (`agent/display_name_guard.py`) instead of hoping the model
complied. CLAUDE.md 4.3: the model may propose; a deterministic check
decides.

**Live introspection stays the source of truth.** Every relationship used
here is a declared foreign key read from the live database
(`TableSchemaInfo.foreign_keys`). Nothing is inferred from a name
resembling another name -- that is exactly the "joins a fact straight to
a table two hops away on similar-looking key columns" mistake the
generation prompt already warns about, and it produces queries that run
and return silently wrong results. `db/relationship_inference.py` exists
for *suggesting* undeclared relationships to a human; it is deliberately
not consulted here, because a repaired query must never rest on a guess.

**Two layers, explicit beats inferred.** A human entry in
`config/display_names.yaml` always wins. Without one, the heuristics
below pick a display column from the real column list. The heuristics are
deliberately conservative: when nothing clearly qualifies, this module
returns `None` and the caller reports an honest limitation rather than
inventing a name (the brief's rule 10, and CLAUDE.md 4.4).

**Scope.** This answers "what is the readable name for this key, and
which declared FK reaches it". It does not rewrite SQL, decide chart
axes, or format labels -- `agent/display_name_guard.py`,
`agent/result_charting.py` and `db/column_formatting.py` own those, each
reading this one source so the fact has a single owner (CLAUDE.md 4.8).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from config.display_names import load_display_name_overrides
from db.schema_introspection import TableSchemaInfo

logger = logging.getLogger(__name__)

#: Column-name patterns that mark a human-readable display column, best
#: first. Ordered, because a dimension often has several: `DimProduct`
#: carries `EnglishProductName`, `ProductAlternateKey` and `Description`,
#: and the first is the one a user means.
#:
#: Matched against the *whole* column name, case-insensitively. A bare
#: `Name` is matched last rather than first: a table with both
#: `EnglishProductName` and `Name` is likelier to mean the specific one,
#: and schemas that have only `Name` still match on the final pattern.
_DISPLAY_NAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Localised/qualified names first -- AdventureWorks-style schemas
        # name the user-facing column `EnglishProductName`, and picking
        # `ProductAlternateKey` instead would be a different kind of key.
        r"^english\w*name$",
        r"^\w+name$",
        r"^name$",
        r"^\w*title$",
        r"^\w*label$",
        r"^\w*description$",
    )
)

#: Columns never treated as a display name even when they match a pattern
#: above. `...AlternateKey` is the trap: it ends in "Key" so it reads as a
#: key, but some schemas also carry `...NameAlternateKey`-shaped columns
#: that would match `^\w+name$`.
_DISPLAY_NAME_EXCLUSIONS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (r"\w*alternatekey$", r"\w*key$", r"\w*id$", r"\w*guid$", r"\w*code$")
)

#: Types that can hold a readable name. The core-name fallback below
#: consults this so it cannot pick a numeric or date column merely
#: because the column is named after its own table (`OrderDate` on a
#: `DimOrder`, say).
_TEXT_TYPE_RE = re.compile(r"char|text|string|varchar|nvarchar|clob", re.IGNORECASE)

#: Prefixes/suffixes stripped to get a dimension's "core" entity name:
#: `DimSalesTerritory` -> `SalesTerritory`.
_TABLE_AFFIX_RE = re.compile(r"^(dim|d)_?|_?(dim|dimension|lookup)$", re.IGNORECASE)

#: Ordered first/last-name column pairs for the composite case. A person
#: dimension usually has no single name column, and returning only
#: `LastName` for "sales by employee" is a worse answer than the full
#: name. The separator and NULL handling are fixed here rather than left
#: to the model (the brief's "define the exact concatenation rule").
_COMPOSITE_NAME_PAIRS: tuple[tuple[str, str], ...] = (
    ("firstname", "lastname"),
    ("first_name", "last_name"),
    ("givenname", "surname"),
    ("forename", "surname"),
)

#: Dimensions that must NEVER get a display name, matched on the table's
#: core name.
#:
#: A date dimension is the important one, and real-schema testing is what
#: found it. `DimDate` carries `EnglishMonthName`, which matches the
#: name patterns below and would make "sales by order date" silently
#: become "sales by month name" -- collapsing every year together and
#: changing what the query answers. That is precisely the silent
#: wrongness this module exists to avoid, arriving from the inside.
#:
#: A date key needs no lookup anyway: the model already joins the date
#: dimension and selects the grain it wants (`CalendarYear`,
#: `FullDateAlternateKey`), and those are readable on their own.
#:
#: System/catalog tables are excluded for a duller reason: they are not
#: business entities, and `sysdiagrams` resolving to `sysdiagrams.name`
#: is noise in the map.
#:
#: An explicit `config/display_names.yaml` entry still wins over this --
#: an operator who genuinely wants a label for one of these may declare
#: it, and that is a decision rather than an inference.
_NEVER_INFERRED_RE = re.compile(
    r"^(date|time|calendar|period|fiscal\w*|sys\w*|dtproperties)$", re.IGNORECASE
)

#: Key columns that are date/time grains rather than entity references.
#: Checked on the FK column itself as well as the target table, because a
#: schema may name the dimension unusually while still spelling the key
#: `OrderDateKey`.
_DATE_KEY_RE = re.compile(r"\w*(date|time)key$", re.IGNORECASE)

#: Table-name tokens that mark a *fact* table. A fact table is never a
#: display-name source: it holds measures and foreign keys, and its own
#: "name"-ish columns (if any) are not entity labels.
_FACT_TABLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (r"^fact\w*", r"\w*facts?$", r"^f_\w+", r"\w*transactions?$")
)

#: Table-name tokens that mark a *dimension* table.
_DIMENSION_TABLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (r"^dim\w*", r"\w*dim$", r"^d_\w+", r"\w*lookup$")
)

TableRole = str  # "fact" | "dimension" | "unknown"


@dataclass(frozen=True)
class DisplayName:
    """How to render one entity's human-readable name.

    Attributes:
        table: The dimension table holding the name, exactly as live
            introspection spells it.
        key_column: That table's key column a fact row joins to.
        columns: The column(s) making up the name, in render order. One
            for the ordinary case, two for a composite person name.
        separator: Joined between `columns` when there is more than one.
        source: `"config"` when a human declared this in
            `config/display_names.yaml`, `"inferred"` when the heuristics
            here chose it. Carried so a caller can log *why* a name was
            used, and so an operator can see which entities still rest on
            inference.
    """

    table: str
    key_column: str
    columns: tuple[str, ...]
    separator: str = " "
    source: str = "inferred"

    @property
    def is_composite(self) -> bool:
        """Whether the name is built from more than one column."""
        return len(self.columns) > 1

    def select_expression(self, table_alias: str, dialect: str = "") -> str:
        """The SQL expression that renders this name, qualified by `table_alias`.

        A composite name is concatenated with the engine's own operator
        and **NULL-guarded**: in every supported engine `NULL || 'x'` (or
        `NULL + 'x'`) is NULL, so an employee with no recorded first name
        would otherwise vanish from the result entirely rather than
        appearing under their last name. Each part is wrapped in
        `COALESCE(..., '')` and the result trimmed, which degrades to
        "whichever part exists" instead of nothing.

        Args:
            table_alias: The alias the dimension is joined under.
            dialect: Target dialect; only `mssql`/`oracle` need the `+`
                and `||` distinction from `CONCAT`.

        Returns:
            A SQL scalar expression. Identifiers are schema-sourced and
            already sanitized by introspection, never caller text.
        """
        qualified = [f"{table_alias}.{column}" for column in self.columns]
        if len(qualified) == 1:
            return qualified[0]

        # CONCAT is the portable choice and is NULL-safe on its own in
        # MSSQL/MySQL, but not in every engine -- COALESCE makes the
        # behaviour identical everywhere rather than dialect-dependent.
        guarded = [f"COALESCE({part}, '')" for part in qualified]

        # Both spellings are accepted because two names for the same
        # engine are in play: `Settings.db_type` says "mssql" while
        # sqlglot's dialect is "tsql" (`db.connection.get_sqlglot_dialect`).
        # Matching only one of them silently fell through to the `||`
        # branch below, which sqlglot does transpile correctly but which
        # renders as `TRIM(...)` -- unavailable before SQL Server 2017.
        # LTRIM(RTRIM(CONCAT(...))) works on every supported version.
        if dialect in ("mssql", "tsql", "mysql"):
            inner = f", '{self.separator}', ".join(guarded)
            return f"LTRIM(RTRIM(CONCAT({inner})))"
        joined = f" || '{self.separator}' || ".join(guarded)
        return f"TRIM({joined})"

    def output_alias(self) -> str:
        """A readable result-column alias for this name.

        Derived from the key column with its key suffix dropped --
        `ProductKey` becomes `Product`, `CustomerID` becomes `Customer` --
        so the result header reads as the business entity rather than as
        the underlying column. Falls back to the first name column when
        stripping leaves nothing usable.
        """
        stem = re.sub(r"(key|id|code|no)$", "", self.key_column, flags=re.IGNORECASE)
        stem = stem.strip("_")
        return stem or self.columns[0]


@dataclass
class DisplayNameMap:
    """Every display name reachable in one database, indexed for lookup.

    Attributes:
        by_key_column: Lowercased foreign-key column name -> the display
            name it resolves to. This is the index the SQL guard uses:
            it sees `f.ProductKey` in a GROUP BY and needs to know what
            that key is a name for.
        by_table: Lowercased dimension table name -> its display name.
        roles: Lowercased table name -> `"fact"`/`"dimension"`/`"unknown"`.
        keys_without_names: Lowercased key columns that reach a dimension
            which has **no** usable display column. Tracked separately
            from "unknown key", because the honest answer differs: this
            one means "the database has no readable name here", which the
            brief requires be stated rather than papered over.
    """

    by_key_column: dict[str, DisplayName] = field(default_factory=dict)
    by_table: dict[str, DisplayName] = field(default_factory=dict)
    roles: dict[str, TableRole] = field(default_factory=dict)
    keys_without_names: set[str] = field(default_factory=set)

    def for_key(self, column_name: str) -> DisplayName | None:
        """The display name for a key column, or `None` if there is none."""
        return self.by_key_column.get(column_name.lower())

    def role_of(self, table_name: str) -> TableRole:
        """`"fact"`, `"dimension"` or `"unknown"` for a table."""
        return self.roles.get(table_name.lower(), "unknown")

    def has_known_gap(self, column_name: str) -> bool:
        """Whether this key reaches a dimension with no readable name.

        Distinct from `for_key(...) is None`, which also covers a column
        this map simply does not recognise.
        """
        return column_name.lower() in self.keys_without_names


def classify_table(table: TableSchemaInfo) -> TableRole:
    """Classify a table as a fact, a dimension, or neither.

    Name patterns first (`DimProduct`, `FactSales`) because a schema that
    uses them is telling you the answer directly. Otherwise structural:
    a table that is mostly foreign keys and numbers is a fact table, and
    one with a single-column primary key plus a readable name column is a
    dimension.

    `"unknown"` is a real answer, not a failure. A table that is neither
    is simply never used as a display-name source, which is the safe
    direction -- the alternative is inventing a label from a table that
    does not describe an entity.
    """
    name = table.table_name.split(".")[-1]

    if any(p.match(name) for p in _DIMENSION_TABLE_PATTERNS):
        return "dimension"
    if any(p.match(name) for p in _FACT_TABLE_PATTERNS):
        return "fact"

    primary_keys = [c for c in table.columns if c.is_primary_key]
    fk_columns = {c.lower() for fk in table.foreign_keys for c in fk.constrained_columns}

    # A fact table is dominated by foreign keys: it references many other
    # tables and carries measures of its own.
    if len(table.foreign_keys) >= 3 and len(fk_columns) >= 3:
        return "fact"

    # A dimension has one key and something readable to show for it.
    if len(primary_keys) == 1 and _find_display_columns(table) is not None:
        return "dimension"

    return "unknown"


def _is_excluded(column_name: str) -> bool:
    return any(p.match(column_name) for p in _DISPLAY_NAME_EXCLUSIONS)


def _find_display_columns(table: TableSchemaInfo) -> tuple[str, ...] | None:
    """The column(s) that render this table's entity name, or `None`.

    Composite person names are checked first: a table with `FirstName`
    and `LastName` and no single name column means both, and returning
    only one of them is a worse answer than the full name.

    Returns `None` rather than a best guess when nothing qualifies. That
    is the whole point -- a caller must be able to tell "no readable name
    exists here" from "here is one", so it can say so (brief rule 9).
    """
    names_lower = {c.name.lower(): c.name for c in table.columns}

    for first, last in _COMPOSITE_NAME_PAIRS:
        if first in names_lower and last in names_lower:
            return (names_lower[first], names_lower[last])

    for pattern in _DISPLAY_NAME_PATTERNS:
        for column in table.columns:
            if _is_excluded(column.name):
                continue
            if pattern.match(column.name):
                return (column.name,)

    return _find_by_table_core_name(table)


def _find_by_table_core_name(table: TableSchemaInfo) -> tuple[str, ...] | None:
    """Fallback: the text column named after the table's own entity.

    Real schemas label an entity with a column that is not name-shaped at
    all. `DimSalesTerritory`'s label is `SalesTerritoryRegion`; a
    `DimGeography`'s is often `City` or `EnglishCountryRegionName`. The
    pattern list above cannot cover these without becoming broad enough
    to start picking wrong columns.

    The principle here is narrower and safer than adding more patterns:
    **a column named after its own table is describing that entity.**
    `DimSalesTerritory.SalesTerritoryRegion` starts with the table's core
    name (`SalesTerritory`), so it is about the territory rather than
    about something else the row happens to carry.

    Three constraints keep it conservative:
      - it runs only after every explicit pattern has failed, so it never
        overrides a real `...Name` column;
      - the column must be a text type, so `OrderDate` on a `DimOrder`
        cannot be chosen;
      - key-shaped columns are excluded as everywhere else, so
        `SalesTerritoryAlternateKey` is not mistaken for a label.

    The shortest candidate wins: given `SalesTerritoryRegion` and
    `SalesTerritoryRegionGroupName`, the shorter is the more direct label
    for the entity rather than a roll-up of it.
    """
    core = _TABLE_AFFIX_RE.sub("", table.table_name.split(".")[-1]).lower()
    if len(core) < 3:
        return None

    candidates = [
        c.name
        for c in table.columns
        if not _is_excluded(c.name)
        and _TEXT_TYPE_RE.search(c.type or "")
        and c.name.lower().startswith(core)
        and c.name.lower() != core
    ]
    if not candidates:
        return None
    return (min(candidates, key=len),)


def _primary_key_of(table: TableSchemaInfo) -> str | None:
    """The single-column primary key, or `None` for none/composite.

    A composite primary key is deliberately unsupported: a fact row
    referencing it would need a multi-column join, and building that
    automatically is exactly the kind of guess this module refuses to
    make.
    """
    primary_keys = [c.name for c in table.columns if c.is_primary_key]
    return primary_keys[0] if len(primary_keys) == 1 else None


def resolve_display_name(
    table: TableSchemaInfo,
    *,
    overrides_path: Path | None = None,
) -> DisplayName | None:
    """The display name for one dimension table, or `None`.

    A `config/display_names.yaml` entry wins outright, including its
    choice of key column and separator -- that file is the human
    override, and an explicit decision always beats inference (the same
    precedence `governance.classification` gives its own explicit
    classifications).

    An override naming a column the live table does not actually have is
    **ignored with a warning**, not honoured: the database is the source
    of truth for what exists, and a stale config entry must not produce
    SQL referencing a dropped column.
    """
    overrides = load_display_name_overrides(overrides_path)
    short_name = table.table_name.split(".")[-1]
    override = overrides.get(short_name.lower()) or overrides.get(table.table_name.lower())

    actual = {c.name.lower(): c.name for c in table.columns}

    if override is not None:
        missing = [c for c in override.columns if c.lower() not in actual]
        key = override.key_column or _primary_key_of(table)
        if missing:
            logger.warning(
                "[display_names] config entry for %r names column(s) %s that do not exist "
                "on the live table -- ignoring the override and falling back to inference",
                short_name,
                missing,
            )
        elif key is None or key.lower() not in actual:
            logger.warning(
                "[display_names] config entry for %r has no usable key column -- "
                "ignoring the override",
                short_name,
            )
        else:
            return DisplayName(
                table=table.table_name,
                key_column=actual[key.lower()],
                columns=tuple(actual[c.lower()] for c in override.columns),
                separator=override.separator,
                source="config",
            )

    if classify_table(table) != "dimension":
        return None

    # A date/time dimension or a system table is never given an inferred
    # label -- see `_NEVER_INFERRED_RE`. Checked after the config
    # override above, so an explicit declaration still wins.
    core = _TABLE_AFFIX_RE.sub("", short_name)
    if _NEVER_INFERRED_RE.match(core):
        return None

    key_column = _primary_key_of(table)
    if key_column is None:
        return None

    columns = _find_display_columns(table)
    if columns is None:
        return None

    return DisplayName(
        table=table.table_name,
        key_column=key_column,
        columns=columns,
        source="inferred",
    )


def build_display_name_map(
    tables: list[TableSchemaInfo],
    *,
    overrides_path: Path | None = None,
) -> DisplayNameMap:
    """Index every display name reachable across `tables`.

    The key index is built from **declared foreign keys only**: a fact
    table's FK column is mapped to the display name of the table that FK
    actually references. A key column that no FK declares is absent from
    the index, so the guard will not try to join on it.

    One deliberate extra: a dimension's own primary key is also indexed,
    so a query that groups by `DimProduct.ProductKey` directly (no fact
    table involved) is still repairable -- the dimension is already in
    the query and only the selected column needs swapping.
    """
    result = DisplayNameMap()
    by_short_name: dict[str, TableSchemaInfo] = {}

    for table in tables:
        short = table.table_name.split(".")[-1].lower()
        by_short_name[short] = table
        by_short_name.setdefault(table.table_name.lower(), table)
        result.roles[table.table_name.lower()] = classify_table(table)
        result.roles.setdefault(short, result.roles[table.table_name.lower()])

    resolved: dict[str, DisplayName | None] = {}
    for table in tables:
        display = resolve_display_name(table, overrides_path=overrides_path)
        short = table.table_name.split(".")[-1].lower()
        resolved[short] = display
        if display is not None:
            result.by_table[short] = display
            result.by_table.setdefault(table.table_name.lower(), display)
            # A dimension grouped by its own PK is repairable in place.
            result.by_key_column.setdefault(display.key_column.lower(), display)

    for table in tables:
        for fk in table.foreign_keys:
            referred_short = fk.referred_table.split(".")[-1].lower()
            display = resolved.get(referred_short)
            if display is None:
                # The FK resolves to a real table that has no readable
                # name. Recorded so the caller can say exactly that,
                # rather than reporting the key as unrecognised.
                #
                # A date/time grain is excluded from that disclosure: a
                # date key is already readable, so there is nothing to
                # apologise for, and a notice here would put a misleading
                # caveat on a perfectly good "sales by order date"
                # answer. Such a key is skipped silently -- neither
                # repaired nor disclosed.
                if referred_short in by_short_name:
                    result.keys_without_names.update(
                        c.lower() for c in fk.constrained_columns if not _DATE_KEY_RE.match(c)
                    )
                continue
            for constrained in fk.constrained_columns:
                # A date-grain key is a grain, not an entity reference,
                # whatever the dimension it points at is called.
                if _DATE_KEY_RE.match(constrained):
                    continue
                result.by_key_column.setdefault(constrained.lower(), display)

    logger.debug(
        "[display_names] indexed %d key column(s) across %d table(s); "
        "%d key(s) reach a dimension with no readable name",
        len(result.by_key_column),
        len(tables),
        len(result.keys_without_names),
    )
    return result


def find_join_path(
    fact: TableSchemaInfo, key_column: str, display: DisplayName
) -> tuple[str, str] | None:
    """The declared FK that joins `fact` to `display`'s table on `key_column`.

    Returns `(fact_column, dimension_column)` for the one declared foreign
    key that matches, or `None`. Single-column foreign keys only: a
    composite FK needs a multi-column ON clause, and constructing one
    automatically is a guess this module does not make.

    `None` is the signal to stop and report, never to improvise a join.
    """
    target = display.table.split(".")[-1].lower()
    for fk in fact.foreign_keys:
        if fk.referred_table.split(".")[-1].lower() != target:
            continue
        if len(fk.constrained_columns) != 1 or len(fk.referred_columns) != 1:
            continue
        if fk.constrained_columns[0].lower() != key_column.lower():
            continue
        return (fk.constrained_columns[0], fk.referred_columns[0])
    return None


__all__ = [
    "DisplayName",
    "DisplayNameMap",
    "TableRole",
    "build_display_name_map",
    "classify_table",
    "find_join_path",
    "resolve_display_name",
]
