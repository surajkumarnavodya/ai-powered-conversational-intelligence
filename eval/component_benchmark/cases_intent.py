"""Analytical-intent cases: `agent.intent.intent_implies_planning` and
`agent.llm_client._parse_intent_response` (Prompt 11).

Prompt 46 (`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`) named "intent" as
one of its own named evaluation domains, and nothing in
`eval/component_benchmark/` covered it before this file -- the real LLM
call (`agent.llm_client.generate_analytical_intent_from_llm`) is not
reachable here (no live Ollama in this harness, matching every other
domain's own "zero I/O" constraint), but its two pure, deterministic
halves are: the response parser's fail-open contract (malformed/
unparseable/out-of-range input must resolve to `None`, never raise, never
a fabricated mid-confidence guess), and the planning-gate widening rule
(`_INTENT_TYPES_IMPLYING_PLANNING`'s own LOOKUP/AGGREGATION exclusion, and
the independent `ambiguity_flags` widening). Not a re-run of
`tests/test_llm_client_planning.py`'s own much finer-grained unit
coverage of every field/edge case -- these are the named, realistic
regression scenarios.
"""

from __future__ import annotations

from agent.intent import AnalyticalIntentType, intent_implies_planning
from agent.llm_client import _parse_intent_response
from eval.component_benchmark.schema import ComponentCase


def _case_a_well_formed_response_parses_cleanly() -> tuple[bool, str]:
    raw = '{"intent": "trend", "confidence": 0.9}'
    parsed = _parse_intent_response(raw)
    passed = parsed is not None and parsed["intent"] == "trend" and parsed["confidence"] == 0.9
    return passed, f"parsed={parsed}"


def _case_a_response_wrapped_in_a_markdown_json_fence_still_parses() -> tuple[bool, str]:
    raw = '```json\n{"intent": "ranking", "confidence": 0.8}\n```'
    parsed = _parse_intent_response(raw)
    passed = parsed is not None and parsed["intent"] == "ranking"
    return passed, f"parsed={parsed}"


def _case_malformed_json_is_rejected_not_guessed() -> tuple[bool, str]:
    parsed = _parse_intent_response("this is not json at all")
    passed = parsed is None
    return passed, f"parsed={parsed} (expected None)"


def _case_an_unrecognized_intent_value_is_rejected() -> tuple[bool, str]:
    """A closed enum, like `retrieval.models.ChunkType` -- an intent value
    the LLM invents is never silently coerced to the nearest real one."""
    raw = '{"intent": "not_a_real_intent", "confidence": 0.5}'
    parsed = _parse_intent_response(raw)
    passed = parsed is None
    return passed, f"parsed={parsed} (expected None)"


def _case_a_confidence_outside_0_to_1_is_rejected() -> tuple[bool, str]:
    raw = '{"intent": "trend", "confidence": 1.5}'
    parsed = _parse_intent_response(raw)
    passed = parsed is None
    return passed, f"parsed={parsed} (expected None)"


def _case_a_missing_confidence_is_rejected_not_defaulted() -> tuple[bool, str]:
    """No default for `confidence` exists on purpose (see
    `AnalyticalIntentClassification`'s own docstring) -- a response that
    omits it must be treated as malformed, never silently assigned a
    fabricated mid-confidence value."""
    raw = '{"intent": "trend"}'
    parsed = _parse_intent_response(raw)
    passed = parsed is None
    return passed, f"parsed={parsed} (expected None)"


def _case_lookup_and_aggregation_do_not_imply_planning_alone() -> tuple[bool, str]:
    lookup = {"intent": AnalyticalIntentType.LOOKUP.value, "ambiguity_flags": ()}
    aggregation = {"intent": AnalyticalIntentType.AGGREGATION.value, "ambiguity_flags": ()}
    passed = not intent_implies_planning(lookup) and not intent_implies_planning(aggregation)
    return passed, (
        f"lookup_implies_planning={intent_implies_planning(lookup)} "
        f"aggregation_implies_planning={intent_implies_planning(aggregation)} (both expected False)"
    )


def _case_every_other_intent_implies_planning() -> tuple[bool, str]:
    other_intents = frozenset(AnalyticalIntentType) - {
        AnalyticalIntentType.LOOKUP,
        AnalyticalIntentType.AGGREGATION,
    }
    results = {
        intent.value: intent_implies_planning({"intent": intent.value, "ambiguity_flags": ()})
        for intent in other_intents
    }
    passed = all(results.values())
    return passed, f"results={results} (all expected True)"


def _case_ambiguity_flags_widen_the_gate_even_for_lookup() -> tuple[bool, str]:
    """Independent of the classified intent itself -- an ambiguous
    LOOKUP question still benefits from an explicit plan."""
    classification = {
        "intent": AnalyticalIntentType.LOOKUP.value,
        "ambiguity_flags": ("'best' is ambiguous",),
    }
    passed = intent_implies_planning(classification)
    return passed, f"implies_planning={passed} (expected True)"


CASES: list[ComponentCase] = [
    ComponentCase(
        "a_well_formed_response_parses_cleanly",
        "A valid {intent, confidence} JSON response parses into a matching dict.",
        _case_a_well_formed_response_parses_cleanly,
    ),
    ComponentCase(
        "a_response_wrapped_in_a_markdown_json_fence_still_parses",
        "A response wrapped in a ```json fence still parses correctly.",
        _case_a_response_wrapped_in_a_markdown_json_fence_still_parses,
    ),
    ComponentCase(
        "malformed_json_is_rejected_not_guessed",
        "Non-JSON text fails parsing and returns None, never a guessed classification.",
        _case_malformed_json_is_rejected_not_guessed,
    ),
    ComponentCase(
        "an_unrecognized_intent_value_is_rejected",
        "An intent value outside the 13-category closed enum is rejected, never coerced.",
        _case_an_unrecognized_intent_value_is_rejected,
    ),
    ComponentCase(
        "a_confidence_outside_0_to_1_is_rejected",
        "A confidence value outside [0.0, 1.0] is rejected.",
        _case_a_confidence_outside_0_to_1_is_rejected,
    ),
    ComponentCase(
        "a_missing_confidence_is_rejected_not_defaulted",
        "A response omitting confidence entirely is rejected, never assigned a fabricated default.",
        _case_a_missing_confidence_is_rejected_not_defaulted,
    ),
    ComponentCase(
        "lookup_and_aggregation_do_not_imply_planning_alone",
        "LOOKUP/AGGREGATION intents with no ambiguity do not widen the planning gate.",
        _case_lookup_and_aggregation_do_not_imply_planning_alone,
    ),
    ComponentCase(
        "every_other_intent_implies_planning",
        "Every one of the other 11 intent types widens the planning gate on its own.",
        _case_every_other_intent_implies_planning,
    ),
    ComponentCase(
        "ambiguity_flags_widen_the_gate_even_for_lookup",
        "A LOOKUP question flagged as ambiguous still widens the planning gate.",
        _case_ambiguity_flags_widen_the_gate_even_for_lookup,
    ),
]
