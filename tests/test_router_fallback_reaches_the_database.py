"""The keyword fallback is the live router here, and it misses SQL questions.

UAT-readiness Phase 11 (`OPS-015`). **Pins current behaviour, which is
wrong** -- the three `xfail(strict=True)` tests at the bottom fail the
day the routing defect is fixed, which is how the fix announces itself.

Why the fallback is what matters. `agent/orchestrator/nodes.py::router_node`
gives the LLM classifier `router_timeout_seconds` (default **5.0s**) and
falls back to `agent.orchestrator.router_contract.keyword_fallback` -- pure
regex, no model -- when that elapses. `docs/ROUTING.md` already records
that on the reference hardware routing costs 31-40s, so the fallback
"routes every question", and calls that "the intended outcome, not a
defect: the fallback is correct on every case the brief names". Measured
on this machine 2026-10-30 over eight consecutive live `/ask` calls:
**7 of 8 fell back**, so the claim about the fallback is a claim about
the whole product, not about a degraded mode.

What the measurement found. The brief's own cases are not the corpus any
more -- `eval/uat_golden/` is, and it is the set this project gates UAT
on. Routing its 28 accuracy cases through `keyword_fallback` offline
(milliseconds, no model) puts **14 of them somewhere other than
`sql_database`**. Two of those fourteen are defensible and are excluded
below by id: `uat_j8_question_the_document_does_not_cover` is a policy
question and `policy_kb` is right for it, and
`uat_j10_multi_source_attribution` deliberately spans a policy and a
count, so a single-primary fallback picking one of the two is not a
defect. That leaves **12 of 26 unambiguously database questions, 46%**,
including three of journey **J4**'s four, which
`docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md` §2.5 calls "the single
highest-value row in this document", and **both** of J11's.

Two distinct causes, both visible in `_FALLBACK_RULES`:

1. **`web_search` is tested before `sql_database` and its vocabulary is
   not news-specific.** The pattern matches a bare ``price``, so
   "...colour, list price and standard cost for every bike" routes to
   Tavily; a bare ``score`` sends "average employee satisfaction score
   per product" the same way. Verified live: that bike question returned
   HTTP 200 / `status="succeeded"` / `sources_used=["web"]` after 184s
   with no SQL, no rows and an empty `synthesized_answer`. The ordering
   itself is deliberate and documented (so "top news stories today"
   stays web), which is why this is flagged rather than quietly
   reordered.
2. **The `sql_database` pattern is a list of aggregate/superlative
   phrasings**, so an ordinary retrieval or ratio question matches
   nothing and falls through to `general_knowledge`: "What was our total
   revenue last year?" has no `total ... by`, "What is the average order
   value?" reads as prose, "List the products that are the color black"
   needs the literal `list all`.

Falling through to `general_knowledge` is the worse of the two outcomes,
not the milder one. `ENABLE_GENERAL_KNOWLEDGE_ANSWERS` is off by
default, so the caller gets one fixed no-match sentence; and per
`HAL-006` no input guard runs on that route, so the question is never
even recorded as refused.

This module needs no database, no Ollama and no network: `keyword_fallback`
is a pure function, which is what makes the measurement cheap enough to
keep as a gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.orchestrator.nodes import _catalog_keywords
from agent.orchestrator.router_contract import keyword_fallback
from eval.dataset_loader import load_benchmark

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_DIR = _REPO_ROOT / "eval" / "uat_golden"

#: Table names from the two databases the golden set is written against
#: (`AdventureWorksDW2025` and the HR schema). Supplied literally rather
#: than introspected so this test needs no database -- `_catalog_keywords`
#: splits CamelCase, so `DimProduct` contributes `product`, which is how a
#: real catalog would reach the fallback too.
_CATALOG = """
DimProduct DimProductCategory DimProductSubcategory DimCustomer DimDate
DimEmployee DimGeography DimReseller DimSalesTerritory DimCurrency
FactInternetSales FactResellerSales FactSurveyResponse
Employees Departments Positions LeaveRequests Attendance Payroll
"""


@pytest.fixture(scope="module")
def catalog_keywords() -> frozenset[str]:
    keywords = _catalog_keywords(_CATALOG)
    # Non-vacuity: the fallback's rule 4 skips `sql_database` entirely
    # when the question names nothing in the catalog, so an empty or
    # unsplit keyword set would make every assertion below meaningless
    # for a reason that has nothing to do with the routing rules.
    assert "product" in keywords, keywords
    assert "sales" in keywords, keywords
    return keywords


#: The golden set's accuracy categories -- the cases whose correct
#: destination is the database. The rest of `eval/uat_golden/` is
#: `behaviour.yaml`'s adversarial and ambiguity cases, which are
#: deliberately not expected to route to SQL.
_ACCURACY_CATEGORIES = frozenset(
    {
        "aggregation",
        "business_questions",
        "cost_estimation",
        "date_filtering",
        "group_by_having",
        "joins",
        "metric_consistency",
        "multi_table_analysis",
        "simple_filtering",
        "window_functions",
    }
)

#: Accuracy cases whose non-SQL route is defensible, each with the reason.
#: Named individually rather than filtered by a rule, so adding one is a
#: visible decision.
_LEGITIMATELY_NOT_SQL = {
    "uat_j8_question_the_document_does_not_cover": "a policy question -- policy_kb is correct",
    "uat_j10_multi_source_attribution": (
        "deliberately spans a policy and a count, so a single-primary fallback "
        "picking one of the two is not a defect"
    ),
}


@pytest.fixture(scope="module")
def sql_questions() -> list:
    """Every golden case whose correct destination is the database."""
    dataset = load_benchmark(_GOLDEN_DIR)
    cases = [
        case
        for case in dataset.standalone_cases
        if case.question
        and case.category in _ACCURACY_CATEGORIES
        and case.id not in _LEGITIMATELY_NOT_SQL
    ]
    # Non-vacuity: a renamed category would empty this list and every
    # assertion built on it would pass without measuring anything.
    assert len(cases) >= 20, f"only {len(cases)} case(s) selected -- did a category rename?"
    return cases


def _route(question: str, keywords: frozenset[str]) -> str:
    return keyword_fallback(question, keywords).primary


class TestTheFallbackItselfBehavesAsDocumented:
    """The parts `docs/ROUTING.md` claims, re-measured rather than trusted."""

    def test_a_news_question_still_routes_to_the_web(self, catalog_keywords):
        assert _route("Top news stories for today around the world", catalog_keywords) == (
            "web_search"
        )

    def test_an_aggregate_question_naming_the_catalog_routes_to_sql(self, catalog_keywords):
        assert _route("What are total sales by territory?", catalog_keywords) == "sql_database"

    def test_a_greeting_routes_to_conversation(self, catalog_keywords):
        assert _route("hello", catalog_keywords) == "conversation"

    def test_an_aggregate_naming_nothing_in_the_catalog_is_not_a_database_question(
        self, catalog_keywords
    ):
        """Rule 4, which is the reason `table_keywords` exists at all."""
        assert _route("How many moons does Jupiter have?", catalog_keywords) != "sql_database"


class TestCommercialVocabularyIsReadAsNews:
    """Cause 1: `web_search` is tested first and matches ordinary
    business words. Each of these is a database question."""

    @pytest.mark.parametrize(
        "question",
        [
            "Show me the product name, colour, list price and standard cost for every bike.",
            "What is the average employee satisfaction score per product?",
            "Which products have the highest list price?",
        ],
    )
    @pytest.mark.xfail(
        strict=True,
        reason="OPS-015: `price`/`score` match the web_search rule, which is tested "
        "before sql_database. Open, flagged for an owner decision -- reordering the "
        "rules or narrowing the web vocabulary both change routing behaviour that "
        "nothing currently measures.",
    )
    def test_a_database_question_mentioning_price_or_score_reaches_the_database(
        self, question, catalog_keywords
    ):
        assert _route(question, catalog_keywords) == "sql_database"


class TestOrdinaryPhrasingFallsThroughToGeneralKnowledge:
    """Cause 2: the `sql_database` pattern only recognises
    aggregate/superlative phrasings."""

    @pytest.mark.parametrize(
        "question",
        [
            "What was our total revenue last year?",
            "What is the average order value?",
            "List the products that are the color black.",
            "Show sales by country as a chart.",
        ],
    )
    @pytest.mark.xfail(
        strict=True,
        reason="OPS-015: no `_FALLBACK_RULES` pattern matches these, so they reach "
        "`general_knowledge` -- which with ENABLE_GENERAL_KNOWLEDGE_ANSWERS off is one "
        "fixed sentence, and per HAL-006 is not even recorded as a refusal. Open.",
    )
    def test_an_ordinarily_phrased_database_question_reaches_the_database(
        self, question, catalog_keywords
    ):
        assert _route(question, catalog_keywords) == "sql_database"


class TestTheGoldenSetAsAWhole:
    def test_the_highest_value_journey_is_currently_unroutable(
        self, sql_questions, catalog_keywords
    ):
        """J4 is "Governed metric correctness -- is the number the
        number?", and the UAT script calls it the single highest-value
        row it contains. Not one of its questions reaches the database.

        Deliberately **not** `xfail`: this asserts the measured fact, so
        it keeps passing once some J4 questions are fixed and only needs
        revisiting when all of them are. The companion assertion below is
        the one that fails on a fix."""
        j4 = [case for case in sql_questions if case.journey == "J4"]
        assert len(j4) >= 3, f"only {len(j4)} J4 case(s) -- has the golden set been retagged?"
        routed = {case.id: _route(case.question, catalog_keywords) for case in j4}
        reaching = [case_id for case_id, route in routed.items() if route == "sql_database"]
        assert len(reaching) <= 1, routed

    @pytest.mark.xfail(
        strict=True,
        reason="OPS-015: 12 of the golden set's 26 unambiguous SQL questions do not "
        "reach `sql_database` through the keyword fallback, which is the live router on "
        "hardware where the classifier cannot meet router_timeout_seconds. Open, "
        "flagged -- this test turning green is the signal that it is fixed.",
    )
    def test_every_golden_sql_question_reaches_the_database(self, sql_questions, catalog_keywords):
        misrouted = {
            case.id: _route(case.question, catalog_keywords)
            for case in sql_questions
            if _route(case.question, catalog_keywords) != "sql_database"
        }
        assert not misrouted, f"{len(misrouted)} of {len(sql_questions)} misrouted: {misrouted}"
