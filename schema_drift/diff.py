"""Deterministic schema-diff engine -- `compare_schemas` is a pure function
over two already-introspected `db.schema_introspection.TableSchemaInfo`
sequences (a previous snapshot, a fresh current one). No I/O, no LLM call,
mirroring every other engine's `analytics.engine.compute_analytics_result`-
style "neither route ever queries a database" posture.

Every `SchemaDriftFact` is `is_inferred=False` (a `DATABASE_FACT`) except
the column-rename heuristic (`DriftChangeKind.COLUMN_RENAMED`), which is
always `is_inferred=True`: pairing an independently-true COLUMN_REMOVED
fact with an independently-true COLUMN_ADDED fact by name/type similarity
is a guess about intent, never something the two schema snapshots
themselves assert. The rename finding is additive -- the underlying
add/remove facts are always reported too, never suppressed in its favor
(master rule 10: never let an inference stand in for an observed fact).
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from db.schema_introspection import ColumnInfo, TableSchemaInfo


class DriftChangeKind(str, Enum):
    TABLE_ADDED = "table_added"
    TABLE_REMOVED = "table_removed"
    VIEW_ADDED = "view_added"
    VIEW_REMOVED = "view_removed"
    OBJECT_TYPE_CHANGED = "object_type_changed"
    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    COLUMN_RENAMED = "column_renamed"
    COLUMN_TYPE_CHANGED = "column_type_changed"
    COLUMN_NULLABILITY_CHANGED = "column_nullability_changed"
    PRIMARY_KEY_CHANGED = "primary_key_changed"
    FOREIGN_KEY_ADDED = "foreign_key_added"
    FOREIGN_KEY_REMOVED = "foreign_key_removed"
    UNIQUE_CONSTRAINT_CHANGED = "unique_constraint_changed"


@dataclass(frozen=True)
class SchemaDriftFact:
    """One deterministic structural difference between two schema
    snapshots. `table`/`column` identify what changed; `previous_value`/
    `current_value` are short, human-readable renderings (never raw
    sampled data -- this module only ever compares catalog metadata, the
    identical scope boundary `db.schema_introspection.introspect_schema`
    itself has)."""

    kind: DriftChangeKind
    table: str
    column: str | None
    previous_value: str | None
    current_value: str | None
    detail: str
    is_inferred: bool = False
    confidence: float = 1.0


def _columns_by_name(table: TableSchemaInfo) -> dict[str, ColumnInfo]:
    return {column.name: column for column in table.columns}


def _primary_key_columns(table: TableSchemaInfo) -> tuple[str, ...]:
    return tuple(sorted(column.name for column in table.columns if column.is_primary_key))


def _foreign_key_set(table: TableSchemaInfo) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        (fk.constrained_columns, fk.referred_table, fk.referred_columns)
        for fk in table.foreign_keys
    }


def _unique_constraint_set(table: TableSchemaInfo) -> set[tuple[str, ...]]:
    return {tuple(sorted(columns)) for columns in table.unique_constraints}


def _diff_columns(
    table_name: str, previous: TableSchemaInfo, current: TableSchemaInfo
) -> list[SchemaDriftFact]:
    facts: list[SchemaDriftFact] = []
    previous_columns = _columns_by_name(previous)
    current_columns = _columns_by_name(current)

    added_names = sorted(set(current_columns) - set(previous_columns))
    removed_names = sorted(set(previous_columns) - set(current_columns))
    common_names = sorted(set(previous_columns) & set(current_columns))

    for name in added_names:
        column = current_columns[name]
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.COLUMN_ADDED,
                table=table_name,
                column=name,
                previous_value=None,
                current_value=f"{column.type}{' NULL' if column.nullable else ' NOT NULL'}",
                detail=f"Column {table_name}.{name} was added ({column.type}).",
            )
        )
    for name in removed_names:
        column = previous_columns[name]
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.COLUMN_REMOVED,
                table=table_name,
                column=name,
                previous_value=f"{column.type}{' NULL' if column.nullable else ' NOT NULL'}",
                current_value=None,
                detail=f"Column {table_name}.{name} was removed (was {column.type}).",
            )
        )
    for name in common_names:
        before, after = previous_columns[name], current_columns[name]
        if before.type != after.type:
            facts.append(
                SchemaDriftFact(
                    kind=DriftChangeKind.COLUMN_TYPE_CHANGED,
                    table=table_name,
                    column=name,
                    previous_value=before.type,
                    current_value=after.type,
                    detail=f"Column {table_name}.{name} changed type from {before.type} to {after.type}.",
                )
            )
        if before.nullable != after.nullable:
            facts.append(
                SchemaDriftFact(
                    kind=DriftChangeKind.COLUMN_NULLABILITY_CHANGED,
                    table=table_name,
                    column=name,
                    previous_value="NULL" if before.nullable else "NOT NULL",
                    current_value="NULL" if after.nullable else "NOT NULL",
                    detail=(
                        f"Column {table_name}.{name} changed nullability from "
                        f"{'NULL' if before.nullable else 'NOT NULL'} to "
                        f"{'NULL' if after.nullable else 'NOT NULL'}."
                    ),
                )
            )

    return facts


def _infer_renames(
    table_name: str,
    previous: TableSchemaInfo,
    current: TableSchemaInfo,
    similarity_threshold: float,
) -> list[SchemaDriftFact]:
    """A bounded, greedy name-similarity pairing between this table's own
    removed and added columns -- never a general fuzzy match across
    tables or across unrelated name pairs. A pair must already share an
    identical type and nullability (a rename does not usually also change
    a column's shape) before similarity is even considered."""
    previous_columns = _columns_by_name(previous)
    current_columns = _columns_by_name(current)
    removed_names = sorted(set(previous_columns) - set(current_columns))
    added_names = sorted(set(current_columns) - set(previous_columns))
    if not removed_names or not added_names:
        return []

    candidates: list[tuple[float, str, str]] = []
    for old_name in removed_names:
        old_column = previous_columns[old_name]
        for new_name in added_names:
            new_column = current_columns[new_name]
            if old_column.type != new_column.type or old_column.nullable != new_column.nullable:
                continue
            ratio = difflib.SequenceMatcher(None, old_name.lower(), new_name.lower()).ratio()
            if ratio >= similarity_threshold:
                candidates.append((ratio, old_name, new_name))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_old: set[str] = set()
    used_new: set[str] = set()
    facts: list[SchemaDriftFact] = []
    for ratio, old_name, new_name in candidates:
        if old_name in used_old or new_name in used_new:
            continue
        used_old.add(old_name)
        used_new.add(new_name)
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.COLUMN_RENAMED,
                table=table_name,
                column=new_name,
                previous_value=old_name,
                current_value=new_name,
                detail=(
                    f"{table_name}.{old_name} may have been renamed to {table_name}.{new_name} "
                    f"(same type, name similarity {ratio:.2f}) -- unconfirmed, review before "
                    "treating this as a single rename rather than two independent changes."
                ),
                is_inferred=True,
                confidence=ratio,
            )
        )
    return facts


