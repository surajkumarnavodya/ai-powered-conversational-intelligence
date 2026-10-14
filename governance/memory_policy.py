"""Conversation-memory governance: what a stored turn is allowed to retain.

The other result-level gates decide what a *caller* may see right now.
This one decides what the application may still be holding months from
now, which is a different question with a different answer: a value that
was correctly shown to an authorized analyst at 10:00 is not thereby
something the database should keep forever.

**The rule, and why it is this narrow.** A column classified in a
never-disclosed category (credential, payment instrument) has its values
replaced with `WITHHELD_MASK` *before* the write. No role -- not an
admin, not the user who ran the query -- is ever shown those values by
`governance.result_policy`, so persisting them buys nothing a reader
could legitimately use and leaves a durable copy of a password or a card
number in a JSONB column. Retaining it is pure risk, so it is not
retained.

Deliberately **not** masked here: compensation, PHI, performance, legal
and the rest of the HIGHLY_SENSITIVE tier. Those have authorized
readers, and a reopened conversation showing an analyst the salary
figures they legitimately ran is the feature working. They are protected
instead at *read* time, re-governed against whoever is asking then
(`api.artifacts._result_out`, `api.shares._projected_turn_out`) -- which
is the control that stays correct when roles change, and masking at
write time would not. Over-masking the store would quietly break every
authorized reload while adding no protection the read gate does not
already give.

`retained_categories` is the audit trail the prompt's §27 asks for:
which data categories a stored turn holds, as category names and counts
only. Never a value, never a cell, never a row.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from governance.classification import DataClassification, classify_columns

logger = logging.getLogger(__name__)

#: What a never-disclosed value becomes in the stored record. Identical
#: to `governance.result_policy.WITHHELD_MASK` by intent -- a reader of a
#: stored snapshot must not be able to tell whether the mask was applied
#: when the row was captured or when it was read back, because the answer
#: ("no role sees this") is the same either way. Defined here rather than
#: imported so this module stays importable on its own.
WITHHELD_MASK = "[withheld]"


@dataclass(frozen=True)
class MemoryGovernedSnapshot:
    """Rows safe to persist, plus what persisting them retains.

    `withheld_columns` and `retained_categories` are names and counts
    only -- safe to log and safe to store alongside the snapshot.
    """

    rows: list[Any]
    withheld_columns: tuple[str, ...]
    retained_categories: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.withheld_columns)


def govern_rows_for_memory(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> MemoryGovernedSnapshot:
    """Protects a result snapshot on its way *into* durable storage.

    Classifies the result's own column names -- the same names the read
    gate classifies, so an alias or a `UNION` reaches this protection
    exactly as it reaches that one -- and blanks any column in a
    never-disclosed category.

    Args:
        columns: The snapshot's column names.
        rows: The rows about to be persisted. Left untouched; a new list
            is returned.

    Returns:
        A `MemoryGovernedSnapshot`. When nothing is never-disclosed the
        rows are returned as-is and `changed` is False, so the common
        case costs one classification pass and no copying.
    """
    if not rows or not columns:
        return MemoryGovernedSnapshot(rows=list(rows), withheld_columns=(), retained_categories=())

    policies = classify_columns(list(columns))
    withheld_indexes: dict[int, str] = {}
    retained: set[str] = set()
    for index, name in enumerate(columns):
        policy = policies[name]
        if policy.is_never_disclosed:
            withheld_indexes[index] = name
            continue
        if policy.classification >= DataClassification.SENSITIVE:
            retained.add(policy.category.value)

    retained_categories = tuple(sorted(retained))
    if not withheld_indexes:
        return MemoryGovernedSnapshot(
            rows=list(rows),
            withheld_columns=(),
            retained_categories=retained_categories,
        )

    protected: list[Any] = []
    for row in rows:
        values = list(row)
        for index in withheld_indexes:
            if index < len(values):
                values[index] = WITHHELD_MASK
        protected.append(tuple(values) if isinstance(row, tuple) else values)

    withheld_columns = tuple(sorted(withheld_indexes.values()))
    logger.info(
        "[memory_governance] withheld %d never-disclosed column(s) from a stored turn",
        len(withheld_columns),
    )
    return MemoryGovernedSnapshot(
        rows=protected,
        withheld_columns=withheld_columns,
        retained_categories=retained_categories,
    )


__all__ = [
    "WITHHELD_MASK",
    "MemoryGovernedSnapshot",
    "govern_rows_for_memory",
]
