"""Measured accuracy for conversational context resolution.

CLAUDE.md 13 records that the eval harness covers the SQL path only and
needs a real database and a real Ollama, which is why routing and
reference resolution had no measured accuracy figure at all -- only a
test suite, which pins behaviour but does not quantify it.

This benchmark closes that for resolution specifically, and it can,
because `agent.context_resolution.resolve_context` is a pure function of
(message, history): no database, no model, no vector store. The whole
corpus runs in milliseconds, so unlike `eval/benchmark/` it is cheap
enough to assert on in CI -- see
`tests/test_context_resolution_benchmark.py`.

What it reports, and what each number is for:

- **Per-relation precision / recall / F1, and macro-F1.** The headline. A
  relation can be wrong in two directions and they cost different things:
  a missed follow-up answers a question with no context (usually a bad
  answer), while a false follow-up carries context into an independent
  question (a wrong answer about the wrong thing).
- **Entity-resolution accuracy.** Of the cases that name what the
  standalone form must carry, how many carried it. This is the number
  that says "its" actually became the right product.
- **Context-leakage rate.** Of the cases that name what the standalone
  form must *not* carry, how many leaked it anyway. The one metric where
  the target is zero: carrying a previous subject into an independent
  question is the failure mode this feature must not have.
- **New-topic accuracy** and **clarification accuracy**, reported
  separately from the macro because they are the two decisions a user
  notices most: a new topic answered independently, and an unresolvable
  reference asked about rather than guessed.
- **Median and p95 latency**, so the cost of all this stays visible
  beside the accuracy.

The labels in `cases.yaml` are hand-written judgements, deliberately not
derived from the implementation -- a corpus generated from the code
measures nothing.
"""

from eval.context_resolution_benchmark.runner import (
    BenchmarkOutcome,
    CaseOutcome,
    RelationMetrics,
    load_cases,
    run_benchmark,
)

__all__ = [
    "BenchmarkOutcome",
    "CaseOutcome",
    "RelationMetrics",
    "load_cases",
    "run_benchmark",
]
