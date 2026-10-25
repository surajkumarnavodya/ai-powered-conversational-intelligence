"""`agent.orchestrator.nodes.strip_source_preamble`.

The prompt tells the model to open with the answer; this is the
belt-and-braces half that removes a stock opener when it writes one
anyway. The tests that matter most here are the *negative* ones: the
strip must never eat a named attribution, which is real provenance
(CLAUDE.md §4.4), and must never turn a refusal into an empty answer.
"""

from __future__ import annotations

import pytest

from agent.orchestrator.nodes import strip_source_preamble


class TestGenericPreamblesAreRemoved:
    @pytest.mark.parametrize(
        "answer",
        [
            "According to a live web search: Narendra Modi is the Prime Minister.",
            "According to a live web search:\n\nNarendra Modi is the Prime Minister.",
            "According to the web search results, Narendra Modi is the Prime Minister.",
            "Based on the web search, Narendra Modi is the Prime Minister.",
            "Based on the live web search results: Narendra Modi is the Prime Minister.",
            "Based on the search results, Narendra Modi is the Prime Minister.",
            "Based on the sources provided, Narendra Modi is the Prime Minister.",
            "According to the sources, Narendra Modi is the Prime Minister.",
            "The web search results indicate that Narendra Modi is the Prime Minister.",
            "The search results show that Narendra Modi is the Prime Minister.",
            "The sources suggest that Narendra Modi is the Prime Minister.",
            "Here's what the web search found: Narendra Modi is the Prime Minister.",
        ],
    )
    def test_opener_is_removed_and_the_answer_survives(self, answer: str) -> None:
        assert strip_source_preamble(answer) == "Narendra Modi is the Prime Minister."

    def test_a_second_stacked_opener_is_also_removed(self) -> None:
        answer = "Based on the web search results, here's what the search found: It rained."
        assert strip_source_preamble(answer) == "It rained."

    def test_a_lowercase_remainder_is_recapitalized(self) -> None:
        answer = "The search results indicate that narendra Modi holds the office."
        assert strip_source_preamble(answer) == "Narendra Modi holds the office."

    def test_markdown_structure_is_not_recapitalized(self) -> None:
        answer = "According to a live web search:\n\n## heading\n\nBody."
        assert strip_source_preamble(answer) == "## heading\n\nBody."


class TestRealContentIsNeverRemoved:
    @pytest.mark.parametrize(
        "answer",
        [
            # The distinction this whole regex exists to make: attribution
            # to a named publisher is provenance worth keeping.
            "According to Reuters, the talks collapsed overnight.",
            "According to Le Monde, protests spread to five cities.",
            "Based on the company's own filing, revenue grew 12%.",
            "According to the European Commission, the rule takes effect in June.",
            # Not an opener at all -- the phrase appears mid-answer.
            "Protests spread overnight. The sources indicate that talks continue.",
            # Ordinary answers that merely begin with a similar word.
            "Search engines rank these pages highly.",
            "The sources of the dispute are historical.",
            "Based on 2025 figures, inflation fell to 2.1%.",
        ],
    )
    def test_answer_is_returned_unchanged(self, answer: str) -> None:
        assert strip_source_preamble(answer) == answer

    def test_a_preamble_that_is_the_whole_answer_is_kept(self) -> None:
        # A model emitting only the opener has declined to answer. Turning
        # that into "" would render an empty answer as if it were one.
        answer = "Based on the web search results:"
        assert strip_source_preamble(answer) == answer

    @pytest.mark.parametrize("answer", ["", "   ", "\n\n"])
    def test_blank_input_is_returned_unchanged(self, answer: str) -> None:
        assert strip_source_preamble(answer) == answer


class TestIdempotence:
    def test_stripping_twice_changes_nothing_further(self) -> None:
        once = strip_source_preamble("According to a live web search: It rained.")
        assert strip_source_preamble(once) == once
