"""Routing decisions: the structured contract and its deterministic fallback.

Two layers are pinned separately, because they fail for different reasons
and a test that conflates them hides which one broke:

* `RouterDecision` -- does a model reply parse into a usable decision, and
  are the bad ones rejected rather than half-accepted?
* `keyword_fallback` -- with no model at all, does the question still
  reach the right source?

**The fallback cases are the ones the brief named, and they are tested
against the fallback on purpose.** On the reference machine the router's
model call measured 31-40s against a 5s budget
(`Settings.router_timeout_seconds`), so the fallback is what actually
routes here -- see `docs/ROUTING.md`. Pinning the brief's cases against
the model instead would be pinning a path this hardware never takes, and
would need a live Ollama in a suite that is fully mocked (CLAUDE.md 7).
"""

from __future__ import annotations

import pytest

from agent.orchestrator.router_contract import (
    ANSWER_STYLES,
    ROUTED_SOURCE_NAMES,
    RouterDecision,
    keyword_fallback,
    parse_router_decision,
)

#: Tokens standing in for a real catalog, so the `sql_database` rule's
#: "does the question name anything we hold?" condition has something to
#: check against (the brief's rule 4).
CATALOG = frozenset(
    {"employee", "employees", "sales", "region", "product", "leave", "department", "order"}
)


class TestTheBriefsRoutingCases:
    """The twelve cases named in the brief, plus the compound one."""

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("Sick leave policy", "policy_kb"),
            ("What is the maternity leave policy?", "policy_kb"),
            ("How many employees joined in September?", "sql_database"),
            ("Total sales by region for Q2", "sql_database"),
            ("Top news of India today", "web_search"),
            ("Weather in Mumbai tomorrow", "web_search"),
            ("Generate an image of a futuristic office", "image_generation"),
            ("Find videos about agile ceremonies", "media_search"),
            ("What is a foreign key?", "general_knowledge"),
            ("Hi", "conversation"),
        ],
    )
    def test_each_question_reaches_its_source(self, question: str, expected: str) -> None:
        assert keyword_fallback(question, CATALOG).primary == expected

    def test_a_policy_question_does_not_become_a_database_question(self) -> None:
        """The reported bug, as a routing assertion.

        "Sick leave policy" names something the catalog holds (`leave`)
        and would match a table keyword, but it is a request for a rule,
        not for rows. The brief's rule 3 says so explicitly: a
        LeaveRequests table does not make this a SQL question.
        """
        decision = keyword_fallback("Sick leave policy", CATALOG)

        assert decision.primary == "policy_kb"
        assert "sql_database" not in decision.ordered_sources()
        assert ROUTED_SOURCE_NAMES[decision.primary] == "policy"

    def test_an_aggregate_naming_nothing_we_hold_is_not_a_database_question(self) -> None:
        """Rule 4: the aggregate *shape* alone is not enough.

        Without this, "how many moons does Jupiter have" routes to SQL on
        the strength of "how many" and returns nothing, slowly.
        """
        assert keyword_fallback("How many moons does Jupiter have?", CATALOG).primary != (
            "sql_database"
        )
        # The same shape, naming something the catalog does hold, is.
        assert keyword_fallback("How many employees are there?", CATALOG).primary == (
            "sql_database"
        )

    def test_show_me_is_a_search_not_a_generation(self) -> None:
        """Rule 6: only an explicit request to create something routes to
        a generation source."""
        assert keyword_fallback("Show me pictures of the office", CATALOG).primary == (
            "media_search"
        )
        assert keyword_fallback("Create a logo for the office", CATALOG).primary == (
            "image_generation"
        )


class TestCompoundQuestions:
    """A question that genuinely needs two sources."""

    def test_the_model_can_return_a_policy_plus_database_route(self) -> None:
        """The brief's "Policy on leave and how many I've taken".

        Asserted through the parser rather than the fallback because the
        fallback deliberately returns a single `primary` -- a keyword
        match is not evidence that *both* halves are needed, and
        inventing a second source from one match would route questions
        to the database on no evidence at all.
        """
        decision = parse_router_decision(
            '{"primary":"policy_kb","secondary":["sql_database"],"fallback":null,'
            '"confidence":0.85,"needs_clarification":false,"clarifying_question":null,'
            '"source_queries":{"policy_kb":"leave entitlement",'
            '"sql_database":"leave days used by the current user"},'
            '"answer_style":"explanation","reason":"policy plus usage"}'
        )

        assert decision is not None
        assert decision.primary == "policy_kb"
        assert decision.secondary == ["sql_database"]
        assert decision.ordered_sources() == ["policy_kb", "sql_database"]
        assert decision.internal_sources() == ["policy", "sql"]
        assert decision.source_queries["sql_database"].startswith("leave days used")


