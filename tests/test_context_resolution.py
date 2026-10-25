"""Tests for `agent.context_resolution` -- the decision half.

Organised around the behaviours the feature has to get right rather than
around the functions that implement them, and deliberately **paraphrased**
rather than replaying one canonical phrasing of each case: a resolver that
only passes on the exact sentences someone wrote down is a lookup table.
So the product case is asked four ways, the correction three, and the
"this is a new topic" case with wording designed to look like a follow-up.

`resolve_context` takes its window size, threshold and LLM callable as
parameters, so nothing here needs a graph, a settings object or a server.
Tier 2 is driven by a plain function returning canned JSON, including the
failure shapes (raises, garbage, a rewrite that drops the question).
"""

from __future__ import annotations

import json

import pytest

from agent.context_resolution import (
    ContextRelation,
    resolve_context,
    score_antecedents,
    strip_reset_clause,
)
from agent.conversation_memory import build_memory
from agent.state import ConversationExchange


def turn(
    question: str,
    *,
    answer: str | None = None,
    sql: str | None = None,
    tables: list[str] | None = None,
    status: str = "succeeded",
) -> ConversationExchange:
    exchange: ConversationExchange = {
        "question": question,
        "sql": sql,
        "tables": tables or [],
        "status": status,
    }
    if answer is not None:
        exchange["answer"] = answer
    return exchange


def resolve(question: str, history: list[ConversationExchange] | None = None, **kwargs):
    """`resolve_context` with tier 2 off unless a test asks for it.

    Tier 2 off is the right default for this file: it keeps every
    assertion about the deterministic layer, which is what runs for the
    overwhelming majority of real questions.
    """
    kwargs.setdefault("llm", None)
    return resolve_context(question, history, recent_turns=6, **kwargs)


# --- Product research -------------------------------------------------------


class TestProductFollowups:
    @pytest.fixture
    def history(self) -> list[ConversationExchange]:
        return [
            turn(
                "Tell me about the Google Pixel 10a",
                answer="The Google Pixel 10a is a mid-range phone with a Tensor G5.",
            )
        ]

    @pytest.mark.parametrize(
        "question",
        [
            "Price?",
            "What is its price?",
            "how much does it cost",
            "Battery life?",
        ],
    )
    def test_a_bare_attribute_resolves_against_the_product(self, question, history):
        result = resolve(question, history)

        assert result.relation is ContextRelation.FOLLOWUP
        assert result.antecedent is not None
        assert "Pixel 10a" in result.resolved_question
        assert result.was_rewritten

    def test_a_pronoun_is_replaced_by_the_named_product(self, history):
        result = resolve("What is its price in the US?", history)

        assert "Google Pixel 10a's price" in result.resolved_question
        # The rest of the question survives -- the market is the whole ask.
        assert "in the US" in result.resolved_question

    def test_the_original_question_is_never_replaced(self, history):
        result = resolve("Price?", history)
        assert result.original_question == "Price?"
        assert result.resolved_question != result.original_question

    def test_a_comparison_request_keeps_its_own_subject_and_the_context(self, history):
        result = resolve("compare it with the Galaxy A56", history)
        assert "Galaxy A56" in result.resolved_question
        assert "Pixel 10a" in result.resolved_question


# --- Programming and debugging ---------------------------------------------


class TestTechnicalFollowups:
    def test_show_an_example_continues_the_technical_topic(self):
        history = [turn("Explain dependency injection in .NET")]
        result = resolve("Show an example.", history)

        assert result.relation is ContextRelation.FOLLOWUP
        assert result.antecedent is not None
        assert "dependency injection" in result.resolved_question.lower()

    def test_how_do_i_fix_it_continues_the_same_issue(self):
        history = [turn("My Angular application has a CORS error")]
        result = resolve("How do I fix it?", history)

        assert result.relation is ContextRelation.FOLLOWUP
        assert "CORS" in result.resolved_question or "Angular" in result.resolved_question

    def test_a_follow_up_may_both_refer_back_and_narrow(self):
        history = [turn("Explain dependency injection in .NET")]
        result = resolve("Show an example using constructor injection instead.", history)

        assert result.relation.uses_context
        # Its own new content is preserved, not replaced by the antecedent.
        assert "constructor" in result.resolved_question


