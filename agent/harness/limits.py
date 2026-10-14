"""Output-size and context limits: the guard against context amplification.

The attack and the accident look identical from here. A tool that
returns 40 MB, a RAG retrieval that matches a whole document set, or a
crafted result designed to push the real instructions out of a model's
window all arrive as "one oversized payload", and the defence is the
same: measure before carrying it, truncate at a declared boundary, and
say so.

**Truncation is always disclosed.** A silently shortened result is
worse than a refused one: the model, and then the user, treat a partial
answer as complete. Every function here returns what was dropped
alongside what was kept, and the caller is expected to surface it --
the same stance `governance.result_policy`'s export ceiling takes.

**Sizing is on the serialized form, not `len()`.** A 10-row result of
1 MB cells is not small, and a row count says nothing about bytes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Appended to a truncated string so a reader -- human or model -- can
#: tell. Deliberately plain text rather than a sentinel the model might
#: treat as an instruction.
TRUNCATION_NOTICE = "\n[... truncated by the agent harness: {dropped} characters omitted ...]"


@dataclass(frozen=True)
class SizedPayload:
    """A payload after the size gate, plus what the gate did."""

    value: Any
    original_bytes: int
    kept_bytes: int
    truncated: bool
    note: str = ""

    @property
    def dropped_bytes(self) -> int:
        return max(0, self.original_bytes - self.kept_bytes)


def measure_bytes(value: Any) -> int:
    """The serialized size of `value` in bytes.

    Falls back to `repr` for anything JSON cannot express. The fallback
    matters: returning 0 for an unserializable object would let exactly
    the payloads we understand least past the gate.
    """
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="replace"))
    if isinstance(value, bytes):
        return len(value)
    try:
        return len(json.dumps(value, default=str).encode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return len(repr(value).encode("utf-8", errors="replace"))


def enforce_text_limit(text: str, max_chars: int) -> SizedPayload:
    """Truncates `text` to `max_chars`, appending a visible notice."""
    original = measure_bytes(text)
    if max_chars <= 0 or len(text) <= max_chars:
        return SizedPayload(text, original, original, False)

    dropped = len(text) - max_chars
    kept = text[:max_chars] + TRUNCATION_NOTICE.format(dropped=dropped)
    logger.warning("[harness] truncated text payload: %d characters omitted", dropped)
    return SizedPayload(
        value=kept,
        original_bytes=original,
        kept_bytes=measure_bytes(kept),
        truncated=True,
        note=f"truncated_text:{dropped}",
    )


def enforce_result_limit(rows: list[Any], max_bytes: int) -> SizedPayload:
    """Drops whole rows from the end until the result fits `max_bytes`.

    Whole rows, never partial ones: half a row is a malformed record
    that downstream code would read as real data. A binary search over
    the row count rather than a row-by-row accumulate, because
    serializing a large result once per row is itself a performance
    problem on exactly the inputs this is meant to handle.
    """
    original = measure_bytes(rows)
    if max_bytes <= 0 or original <= max_bytes or not rows:
        return SizedPayload(rows, original, original, False)

    low, high, best = 0, len(rows), 0
    while low <= high:
        mid = (low + high) // 2
        if measure_bytes(rows[:mid]) <= max_bytes:
            best, low = mid, mid + 1
        else:
            high = mid - 1

    kept_rows = rows[:best]
    dropped = len(rows) - best
    logger.warning(
        "[harness] truncated result payload: %d of %d rows dropped (%d bytes > %d limit)",
        dropped,
        len(rows),
        original,
        max_bytes,
    )
    return SizedPayload(
        value=kept_rows,
        original_bytes=original,
        kept_bytes=measure_bytes(kept_rows),
        truncated=True,
        note=f"truncated_rows:{dropped}",
    )


def exceeds_limit(value: Any, max_bytes: int | None) -> bool:
    """Whether `value` is over `max_bytes`. `None`/non-positive means no limit."""
    if max_bytes is None or max_bytes <= 0:
        return False
    return measure_bytes(value) > max_bytes


__all__ = [
    "TRUNCATION_NOTICE",
    "SizedPayload",
    "enforce_result_limit",
    "enforce_text_limit",
    "exceeds_limit",
    "measure_bytes",
]
