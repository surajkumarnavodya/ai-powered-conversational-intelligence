"""Unit tests for top-k schema retrieval.

`retrieve_relevant_schema` talks to a real ChromaDB collection + embedding
model in production; that's exercised end-to-end by
`scripts/build_embeddings.py` and `scripts/integration_test.py`, not here.
These tests mock the Chroma collection so they run fast, offline, and
without requiring a real database or the schema index to have been built --
they check the retrieval *logic* (result shaping, top_k, error handling),
not the embedding backend.

(Schema *chunking* -- turning introspected tables into embeddable DDL text
-- is covered by `tests/test_schema_introspection.py`, since that's now
owned by `db/schema_introspection.py`, not this module.)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.exceptions import SchemaRetrievalError
from agent.state import TableSchema
from embeddings.retriever import (
    _MEASURE_INTENT_PATTERN,
    _select_by_relevance,
    _table_has_measure_column,
    retrieve_relevant_schema,
)


class TestRetrieveRelevantSchema:
    def _mock_settings(self, schema_top_k=4):
        settings = MagicMock()
        settings.schema_top_k = schema_top_k
        return settings

    def test_returns_top_k_tables_ordered_by_similarity(self, monkeypatch):
        # Distances close enough together that neither table decisively
        # outscores the other (see TestAdaptiveSelection below for the
        # decisive-gap case) -- both should survive adaptive selection.
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "documents": [["CREATE TABLE orders (...)", "CREATE TABLE customers (...)"]],
            "metadatas": [[{"table_name": "orders"}, {"table_name": "customers"}]],
            "distances": [[0.1, 0.2]],
        }
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        tables = retrieve_relevant_schema(
            "How many orders per customer?",
            top_k=2,
            db_name="testdb",
            settings=self._mock_settings(),
        )

        assert [t["table_name"] for t in tables] == ["orders", "customers"]
        assert tables[0]["similarity_score"] > tables[1]["similarity_score"]

    def test_queries_exactly_top_k_candidates_not_a_wider_pool(self, monkeypatch):
        """Regression guard: querying a wider pool than top_k was tried and
        reverted (see this module's docstring and embeddings/retriever.py's
        docstring) -- it measurably increased, not decreased, the average
        retrieved-table count on this project's own benchmark. The primary
        query must stay bounded to top_k candidates."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 20
        mock_collection.query.return_value = {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        retrieve_relevant_schema(
            "any question", top_k=2, db_name="testdb", settings=self._mock_settings()
        )

        _, kwargs = mock_collection.query.call_args
        assert kwargs["n_results"] == 2

    def test_raises_when_index_is_empty(self, monkeypatch):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 0
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        with pytest.raises(SchemaRetrievalError):
            retrieve_relevant_schema(
                "any question", db_name="testdb", settings=self._mock_settings()
            )

    def test_wraps_unexpected_query_errors(self, monkeypatch):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.side_effect = RuntimeError("embedding backend exploded")
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        with pytest.raises(SchemaRetrievalError):
            retrieve_relevant_schema(
                "any question", db_name="testdb", settings=self._mock_settings()
            )


class TestAdaptiveSelection:
    """Regression coverage for the precision fix: a fixed top_k count padded
    every simple, single-table question with near-guaranteed-irrelevant
    filler tables (measured: 25.0% relevant-table precision against 82.2%
    recall on this project's own benchmark). See
    `embeddings/retriever.py::_select_by_relevance`.
    """

    def _mock_settings(self, schema_top_k=4):
        settings = MagicMock()
        settings.schema_top_k = schema_top_k
        return settings

    def test_decisive_single_winner_trims_below_top_k(self, monkeypatch):
        # A simple, single-focus question ("total sales") -- the fact table
        # decisively outscores everything else, so only it should be kept
        # even though top_k=4 would otherwise pad the result with filler.
        mock_collection = MagicMock()
        mock_collection.count.return_value = 6
        mock_collection.query.return_value = {
            "documents": [
                # The winner carries a real measure column, which is what
                # makes trimming to it alone safe for a "total revenue"
                # question -- see `_select_by_relevance`'s measure guard,
                # and `test_a_measure_question_is_not_trimmed_to_a_table_`
                # `without_a_measure` below for the opposite case.
                [
                    "CREATE TABLE FactInternetSales (\n"
                    "    SalesOrderNumber NVARCHAR(20) PRIMARY KEY,\n"
                    "    ProductKey INTEGER NOT NULL,\n"
                    "    SalesAmount MONEY,\n"
                    ");",
                    "CREATE TABLE DimPromotion (...)",
                    "CREATE TABLE DimCurrency (...)",
                    "CREATE TABLE DimSalesReason (...)",
                ]
            ],
            "metadatas": [
                [
                    {"table_name": "FactInternetSales", "fk_targets": ""},
                    {"table_name": "DimPromotion", "fk_targets": ""},
                    {"table_name": "DimCurrency", "fk_targets": ""},
                    {"table_name": "DimSalesReason", "fk_targets": ""},
                ]
            ],
            # 0.05 vs 0.45+ -- a decisive gap once converted to similarity.
            "distances": [[0.05, 0.55, 0.6, 0.62]],
        }
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        tables = retrieve_relevant_schema(
            "What is the total revenue?", db_name="testdb", settings=self._mock_settings()
        )

        assert [t["table_name"] for t in tables] == ["FactInternetSales"]

    def test_close_cluster_keeps_full_top_k(self, monkeypatch):
        # No decisive winner (real fact-table pairs routinely score within
        # ~0.05 of each other) -- falls back to the pre-existing behavior of
        # keeping the full top_k, since trimming here risks recall.
        mock_collection = MagicMock()
        mock_collection.count.return_value = 6
        mock_collection.query.return_value = {
            "documents": [
                [
                    "CREATE TABLE FactResellerSales (...)",
                    "CREATE TABLE FactInternetSales (...)",
                    "CREATE TABLE FactSurveyResponse (...)",
                ]
            ],
            "metadatas": [
                [
                    {"table_name": "FactResellerSales", "fk_targets": ""},
                    {"table_name": "FactInternetSales", "fk_targets": ""},
                    {"table_name": "FactSurveyResponse", "fk_targets": ""},
                ]
            ],
            "distances": [[0.66, 0.67, 0.69]],
        }
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        tables = retrieve_relevant_schema(
            "total sales", top_k=3, db_name="testdb", settings=self._mock_settings()
        )

        assert len(tables) == 3

    def test_two_independent_name_matches_are_never_trimmed(self, monkeypatch):
        # Regression case: "each employee's ... sales territory region"
        # literally name-matches both DimEmployee and DimSalesTerritory, but
        # DimSalesTerritory's raw vector score leads DimEmployee's by more
        # than _DOMINANT_GAP_THRESHOLD regardless (a flat bonus added to
        # both doesn't close a pre-existing large vector gap). Two
        # independent literal name matches is itself strong evidence of a
        # genuine multi-table question -- trimming to one here would be a
        # real recall regression (caught via live calibration against
        # med_multi_employee_territory in eval/benchmark/medium.yaml).
        mock_collection = MagicMock()
        mock_collection.count.return_value = 8
        mock_collection.query.return_value = {
            "documents": [
                [
                    "CREATE TABLE DimSalesTerritory (...)",
                    "CREATE TABLE DimGeography (...)",
                    "CREATE TABLE DimDepartmentGroup (...)",
                    "CREATE TABLE DimEmployee (...)",
                ]
            ],
            "metadatas": [
                [
                    {"table_name": "DimSalesTerritory", "fk_targets": ""},
                    {"table_name": "DimGeography", "fk_targets": ""},
                    {"table_name": "DimDepartmentGroup", "fk_targets": ""},
                    {"table_name": "DimEmployee", "fk_targets": ""},
                ]
            ],
            # DimSalesTerritory leads DimEmployee by 0.21 raw -- alone, well
            # past _DOMINANT_GAP_THRESHOLD (0.18).
            "distances": [[0.4834, 0.6266, 0.6342, 0.6896]],
        }
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        tables = retrieve_relevant_schema(
            "Show each employee's name and their assigned sales territory region",
            db_name="testdb",
            settings=self._mock_settings(),
        )

        names = [t["table_name"] for t in tables]
        assert "DimSalesTerritory" in names
        assert "DimEmployee" in names

    # --- The measure guard -----------------------------------------------
    #
    # Reported 2026-10-22: "Top selling products." answered "I can only
    # answer questions about this database." Measured against the real
    # AdventureWorks index, retrieval had trimmed to `DimProductSubcategory`
    # alone -- a dimension with no sales measure in it -- so the model was
    # handed a schema that genuinely could not answer the question and
    # correctly returned the off-topic sentinel. Six phrasings of the same
    # question behaved identically; "Top selling products" *without* the
    # trailing period did not, because the period nudged the embedding
    # enough to change the lexical-match count. A one-character difference
    # flipping a question between answerable and refused is what these
    # tests exist to prevent recurring.

    _DIMENSION_DDL = (
        "CREATE TABLE DimProductSubcategory (\n"
        "    ProductSubcategoryKey INTEGER PRIMARY KEY IDENTITY,\n"
        "    ProductSubcategoryAlternateKey INTEGER,\n"
        "    EnglishProductSubcategoryName NVARCHAR(50) NOT NULL,\n"
        "    ProductCategoryKey INTEGER,\n"
        "    FOREIGN KEY (ProductCategoryKey) REFERENCES DimProductCategory (ProductCategoryKey)\n"
        ");"
    )
    _FACT_DDL = (
        "CREATE TABLE FactResellerSales (\n"
        "    SalesOrderNumber NVARCHAR(20) PRIMARY KEY,\n"
        "    ProductKey INTEGER NOT NULL,\n"
        "    OrderQuantity SMALLINT,\n"
        "    SalesAmount MONEY\n"
        ");"
    )

    def _decisive_dimension_winner(self, monkeypatch, winner_ddl):
        """A decisive single winner, with `winner_ddl` as its DDL.

        The gap (0.62 vs 0.22 similarity) is well past
        `_DOMINANT_GAP_THRESHOLD`, and only one candidate earns a lexical
        match -- so every *other* reason to keep the full top_k is absent
        and the measure guard is the only thing that can act. That is what
        makes these assertions non-vacuous.
        """
        mock_collection = MagicMock()
        mock_collection.count.return_value = 8
        mock_collection.query.return_value = {
            "documents": [
                [
                    winner_ddl,
                    "CREATE TABLE DimReseller (...)",
                    "CREATE TABLE DimPromotion (...)",
                    self._FACT_DDL,
                ]
            ],
            "metadatas": [
                [
                    {"table_name": "DimProductSubcategory", "fk_targets": ""},
                    {"table_name": "DimReseller", "fk_targets": ""},
                    {"table_name": "DimPromotion", "fk_targets": ""},
                    {"table_name": "FactResellerSales", "fk_targets": ""},
                ]
            ],
            "distances": [[0.38, 0.78, 0.80, 0.82]],
        }
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection",
            lambda client, settings, db_name: mock_collection,
        )
        return mock_collection

    @pytest.mark.parametrize(
        "question",
        [
            "Top selling products.",
            "Top selling products",
            "What are the top selling products?",
            "Show me the top 10 selling products",
            "Best selling products",
            "Which products sold the most?",
            "Worst selling products",
            "Most popular products",
            "Most profitable products",
            "Highest revenue products",
        ],
    )
    def test_a_measure_question_is_not_trimmed_to_a_table_without_a_measure(
        self, monkeypatch, question
    ):
        """Every phrasing of the reported failure keeps the fact table."""
        self._decisive_dimension_winner(monkeypatch, self._DIMENSION_DDL)

        tables = retrieve_relevant_schema(
            question, db_name="testdb", settings=self._mock_settings()
        )

        names = [t["table_name"] for t in tables]
        assert names[0] == "DimProductSubcategory", "the decisive winner still ranks first"
        assert "FactResellerSales" in names, (
            f"{question!r} asks for a measure that DimProductSubcategory cannot "
            "supply, so the fact table must survive selection"
        )

    def test_a_measure_question_still_trims_when_the_winner_has_the_measure(self, monkeypatch):
        """The guard is narrow: a decisive *fact* winner is still trusted.

        Without this, the guard would widen every measure question back to
        `top_k` and undo the precision the adaptive selection exists for.
        """
        self._decisive_dimension_winner(monkeypatch, self._FACT_DDL)

        tables = retrieve_relevant_schema(
            "Top selling products", db_name="testdb", settings=self._mock_settings()
        )

        assert len(tables) == 1

    def _candidates(self, winner_ddl):
        """Two candidates with a decisive gap, winner first."""
        return [
            TableSchema(table_name="DimProductSubcategory", ddl=winner_ddl, similarity_score=0.62),
            TableSchema(table_name="FactResellerSales", ddl=self._FACT_DDL, similarity_score=0.22),
        ]

    def test_the_branch_trims_a_lookup_question_even_without_a_measure(self):
        """No measure intent, so the guard never engages -- even though
        this winner has no measure column at all.

        Called directly rather than through `retrieve_relevant_schema`
        because most natural lookup phrasings earn two lexical matches,
        which the *pre-existing* guard already acts on; that would make
        the assertion pass without the measure guard being involved at
        all. Here `lexically_matched_count=1`, so the measure guard is
        provably the only thing that can decide.
        """
        selected = _select_by_relevance(
            self._candidates(self._DIMENSION_DDL), 4, 1, "List the product subcategories"
        )

        assert [t["table_name"] for t in selected] == ["DimProductSubcategory"]

    def test_the_branch_keeps_top_k_for_a_measure_question(self):
        """The same inputs, changing only the question, take the other path."""
        selected = _select_by_relevance(
            self._candidates(self._DIMENSION_DDL), 4, 1, "Top selling products."
        )

        assert [t["table_name"] for t in selected] == [
            "DimProductSubcategory",
            "FactResellerSales",
        ]

    def test_the_branch_is_unchanged_when_no_question_is_passed(self):
        """`question` defaults to "", which keeps any caller that omits it
        on exactly its previous behavior."""
        selected = _select_by_relevance(self._candidates(self._DIMENSION_DDL), 4, 1)

        assert [t["table_name"] for t in selected] == ["DimProductSubcategory"]


