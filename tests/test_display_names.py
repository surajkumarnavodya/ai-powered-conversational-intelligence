"""Readable business names, end to end: metadata, SQL repair, charts.

Organised by the brief's own requirements:

  - `TestSchemaMetadata` -- fact/dimension classification and the
    canonical display column per entity (brief 2).
  - `TestGeneratedSqlIsRepaired` -- the nine named question scenarios
    (brief 7), each asserting the join, the display column and the
    grouping.
  - `TestNeverGuesses` -- the rules that matter more than the repair:
    no invented join, no invented name, no touched measure.
  - `TestSafetyIsPreserved` -- every repaired statement still passes the
    SELECT-only allowlist.
  - `TestChartsAndTables` -- a key is never a chart axis.

Fully mocked: an AdventureWorks-shaped schema is built from
`TableSchemaInfo` directly, so no database, no Chroma and no Ollama is
involved.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agent.display_name_guard import (
    NO_DISPLAY_NAME_NOTICE,
    enforce_display_names,
)
from agent.result_charting import classify_columns, recommend_chart
from agent.sql_validator import validate_sql
from db.display_names import (
    build_display_name_map,
    classify_table,
    find_join_path,
    resolve_display_name,
)
from db.schema_introspection import (
    ColumnInfo,
    ForeignKeyInfo,
    TableSchemaInfo,
    parse_ddl_table_info,
    render_ddl,
)

DIALECT = "tsql"


def _col(name: str, type_: str = "int", *, pk: bool = False) -> ColumnInfo:
    return ColumnInfo(name=name, type=type_, nullable=not pk, is_primary_key=pk)


def _table(name, columns, fks=()) -> TableSchemaInfo:
    return TableSchemaInfo(table_name=name, columns=tuple(columns), foreign_keys=tuple(fks), ddl="")


DIM_PRODUCT = _table(
    "DimProduct",
    [
        _col("ProductKey", pk=True),
        # Deliberately present: a key-shaped column that also contains
        # "Name"-adjacent text. The resolver must not pick it.
        _col("ProductAlternateKey", "nvarchar"),
        _col("EnglishProductName", "nvarchar"),
        _col("ProductSubcategoryKey"),
    ],
    [
        ForeignKeyInfo(
            ("ProductSubcategoryKey",), "DimProductSubcategory", ("ProductSubcategoryKey",)
        )
    ],
)
DIM_SUBCATEGORY = _table(
    "DimProductSubcategory",
    [
        _col("ProductSubcategoryKey", pk=True),
        _col("EnglishProductSubcategoryName", "nvarchar"),
        _col("ProductCategoryKey"),
    ],
    [ForeignKeyInfo(("ProductCategoryKey",), "DimProductCategory", ("ProductCategoryKey",))],
)
DIM_CATEGORY = _table(
    "DimProductCategory",
    [_col("ProductCategoryKey", pk=True), _col("EnglishProductCategoryName", "nvarchar")],
)
DIM_CUSTOMER = _table(
    "DimCustomer",
    [_col("CustomerKey", pk=True), _col("FirstName", "nvarchar"), _col("LastName", "nvarchar")],
)
DIM_EMPLOYEE = _table(
    "DimEmployee",
    [_col("EmployeeKey", pk=True), _col("FirstName", "nvarchar"), _col("LastName", "nvarchar")],
)
DIM_TERRITORY = _table(
    "DimSalesTerritory",
    [_col("SalesTerritoryKey", pk=True), _col("SalesTerritoryRegion", "nvarchar")],
)
#: A dimension with no readable column at all -- the "say so" case.
DIM_CURRENCY = _table(
    "DimCurrency", [_col("CurrencyKey", pk=True), _col("CurrencyAlternateKey", "nchar")]
)
DIM_DATE = _table(
    "DimDate",
    [_col("DateKey", pk=True), _col("CalendarYear"), _col("EnglishMonthName", "nvarchar")],
)

FACT_SALES = _table(
    "FactInternetSales",
    [
        _col("ProductKey"),
        _col("CustomerKey"),
        _col("EmployeeKey"),
        _col("SalesTerritoryKey"),
        _col("CurrencyKey"),
        _col("OrderDateKey"),
        _col("SalesOrderNumber", "nvarchar"),
        _col("SalesAmount", "money"),
        _col("OrderQuantity"),
    ],
    [
        ForeignKeyInfo(("ProductKey",), "DimProduct", ("ProductKey",)),
        ForeignKeyInfo(("CustomerKey",), "DimCustomer", ("CustomerKey",)),
        ForeignKeyInfo(("EmployeeKey",), "DimEmployee", ("EmployeeKey",)),
        ForeignKeyInfo(("SalesTerritoryKey",), "DimSalesTerritory", ("SalesTerritoryKey",)),
        ForeignKeyInfo(("CurrencyKey",), "DimCurrency", ("CurrencyKey",)),
        ForeignKeyInfo(("OrderDateKey",), "DimDate", ("DateKey",)),
    ],
)

TABLES = [
    DIM_PRODUCT,
    DIM_SUBCATEGORY,
    DIM_CATEGORY,
    DIM_CUSTOMER,
    DIM_EMPLOYEE,
    DIM_TERRITORY,
    DIM_CURRENCY,
    DIM_DATE,
    FACT_SALES,
]


@pytest.fixture(scope="module")
def display_map():
    return build_display_name_map(TABLES)


def _repair(sql: str, display_map):
    return enforce_display_names(sql, TABLES, display_map, dialect=DIALECT)


class TestSchemaMetadata:
    """The layer that knows which column is an entity's name (brief 2)."""

    def test_fact_and_dimension_tables_are_classified(self) -> None:
        assert classify_table(FACT_SALES) == "fact"
        for dimension in (DIM_PRODUCT, DIM_CUSTOMER, DIM_EMPLOYEE, DIM_TERRITORY):
            assert classify_table(dimension) == "dimension", dimension.table_name

    @pytest.mark.parametrize(
        ("table", "expected"),
        [
            (DIM_PRODUCT, ("EnglishProductName",)),
            (DIM_SUBCATEGORY, ("EnglishProductSubcategoryName",)),
            (DIM_CATEGORY, ("EnglishProductCategoryName",)),
            (DIM_TERRITORY, ("SalesTerritoryRegion",)),
            (DIM_CUSTOMER, ("FirstName", "LastName")),
            (DIM_EMPLOYEE, ("FirstName", "LastName")),
        ],
    )
    def test_canonical_display_column_per_entity(self, table, expected) -> None:
        resolved = resolve_display_name(table)
        assert resolved is not None, table.table_name
        assert resolved.columns == expected

    def test_a_key_shaped_column_is_never_the_display_name(self) -> None:
        """`ProductAlternateKey` is a key, not a label, despite its type."""
        resolved = resolve_display_name(DIM_PRODUCT)
        assert resolved is not None
        assert "ProductAlternateKey" not in resolved.columns

    def test_a_dimension_with_no_readable_column_resolves_to_nothing(self) -> None:
        assert resolve_display_name(DIM_CURRENCY) is None

    def test_composite_names_are_null_guarded(self) -> None:
        """An employee with no first name must not vanish from the result.

        `NULL + 'x'` is NULL in every supported engine, so an unguarded
        concatenation drops the whole row rather than degrading to the
        part that exists.
        """
        resolved = resolve_display_name(DIM_CUSTOMER)
        assert resolved is not None and resolved.is_composite
        expression = resolved.select_expression("c", DIALECT)
        assert expression.count("COALESCE") == 2
        # Version-safe on every SQL Server, unlike TRIM (2017+).
        assert "TRIM" in expression

    def test_key_columns_index_to_their_dimension(self, display_map) -> None:
        assert display_map.for_key("ProductKey").table == "DimProduct"
        assert display_map.for_key("CustomerKey").table == "DimCustomer"
        assert display_map.for_key("SalesTerritoryKey").table == "DimSalesTerritory"

    def test_a_key_with_no_readable_target_is_tracked_separately(self, display_map) -> None:
        """ "No name exists" and "key not recognised" need different answers."""
        assert display_map.for_key("CurrencyKey") is None
        assert display_map.has_known_gap("CurrencyKey") is True
        assert display_map.has_known_gap("SomeUnknownKey") is False

    def test_join_paths_come_only_from_declared_foreign_keys(self, display_map) -> None:
        product = display_map.for_key("ProductKey")
        assert find_join_path(FACT_SALES, "ProductKey", product) == ("ProductKey", "ProductKey")

        # The fact table has no declared FK to the subcategory dimension,
        # even though a same-named column exists on DimProduct.
        subcategory = display_map.for_key("ProductSubcategoryKey")
        assert find_join_path(FACT_SALES, "ProductSubcategoryKey", subcategory) is None

    def test_metadata_survives_a_ddl_round_trip(self) -> None:
        """The guard reads schema rebuilt from rendered DDL, not live info."""
        ddl = render_ddl(DIM_PRODUCT.table_name, DIM_PRODUCT.columns, DIM_PRODUCT.foreign_keys)
        parsed = parse_ddl_table_info("DimProduct", ddl)
        assert resolve_display_name(parsed) == resolve_display_name(DIM_PRODUCT)


