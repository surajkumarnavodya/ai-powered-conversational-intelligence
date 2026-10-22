"""Result-column semantics: what each column means, and which axis it may bind.

The reported bug: "total sales by product" charted **ProductKey** as the
measure. `column_types` says a column is `numeric`, and a surrogate key
is numeric, so the chart engine took whichever numeric column the SQL
returned first. Type alone cannot separate `ProductKey` from
`TotalSales`; meaning can, and this module supplies it.

Organised by the brief's scenarios, plus the rules that must not regress.
Fully mocked -- no database, no chart library.
"""

from __future__ import annotations

import pytest

from db.column_semantics import (
    ColumnSemantics,
    category_columns,
    classify_result_columns,
    has_chartable_measure,
    measure_columns,
    primary_measure,
)


def _labels(columns: list[ColumnSemantics]) -> list[str]:
    return [c.name for c in columns]


class TestKeysAreNeverMeasures:
    """The core of the bug."""

    def test_a_numeric_key_is_classified_as_a_key(self) -> None:
        result = classify_result_columns(
            ["ProductKey", "TotalSales"],
            {"ProductKey": "numeric", "TotalSales": "numeric"},
            {"productkey"},
        )
        key, measure = result
        assert key.semantic_type == "key"
        assert key.suitable_for_y is False
        assert measure.semantic_type == "currency"
        assert measure.suitable_for_y is True

    def test_the_primary_measure_is_never_the_key(self) -> None:
        result = classify_result_columns(
            ["ProductKey", "TotalSales"],
            {"ProductKey": "numeric", "TotalSales": "numeric"},
            {"productkey"},
        )
        assert primary_measure(result).name == "TotalSales"

    def test_a_key_is_not_a_category_axis_either(self) -> None:
        """`ProductKey` on an x-axis is as unreadable as on a y-axis."""
        result = classify_result_columns(["ProductKey"], {"ProductKey": "numeric"}, {"productkey"})
        assert result[0].suitable_for_x is False

    def test_a_key_is_hidden_from_the_default_table_view(self) -> None:
        result = classify_result_columns(["ProductKey"], {"ProductKey": "numeric"}, {"productkey"})
        assert result[0].hidden_by_default is True

    def test_the_name_fallback_catches_a_key_with_no_schema_signal(self) -> None:
        """An aggregate's alias never traces back to a schema column, so
        the name signal has to work on its own."""
        result = classify_result_columns(
            ["CustomerID", "Total"], {"CustomerID": "numeric", "Total": "numeric"}
        )
        assert result[0].semantic_type == "key"
        assert primary_measure(result).name == "Total"

    @pytest.mark.parametrize("name", ["PostalCode", "AccountNumber", "Valid", "Quantity"])
    def test_a_business_column_that_merely_looks_key_ish_is_not_a_key(self, name: str) -> None:
        result = classify_result_columns([name], {name: "numeric"})
        assert result[0].semantic_type != "key"


class TestBriefScenarios:
    """The question shapes the brief names."""

    def test_total_sales_by_product(self) -> None:
        result = classify_result_columns(
            ["Product", "TotalSales"], {"Product": "text", "TotalSales": "numeric"}
        )
        assert category_columns(result)[0].name == "Product"
        assert primary_measure(result).name == "TotalSales"
        assert primary_measure(result).label == "Total Sales"

    def test_which_customer_bought_frequently(self) -> None:
        """Total Orders wins -- because the query ranks by it.

        By name alone Total Sales outranks Total Orders. The query's own
        `ORDER BY TotalOrders DESC` is there *because* the question asked
        who bought most frequently, so it is the better signal for "the
        measure the question mentioned" and outranks the name ladder.
        """
        columns = ["Customer", "TotalOrders", "TotalSales"]
        types = {"Customer": "text", "TotalOrders": "numeric", "TotalSales": "numeric"}

        without_order = classify_result_columns(columns, types)
        assert primary_measure(without_order).name == "TotalSales"

        with_order = classify_result_columns(columns, types, order_by=["TotalOrders DESC"])
        assert primary_measure(with_order).name == "TotalOrders"
        assert category_columns(with_order)[0].name == "Customer"

    def test_year_wise_sales(self) -> None:
        """A numeric year is a category, never a measure."""
        result = classify_result_columns(
            ["CalendarYear", "TotalSales"],
            {"CalendarYear": "numeric", "TotalSales": "numeric"},
        )
        year, sales = result
        assert year.suitable_for_x is True
        assert year.suitable_for_y is False
        assert sales.suitable_for_y is True
        assert primary_measure(result).name == "TotalSales"

    def test_top_products_by_revenue_and_quantity(self) -> None:
        """Revenue outranks quantity, whatever order they are returned in."""
        result = classify_result_columns(
            ["Product", "OrderQuantity", "Revenue"],
            {"Product": "text", "OrderQuantity": "numeric", "Revenue": "numeric"},
        )
        assert primary_measure(result).name == "Revenue"
        assert _labels(measure_columns(result)) == ["Revenue", "OrderQuantity"]

    def test_a_result_with_only_keys_is_not_chartable(self) -> None:
        result = classify_result_columns(
            ["ProductKey", "CustomerKey"],
            {"ProductKey": "numeric", "CustomerKey": "numeric"},
            {"productkey", "customerkey"},
        )
        assert has_chartable_measure(result) is False
        assert primary_measure(result) is None
        assert measure_columns(result) == []


