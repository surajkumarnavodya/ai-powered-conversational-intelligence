"""Runs the labelled corpus in CI and asserts floors on what it measures.

CLAUDE.md 13 records that the eval harness covers the SQL path only and
needs real infrastructure, which is why resolution had no measured
accuracy figure. This benchmark can run here because
`agent.context_resolution.resolve_context` is pure -- the whole corpus
scores in well under a second.

**On reading the headline number honestly.** The corpus was authored, then
six real defects it surfaced were fixed, so it now scores 100%. That is
what a corpus does; it is not evidence that resolution is perfect on
unseen phrasings. The value is in what it found (an entity extracted as
"Top 10", a new "summarize X" treated as refining the previous document,
a dangling pronoun on a first turn read as a new topic) and in the two
metrics whose target is absolute: **zero context leakage** and 100%
clarification accuracy.

The floors below are therefore set *under* the current numbers. They
exist to catch a regression, not to advertise a score -- a floor pinned
at the measured value turns every improvement elsewhere into a failure
here.
"""

from __future__ import annotations

import pytest

from eval.context_resolution_benchmark.runner import (
    ContextRelation,
    format_report,
    load_cases,
    run_benchmark,
)


@pytest.fixture(scope="module")
def outcome():
    return run_benchmark()


class TestTheCorpusItself:
    def test_it_is_substantial_enough_to_mean_something(self):
        cases = load_cases()
        # A floor on the corpus, not on the score: a benchmark that
        # silently shrank would inflate every ratio computed from it.
        assert len(cases) >= 55

    def test_every_relation_is_represented(self):
        labelled = {case.relation for case in load_cases()}
        # A relation with no case is a relation with no evidence behind
        # it -- exactly the "documented capability nobody measured"
        # problem, one layer up.
        assert labelled == set(ContextRelation)

    def test_case_ids_are_unique(self):
        ids = [case.id for case in load_cases()]
        assert len(ids) == len(set(ids))

    def test_the_corpus_covers_more_than_one_phrasing_per_behaviour(self):
        # Paraphrases are the difference between a corpus and a lookup
        # table. Several behaviours are asked more than one way.
        ids = {case.id for case in load_cases()}
        assert {"product_bare_attribute", "product_how_much", "product_specs_paraphrase"} <= ids
        assert {"writing_tone", "writing_tone_paraphrase"} <= ids
        assert {"business_scenario", "business_scenario_paraphrase"} <= ids


class TestMeasuredFloors:
    def test_no_case_leaks_context_into_an_independent_question(self, outcome):
        """The one metric whose target is zero, not a floor.

        Carrying a previous subject into an independent question is the
        failure this feature must not have: it produces a confident answer
        about the wrong thing. Thirteen cases name terms that must not
        appear in the resolved question.
        """
        assert outcome.context_leakage_rate == 0.0, format_report(outcome)

    def test_an_unresolvable_reference_is_always_asked_about(self, outcome):
        """Also absolute. Guessing a referent is a wrong answer; asking is
        a mild annoyance."""
        accuracy = outcome.accuracy_for(ContextRelation.AMBIGUOUS, ContextRelation.MISSING_CONTEXT)
        assert accuracy == 1.0, format_report(outcome)

    def test_relation_accuracy_holds(self, outcome):
        assert outcome.relation_accuracy >= 0.93, format_report(outcome)

    def test_macro_f1_holds(self, outcome):
        # Macro, not weighted: `followup` is the most common label, and a
        # weighted mean would let a resolver that gets corrections wrong
        # still look near-perfect.
        assert outcome.macro_f1 >= 0.90, format_report(outcome)

    def test_entity_resolution_holds(self, outcome):
        # Of the cases that say what the standalone form must carry, how
        # many carried it -- the number that says "its" became the right
        # product.
        assert outcome.entity_resolution_accuracy >= 0.95, format_report(outcome)

    def test_antecedent_selection_holds(self, outcome):
        assert outcome.antecedent_accuracy >= 0.93, format_report(outcome)

    def test_new_topics_stay_independent(self, outcome):
        assert outcome.accuracy_for(ContextRelation.NEW_TOPIC) >= 0.95, format_report(outcome)

    def test_resolution_stays_sub_millisecond(self, outcome):
        # The cost of all of the above. Generous relative to the measured
        # ~0.1ms median so a slow CI machine does not fail the build, but
        # tight enough to catch an accidental O(history^2) or a model call
        # sneaking onto this path.
        assert outcome.median_latency_ms < 5.0, format_report(outcome)
        assert outcome.p95_latency_ms < 25.0, format_report(outcome)


class TestTheReport:
    def test_it_names_every_failing_case(self, outcome):
        report = format_report(outcome)
        assert "Context resolution benchmark" in report
        assert "context leakage" in report
        for failed in outcome.failures:
            assert failed.case.id in report

    def test_the_benchmark_makes_no_model_call(self, monkeypatch):
        # It is a pure-function benchmark; if that ever stopped being
        # true it would quietly become infrastructure-dependent and
        # unusable in CI. `tests/conftest.py` already blocks real calls,
        # so this asserts the stronger property that none is attempted.
        def _explode(*_a: object, **_k: object) -> str:
            raise AssertionError("the resolution benchmark must not call a model")

        monkeypatch.setattr("rag.llm.call_ollama", _explode)
        assert run_benchmark().total > 0