class TestDateDimensionsAreNeverLabelled:
    """A date key is a grain, not an entity reference.

    Found by running the resolver against a real AdventureWorks schema
    rather than this file's fixture: `DimDate` carries
    `EnglishMonthName`, which matches the name patterns, so
    `OrderDateKey` resolved to it. "Sales by order date" would have been
    silently rewritten into "sales by month name" -- collapsing every
    year together and changing what the query answers. That is exactly
    the silent wrongness this guard exists to prevent, arriving from
    the inside.
    """

    def test_a_date_dimension_gets_no_inferred_display_name(self) -> None:
        dim_date = _table(
            "DimDate",
            [
                _col("DateKey", pk=True),
                _col("FullDateAlternateKey", "date"),
                _col("EnglishMonthName", "nvarchar"),
                _col("CalendarYear"),
            ],
        )
        assert resolve_display_name(dim_date) is None

    def test_a_date_key_is_not_indexed_as_an_entity(self, display_map) -> None:
        assert display_map.for_key("OrderDateKey") is None

    def test_a_date_key_is_not_disclosed_as_a_missing_name_either(self, display_map) -> None:
        """Nothing to apologise for: a date is already readable.

        A notice here would put a misleading caveat on a perfectly good
        answer, so the key is skipped silently rather than reported.
        """
        assert display_map.has_known_gap("OrderDateKey") is False

    def test_grouping_by_a_date_key_is_left_completely_alone(self, display_map) -> None:
        sql = (
            "SELECT f.OrderDateKey, SUM(f.SalesAmount) AS Total "
            "FROM FactInternetSales f GROUP BY f.OrderDateKey"
        )
        result = _repair(sql, display_map)
        assert result.status == "ok"
        assert result.sql == sql
        assert result.message == ""

    def test_a_date_and_an_entity_together_repair_only_the_entity(self, display_map) -> None:
        result = _repair(
            "SELECT f.OrderDateKey, f.ProductKey, SUM(f.SalesAmount) AS Total "
            "FROM FactInternetSales f GROUP BY f.OrderDateKey, f.ProductKey",
            display_map,
        )
        assert result.status == "repaired"
        assert "f.OrderDateKey" in result.sql
        assert "EnglishProductName" in result.sql
        assert result.repaired_keys == ("ProductKey",)

    def test_a_system_table_is_never_an_entity(self) -> None:
        """`sysdiagrams` has a single PK and a `name` column, so it looked
        like a dimension. It is not a business entity."""
        sysdiagrams = _table("sysdiagrams", [_col("diagram_id", pk=True), _col("name", "nvarchar")])
        assert resolve_display_name(sysdiagrams) is None