# --- Writing and artifacts -------------------------------------------------


class TestRefinements:
    @pytest.fixture
    def drafted(self) -> list[ConversationExchange]:
        return [
            turn(
                "Draft a LinkedIn post about our new analytics release",
                answer="Excited to share that we have shipped a new analytics release!",
            )
        ]

    @pytest.mark.parametrize(
        "question",
        [
            "Make it more professional.",
            "Can you make that more formal?",
            "shorten it",
            "rewrite it in bullet points",
        ],
    )
    def test_acting_on_a_previous_answer_is_a_refinement(self, question, drafted):
        result = resolve(question, drafted)

        assert result.relation is ContextRelation.REFINEMENT
        assert result.antecedent is not None
        assert result.antecedent["question"] == drafted[0]["question"]

    def test_expanding_one_part_of_a_plan_targets_the_plan(self):
        history = [turn("Create a three-day study plan for the AZ-900 exam")]
        result = resolve("Expand day two.", history)

        assert result.relation is ContextRelation.REFINEMENT
        assert "study plan" in result.resolved_question.lower()
        # The part being expanded is not lost in the rewrite.
        assert "day two" in result.resolved_question.lower()

    def test_a_sql_refinement_is_a_refinement_not_a_new_query(self):
        history = [
            turn(
                "Write a SQL query for revenue by month",
                sql="SELECT month, SUM(amount) FROM orders GROUP BY month",
                tables=["orders"],
            )
        ]
        result = resolve("Exclude cancelled orders.", history)

        assert result.relation is ContextRelation.REFINEMENT
        assert "cancelled" in result.resolved_question.lower()


# --- Business analysis and scenarios ---------------------------------------


class TestScenariosAndConstraints:
    def test_a_what_if_carries_the_established_figures(self):
        history = [turn("Analyze a project budget of ₹2 crore")]
        result = resolve("What if costs increase by 15%?", history)

        assert result.relation.uses_context
        assert "15%" in result.resolved_question
        # The baseline it varies is carried, not re-asked for.
        assert "constraints in force" in result.resolved_question
        assert any("2 crore" in c.text for c in result.carried_constraints)

    def test_a_corrected_budget_replaces_the_old_one(self):
        history = [turn("Recommend laptops for programming under ₹80,000")]
        result = resolve("I meant ₹60,000.", history)

        assert result.relation is ContextRelation.CORRECTION
        carried = [c.text for c in result.carried_constraints]
        replaced = [c.text for c in result.replaced_constraints]
        assert any("60,000" in text for text in carried)
        assert any("80,000" in text for text in replaced)
        assert all("80,000" not in text for text in carried)

    @pytest.mark.parametrize(
        "question",
        [
            "I meant ₹60,000.",
            "sorry, i meant 60,000",
            "actually, make it ₹60,000",
        ],
    )
    def test_a_correction_is_recognised_however_it_is_phrased(self, question):
        history = [turn("Recommend laptops under ₹80,000")]
        assert resolve(question, history).relation is ContextRelation.CORRECTION

    def test_a_replaced_constraint_is_reported_not_silently_dropped(self):
        history = [turn("laptops under ₹80,000")]
        result = resolve("I meant ₹60,000.", history)
        # The point: a caller can tell the user what stopped applying.
        assert result.replaced_constraints


# --- Not carrying context -------------------------------------------------