def _diff_keys_and_relationships(
    table_name: str, previous: TableSchemaInfo, current: TableSchemaInfo
) -> list[SchemaDriftFact]:
    facts: list[SchemaDriftFact] = []

    previous_pk = _primary_key_columns(previous)
    current_pk = _primary_key_columns(current)
    if previous_pk != current_pk:
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.PRIMARY_KEY_CHANGED,
                table=table_name,
                column=None,
                previous_value=", ".join(previous_pk) or "(none)",
                current_value=", ".join(current_pk) or "(none)",
                detail=(
                    f"Primary key of {table_name} changed from "
                    f"({', '.join(previous_pk) or 'none'}) to ({', '.join(current_pk) or 'none'})."
                ),
            )
        )

    previous_fks = _foreign_key_set(previous)
    current_fks = _foreign_key_set(current)
    for constrained, referred_table, referred_columns in sorted(current_fks - previous_fks):
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.FOREIGN_KEY_ADDED,
                table=table_name,
                column=", ".join(constrained),
                previous_value=None,
                current_value=f"{referred_table}({', '.join(referred_columns)})",
                detail=(
                    f"Foreign key added: {table_name}({', '.join(constrained)}) -> "
                    f"{referred_table}({', '.join(referred_columns)})."
                ),
            )
        )
    for constrained, referred_table, referred_columns in sorted(previous_fks - current_fks):
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.FOREIGN_KEY_REMOVED,
                table=table_name,
                column=", ".join(constrained),
                previous_value=f"{referred_table}({', '.join(referred_columns)})",
                current_value=None,
                detail=(
                    f"Foreign key removed: {table_name}({', '.join(constrained)}) -> "
                    f"{referred_table}({', '.join(referred_columns)})."
                ),
            )
        )

    previous_unique = _unique_constraint_set(previous)
    current_unique = _unique_constraint_set(current)
    for columns in sorted(current_unique - previous_unique):
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.UNIQUE_CONSTRAINT_CHANGED,
                table=table_name,
                column=", ".join(columns),
                previous_value=None,
                current_value=", ".join(columns),
                detail=f"Unique constraint added on {table_name}({', '.join(columns)}).",
            )
        )
    for columns in sorted(previous_unique - current_unique):
        facts.append(
            SchemaDriftFact(
                kind=DriftChangeKind.UNIQUE_CONSTRAINT_CHANGED,
                table=table_name,
                column=", ".join(columns),
                previous_value=", ".join(columns),
                current_value=None,
                detail=f"Unique constraint removed on {table_name}({', '.join(columns)}).",
            )
        )

    return facts


