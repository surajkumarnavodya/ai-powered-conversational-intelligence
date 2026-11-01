"""Is a label column chronological? One owner for that question.

**Why this is its own module.** Two different layers need the same
answer. `analytics.engine` needs it to classify a result as a
`TIME_SERIES` and to walk for missing periods, and `agent.insight` needs
it to decide whether a first-versus-last comparison of a label column is
a period-over-period *trend* at all or just two unrelated rows. Both
answering it separately is the defect CLAUDE.md 6.2 names, and
`agent.insight` cannot import `analytics.engine` because
`analytics.models` imports `agent.insight` -- that would be a real
runtime import cycle. This module imports nothing from either side, so
both can depend on it.

The calendar *arithmetic* built on top of this classification --
`next_period_label`, `detect_missing_periods` -- deliberately stays in
`analytics.engine`. Those take a `period_kind` string and never look at
a label's shape, so they are not part of the question this module owns.
`analytics.engine` re-exports the two names below, so every existing
`from analytics.engine import classify_period_column` call site is
unchanged.
"""

from __future__ import annotations

import re

# Three recognized period label shapes -- see `classify_period_column`'s
# own docstring for the disclosed, bounded scope (no general date-string
# parsing).
_YEAR_RE = re.compile(r"^\d{4}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_ISO_DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

#: A label column is treated as period-shaped when at least this fraction
#: of its (non-null) values match the same recognized pattern -- a
#: majority, not unanimity, so one stray non-conforming value (a "Total"
#: summary row, a typo) doesn't disqualify an otherwise clearly
#: chronological column.
PERIOD_MAJORITY_THRESHOLD = 0.5


def period_kind(value: str) -> str | None:
    """The recognized period shape of one label, or `None`.

    Args:
        value: One label, already rendered as a string.

    Returns:
        `"date"`, `"year_month"`, `"year"`, or `None` when the label
        matches none of the three recognized shapes.
    """
    if _ISO_DATE_RE.match(value):
        return "date"
    if _YEAR_MONTH_RE.match(value):
        return "year_month"
    if _YEAR_RE.match(value):
        return "year"
    return None


def classify_period_column(labels: list[str]) -> str | None:
    """Returns the dominant recognized period pattern among `labels`
    (`"year"` / `"year_month"` / `"date"`), or `None` if no single
    pattern covers at least `PERIOD_MAJORITY_THRESHOLD` of them.

    A disclosed, bounded scope: only these three label shapes are
    recognized at all -- no general natural-language date parsing (e.g.
    `"Jan 2021"`, `"Q1 2021"`) is attempted, to avoid a fragile,
    heavyweight date-parsing dependency for a feature whose main job is
    classification, not calendar arithmetic.

    `None` is the safe answer in both directions: a caller that needs a
    chronology does not get one it cannot trust, and a caller deciding
    whether a trend exists reports no trend rather than a fabricated one.
    """
    if not labels:
        return None
    counts: dict[str, int] = {}
    for label in labels:
        kind = period_kind(label)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return None
    best_kind, best_count = max(counts.items(), key=lambda item: item[1])
    if best_count / len(labels) >= PERIOD_MAJORITY_THRESHOLD:
        return best_kind
    return None


__all__ = [
    "PERIOD_MAJORITY_THRESHOLD",
    "classify_period_column",
    "period_kind",
]