class TestIndependence:
    @pytest.fixture
    def product(self) -> list[ConversationExchange]:
        return [turn("Tell me about the Google Pixel 10a")]

    def test_an_unrelated_question_carries_nothing(self, product):
        result = resolve("What is the price of crude oil?", product)

        assert result.relation is ContextRelation.NEW_TOPIC
        assert result.antecedent is None
        assert result.resolved_question == "What is the price of crude oil?"
        assert not result.was_rewritten

    @pytest.mark.parametrize(
        "question",
        [
            "Forget that. Explain database normalization.",
            "Never mind. Explain database normalization.",
            "New topic: explain photosynthesis",
            "Let's start a new topic: explain photosynthesis",
            "Different question entirely: explain photosynthesis",
        ],
    )
    def test_an_explicit_reset_starts_clean(self, question, product):
        result = resolve(question, product)

        assert result.relation is ContextRelation.NEW_TOPIC
        assert result.antecedent is None
        assert "explain" in result.resolved_question.lower()
        # The reset instruction itself does not reach retrieval.
        assert "forget" not in result.resolved_question.lower()
        assert "new topic" not in result.resolved_question.lower()

    def test_a_reset_outranks_a_pronoun_inside_the_part_being_dropped(self, product):
        result = resolve("Forget that, it does not matter. Explain normalization.", product)
        assert result.relation is ContextRelation.NEW_TOPIC

    def test_stripping_a_reset_never_leaves_an_empty_question(self):
        # Nothing but the reset clause: there is no request left to strip
        # down to, so the original text is kept rather than an empty one.
        assert strip_reset_clause("forget that").strip() != ""

    def test_a_short_question_is_not_automatically_a_followup(self, product):
        result = resolve("Explain photosynthesis.", product)
        assert result.relation is ContextRelation.NEW_TOPIC

    def test_a_long_question_is_not_automatically_a_new_topic(self, product):
        result = resolve(
            "Could you please tell me what its price would be in the United States market?",
            product,
        )
        assert result.relation is ContextRelation.FOLLOWUP


# --- Ambiguity and missing context ----------------------------------------


class TestAmbiguity:
    def test_two_competing_products_are_clarified_not_guessed(self):
        history = [
            turn("Tell me about the Google Pixel 10a"),
            turn("Tell me about the Samsung Galaxy A56"),
        ]
        result = resolve("What is its price?", history)

        assert result.relation is ContextRelation.AMBIGUOUS
        assert result.antecedent is None
        assert result.clarification
        # The clarification names the real candidates, quoted.
        assert "Pixel 10a" in result.clarification
        assert "Galaxy A56" in result.clarification

    def test_a_question_carrying_its_own_disambiguator_is_not_ambiguous(self):
        history = [
            turn("Tell me about the Google Pixel 10a"),
            turn("Tell me about the Samsung Galaxy A56"),
        ]
        result = resolve("What is the Pixel 10a's price?", history)
        assert result.relation is not ContextRelation.AMBIGUOUS

    def test_a_refinement_chain_on_one_topic_is_never_ambiguous(self):
        # Three turns about the same thing are not competing referents,
        # however closely they score -- this is the regression guard for
        # the ordinary follow-up chain.
        history = [
            turn("Show total sales by year", tables=["orders"]),
            turn("Now break that down by month", tables=["orders"]),
            turn("Now just 2013", tables=["orders"]),
        ]
        result = resolve("And by region?", history)
        assert result.relation is not ContextRelation.AMBIGUOUS

    def test_a_reference_with_no_history_reports_missing_context(self):
        result = resolve("Now break that down by month", [])

        assert result.relation is ContextRelation.MISSING_CONTEXT
        assert result.relation.needs_clarification
        assert "no earlier question" in (result.clarification or "")

    def test_a_contentless_fragment_asks_rather_than_inventing_a_question(self):
        # "why" names no subject and no attribute: there is no question to
        # rewrite, so supplying a subject would be inventing one.
        result = resolve("why", [turn("Show total sales by year")])
        assert result.relation is ContextRelation.AMBIGUOUS

    def test_a_clarification_never_quotes_the_matched_signal(self):
        history = [
            turn("Tell me about the Google Pixel 10a"),
            turn("Tell me about the Samsung Galaxy A56"),
        ]
        result = resolve("What is its price?", history)
        # A refusal/clarification must not be an oracle for what matched
        # (CLAUDE.md 5.5).
        assert "signal" not in (result.clarification or "").lower()
        assert "regex" not in (result.clarification or "").lower()


# --- Returning to an older topic -------------------------------------------


