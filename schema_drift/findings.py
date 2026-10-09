"""Wraps `schema_drift.diff.SchemaDriftFact`s into
`semantic.intelligence.detect.Finding` -- the exact same typed shape
Prompt 35's catalog detectors already produce, so this feature can reuse
`identity.repositories.semantic_intelligence`'s governed review-queue
storage/lifecycle unmodified (see `schema_drift/__init__.py`'s own
docstring for why).

Every `kind` here is prefixed `schema_drift_` (`KIND_PREFIX`) so a
schema-drift finding is never visually or programmatically confused with
a Prompt-35 catalog-detector finding sharing the same `SemanticFinding`
table -- `api/schema_drift.py`'s own `/findings` route filters on this
exact prefix.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from semantic.intelligence.detect import Finding

from agent.provenance import DataTruthLevel
from schema_drift.diff import DriftChangeKind, SchemaDriftFact

KIND_PREFIX = "schema_drift_"

#: One finding kind per `DriftChangeKind` value -- a flat string, never a
#: free-text label, so `semantic.intelligence.risk.score`'s own `_BASE`
#: dict lookup (extended for these exact values -- see that module's own
#: docstring) and `api/schema_drift.py`'s `/findings` kind-prefix filter
#: both have a closed, enumerable set to work from.
_KIND_BY_CHANGE: dict[DriftChangeKind, str] = {
    change: f"{KIND_PREFIX}{change.value}" for change in DriftChangeKind
}

#: The hard limit on `identity.models.SemanticFinding.kind`. Restated here
#: rather than imported, because this package deliberately never imports
#: `identity` (see `schema_drift/__init__.py`), mirroring
#: `kpi_monitoring.findings.KIND_MAX_LENGTH`'s own identical constant.
#:
#: This module is why that column had to be widened: two of the kinds
#: below are 38 and 39 characters, and the column was `String(32)` until
#: migration `m6q9r2s5t8u1`. SQLite silently ignores a VARCHAR length, so
#: every test passed while every write to the real PostgreSQL identity
#: database was rejected -- disclosed as a known latent bug by Prompt 50
#: and confirmed live during Prompt 51's end-to-end run. The assertion
#: below is what keeps a future `DriftChangeKind` from reintroducing it.
KIND_MAX_LENGTH = 64


def _assert_kinds_fit() -> None:
    too_long = [kind for kind in _KIND_BY_CHANGE.values() if len(kind) > KIND_MAX_LENGTH]
    if too_long:
        raise RuntimeError(
            f"schema-drift finding kind(s) exceed SemanticFinding.kind's "
            f"{KIND_MAX_LENGTH}-character column and would fail on PostgreSQL: {too_long}"
        )


_assert_kinds_fit()


def _finding_key(database_id: str, fact: SchemaDriftFact) -> str:
    """Stable identity for one fact: database + kind + table + column,
    hashed -- deterministic across repeated scans of an unchanged schema,
    exactly like `semantic.intelligence.detect._finding_key`'s own
    contract (a different hash input, since a schema-drift fact's
    identity is table/column-shaped, not catalog-concept-shaped)."""
    raw = "|".join([database_id, fact.kind.value, fact.table, fact.column or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _subject(database_id: str, fact: SchemaDriftFact) -> dict[str, Any]:
    return {
        "database_id": database_id,
        "table": fact.table,
        "column": fact.column,
    }


def fact_to_finding(database_id: str, fact: SchemaDriftFact) -> Finding:
    """Converts one deterministic structural fact into a `Finding`.

    `truth_level` is `DataTruthLevel.DATABASE_FACT` for every change kind
    except `COLUMN_RENAMED` (`AI_INFERENCE`, matching `fact.is_inferred`)
    -- master rule 10: a diff between two real snapshots is an observed
    fact; a rename pairing is a guess about intent.
    """
    truth_level = DataTruthLevel.AI_INFERENCE if fact.is_inferred else DataTruthLevel.DATABASE_FACT
    subject = fact.table if fact.column is None else f"{fact.table}.{fact.column}"
    title = f"{fact.kind.value.replace('_', ' ').capitalize()}: {subject}"
    evidence: tuple[dict[str, Any], ...] = (
        {
            "table": fact.table,
            "column": fact.column,
            "previous_value": fact.previous_value,
            "current_value": fact.current_value,
        },
    )
    return Finding(
        kind=_KIND_BY_CHANGE[fact.kind],
        key=_finding_key(database_id, fact),
        title=title,
        detail=fact.detail,
        subjects=(_subject(database_id, fact),),
        evidence=evidence,
        confidence=fact.confidence,
        truth_level=truth_level.value,
        payload={
            "database_id": database_id,
            "table": fact.table,
            "column": fact.column,
            "change_kind": fact.kind.value,
            "is_inferred": fact.is_inferred,
        },
    )


def facts_to_findings(database_id: str, facts: Sequence[SchemaDriftFact]) -> list[Finding]:
    """`fact_to_finding` applied to every fact, in the same deterministic
    order `schema_drift.diff.compare_schemas` already returns them in."""
    return [fact_to_finding(database_id, fact) for fact in facts]


def is_schema_drift_kind(kind: str) -> bool:
    """True for any `Finding.kind`/`identity.models.SemanticFinding.kind`
    this module produced -- the one place `KIND_PREFIX` itself is
    checked, so `api/schema_drift.py` never hardcodes the prefix string
    a second time."""
    return kind.startswith(KIND_PREFIX)


__all__ = [
    "KIND_MAX_LENGTH",
    "KIND_PREFIX",
    "fact_to_finding",
    "facts_to_findings",
    "is_schema_drift_kind",
]