class TestTableHasMeasureColumn:
    """`_table_has_measure_column` decides whether a table could supply the
    quantity a ranking/aggregate question asks for. A surrogate key is the
    most common numeric column in a star schema's dimensions, so counting
    one as a measure would make the check vacuous."""

    def test_a_money_column_is_a_measure(self):
        assert _table_has_measure_column(
            "CREATE TABLE F (\n    ProductKey INTEGER NOT NULL,\n    SalesAmount MONEY\n);"
        )

    def test_a_quantity_column_is_a_measure(self):
        assert _table_has_measure_column(
            "CREATE TABLE F (\n    ProductKey INTEGER,\n    OrderQuantity SMALLINT\n);"
        )

    def test_a_table_of_keys_and_names_is_not(self):
        assert not _table_has_measure_column(
            "CREATE TABLE D (\n"
            "    ProductSubcategoryKey INTEGER PRIMARY KEY IDENTITY,\n"
            "    EnglishProductSubcategoryName NVARCHAR(50) NOT NULL,\n"
            "    ProductCategoryKey INTEGER\n"
            ");"
        )

    def test_a_numeric_primary_key_alone_is_not_a_measure(self):
        assert not _table_has_measure_column(
            "CREATE TABLE D (\n    CurrencyKey INTEGER PRIMARY KEY,\n    CurrencyName NVARCHAR(50)\n);"
        )

    def test_a_foreign_key_clause_is_not_read_as_a_column(self):
        assert not _table_has_measure_column(
            "CREATE TABLE D (\n"
            "    CategoryKey INTEGER PRIMARY KEY,\n"
            "    FOREIGN KEY (CategoryKey) REFERENCES DimProductCategory (CategoryKey)\n"
            ");"
        )

    def test_an_unparseable_ddl_reports_no_measure(self):
        """Fails toward recall, never toward a refusal.

        An empty or stub DDL yields "no measure", which makes
        `_select_by_relevance` keep the full top_k rather than trim. Per
        CLAUDE.md 4.2 an accuracy aid fails open: the cost is a slightly
        larger prompt, where the opposite default would reproduce exactly
        the refusal this guard was added to fix.
        """
        assert not _table_has_measure_column("CREATE TABLE FactInternetSales (...)")
        assert not _table_has_measure_column("")


