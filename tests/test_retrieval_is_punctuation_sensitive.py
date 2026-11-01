"""Schema retrieval changes with a question's punctuation.

UAT-readiness Phase 7, `CTX-007`. **Open and flagged for an owner --
these tests pin the CURRENT behaviour, which is a defect.**

Measured 2026-10-29 against the live `adventureworks` Chroma collection,
`schema_top_k=4` plus FK-bridge expansion:

| question                       | tables retrieved |
|--------------------------------|------------------|
| `top selling products`         | 5                |
| `top selling products.`        | **8**            |
| `top selling products?`        | **6**            |
| upper / title / extra internal and trailing spaces / leading newline | 5 (unchanged) |

So a trailing period adds three tables -- `DimSalesReason`,
`FactInternetSales` and `FactInternetSalesReason` -- and a question mark
adds `DimPromotion`. Two other questions in the sweep
(`total sales by product category`, `list every employee with their
department`) were stable across all eight variants, so this is not
uniform: it bites where the relevance scores are close.

**The mechanism**, and why it is not simply a missing `.strip()`.
`embeddings.retriever._extract_keywords` already normalises for the
*lexical* path (`[a-zA-Z]+` over `question.lower()`), which is why case
and whitespace have no effect above. Punctuation survives into the
**embedding** query instead, shifting the vector slightly; and
`_select_by_relevance` decides *how many* tables to keep from the gap
between consecutive scores (`_DOMINANT_GAP_THRESHOLD`). A small shift
moves a score across that gap, so the retrieved *count* changes, not
just the order.

**Why this is flagged rather than fixed.** Normalising the question
before embedding would change which tables reach the generation prompt,
which changes the SQL, which moves the only execution-accuracy numbers
this project has (`docs/EVALUATION_CURRENT.md`) -- and it cannot be
assumed to be an improvement: the trailing-period variant retrieves
`FactInternetSales`, which for "top selling products" is arguably the
*more* correct table than the `FactResellerSales` the base question
gets. CLAUDE.md 13 separately records that this exact question returns
poor results. So "normalise and keep the base behaviour" might make
retrieval worse on the very question that exposed the bug. Deciding that
needs the benchmark re-run, which is an owner decision, not a drive-by
fix.

These tests use a stubbed Chroma collection rather than the live one, so
they assert the *mechanism* -- that punctuation reaches the embedded
query text unmodified -- and not the live table counts, which depend on
an indexed database no CI machine has.

**When the decision is made, this file is what should fail.** The
`xfail(strict=True)` marks mean a fix cannot land silently: implementing
normalisation turns these into unexpected passes and fails the suite,
which is the signal to delete them and record the new measurement.
"""

from __future__ import annotations

import pytest

from embeddings.retriever import _extract_keywords


class TestTheLexicalPathIsAlreadyNormalised:
    """The half that is correct today, pinned so a fix cannot regress it."""

    @pytest.mark.parametrize(
        "variant",
        [
            "top selling products",
            "top selling products.",
            "top selling products?",
            "TOP SELLING PRODUCTS",
            "Top Selling Products",
            "  top  selling  products  ",
            "\ntop selling products",
        ],
    )
    def test_keyword_extraction_is_invariant(self, variant):
        assert _extract_keywords(variant) == _extract_keywords("top selling products")


class TestTheEmbeddedQueryIsNotNormalised:
    """The half that is the finding: punctuation reaches the vector query."""

    def _captured_query(self, monkeypatch, question: str) -> str:
        """Returns the text `retrieve_relevant_schema` embeds for `question`."""
        from embeddings import retriever

        captured: list[str] = []

        class _FakeCollection:
            def count(self) -> int:
                return 1

            def query(self, query_texts, n_results, **kwargs):
                captured.append(query_texts[0])
                return {
                    "documents": [[]],
                    "metadatas": [[]],
                    "distances": [[]],
                    "ids": [[]],
                }

            def get(self, **kwargs):
                return {"documents": [], "metadatas": [], "ids": []}

        monkeypatch.setattr(retriever, "get_chroma_client", lambda settings: object())
        monkeypatch.setattr(retriever, "get_collection", lambda *a, **k: _FakeCollection())
        retriever.retrieve_relevant_schema(question, db_name="default")
        assert captured, "the retriever never issued a vector query"
        return captured[0]

    def test_the_question_is_embedded_verbatim(self, monkeypatch):
        # Establishes the baseline the xfails below rest on: whatever the
        # caller typed is what gets embedded.
        assert self._captured_query(monkeypatch, "top selling products") == "top selling products"

    @pytest.mark.xfail(
        strict=True,
        reason="CTX-007: a trailing period reaches the embedding, and measurably "
        "changed adventureworks retrieval from 5 tables to 8 on 2026-10-29. Open "
        "and flagged -- normalising here changes execution accuracy and needs the "
        "benchmark re-run. See this module's docstring.",
    )
    def test_a_trailing_period_is_stripped_before_embedding(self, monkeypatch):
        assert self._captured_query(monkeypatch, "top selling products.") == "top selling products"

    @pytest.mark.xfail(
        strict=True,
        reason="CTX-007: a question mark reaches the embedding, and measurably added "
        "DimPromotion to adventureworks retrieval on 2026-10-29. Same owner decision.",
    )
    def test_a_question_mark_is_stripped_before_embedding(self, monkeypatch):
        assert self._captured_query(monkeypatch, "top selling products?") == "top selling products"

    @pytest.mark.xfail(
        strict=True,
        reason="CTX-007: surrounding whitespace reaches the embedding too. It had no "
        "measured effect on the live collection, unlike the two above, but it is the "
        "same missing normalisation and would be fixed by the same change.",
    )
    def test_surrounding_whitespace_is_stripped_before_embedding(self, monkeypatch):
        assert (
            self._captured_query(monkeypatch, "  top selling products  ") == "top selling products"
        )
