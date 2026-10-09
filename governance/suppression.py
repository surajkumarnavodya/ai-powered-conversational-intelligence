"""Small-group privacy: k-anonymity suppression over a result set.

The mission's §22. An aggregate that covers two people is not an
aggregate -- "average salary in a team of 2" discloses an individual's
pay to anyone who knows the other person's. So a grouped result whose
group is smaller than the threshold has its sensitive measures
suppressed, and the suppression itself is reported.

**What is suppressed, and what is not.** Only cells in columns carrying
a sensitive classification (`governance.classification`) are suppressed.
The group label and the count stay visible: knowing that a department
has three people is ordinary org data, while knowing their average
salary is not. Blanking the whole row would destroy legitimate analytics
and tell the reader nothing about why.

**Why the count stays.** Hiding the count too would make suppression
invisible -- a reader could not tell a suppressed small group from a
group that does not exist, and a suppressed row that silently vanished
would be worse: differencing a total against the visible rows would
recover the hidden value exactly. Keeping the row and the count, and
suppressing only the measure, is what makes the suppression honest and
non-reconstructable in the same step.

**Differencing is bounded, not solved.** A determined caller with many
queries can still narrow a suppressed value by other means (§22's
"repeated queries"). This module closes the single-result-set case and
the obvious total-minus-visible case; it does not implement a query
auditor across a session, and `governance/__init__.py` says so. The
honest framing is defense in depth, not a proof.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from governance.classification import (
    DataClassification,
    FieldPolicy,
    classify_columns,
)

logger = logging.getLogger(__name__)

#: What a suppressed measure is replaced with. Distinct from
#: `governance.result_policy.RESTRICTED_MASK` on purpose: that mask means
#: "you may not see this value", this one means "this group is too small
#: to report". Two different facts, and a reader should not have to guess
#: which applies.
SUPPRESSED_MASK = "[suppressed: group too small]"

#: Column names that count as a group-size measure. A grouped result
#: produced by this application's own SQL generation routinely aliases
#: its count as one of these.
_COUNT_NAME_HINTS = (
    "count",
    "cnt",
    "num",
    "number_of",
    "total_count",
    "headcount",
    "employees",
    "records",
    "rows",
    "frequency",
    "freq",
)


@dataclass(frozen=True)
class SuppressionResult:
    """A result set with small groups suppressed.

    `suppressed_rows` and `suppressed_columns` describe what changed
    without carrying any suppressed value, so this object is safe to log
    and to return in an API response.
    """

    columns: list[str]
    rows: list[Any]
    suppressed_rows: int = 0
    suppressed_columns: tuple[str, ...] = ()
    minimum_group_size: int = 0
    #: True when no group-size column could be identified, so no
    #: suppression was attempted. Reported rather than silently treated
    #: as "nothing to suppress".
    group_size_unknown: bool = False

    @property
    def changed(self) -> bool:
        return self.suppressed_rows > 0

    def notice(self) -> str:
        """A caller-facing explanation, or `""` when nothing changed."""
        if not self.changed:
            return ""
        return (
            f"{self.suppressed_rows} group(s) smaller than {self.minimum_group_size} "
            "had sensitive measures suppressed to protect individual privacy. Group "
            "labels and counts are shown; the measures are not."
        )


def _count_column_index(columns: Sequence[str], policies: dict[str, FieldPolicy]) -> int | None:
    """The index of the column holding each row's group size, if any.

    Prefers an explicit count-shaped name. A column carrying a sensitive
    classification is never treated as the count, even if its name looks
    like one, so a column named `employee_count` in a table of salaries
    cannot be read as the group size and simultaneously be the measure
    being protected.
    """
    for index, name in enumerate(columns):
        lowered = name.lower()
        if policies[name].classification >= DataClassification.SENSITIVE:
            continue
        if any(hint in lowered for hint in _COUNT_NAME_HINTS):
            return index
    return None


def _is_sensitive_measure(policy: FieldPolicy) -> bool:
    """Whether a column's values must be suppressed for a small group.

    SENSITIVE and above. A column merely marked `aggregation_required` is
    included by that same test, since every such classification in
    `governance.classification` is at least SENSITIVE.
    """
    return policy.classification >= DataClassification.SENSITIVE


def suppress_small_groups(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    minimum_group_size: int,
    table: str = "",
    skip_indexes: frozenset[int] = frozenset(),
) -> SuppressionResult:
    """Suppresses sensitive measures for groups below the threshold.

    Args:
        columns: The result's column names.
        rows: The result's rows. Left untouched; a new list is returned.
        minimum_group_size: The k threshold. `0` or less disables
            suppression entirely and returns the rows unchanged, which
            is how a domain with no individual-level data opts out (see
            `governance.domains.minimum_group_size_for`).
        table: The originating table, when known, so an explicit
            configuration entry can be found for a column.
        skip_indexes: Column indexes already protected by a stronger
            rule, left exactly as they are. `governance.result_policy`
            passes the columns it masked or withheld, so a credential
            column reads `[withheld]` ("no role may see this") rather
            than being relabelled `[suppressed]` ("this group is too
            small") -- two different facts, and the stronger one must
            survive.

    Returns:
        A `SuppressionResult`. When no group-size column can be
        identified, nothing is suppressed and `group_size_unknown` is
        set -- an ungrouped detail result is handled by
        `governance.result_policy`'s own classification masking, not here.
    """
    if minimum_group_size <= 0 or not rows:
        return SuppressionResult(
            columns=list(columns), rows=list(rows), minimum_group_size=max(0, minimum_group_size)
        )

    policies = classify_columns(list(columns), table)
    count_index = _count_column_index(columns, policies)
    if count_index is None:
        return SuppressionResult(
            columns=list(columns),
            rows=list(rows),
            minimum_group_size=minimum_group_size,
            group_size_unknown=True,
        )

    sensitive_indexes = {
        index: name
        for index, name in enumerate(columns)
        if index != count_index
        and index not in skip_indexes
        and _is_sensitive_measure(policies[name])
    }
    if not sensitive_indexes:
        return SuppressionResult(
            columns=list(columns), rows=list(rows), minimum_group_size=minimum_group_size
        )

    suppressed_rows = 0
    suppressed_columns: set[str] = set()
    out_rows: list[Any] = []
    for row in rows:
        size = row[count_index] if count_index < len(row) else None
        too_small = (
            isinstance(size, int | float)
            and not isinstance(size, bool)
            and (0 < float(size) < minimum_group_size)
        )
        if not too_small:
            out_rows.append(row)
            continue
        values = list(row)
        for index, name in sensitive_indexes.items():
            if index < len(values):
                values[index] = SUPPRESSED_MASK
                suppressed_columns.add(name)
        suppressed_rows += 1
        out_rows.append(tuple(values) if isinstance(row, tuple) else values)

    if suppressed_rows:
        logger.info(
            "[governance.suppression] suppressed sensitive measures in %d group(s) below k=%d",
            suppressed_rows,
            minimum_group_size,
        )

    return SuppressionResult(
        columns=list(columns),
        rows=out_rows,
        suppressed_rows=suppressed_rows,
        suppressed_columns=tuple(sorted(suppressed_columns)),
        minimum_group_size=minimum_group_size,
    )


__all__ = ["SUPPRESSED_MASK", "SuppressionResult", "suppress_small_groups"]