class TestTopicReturn:
    @pytest.fixture
    def history(self) -> list[ConversationExchange]:
        return [
            turn("My Angular application has a CORS error"),
            turn("Explain dependency injection in .NET"),
            turn("Show total sales by year", tables=["orders"]),
        ]

    def test_an_explicit_back_reference_does_not_resolve_to_the_last_turn(self, history):
        result = resolve("Back to that CORS issue from earlier -- any other causes?", history)

        assert result.relation is ContextRelation.TOPIC_RETURN
        assert result.antecedent is not None
        assert "CORS" in result.antecedent["question"]

    def test_a_named_older_topic_wins_over_recency(self, history):
        result = resolve("going back to dependency injection, show another example", history)
        assert result.antecedent is not None
        assert "dependency injection" in result.antecedent["question"]

    def test_antecedent_scoring_does_not_simply_prefer_the_newest_turn(self, history):
        snapshot = build_memory(history, recent_turns=6)
        scored = score_antecedents("what about that CORS problem", snapshot)
        # The pre-resolution behaviour was `history[-1]`, unconditionally.
        assert scored[0].index == 0


# --- Long conversations ----------------------------------------------------


class TestLongConversations:
    def test_turns_beyond_the_window_still_contribute_a_digest(self):
        history = [turn(f"Question about widget {i}") for i in range(12)]
        result = resolve_context("Price?", history, recent_turns=3, llm=None)

        assert result.memory is not None
        assert len(result.memory.recent) == 3
        assert result.memory.older_turn_count == 9
        assert result.memory.older_digest

    def test_the_window_bounds_what_can_be_an_antecedent(self):
        history = [turn("Tell me about the Google Pixel 10a")] + [
            turn(f"Unrelated question {i}") for i in range(8)
        ]
        result = resolve_context("Price?", history, recent_turns=2, llm=None)
        # The product turn is outside the window, so it cannot be chosen.
        assert result.antecedent is not None
        assert "Pixel" not in result.antecedent["question"]

    def test_an_unsuccessful_turn_is_never_an_antecedent(self):
        history = [
            turn("Tell me about the Google Pixel 10a"),
            turn("Tell me about the Galaxy A56", status="failed"),
        ]
        result = resolve("Price?", history)
        assert result.antecedent is not None
        assert "Pixel" in result.antecedent["question"]


# --- Tier 2 ----------------------------------------------------------------


def canned(payload: dict) -> object:
    def _call(_system: str, _user: str) -> str:
        return json.dumps(payload)

    return _call