class TestGeneratedSqlIsRepaired:
    """The nine question scenarios the brief names (brief 7)."""

    def test_total_sales_by_product(self, display_map) -> None:
        """The originally reported bug."""
        result = _repair(
            "SELECT f.ProductKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f GROUP BY f.ProductKey ORDER BY TotalSales DESC",
            display_map,
        )
        assert result.status == "repaired"
        assert "EnglishProductName" in result.sql
        assert "INNER JOIN DimProduct" in result.sql
        assert "GROUP BY" in result.sql and "ProductKey" not in result.sql.split("GROUP BY")[1]
        assert "SUM(f.SalesAmount)" in result.sql

    def test_which_customer_bought_frequently(self, display_map) -> None:
        """Customer name plus BOTH measures, unchanged."""
        result = _repair(
            "SELECT f.CustomerKey, COUNT(DISTINCT f.SalesOrderNumber) AS TotalOrders, "
            "SUM(f.SalesAmount) AS TotalSales FROM FactInternetSales f "
            "GROUP BY f.CustomerKey ORDER BY TotalOrders DESC",
            display_map,
        )
        assert result.status == "repaired"
        assert "FirstName" in result.sql and "LastName" in result.sql
        assert "INNER JOIN DimCustomer" in result.sql
        assert "COUNT(DISTINCT f.SalesOrderNumber)" in result.sql
        assert "SUM(f.SalesAmount)" in result.sql

    def test_top_10_products_by_sales(self, display_map) -> None:
        """Ranking and row limit must survive the repair."""
        result = _repair(
            "SELECT TOP 10 f.ProductKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f GROUP BY f.ProductKey ORDER BY TotalSales DESC",
            display_map,
        )
        assert result.status == "repaired"
        assert "TOP 10" in result.sql
        assert "ORDER BY TotalSales DESC" in result.sql
        assert "EnglishProductName" in result.sql

    def test_sales_by_customer(self, display_map) -> None:
        result = _repair(
            "SELECT f.CustomerKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f GROUP BY f.CustomerKey",
            display_map,
        )
        assert result.status == "repaired"
        assert "DimCustomer" in result.sql
        assert "CustomerKey" not in result.sql.split("GROUP BY")[1]

    def test_sales_by_employee(self, display_map) -> None:
        result = _repair(
            "SELECT f.EmployeeKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f GROUP BY f.EmployeeKey",
            display_map,
        )
        assert result.status == "repaired"
        assert "INNER JOIN DimEmployee" in result.sql
        assert "FirstName" in result.sql and "LastName" in result.sql

    def test_sales_by_territory(self, display_map) -> None:
        result = _repair(
            "SELECT f.SalesTerritoryKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f GROUP BY f.SalesTerritoryKey",
            display_map,
        )
        assert result.status == "repaired"
        assert "SalesTerritoryRegion" in result.sql
        assert "INNER JOIN DimSalesTerritory" in result.sql

    def test_sales_by_category_and_subcategory(self, display_map) -> None:
        """Two entities in one query -- BOTH get a readable name."""
        result = _repair(
            "SELECT s.ProductSubcategoryKey, c.ProductCategoryKey, "
            "SUM(f.SalesAmount) AS TotalSales FROM FactInternetSales f "
            "INNER JOIN DimProduct p ON f.ProductKey = p.ProductKey "
            "INNER JOIN DimProductSubcategory s "
            "ON p.ProductSubcategoryKey = s.ProductSubcategoryKey "
            "INNER JOIN DimProductCategory c "
            "ON s.ProductCategoryKey = c.ProductCategoryKey "
            "GROUP BY s.ProductSubcategoryKey, c.ProductCategoryKey",
            display_map,
        )
        assert result.status == "repaired"
        assert "EnglishProductSubcategoryName" in result.sql
        assert "EnglishProductCategoryName" in result.sql
        assert set(result.repaired_keys) == {"ProductSubcategoryKey", "ProductCategoryKey"}

    def test_year_wise_sales_by_customer(self, display_map) -> None:
        """A non-entity grouping column (year) is left exactly as it is."""
        result = _repair(
            "SELECT d.CalendarYear, f.CustomerKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f "
            "INNER JOIN DimDate d ON f.OrderDateKey = d.DateKey "
            "GROUP BY d.CalendarYear, f.CustomerKey",
            display_map,
        )
        assert result.status == "repaired"
        assert "d.CalendarYear" in result.sql
        assert "FirstName" in result.sql
        assert "INNER JOIN DimCustomer" in result.sql

    def test_a_question_with_no_matching_dimension_name(self, display_map) -> None:
        """The brief's ninth case: explain, never fabricate."""
        result = _repair(
            "SELECT f.CurrencyKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f GROUP BY f.CurrencyKey",
            display_map,
        )
        assert result.status == "no_display_name"
        assert result.message == NO_DISPLAY_NAME_NOTICE
        assert result.unresolved_keys == ("CurrencyKey",)
        # The SQL is untouched: the key is shown honestly.
        assert result.sql.count("CurrencyKey") == 2