class TestMeasureIntentPattern:
    """Which questions are treated as asking for a measure."""

    @pytest.mark.parametrize(
        "question",
        [
            "Top selling products.",
            "Best selling products",
            "Which products sold the most?",
            "Worst selling products",
            "Most popular products",
            "Highest revenue products",
            "Total sales by product",
            "Average order value",
            "Top 5 customers by revenue",
            "Most profitable products",
            "Sales ranking by territory",
            "Lowest margin categories",
            # Added after a 270-phrasing sweep across lead x subject x
            # punctuation found "Biggest products" as the only remaining
            # measure question retrieving no table with a measure in it.
            "Biggest products",
            "Greatest revenue by region",
            "Leading products",
            "Fewest orders by month",
            "Maximum order value",
            "Minimum unit price",
        ],
    )
    def test_measure_questions_match(self, question):
        assert _MEASURE_INTENT_PATTERN.search(question)

    @pytest.mark.parametrize(
        "question",
        [
            "List all product categories",
            "Show me the product subcategories",
            "What currencies are available?",
            "Show employee names",
            "Which cities are in the geography table?",
        ],
    )
    def test_plain_lookups_do_not_match(self, question):
        assert not _MEASURE_INTENT_PATTERN.search(question)


class TestLexicalBonus:
    """A distinctive question keyword matching a candidate's table/column
    name should be able to promote it above a candidate with better raw
    vector similarity but no lexical match -- see
    `embeddings/retriever.py::_lexical_bonus`."""

    def _mock_settings(self, schema_top_k=4):
        settings = MagicMock()
        settings.schema_top_k = schema_top_k
        return settings

    def test_column_name_match_promotes_a_weaker_vector_candidate(self, monkeypatch):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 6
        mock_collection.query.return_value = {
            "documents": [
                [
                    "CREATE TABLE FactResellerSales (...)",
                    "CREATE TABLE DimPromotion (\n    PromotionKey int PRIMARY KEY,\n    DiscountPct float\n);",
                ]
            ],
            "metadatas": [
                [
                    {"table_name": "FactResellerSales", "fk_targets": ""},
                    {"table_name": "DimPromotion", "fk_targets": ""},
                ]
            ],
            # FactResellerSales has the better raw vector score...
            "distances": [[0.3, 0.45]],
        }
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        # ...but "discount" is a literal column-name match only DimPromotion has.
        tables = retrieve_relevant_schema(
            "What was the average discount percentage?",
            top_k=2,
            db_name="testdb",
            settings=self._mock_settings(),
        )

        names = [t["table_name"] for t in tables]
        assert "DimPromotion" in names
        assert tables[0]["table_name"] == "DimPromotion"