class TestParsingAModelReply:
    def test_a_clean_reply_parses(self) -> None:
        decision = parse_router_decision('{"primary":"web_search","confidence":0.9}')

        assert decision is not None
        assert decision.primary == "web_search"
        assert decision.internal_sources() == ["web"]

    @pytest.mark.parametrize(
        "raw",
        [
            'Sure! ```json\n{"primary":"policy_kb"}\n```',
            'Here is the routing:\n{"primary":"policy_kb"}',
            '{"primary":"policy_kb"}\nHope that helps.',
        ],
    )
    def test_commentary_and_fences_are_recovered(self, raw: str) -> None:
        """A local model told to emit bare JSON routinely does not.

        Recovering the brace-delimited span costs nothing and avoids
        throwing away a correct decision over its packaging.
        """
        decision = parse_router_decision(raw)

        assert decision is not None
        assert decision.primary == "policy_kb"

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "not json", "[]", "null", '{"primary":"crm_database"}', '{"secondary":["x"]}'],
    )
    def test_unusable_replies_are_rejected_rather_than_guessed(self, raw: str) -> None:
        """None is the signal to retry and then fall back.

        An invented source name fails the whole decision precisely
        because `primary` is the one field with no safe default -- there
        is no "probably meant SQL".
        """
        assert parse_router_decision(raw) is None

    def test_an_unknown_secondary_is_dropped_not_fatal(self) -> None:
        """One bad pick must not discard the good ones."""
        decision = parse_router_decision(
            '{"primary":"policy_kb","secondary":["crm_database","sql_database"]}'
        )

        assert decision is not None
        assert decision.secondary == ["sql_database"]

    def test_an_unknown_answer_style_degrades_to_explanation(self) -> None:
        """Style only shapes prose length, so a wrong one is cosmetic --
        discarding a correct route over it would not be."""
        decision = parse_router_decision('{"primary":"policy_kb","answer_style":"interpretive"}')

        assert decision is not None
        assert decision.answer_style == "explanation"

    @pytest.mark.parametrize("style", sorted(ANSWER_STYLES))
    def test_every_documented_style_survives(self, style: str) -> None:
        decision = parse_router_decision(f'{{"primary":"policy_kb","answer_style":"{style}"}}')

        assert decision is not None
        assert decision.answer_style == style

    @pytest.mark.parametrize("confidence", [-0.1, 1.5])
    def test_confidence_outside_zero_to_one_is_rejected(self, confidence: float) -> None:
        assert parse_router_decision(f'{{"primary":"policy_kb","confidence":{confidence}}}') is None

    def test_a_clarification_request_round_trips(self) -> None:
        decision = parse_router_decision(
            '{"primary":"user_documents","needs_clarification":true,'
            '"clarifying_question":"Which report do you mean?"}'
        )

        assert decision is not None
        assert decision.needs_clarification is True
        assert decision.clarifying_question == "Which report do you mean?"

    def test_a_blank_clarifying_question_is_none(self) -> None:
        """`needs_clarification` with nothing to ask would stall the
        question behind an empty prompt."""
        decision = parse_router_decision(
            '{"primary":"user_documents","needs_clarification":true,"clarifying_question":"  "}'
        )

        assert decision is not None
        assert decision.clarifying_question is None


class TestNoRetrievalRoutes:
    """`general_knowledge` and `conversation` run no source at all."""

    @pytest.mark.parametrize("primary", ["general_knowledge", "conversation"])
    def test_they_map_to_no_source(self, primary: str) -> None:
        decision = RouterDecision(primary=primary)

        assert decision.internal_sources() == []
        assert decision.is_no_retrieval() is True

    def test_a_retrieval_route_is_not_mistaken_for_one(self) -> None:
        assert RouterDecision(primary="policy_kb").is_no_retrieval() is False


class TestFallbackProperties:
    def test_confidence_is_reported_as_low(self) -> None:
        """A keyword match is evidence, not a calibrated probability.

        Reporting it high would let a consumer treat a guess as a
        judgement -- the same reason `source_signals` documents its own
        scores as evidence (CLAUDE.md 13).
        """
        assert keyword_fallback("Sick leave policy", CATALOG).confidence == pytest.approx(0.3)

    def test_an_unmatched_question_still_routes_somewhere(self) -> None:
        """Routing to nothing silently drops the question, which is worse
        than routing it imperfectly."""
        decision = keyword_fallback("zxcvbnm qwerty", CATALOG)

        assert decision.primary == "general_knowledge"
        assert decision.fallback == "web_search"

    def test_it_needs_no_catalog_to_work(self) -> None:
        """The fallback runs when the model is unreachable, which may be
        the same moment the catalog's stores are unreachable."""
        assert keyword_fallback("Sick leave policy").primary == "policy_kb"

    def test_it_never_asks_for_clarification(self) -> None:
        """Only the model judges ambiguity. A keyword match that stopped
        to ask a question would be a guess wearing a question mark."""
        for question in ("Sick leave policy", "Show me the report", "Hi"):
            assert keyword_fallback(question, CATALOG).needs_clarification is False
