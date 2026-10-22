"""What each result column *means*, so a chart can bind the right axis.

**The gap this closes.** A result column reaches the client as a name, a
list of values, and one of three types: `numeric`, `date` or `text`
(`agent.result_charting.classify_columns`). That is enough to validate a
chart type and not enough to choose an axis, because **a surrogate key is
numeric**. `frontend/src/lib/chartEngine.ts` picked the first numeric
column as the y-axis, so `SELECT ProductKey, SUM(SalesAmount)` plotted
*ProductKey* as the measure -- and, since a numeric column was also
ineligible as a category, produced no sensible x-axis either.

Type alone cannot fix that. `ProductKey` and `TotalSales` are both
numeric; what separates them is meaning. This module supplies the
meaning.

**Two signals, schema first.** A column is a key when live introspection
says it is a primary or foreign key (`db.column_formatting
.get_key_column_names`), and otherwise when its last name token is
`key`/`id` -- the same two-signal approach `is_probable_surrogate_key`
already establishes, reused rather than re-derived. The schema signal is
the reliable one; the name fallback exists because an aggregate's alias
never traces back to a schema column.

**Priority is deterministic, not learned.** When several measures are
returned, `measure_priority` ranks them by what the column is *called*,
following the brief's own ladder: revenue before profit before quantity
before counts before averages before percentages. Ties break on result
order, which is the order the SQL named them -- and that order already
reflects the question, because the generator writes the asked-for measure
first. No model call, no guessing.

**This module classifies; it does not decide.** It returns facts about
columns. `frontend/src/lib/chartEngine.ts` picks the axes and
`agent.result_charting.recommend_chart` picks the chart type; both read
this rather than re-deriving it, so the fact has one owner
(CLAUDE.md 4.8).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from config.column_labels import SURROGATE_KEY_SUFFIXES
from db.column_formatting import format_column_label, is_probable_surrogate_key

logger = logging.getLogger(__name__)

SemanticType = Literal[
    "dimension",
    "key",
    "measure",
    "date",
    "percentage",
    "currency",
    "count",
    "text",
]

NumberFormat = Literal["plain", "currency", "percent", "integer"]

#: The measure-priority ladder. Checked in order; first match wins, so a
#: column named `TotalSalesAmount` scores as revenue rather than falling
#: through to a lower rung.
#:
#: Ordered exactly as the brief specifies: revenue, then profit, then
#: quantity, then counts, then averages, then percentages, then anything
#: else numeric. Higher is more primary.
#:
#: Matched against the *whole* column name, case-insensitively, after
#: stripping separators -- so `total_sales`, `TotalSales` and `totalsales`
#: all score the same.
_MEASURE_PRIORITY_RULES: tuple[tuple[int, re.Pattern[str], SemanticType, NumberFormat], ...] = (
    (
        100,
        re.compile(r"(revenue|sales|salesamount|amount|turnover|grossvalue|netvalue)", re.I),
        "currency",
        "currency",
    ),
    (90, re.compile(r"(profit|margin|earnings|income|ebitda)", re.I), "currency", "currency"),
    (80, re.compile(r"(quantity|qty|units|volume|weight)", re.I), "measure", "integer"),
    (
        70,
        re.compile(r"(count|orders|transactions|customers|products|visits|sessions)", re.I),
        "count",
        "integer",
    ),
    (60, re.compile(r"(average|avg|mean|median)", re.I), "measure", "plain"),
    (50, re.compile(r"(percent|pct|ratio|rate|share|proportion)", re.I), "percentage", "percent"),
)

#: Boost applied to a measure the query itself ranks by.
#:
#: This implements the brief's first priority rule -- "the measure
#: explicitly mentioned in the user question" -- without needing to read
#: the question, which is both unreliable and not always in scope.
#: `ORDER BY` is the better signal and a deterministic one: the generator
#: writes `ORDER BY TotalOrders DESC` precisely *because* the question
#: asked who bought most frequently. The query already encodes the
#: ranking the question asked for.
#:
#: Large enough to outrank the whole name ladder, so "which customer
#: bought frequently" defaults to Total Orders even though Total Sales
#: scores higher by name alone.
_ORDER_BY_BOOST = 1000

#: Fallback priority for a numeric column matching no rule above. Low, so
#: any named measure outranks it, but positive, so it is still a valid
#: y-axis -- which matters: a column called `Total` or `Value` is a real
#: measure even though it names no business concept.
_DEFAULT_MEASURE_PRIORITY = 10

#: A key is never a measure, whatever it is called. Priority 0 is the
#: signal to every consumer that this column must not be charted as a
#: value.
_KEY_PRIORITY = 0

#: Currency-ish words that make a column a currency even when it did not
#: match the revenue rung -- e.g. `UnitPrice`, `StandardCost`.
_CURRENCY_HINT_RE = re.compile(r"(price|cost|fee|charge|salary|wage|budget|spend)", re.I)

#: Year columns are a special case worth naming. `CalendarYear` is
#: numeric and is a perfectly good *category* axis, but it is never a
#: measure -- plotting "sales by year" with year on the y-axis is the
#: same class of error as plotting a key there.
_YEAR_LIKE_RE = re.compile(r"^(\w*year|yr|fiscalyear|calendaryear)$", re.I)

#: Date-part columns that behave as categories rather than measures, for
#: the same reason as year.
_DATE_PART_RE = re.compile(r"^(\w*(month|quarter|week|day|period)(name|number|num|no)?)$", re.I)


@dataclass(frozen=True)
class ColumnSemantics:
    """One result column's meaning, and what a chart may do with it.

    Attributes:
        name: The column name exactly as the result returned it (the SQL
            alias, when the statement used one).
        label: A human-readable label (`format_column_label`), e.g.
            `TotalSales` -> "Total Sales".
        data_type: `numeric` / `date` / `text`, from
            `agent.result_charting.classify_columns`.
        semantic_type: What the column *means* -- see `SemanticType`.
        number_format: How a value should be rendered.
        suitable_for_x: Whether this column may label a category axis.
        suitable_for_y: Whether this column may be plotted as a value.
            **False for every key**, which is the point of this module.
        hidden_by_default: Whether a results table should hide it unless
            asked for. Keys only; the data is still returned, never
            dropped.
        measure_priority: How primary this measure is (higher is more
            primary, 0 means "never a measure"). See the ladder above.
    """

    name: str
    label: str
    data_type: str
    semantic_type: SemanticType
    number_format: NumberFormat = "plain"
    suitable_for_x: bool = False
    suitable_for_y: bool = False
    hidden_by_default: bool = False
    measure_priority: int = 0

    def as_dict(self) -> dict[str, Any]:
        """A plain dict for the API response.

        Written out rather than `dataclasses.asdict` so the wire shape is
        visible here and cannot change by accident when a field is added
        for internal use only.
        """
        return {
            "name": self.name,
            "label": self.label,
            "data_type": self.data_type,
            "semantic_type": self.semantic_type,
            "number_format": self.number_format,
            "suitable_for_x": self.suitable_for_x,
            "suitable_for_y": self.suitable_for_y,
            "hidden_by_default": self.hidden_by_default,
            "measure_priority": self.measure_priority,
        }


def _normalize(name: str) -> str:
    """Strip separators so `total_sales` and `TotalSales` score alike."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _classify_numeric(name: str) -> tuple[SemanticType, NumberFormat, int]:
    """Semantic type, format and priority for a non-key numeric column."""
    normalized = _normalize(name)

    # A year or date-part is numeric but is a category, never a value.
    if _YEAR_LIKE_RE.match(name) or _DATE_PART_RE.match(name):
        return "date", "plain", _KEY_PRIORITY

    for priority, pattern, semantic, number_format in _MEASURE_PRIORITY_RULES:
        if pattern.search(normalized):
            return semantic, number_format, priority

    if _CURRENCY_HINT_RE.search(normalized):
        return "currency", "currency", _DEFAULT_MEASURE_PRIORITY

    return "measure", "plain", _DEFAULT_MEASURE_PRIORITY