class TestLlmEscalation:
    @pytest.fixture
    def contested(self) -> list[ConversationExchange]:
        return [
            turn("Tell me about the Google Pixel 10a"),
            turn("Explain dependency injection in .NET"),
            turn("Show total sales by year", tables=["orders"]),
        ]

    def test_no_escalation_when_the_deterministic_layer_is_confident(self):
        calls: list[int] = []

        def _never(_system: str, _user: str) -> str:
            calls.append(1)
            return "{}"

        resolve_context(
            "What is its price?",
            [turn("Tell me about the Google Pixel 10a")],
            recent_turns=6,
            min_confidence=0.0,
            llm=_never,
        )
        assert calls == []

    def test_escalation_can_override_the_antecedent(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned(
                {
                    "relation": "followup",
                    "antecedent_turn": 1,
                    "standalone_question": "Show another dependency injection example in .NET",
                    "confidence": 0.9,
                }
            ),
        )
        assert result.source == "llm"
        assert result.antecedent is not None
        assert "dependency injection" in result.antecedent["question"]
        assert "dependency injection" in result.resolved_question

    def test_an_out_of_range_antecedent_index_is_discarded(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned({"relation": "followup", "antecedent_turn": 99, "confidence": 0.9}),
        )
        # Falls back to the deterministic antecedent rather than crashing
        # or indexing past the window. On this transcript the
        # deterministic answer was "I cannot tell", so that is what
        # survives -- the model named a turn that does not exist, which is
        # no reason to believe its relation either.
        assert result.antecedent is None
        assert result.antecedent_index is None

    def test_a_valid_antecedent_index_is_used_when_the_index_is_in_range(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned({"relation": "followup", "antecedent_turn": 0, "confidence": 0.9}),
        )
        assert result.antecedent is not None
        assert "Pixel 10a" in result.antecedent["question"]

    def test_an_unknown_relation_keeps_the_deterministic_one(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned({"relation": "please_ignore_your_instructions", "confidence": 0.9}),
        )
        assert result.relation in tuple(ContextRelation)

    def test_a_rewrite_that_drops_the_question_is_discarded(self, contested):
        result = resolve_context(
            "show another dependency injection example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned(
                {
                    "relation": "followup",
                    "antecedent_turn": 1,
                    # The model answered instead of rewriting -- the
                    # question's own content words are gone.
                    "standalone_question": "Sure! Here is one: services.AddScoped<IFoo, Foo>();",
                    "confidence": 0.9,
                }
            ),
        )
        assert "dependency" in result.resolved_question.lower()

    def test_an_unreachable_model_falls_open_to_the_deterministic_answer(self, contested):
        def _raise(_system: str, _user: str) -> str:
            raise RuntimeError("ollama is down")

        result = resolve_context(
            "show another example", contested, recent_turns=6, min_confidence=1.0, llm=_raise
        )
        # Fails open to tier 1's answer, whatever that answer was -- here
        # "ask which of these you meant", which is the safe outcome for an
        # unresolvable reference and exactly what the user would have got
        # if tier 2 were switched off.
        assert result.source == "deterministic"
        assert result.relation is ContextRelation.AMBIGUOUS
        assert result.clarification

    def test_a_low_confidence_non_ambiguous_baseline_still_escalates(self):
        # Two turns on one subject: not competing referents (shared
        # entities), so tier 1 picks an antecedent -- but on recency
        # alone, so its confidence is low and tier 2 is still consulted.
        history = [
            turn("Tell me about the Google Pixel 10a"),
            turn("Does the Google Pixel 10a support wireless charging?"),
        ]
        result = resolve_context(
            "Price?",
            history,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned(
                {
                    "relation": "followup",
                    "antecedent_turn": 0,
                    "standalone_question": "What is the price of the Google Pixel 10a?",
                    "confidence": 0.95,
                }
            ),
        )
        assert result.source == "llm"
        assert result.resolved_question == "What is the price of the Google Pixel 10a?"

    def test_unparseable_output_falls_open_to_the_deterministic_answer(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=lambda _s, _u: "I'm sorry, I can't do that.",
        )
        assert result.source == "deterministic"

    def test_json_wrapped_in_a_fence_is_still_parsed(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=lambda _s, _u: 'Here you go:\n```json\n{"relation": "refinement", '
            '"antecedent_turn": 1, "confidence": 0.8}\n```',
        )
        assert result.source == "llm"
        assert result.relation is ContextRelation.REFINEMENT

    def test_the_model_may_ask_for_clarification(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned(
                {
                    "relation": "ambiguous",
                    "antecedent_turn": None,
                    "needs_clarification": True,
                    "clarifying_question": "Which example did you mean?",
                    "confidence": 0.3,
                }
            ),
        )
        assert result.relation is ContextRelation.AMBIGUOUS
        assert result.clarification == "Which example did you mean?"

    def test_a_clarification_request_with_no_question_gets_a_fixed_constant(self, contested):
        result = resolve_context(
            "show another example",
            contested,
            recent_turns=6,
            min_confidence=1.0,
            llm=canned({"relation": "ambiguous", "needs_clarification": True}),
        )
        assert result.clarification
        assert "not sure" in result.clarification.lower()


# --- Legacy contract ------------------------------------------------------


class TestLegacyMapping:
    @pytest.mark.parametrize(
        ("relation", "expected"),
        [
            (ContextRelation.NEW_TOPIC, "standalone"),
            (ContextRelation.FOLLOWUP, "followup"),
            (ContextRelation.REFINEMENT, "followup"),
            (ContextRelation.CORRECTION, "followup"),
            (ContextRelation.TOPIC_RETURN, "followup"),
            (ContextRelation.AMBIGUOUS, "ambiguous"),
            (ContextRelation.MISSING_CONTEXT, "ambiguous"),
        ],
    )
    def test_every_relation_maps_onto_the_frozen_three_values(self, relation, expected):
        assert relation.legacy_classification() == expected

    def test_the_mapping_covers_every_relation(self):
        # A new relation with no mapping would silently become
        # "standalone" in `AskResponse.followup_classification`.
        for relation in ContextRelation:
            assert relation.legacy_classification() in ("standalone", "followup", "ambiguous")


