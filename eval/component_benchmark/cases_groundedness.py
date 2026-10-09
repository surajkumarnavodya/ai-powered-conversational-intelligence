"""Groundedness cases: `agent.insight.is_insight_grounded` -- the
runtime safety net that drops an LLM-narrated insight sentence unless
every number in it is traceable to the real result data (or to a
literal value in the question/SQL itself).

Each case feeds a realistic `(columns, rows)` shape through
`agent.insight.summarize_result` (the identical reduction
`generate_insight_node` itself uses) and asserts whether a given
candidate insight sentence should be accepted or rejected -- "did the
grounding check stay correct," not a re-run of `tests/test_insight.py`'s
own much finer-grained unit coverage of every allowed-value computation.
"""

from __future__ import annotations

from agent.insight import is_insight_grounded, summarize_result
from eval.component_benchmark.schema import ComponentCase


def _case_a_real_total_is_accepted() -> tuple[bool, str]:
    summary = summarize_result(["revenue"], [(150_000.0,)])
    grounded = is_insight_grounded("Total revenue was 150000.", summary)
    return grounded, f"grounded={grounded}"


def _case_a_fabricated_number_is_rejected() -> tuple[bool, str]:
    summary = summarize_result(["revenue"], [(150_000.0,)])
    grounded = is_insight_grounded("Total revenue was 999999.", summary)
    passed = grounded is False
    return passed, f"grounded={grounded} (expected False)"


def _case_a_value_literally_in_the_question_is_accepted() -> tuple[bool, str]:
    """A filter value the user themselves typed (e.g. a year) is not a
    fabricated claim, even though it isn't part of the result data."""
    summary = summarize_result(["revenue"], [(150_000.0,)])
    grounded = is_insight_grounded(
        "In 2023, total revenue was 150000.",
        summary,
        question="What was revenue in 2023?",
    )
    return grounded, f"grounded={grounded}"


def _case_the_top_share_percent_claim_is_accepted() -> tuple[bool, str]:
    summary = summarize_result(["region", "revenue"], [("East", 600_000.0), ("West", 400_000.0)])
    grounded = is_insight_grounded("East accounted for roughly 60% of total revenue.", summary)
    return grounded, f"grounded={grounded} top_share_percent={summary.top_share_percent}"


def _case_a_fabricated_percent_is_rejected() -> tuple[bool, str]:
    summary = summarize_result(["region", "revenue"], [("East", 600_000.0), ("West", 400_000.0)])
    grounded = is_insight_grounded("East accounted for 91% of total revenue.", summary)
    passed = grounded is False
    return passed, f"grounded={grounded} (expected False)"


def _case_row_count_itself_is_a_grounded_claim() -> tuple[bool, str]:
    summary = summarize_result(
        ["region", "revenue"], [("East", 1.0), ("West", 2.0), ("North", 3.0)]
    )
    grounded = is_insight_grounded("The result contains 3 rows.", summary)
    return grounded, f"grounded={grounded} row_count={summary.row_count}"


def _case_an_empty_result_rejects_any_fabricated_number() -> tuple[bool, str]:
    summary = summarize_result(["revenue"], [])
    grounded = is_insight_grounded("Total revenue was 50000.", summary)
    passed = grounded is False
    return passed, f"grounded={grounded} (expected False on an empty result)"


CASES: list[ComponentCase] = [
    ComponentCase(
        "a_real_total_is_accepted",
        "An insight sentence citing the result's own real total is grounded.",
        _case_a_real_total_is_accepted,
    ),
    ComponentCase(
        "a_fabricated_number_is_rejected",
        "An insight sentence citing a number absent from the result is rejected.",
        _case_a_fabricated_number_is_rejected,
    ),
    ComponentCase(
        "a_value_literally_in_the_question_is_accepted",
        "A filter value the user themselves typed (e.g. a year) is accepted even though it is "
        "not part of the result data.",
        _case_a_value_literally_in_the_question_is_accepted,
    ),
    ComponentCase(
        "the_top_share_percent_claim_is_accepted",
        "A correct 'X accounted for Y% of the total' claim matching top_share_percent is grounded.",
        _case_the_top_share_percent_claim_is_accepted,
    ),
    ComponentCase(
        "a_fabricated_percent_is_rejected",
        "An incorrect share-of-total percent claim is rejected.",
        _case_a_fabricated_percent_is_rejected,
    ),
    ComponentCase(
        "row_count_itself_is_a_grounded_claim",
        "A sentence citing the result's own row count is grounded.",
        _case_row_count_itself_is_a_grounded_claim,
    ),
    ComponentCase(
        "an_empty_result_rejects_any_fabricated_number",
        "With zero rows, any specific fabricated number is still rejected, never silently accepted.",
        _case_an_empty_result_rejects_any_fabricated_number,
    ),
]