def _order_by_names(order_by: list[str] | tuple[str, ...] | None) -> set[str]:
    """Bare column names from `SqlShape.order_by` entries.

    Entries arrive as rendered fragments (`"TotalOrders DESC"`), so the
    direction and any qualifier are stripped. An expression rather than a
    plain column (`SUM(x) DESC`) simply matches nothing, which is the
    safe outcome -- it leaves the name ladder in charge.
    """
    names: set[str] = set()
    for entry in order_by or ():
        token = str(entry).strip().split()[0] if str(entry).strip() else ""
        token = token.split(".")[-1].strip("[]`\"'")
        if token and re.fullmatch(r"\w+", token):
            names.add(token.lower())
    return names


def classify_result_columns(
    columns: list[str],
    column_types: dict[str, str],
    key_columns: set[str] | None = None,
    order_by: list[str] | tuple[str, ...] | None = None,
) -> list[ColumnSemantics]:
    """Classify every column in one result.

    Args:
        columns: Result column names, in result order.
        column_types: `agent.result_charting.classify_columns`'s output --
            `numeric` / `date` / `text` per column. A column absent from
            it is treated as text, which is the conservative default (a
            text column is never charted as a value).
        key_columns: Lowercased primary/foreign key column names from live
            introspection (`db.column_formatting.get_key_column_names`).
            Omitted or empty falls back to the name-suffix signal alone,
            which still catches `ProductKey`/`CustomerID` -- the shapes
            that actually cause this bug.
        order_by: The statement's `ORDER BY` entries
            (`agent.sql_validator.describe_sql`'s `SqlShape.order_by`). A
            measure the query ranks by is the one the question asked
            about, so it outranks the name ladder -- see
            `_ORDER_BY_BOOST`. Omitted leaves the ladder in sole charge.

    Returns:
        One `ColumnSemantics` per column, in result order. Order is
        preserved because it is meaningful: it breaks priority ties, and
        it is the order the SQL named the columns.
    """
    keys = key_columns or set()
    ranked = _order_by_names(order_by)
    result: list[ColumnSemantics] = []

    for name in columns:
        data_type = column_types.get(name, "text")
        label = format_column_label(name)

        # Schema signal first, name fallback second -- the two-signal
        # approach `is_probable_surrogate_key` already owns.
        if is_probable_surrogate_key(name, keys, SURROGATE_KEY_SUFFIXES):
            result.append(
                ColumnSemantics(
                    name=name,
                    label=label,
                    data_type=data_type,
                    semantic_type="key",
                    # A key is not a category either: `ProductKey` on an
                    # x-axis is as unreadable as on a y-axis. The repaired
                    # SQL supplies a name column for that
                    # (`agent/display_name_guard.py`); where it could
                    # not, no chart is better than a chart of numbers.
                    suitable_for_x=False,
                    suitable_for_y=False,
                    hidden_by_default=True,
                    measure_priority=_KEY_PRIORITY,
                )
            )
            continue

        if data_type == "date":
            result.append(
                ColumnSemantics(
                    name=name,
                    label=label,
                    data_type=data_type,
                    semantic_type="date",
                    suitable_for_x=True,
                    suitable_for_y=False,
                    measure_priority=_KEY_PRIORITY,
                )
            )
            continue

        if data_type == "numeric":
            semantic, number_format, priority = _classify_numeric(name)
            is_category = semantic == "date"
            # A measure the query ranks by is what the question asked
            # for. Never applied to a category (a year ordered by is
            # still an axis, not a value).
            if not is_category and name.lower() in ranked:
                priority += _ORDER_BY_BOOST
            result.append(
                ColumnSemantics(
                    name=name,
                    label=label,
                    data_type=data_type,
                    semantic_type=semantic,
                    number_format=number_format,
                    # A year is a numeric category: good x, never y.
                    suitable_for_x=is_category,
                    suitable_for_y=not is_category,
                    measure_priority=priority,
                )
            )
            continue

        result.append(
            ColumnSemantics(
                name=name,
                label=label,
                data_type=data_type,
                semantic_type="dimension",
                suitable_for_x=True,
                suitable_for_y=False,
                measure_priority=_KEY_PRIORITY,
            )
        )

    return result