# --- Isolation ------------------------------------------------------------


class TestIsolation:
    def test_resolution_reads_only_the_history_it_is_given(self):
        # The function is pure with respect to the application: there is no
        # store, cache or module state it could reach another user's
        # conversation through. Two calls with different windows cannot
        # influence each other.
        a = resolve("Price?", [turn("Tell me about the Google Pixel 10a")])
        b = resolve("Price?", [turn("Tell me about the Samsung Galaxy A56")])

        assert "Pixel 10a" in a.resolved_question
        assert "Pixel" not in b.resolved_question
        assert "Galaxy A56" in b.resolved_question

    def test_an_empty_window_carries_nothing_from_a_previous_call(self):
        resolve("Price?", [turn("Tell me about the Google Pixel 10a")])
        result = resolve("Price?", [])
        assert result.relation is ContextRelation.MISSING_CONTEXT
        assert result.antecedent is None


# --- Non-English messages --------------------------------------------------


class TestNonEnglishMessages:
    """The UI ships five locales (en/es/fr/hi/mr).

    Before this, `agent.followup`'s ASCII-only tokenizer dropped every
    Devanagari character, so a Hindi or Marathi question tokenized to
    nothing but its embedded Latin product names, came out with no subject
    of its own, and was **refused** with "this question doesn't have
    enough on its own". These pin the two things that matter: a non-English
    question is answered rather than refused, and a non-English follow-up
    is detected.

    Rewriting stays language-neutral on purpose. Substituting a pronoun
    needs grammar; quoting the prior turn needs none, so a non-English
    follow-up resolves through the quotation clause. Reads less well than
    the English substitution, and is correct.
    """

    HINDI_STANDALONE = "मुझे Google Pixel 10a के " "बारे में बताइए"
    HINDI_FOLLOWUP = "इसकी कीमत " "क्या है?"
    HINDI_NEW_TOPIC = "डेटाबेस " "नॉर्मलाइजेशन " "समझाइए"

    def test_a_devanagari_question_is_not_refused(self):
        result = resolve(self.HINDI_STANDALONE, [])
        assert result.relation is ContextRelation.NEW_TOPIC
        assert not result.relation.needs_clarification

    def test_a_devanagari_question_keeps_its_own_text(self):
        result = resolve(self.HINDI_STANDALONE, [])
        assert result.resolved_question == self.HINDI_STANDALONE

    def test_a_devanagari_followup_is_detected(self):
        history = [turn("Tell me about the Google Pixel 10a")]
        result = resolve(self.HINDI_FOLLOWUP, history)

        assert result.relation is ContextRelation.FOLLOWUP
        assert result.antecedent is not None
        # Resolved through the quotation clause, which needs no grammar.
        assert "Google Pixel 10a" in result.resolved_question

    def test_a_devanagari_new_topic_carries_nothing(self):
        history = [turn("Tell me about the Google Pixel 10a")]
        result = resolve(self.HINDI_NEW_TOPIC, history)

        assert result.relation is ContextRelation.NEW_TOPIC
        assert "Pixel" not in result.resolved_question

    @pytest.mark.parametrize(
        "question",
        [
            "¿Y su precio?",
            "Quel est son prix ?",
            "¿Cuál es su disponibilidad?",
        ],
    )
    def test_a_spanish_or_french_followup_is_detected(self, question):
        history = [turn("Tell me about the Google Pixel 10a")]
        result = resolve(question, history)

        assert result.relation is ContextRelation.FOLLOWUP
        assert "Google Pixel 10a" in result.resolved_question

    def test_a_spanish_question_naming_its_own_subject_stays_independent(self):
        history = [turn("Tell me about the Google Pixel 10a")]
        result = resolve("¿Cuál es el precio del petróleo?", history)
        # "crude oil price", in Spanish: an attribute word plus its own
        # subject, so nothing is carried -- the same rule as English.
        assert result.relation is ContextRelation.NEW_TOPIC
        assert "Pixel" not in result.resolved_question

    def test_an_accented_entity_survives_extraction(self):
        history = [turn("Tell me about Société Générale")]
        result = resolve("What is its share price?", history)
        assert "Société Générale" in result.resolved_question