class TestNeverGuesses:
    """The rules that matter more than the repair itself."""

    def test_an_undeclared_relationship_is_never_joined(self, display_map) -> None:
        """Joining on similar-looking keys is how a query runs and is
        silently wrong. The guard must refuse and ask for a retry."""
        result = _repair(
            "SELECT f.ProductSubcategoryKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f GROUP BY f.ProductSubcategoryKey",
            display_map,
        )
        assert result.status == "needs_retry"
        assert "DimProductSubcategory" in result.message
        assert result.sql.count("JOIN") == 0

    def test_retry_feedback_names_the_table_join_and_column(self, display_map) -> None:
        """ "Use a readable column" is the instruction the model already
        ignored, so the feedback has to be specific to be worth a retry."""
        result = _repair(
            "SELECT f.ProductSubcategoryKey, SUM(f.SalesAmount) AS s "
            "FROM FactInternetSales f GROUP BY f.ProductSubcategoryKey",
            display_map,
        )
        assert "DimProductSubcategory" in result.message
        assert "ProductSubcategoryKey" in result.message
        assert "EnglishProductSubcategoryName" in result.message

    def test_a_key_inside_an_aggregate_is_a_measure_not_a_label(self, display_map) -> None:
        """`COUNT(DISTINCT f.CustomerKey)` counts customers. Swapping the
        key for a name would change what the query computes."""
        sql = (
            "SELECT p.EnglishProductName AS Product, "
            "COUNT(DISTINCT f.CustomerKey) AS Customers "
            "FROM FactInternetSales f "
            "INNER JOIN DimProduct p ON f.ProductKey = p.ProductKey "
            "GROUP BY p.EnglishProductName"
        )
        result = _repair(sql, display_map)
        assert result.status == "ok"
        assert result.sql == sql

    def test_correct_sql_is_left_byte_for_byte_alone(self, display_map) -> None:
        sql = (
            "SELECT p.EnglishProductName AS Product, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f "
            "INNER JOIN DimProduct p ON f.ProductKey = p.ProductKey "
            "GROUP BY p.EnglishProductName"
        )
        result = _repair(sql, display_map)
        assert result.status == "ok"
        assert result.sql == sql

    def test_an_already_joined_dimension_is_not_joined_twice(self, display_map) -> None:
        """A second copy of the dimension would multiply rows and silently
        change every measure in the query."""
        result = _repair(
            "SELECT f.ProductKey, SUM(f.SalesAmount) AS TotalSales "
            "FROM FactInternetSales f "
            "INNER JOIN DimProduct d ON f.ProductKey = d.ProductKey "
            "GROUP BY f.ProductKey",
            display_map,
        )
        assert result.status == "repaired"
        assert result.sql.upper().count("JOIN DIMPRODUCT") == 1

    def test_grouping_on_a_dimensions_own_key_needs_no_join(self, display_map) -> None:
        result = _repair(
            "SELECT d.ProductKey, COUNT(*) AS N FROM DimProduct d GROUP BY d.ProductKey",
            display_map,
        )
        assert result.status == "repaired"
        assert "EnglishProductName" in result.sql
        assert "JOIN" not in result.sql.upper()

    def test_a_query_with_no_entity_is_untouched(self, display_map) -> None:
        sql = "SELECT SUM(f.SalesAmount) AS TotalSales FROM FactInternetSales f"
        assert _repair(sql, display_map).status == "ok"

    def test_unparseable_sql_is_left_to_the_validator(self, display_map) -> None:
        """Rejecting it here would give one verdict two owners."""
        result = _repair("SELECT FROM WHERE ((", display_map)
        assert result.status == "ok"

    def test_a_measure_is_never_renamed_or_dropped(self, display_map) -> None:
        result = _repair(
            "SELECT f.ProductKey, SUM(f.SalesAmount) AS TotalSales, "
            "AVG(f.OrderQuantity) AS AvgQty FROM FactInternetSales f GROUP BY f.ProductKey",
            display_map,
        )
        assert "SUM(f.SalesAmount) AS TotalSales" in result.sql
        assert "AVG(f.OrderQuantity) AS AvgQty" in result.sql