class TestMeasurePriorityLadder:
    """The brief's §3.6 ordering."""

    @pytest.mark.parametrize(
        ("higher", "lower"),
        [
            ("Revenue", "Profit"),
            ("TotalSales", "GrossProfit"),
            ("Profit", "OrderQuantity"),
            ("OrderQuantity", "OrderCount"),
            ("OrderCount", "AveragePrice"),
            ("AverageValue", "PercentOfTotal"),
        ],
    )
    def test_the_ladder_orders_measures_as_specified(self, higher: str, lower: str) -> None:
        result = classify_result_columns([lower, higher], {lower: "numeric", higher: "numeric"})
        assert primary_measure(result).name == higher

    def test_an_unnamed_numeric_is_still_a_valid_measure(self) -> None:
        """`Total` or `Value` names no business concept but is a measure."""
        result = classify_result_columns(
            ["Dimension", "Total"], {"Dimension": "text", "Total": "numeric"}
        )
        assert primary_measure(result).name == "Total"

    def test_ties_break_on_result_order(self) -> None:
        """Result order is the order the SQL named them, which reflects
        the question."""
        result = classify_result_columns(
            ["FirstTotal", "SecondTotal"],
            {"FirstTotal": "numeric", "SecondTotal": "numeric"},
        )
        assert primary_measure(result).name == "FirstTotal"

    def test_order_by_outranks_the_whole_ladder(self) -> None:
        result = classify_result_columns(
            ["Revenue", "OrderCount"],
            {"Revenue": "numeric", "OrderCount": "numeric"},
            order_by=["OrderCount DESC"],
        )
        assert primary_measure(result).name == "OrderCount"

    def test_order_by_never_promotes_a_key(self) -> None:
        """Ranking by a key does not make it a measure."""
        result = classify_result_columns(
            ["ProductKey", "TotalSales"],
            {"ProductKey": "numeric", "TotalSales": "numeric"},
            {"productkey"},
            order_by=["ProductKey ASC"],
        )
        assert primary_measure(result).name == "TotalSales"

    def test_order_by_never_promotes_a_year_to_a_measure(self) -> None:
        result = classify_result_columns(
            ["CalendarYear", "TotalSales"],
            {"CalendarYear": "numeric", "TotalSales": "numeric"},
            order_by=["CalendarYear DESC"],
        )
        assert primary_measure(result).name == "TotalSales"

    @pytest.mark.parametrize(
        "entry", ["TotalOrders DESC", "f.TotalOrders", "[TotalOrders]", "TotalOrders"]
    )
    def test_order_by_entries_are_parsed_in_their_real_shapes(self, entry: str) -> None:
        result = classify_result_columns(
            ["TotalSales", "TotalOrders"],
            {"TotalSales": "numeric", "TotalOrders": "numeric"},
            order_by=[entry],
        )
        assert primary_measure(result).name == "TotalOrders"

    def test_an_order_by_expression_leaves_the_ladder_in_charge(self) -> None:
        result = classify_result_columns(
            ["TotalSales", "TotalOrders"],
            {"TotalSales": "numeric", "TotalOrders": "numeric"},
            order_by=["SUM(x) DESC"],
        )
        assert primary_measure(result).name == "TotalSales"


class TestFormattingAndLabels:
    """What the axis title and tooltip will show."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("TotalSales", "currency"),
            ("GrossProfit", "currency"),
            ("UnitPrice", "currency"),
            ("OrderQuantity", "integer"),
            ("OrderCount", "integer"),
            ("PercentOfTotal", "percent"),
        ],
    )
    def test_number_format_follows_the_semantic_type(self, name: str, expected: str) -> None:
        result = classify_result_columns([name], {name: "numeric"})
        assert result[0].number_format == expected

    def test_labels_are_human_readable(self) -> None:
        result = classify_result_columns(
            ["TotalSales", "order_qty"], {"TotalSales": "numeric", "order_qty": "numeric"}
        )
        assert result[0].label == "Total Sales"
        assert result[1].label == "Order Quantity"

    def test_the_wire_shape_is_explicit(self) -> None:
        """Written out rather than derived, so it cannot change by accident."""
        payload = classify_result_columns(["TotalSales"], {"TotalSales": "numeric"})[0].as_dict()
        assert set(payload) == {
            "name",
            "label",
            "data_type",
            "semantic_type",
            "number_format",
            "suitable_for_x",
            "suitable_for_y",
            "hidden_by_default",
            "measure_priority",
        }


class TestOrdinaryResultsAreUnaffected:
    """No regression for results that never had a key."""

    def test_a_plain_category_and_measure_result(self) -> None:
        result = classify_result_columns(
            ["Region", "Total"], {"Region": "text", "Total": "numeric"}
        )
        assert category_columns(result)[0].name == "Region"
        assert primary_measure(result).name == "Total"

    def test_a_real_date_is_the_preferred_category(self) -> None:
        result = classify_result_columns(
            ["Product", "OrderDate", "TotalSales"],
            {"Product": "text", "OrderDate": "date", "TotalSales": "numeric"},
        )
        assert category_columns(result)[0].name == "OrderDate"

    def test_a_text_only_result_has_no_measure(self) -> None:
        result = classify_result_columns(["A", "B"], {"A": "text", "B": "text"})
        assert has_chartable_measure(result) is False

    def test_an_empty_result_is_handled(self) -> None:
        assert classify_result_columns([], {}) == []
        assert primary_measure([]) is None

    def test_a_column_missing_from_column_types_defaults_to_text(self) -> None:
        """Conservative: a text column is never charted as a value."""
        result = classify_result_columns(["Mystery"], {})
        assert result[0].data_type == "text"
        assert result[0].suitable_for_y is False