class TestKeywordMatchExpansion:
    """Regression coverage for a real, reproduced failure: DimProduct ranked
    outside the top 15 of 31 tables (pure vector similarity) for a question
    containing the word "product," because its embedded DDL is dominated by
    nine language-variant description columns -- causing the agent to
    invent wrong column names for a table it was never shown. See
    `embeddings/retriever.py::_expand_with_keyword_matches`.
    """

    def _mock_settings(self, schema_top_k=2):
        settings = MagicMock()
        settings.schema_top_k = schema_top_k
        return settings

    def _mock_collection(self, top_k_result, all_metadatas, documents_by_id):
        collection = MagicMock()
        collection.count.return_value = len(all_metadatas)
        collection.query.return_value = top_k_result

        def _get(ids=None, include=None):
            if ids is not None:
                return {
                    "ids": ids,
                    "documents": [documents_by_id[i] for i in ids],
                }
            return {"metadatas": all_metadatas}

        collection.get.side_effect = _get
        return collection

    def test_table_matching_a_distinctive_keyword_is_added(self, monkeypatch):
        # Vector similarity only returns FactInternetSales -- DimProduct
        # never makes it into the top_k despite "product" being a literal
        # substring of its name.
        top_k_result = {
            "documents": [["CREATE TABLE FactInternetSales (...)"]],
            "metadatas": [[{"table_name": "FactInternetSales", "fk_targets": ""}]],
            "distances": [[0.2]],
        }
        all_metadatas = [
            {"table_name": "FactInternetSales", "fk_targets": ""},
            {"table_name": "DimProduct", "fk_targets": ""},
            {"table_name": "DimCurrency", "fk_targets": ""},
        ]
        documents_by_id = {"DimProduct": "CREATE TABLE DimProduct (...)"}
        mock_collection = self._mock_collection(top_k_result, all_metadatas, documents_by_id)
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        tables = retrieve_relevant_schema(
            "Show total sales by year and product name.",
            db_name="testdb",
            settings=self._mock_settings(),
        )

        names = [t["table_name"] for t in tables]
        assert "DimProduct" in names
        assert "DimCurrency" not in names  # no keyword match, no vector match -- must not appear

    def test_no_keyword_match_leaves_result_unchanged(self, monkeypatch):
        top_k_result = {
            "documents": [["CREATE TABLE FactInternetSales (...)"]],
            "metadatas": [[{"table_name": "FactInternetSales", "fk_targets": ""}]],
            "distances": [[0.2]],
        }
        all_metadatas = [
            {"table_name": "FactInternetSales", "fk_targets": ""},
            {"table_name": "DimProduct", "fk_targets": ""},
        ]
        mock_collection = self._mock_collection(top_k_result, all_metadatas, {})
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        tables = retrieve_relevant_schema(
            "What is the average order value?", db_name="testdb", settings=self._mock_settings()
        )

        assert [t["table_name"] for t in tables] == ["FactInternetSales"]

    def test_matches_are_capped_at_the_budget(self, monkeypatch):
        top_k_result: dict[str, list[list[Any]]] = {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        # Five tables all contain "product" -- only _MAX_KEYWORD_MATCHES (3)
        # should be added, picked deterministically (alphabetical).
        table_names = [
            "DimProduct",
            "DimProductCategory",
            "DimProductSubcategory",
            "FactProductInventory",
            "FactAdditionalInternationalProductDescription",
        ]
        all_metadatas = [{"table_name": name, "fk_targets": ""} for name in table_names]
        documents_by_id = {name: f"CREATE TABLE {name} (...)" for name in table_names}
        mock_collection = self._mock_collection(top_k_result, all_metadatas, documents_by_id)
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        tables = retrieve_relevant_schema(
            "product name", db_name="testdb", settings=self._mock_settings()
        )

        assert len(tables) == 3
        assert [t["table_name"] for t in tables] == [
            "DimProduct",
            "DimProductCategory",
            "DimProductSubcategory",
        ]

    def test_short_and_stopword_terms_never_trigger_a_match(self, monkeypatch):
        top_k_result: dict[str, list[list[Any]]] = {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        all_metadatas = [
            {"table_name": "FactSales", "fk_targets": ""},
            {"table_name": "DimDate", "fk_targets": ""},
        ]
        mock_collection = self._mock_collection(top_k_result, all_metadatas, {})
        monkeypatch.setattr("embeddings.retriever.get_chroma_client", lambda settings: MagicMock())
        monkeypatch.setattr(
            "embeddings.retriever.get_collection", lambda client, settings, db_name: mock_collection
        )

        # "sales", "date", "total" are all stopwords for this fallback
        # (vector similarity already handles these broad terms well).
        tables = retrieve_relevant_schema(
            "What was the total sales by date?", db_name="testdb", settings=self._mock_settings()
        )

        assert tables == []