class TestSafetyIsPreserved:
    """A repair must never widen what the SQL is allowed to do."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT f.ProductKey, SUM(f.SalesAmount) AS s FROM FactInternetSales f "
            "GROUP BY f.ProductKey",
            "SELECT f.CustomerKey, COUNT(*) AS n FROM FactInternetSales f GROUP BY f.CustomerKey",
            "SELECT TOP 10 f.EmployeeKey, SUM(f.SalesAmount) AS s FROM FactInternetSales f "
            "GROUP BY f.EmployeeKey ORDER BY s DESC",
        ],
    )
    def test_every_repaired_statement_still_passes_the_allowlist(
        self, sql: str, display_map
    ) -> None:
        result = _repair(sql, display_map)
        assert result.status == "repaired"
        verdict = validate_sql(result.sql, DIALECT)
        assert verdict.is_valid, verdict.reason

    def test_the_repair_never_emits_a_non_select_statement(self, display_map) -> None:
        result = _repair(
            "SELECT f.ProductKey, SUM(f.SalesAmount) AS s FROM FactInternetSales f "
            "GROUP BY f.ProductKey",
            display_map,
        )
        upper = result.sql.upper()
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "MERGE"):
            assert forbidden not in upper

    def test_a_write_statement_is_not_made_valid_by_the_guard(self, display_map) -> None:
        """Defence in depth: the guard does not parse it as a SELECT, and
        the validator rejects it whatever the guard did."""
        result = _repair("DROP TABLE DimProduct", display_map)
        assert not validate_sql(result.sql, DIALECT).is_valid


class TestChartsAndTables:
    """A key is neither a category nor a measure (brief 6)."""

    def test_a_numeric_key_is_never_plotted_as_a_measure(self) -> None:
        """The reported chart bug: (ProductKey, TotalSales) rendered a
        scatter plot of key against sales."""
        df = pd.DataFrame({"ProductKey": [1, 2, 3], "TotalSales": [10.0, 20.0, 30.0]})
        types = classify_columns(df)
        assert recommend_chart(df, types)["chart_type"] == "scatter"  # the old behaviour
        assert recommend_chart(df, types, {"productkey"}) is None

    def test_the_display_name_is_chosen_as_the_category_axis(self) -> None:
        df = pd.DataFrame({"Product": ["A", "B"], "ProductKey": [1, 2], "TotalSales": [10.0, 20.0]})
        recommendation = recommend_chart(df, classify_columns(df), {"productkey"})
        assert recommendation is not None
        assert recommendation["x_column"] == "Product"
        assert recommendation["y_column"] == "TotalSales"

    def test_a_string_key_is_never_the_category_axis(self) -> None:
        df = pd.DataFrame(
            {"CustomerKey": ["K1", "K2"], "Customer": ["Ann", "Bo"], "Total": [1.0, 2.0]}
        )
        recommendation = recommend_chart(df, classify_columns(df), {"customerkey"})
        assert recommendation is not None
        assert recommendation["x_column"] == "Customer"

    def test_omitting_key_columns_preserves_the_previous_behaviour(self) -> None:
        """Every existing caller and test must be unaffected."""
        df = pd.DataFrame({"Region": ["N", "S"], "Total": [1.0, 2.0]})
        types = classify_columns(df)
        assert recommend_chart(df, types) == recommend_chart(df, types, set())
