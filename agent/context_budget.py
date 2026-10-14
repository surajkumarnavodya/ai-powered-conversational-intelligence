"""Deterministic context budgeting for the SQL generation prompt.

### The problem this exists for

Retrieval in this codebase is capped by **count** -- `schema_top_k=4`,
`golden_examples_top_k=3`, `rag_top_k` -- never by **size**. Four
tables is four tables whether each has 10 columns or 200, so the
assembled prompt ranges from ~2,800 to ~19,700 tokens on realistic
inputs (`docs/INFERENCE_ENGINEERING_BASELINE.md` §1).

Ollama does not reject an oversized prompt. It silently truncates, and
it truncates from the **start** -- which is where the system prompt
lives. Measured before this module existed: a 21,500-token prompt came
back with `prompt_eval_count=2050`, and the model ignored its system
instruction entirely because truncation had removed it.

So the failure mode was not "the prompt is too long and errors". It was
"the safety and dialect rules quietly stopped being in the prompt", and
nothing reported it.

### What this module does, and deliberately does not do

It decides **whether a whole block is included**, and reports what it
left out. It never rewrites a block's wording -- `_INSIGHT_SYSTEM_PROMPT`
and the generation system prompt are benchmark-pinned (CLAUDE.md §12)
and the accuracy numbers in `docs/EVALUATION_CURRENT.md` were measured
against their exact text.

It also never truncates a block mid-way. Half a `CREATE TABLE` is worse
than no table: it reads as a complete, smaller schema and the model
will confidently write SQL against columns that do not exist. Blocks
are dropped whole, from the lowest-priority end.

### Priority, and why it is this order

`SectionPriority` is the whole design. Two tiers:

**Never dropped.** Schema (no valid SQL without it), governing metrics
(a *mandatory* governance block -- dropping one silently would let the
model answer with an ungoverned metric definition), and error feedback
(a retry without its error is just the same failing call again).

**Dropped first, lowest priority first.** Business context, then golden
examples, then follow-up context, then the plan. All four are accuracy
aids, and this codebase's standing rule is that an accuracy aid
degrades to "no extra help" rather than failing a request
(CLAUDE.md §4.2). Dropping one costs quality; dropping the schema costs
correctness, and dropping a governing metric costs governance.

Truncation is **always reported** through `BudgetResult.dropped`, which
the caller logs and surfaces. A silently shortened prompt is what this
module exists to stop; introducing a new silent one would be a poor
trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum

logger = logging.getLogger(__name__)

#: Characters per token, the standard rough estimate for English prose
#: and SQL. Deliberately an estimate: the real tokenizer lives in
#: Ollama and this module must not take a tokenizer dependency to make
#: a sizing decision. Erring high (4) would under-count tokens and
#: under-protect, so callers budget in characters and convert once.
CHARS_PER_TOKEN = 4


class SectionPriority(IntEnum):
    """Eviction order. Lower value is dropped first.

    `CRITICAL` is not merely "important" -- it is the set this module
    refuses to drop at any budget, reporting an over-budget prompt
    instead of a quietly incorrect one.
    """

    BUSINESS_CONTEXT = 10
    GOLDEN_EXAMPLES = 20
    FOLLOWUP = 30
    PLAN = 40
    CRITICAL = 100


@dataclass(frozen=True)
class Section:
    """One labelled prompt block with an eviction priority."""

    name: str
    text: str
    priority: SectionPriority

    @property
    def size(self) -> int:
        return len(self.text)


@dataclass
class BudgetResult:
    """What survived the budget, and what did not.

    `dropped` and `kept` carry **section names and sizes only** -- never
    the text. These go into a log line and a telemetry counter, and this
    codebase does not log prompt content (CLAUDE.md §5.6).
    """

    sections: list[Section]
    dropped: list[str] = field(default_factory=list)
    original_chars: int = 0
    final_chars: int = 0
    over_budget: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.dropped)

    @property
    def dropped_chars(self) -> int:
        return max(0, self.original_chars - self.final_chars)

    def summary(self) -> dict[str, object]:
        """A log/metric-safe description. Names, counts and sizes only."""
        return {
            "original_chars": self.original_chars,
            "final_chars": self.final_chars,
            "dropped_sections": list(self.dropped),
            "dropped_chars": self.dropped_chars,
            "over_budget": self.over_budget,
            "estimated_tokens": self.final_chars // CHARS_PER_TOKEN,
        }


def apply_budget(
    sections: list[Section],
    max_chars: int,
    *,
    reserved_chars: int = 0,
) -> BudgetResult:
    """Drops the lowest-priority sections until the prompt fits.

    Args:
        sections: The assembled blocks, in the order they should appear.
            Order is preserved for everything kept -- eviction changes
            *which* blocks appear, never their sequence, so a prompt
            stays byte-identical to the unbudgeted one whenever nothing
            is dropped.
        max_chars: The total character budget for these sections.
            Non-positive disables budgeting entirely, which is the
            documented way to turn this off.
        reserved_chars: Space to leave for content this call does not
            see -- the system prompt, chiefly. Subtracted from
            `max_chars` before anything is measured, because the system
            prompt is what truncation would otherwise eat first.

    Returns:
        A `BudgetResult`. When everything fits, `sections` is the input
        list unchanged and `changed` is False.

    `over_budget` is set when the CRITICAL sections alone exceed the
    budget. The module does **not** then drop one: it returns them all
    and says so. A caller that silently dropped a schema to satisfy a
    number would produce confident SQL against tables the model never
    saw, which is strictly worse than a long prompt.
    """
    original = sum(section.size for section in sections)
    effective = max_chars - reserved_chars

    if max_chars <= 0 or original <= effective:
        return BudgetResult(
            sections=list(sections),
            original_chars=original,
            final_chars=original,
        )

    kept = list(sections)
    dropped: list[str] = []

    # Lowest priority first; within a priority, largest first, so one
    # oversized block is removed before several small ones. Ties break
    # on name, so the decision is deterministic for identical inputs --
    # a budget that evicted different blocks on different runs would
    # make a regression impossible to reproduce.
    candidates = sorted(
        (s for s in sections if s.priority is not SectionPriority.CRITICAL),
        key=lambda s: (s.priority, -s.size, s.name),
    )

    current = original
    for candidate in candidates:
        if current <= effective:
            break
        kept.remove(candidate)
        dropped.append(candidate.name)
        current -= candidate.size

    over_budget = current > effective
    if over_budget:
        logger.warning(
            "[context_budget] still over budget after dropping every optional section: "
            "%d chars against a %d-char budget. The critical sections (schema, governing "
            "metrics, error feedback) are never dropped -- the model may truncate.",
            current,
            effective,
        )

    if dropped:
        logger.info(
            "[context_budget] dropped %d section(s) to fit: %s (%d -> %d chars)",
            len(dropped),
            ", ".join(dropped),
            original,
            current,
        )

    return BudgetResult(
        sections=kept,
        dropped=dropped,
        original_chars=original,
        final_chars=current,
        over_budget=over_budget,
    )


def estimate_tokens(text: str) -> int:
    """A rough token count for `text`. See `CHARS_PER_TOKEN`."""
    return len(text) // CHARS_PER_TOKEN


__all__ = [
    "CHARS_PER_TOKEN",
    "BudgetResult",
    "Section",
    "SectionPriority",
    "apply_budget",
    "estimate_tokens",
]