def compare_schemas(
    previous: Sequence[TableSchemaInfo],
    current: Sequence[TableSchemaInfo],
    rename_similarity_threshold: float = 0.6,
) -> list[SchemaDriftFact]:
    """Compares two already-introspected schema snapshots of one database.

    Args:
        previous: The prior snapshot (`schema_drift.snapshot_store
            .load_snapshot`'s own return shape) -- `None`/empty means no
            baseline exists yet (a first scan), in which case every table
            legitimately reports as `TABLE_ADDED`/`VIEW_ADDED`, the same
            "a first build has nothing to diff against" precedent
            `embeddings.schema_indexer.SchemaDiscoveryDiff` already
            establishes for table-level discovery.
        current: A fresh live introspection
            (`db.schema_introspection.introspect_schema`'s own output).
        rename_similarity_threshold: `schema_drift.diff._infer_renames`'s
            own minimum `difflib.SequenceMatcher` ratio before a
            removed+added column pair is even offered as a rename
            candidate.

    Returns:
        Every `SchemaDriftFact` found, in a deterministic order (by table
        name, then by kind, then by column name) -- never a reason this
        function itself raises; a genuinely malformed snapshot pair
        (e.g. a table name appearing twice) is a caller bug this function
        does not defend against, mirroring `analytics.engine
        .compute_analytics_result`'s own "trusts its own typed input"
        posture.
    """
    previous_by_name = {table.table_name: table for table in previous}
    current_by_name = {table.table_name: table for table in current}

    facts: list[SchemaDriftFact] = []

    added_tables = sorted(set(current_by_name) - set(previous_by_name))
    removed_tables = sorted(set(previous_by_name) - set(current_by_name))
    common_tables = sorted(set(previous_by_name) & set(current_by_name))

    for name in added_tables:
        table = current_by_name[name]
        kind = DriftChangeKind.VIEW_ADDED if table.is_view else DriftChangeKind.TABLE_ADDED
        facts.append(
            SchemaDriftFact(
                kind=kind,
                table=name,
                column=None,
                previous_value=None,
                current_value="view" if table.is_view else "table",
                detail=f"{'View' if table.is_view else 'Table'} {name} was added "
                f"({len(table.columns)} column(s)).",
            )
        )
    for name in removed_tables:
        table = previous_by_name[name]
        kind = DriftChangeKind.VIEW_REMOVED if table.is_view else DriftChangeKind.TABLE_REMOVED
        facts.append(
            SchemaDriftFact(
                kind=kind,
                table=name,
                column=None,
                previous_value="view" if table.is_view else "table",
                current_value=None,
                detail=f"{'View' if table.is_view else 'Table'} {name} was removed.",
            )
        )
    for name in common_tables:
        before, after = previous_by_name[name], current_by_name[name]
        if before.is_view != after.is_view:
            facts.append(
                SchemaDriftFact(
                    kind=DriftChangeKind.OBJECT_TYPE_CHANGED,
                    table=name,
                    column=None,
                    previous_value="view" if before.is_view else "table",
                    current_value="view" if after.is_view else "table",
                    detail=(
                        f"{name} changed from a "
                        f"{'view' if before.is_view else 'table'} to a "
                        f"{'view' if after.is_view else 'table'}."
                    ),
                )
            )
        facts.extend(_diff_columns(name, before, after))
        facts.extend(_infer_renames(name, before, after, rename_similarity_threshold))
        facts.extend(_diff_keys_and_relationships(name, before, after))

    facts.sort(key=lambda fact: (fact.table, fact.kind.value, fact.column or ""))
    return facts


__all__ = ["DriftChangeKind", "SchemaDriftFact", "compare_schemas"]