def primary_measure(semantics: list[ColumnSemantics]) -> ColumnSemantics | None:
    """The column a chart should plot on the y-axis by default.

    The highest `measure_priority` among columns marked `suitable_for_y`,
    breaking ties on result order -- which is the order the SQL named
    them, and therefore reflects what the question asked for first.

    Returns `None` when the result carries no measure at all. That is a
    real answer and callers must honour it: forcing a chart onto a
    key-only result is how `ProductKey` ended up on a y-axis.
    """
    candidates = [c for c in semantics if c.suitable_for_y]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c.measure_priority)
    # `max` returns the first maximum, which is already result order --
    # stated explicitly because relying on that silently would be fragile.
    for candidate in candidates:
        if candidate.measure_priority == best.measure_priority:
            return candidate
    return best


def category_columns(semantics: list[ColumnSemantics]) -> list[ColumnSemantics]:
    """Columns that may label a category axis, best first.

    A real date sorts ahead of a numeric year, which sorts ahead of a
    text dimension: given both an order date and a product name, the date
    is the more natural x-axis for a trend.
    """
    order = {"date": 0, "dimension": 1, "text": 2}
    return sorted(
        (c for c in semantics if c.suitable_for_x),
        key=lambda c: order.get(c.semantic_type, 3),
    )


def measure_columns(semantics: list[ColumnSemantics]) -> list[ColumnSemantics]:
    """Every chartable measure, most primary first."""
    return sorted(
        (c for c in semantics if c.suitable_for_y),
        key=lambda c: -c.measure_priority,
    )


def has_chartable_measure(semantics: list[ColumnSemantics]) -> bool:
    """Whether anything here can honestly be plotted as a value."""
    return any(c.suitable_for_y for c in semantics)


__all__ = [
    "ColumnSemantics",
    "NumberFormat",
    "SemanticType",
    "category_columns",
    "classify_result_columns",
    "has_chartable_measure",
    "measure_columns",
    "primary_measure",
]
