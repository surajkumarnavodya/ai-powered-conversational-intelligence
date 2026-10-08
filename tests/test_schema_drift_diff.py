"""Unit tests for the deterministic schema-diff engine
(`schema_drift.diff.compare_schemas`, Prompt 43,
`43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md`). Pure function, no mocks
needed."""

from __future__ import annotations

from db.schema_introspection import ColumnInfo, ForeignKeyInfo, TableSchemaInfo
from schema_drift.diff import DriftChangeKind, compare_schemas


def _table(
    name: str,
    columns: tuple[ColumnInfo, ...],
    *,
    foreign_keys: tuple[ForeignKeyInfo, ...] = (),
    is_view: bool = False,
    unique_constraints: tuple[tuple[str, ...], ...] = (),
) -> TableSchemaInfo:
    return TableSchemaInfo(
        table_name=name,
        columns=columns,
        foreign_keys=foreign_keys,
        ddl="x",
        is_view=is_view,
        unique_constraints=unique_constraints,
    )


def _kinds(facts, table: str | None = None) -> set[DriftChangeKind]:
    return {f.kind for f in facts if table is None or f.table == table}


class TestTableAndViewChanges:
    def test_a_new_table_is_table_added(self):
        previous = [_table("A", (ColumnInfo("id", "int", False, True),))]
        current = [
            _table("A", (ColumnInfo("id", "int", False, True),)),
            _table("B", (ColumnInfo("id", "int", False, True),)),
        ]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.TABLE_ADDED in _kinds(facts, "B")

    def test_a_removed_table_is_table_removed(self):
        previous = [
            _table("A", (ColumnInfo("id", "int", False, True),)),
            _table("B", (ColumnInfo("id", "int", False, True),)),
        ]
        current = [_table("A", (ColumnInfo("id", "int", False, True),))]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.TABLE_REMOVED in _kinds(facts, "B")

    def test_a_new_view_is_view_added_not_table_added(self):
        previous: list[TableSchemaInfo] = []
        current = [_table("V", (ColumnInfo("id", "int", False, False),), is_view=True)]
        facts = compare_schemas(previous, current)
        assert _kinds(facts, "V") == {DriftChangeKind.VIEW_ADDED}

    def test_a_table_becoming_a_view_is_object_type_changed(self):
        previous = [_table("A", (ColumnInfo("id", "int", False, True),), is_view=False)]
        current = [_table("A", (ColumnInfo("id", "int", False, True),), is_view=True)]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.OBJECT_TYPE_CHANGED in _kinds(facts, "A")

    def test_a_first_scan_with_no_previous_snapshot_reports_everything_as_added(self):
        current = [_table("A", (ColumnInfo("id", "int", False, True),))]
        facts = compare_schemas([], current)
        assert _kinds(facts) == {DriftChangeKind.TABLE_ADDED}


class TestColumnChanges:
    def test_a_new_column_is_column_added(self):
        previous = [_table("A", (ColumnInfo("id", "int", False, True),))]
        current = [
            _table(
                "A",
                (ColumnInfo("id", "int", False, True), ColumnInfo("name", "text", True, False)),
            )
        ]
        facts = compare_schemas(previous, current)
        added = [f for f in facts if f.kind == DriftChangeKind.COLUMN_ADDED]
        assert len(added) == 1
        assert added[0].column == "name"
        assert added[0].is_inferred is False

    def test_a_removed_column_is_column_removed(self):
        previous = [
            _table(
                "A",
                (ColumnInfo("id", "int", False, True), ColumnInfo("name", "text", True, False)),
            )
        ]
        current = [_table("A", (ColumnInfo("id", "int", False, True),))]
        facts = compare_schemas(previous, current)
        removed = [f for f in facts if f.kind == DriftChangeKind.COLUMN_REMOVED]
        assert len(removed) == 1
        assert removed[0].column == "name"

    def test_a_type_change_is_reported(self):
        previous = [_table("A", (ColumnInfo("amount", "int", False, False),))]
        current = [_table("A", (ColumnInfo("amount", "decimal", False, False),))]
        facts = compare_schemas(previous, current)
        [fact] = [f for f in facts if f.kind == DriftChangeKind.COLUMN_TYPE_CHANGED]
        assert fact.previous_value == "int"
        assert fact.current_value == "decimal"

    def test_a_nullability_change_is_reported(self):
        previous = [_table("A", (ColumnInfo("amount", "int", False, False),))]
        current = [_table("A", (ColumnInfo("amount", "int", True, False),))]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.COLUMN_NULLABILITY_CHANGED in _kinds(facts, "A")

    def test_an_unchanged_column_produces_no_fact(self):
        previous = [_table("A", (ColumnInfo("id", "int", False, True),))]
        current = [_table("A", (ColumnInfo("id", "int", False, True),))]
        assert compare_schemas(previous, current) == []


class TestRenameHeuristic:
    def test_a_same_type_similarly_named_pair_is_offered_as_a_rename_candidate(self):
        previous = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("custmer_name", "text", True, False),
                ),
            )
        ]
        current = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("customer_name", "text", True, False),
                ),
            )
        ]
        facts = compare_schemas(previous, current)
        renames = [f for f in facts if f.kind == DriftChangeKind.COLUMN_RENAMED]
        assert len(renames) == 1
        assert renames[0].is_inferred is True
        assert renames[0].previous_value == "custmer_name"
        assert renames[0].current_value == "customer_name"
        # The underlying add/remove facts are never suppressed in favor
        # of the inferred rename (master rule 10).
        assert DriftChangeKind.COLUMN_ADDED in _kinds(facts, "A")
        assert DriftChangeKind.COLUMN_REMOVED in _kinds(facts, "A")

    def test_a_type_mismatch_is_never_offered_as_a_rename(self):
        previous = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("custmer_name", "text", True, False),
                ),
            )
        ]
        current = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("customer_name", "int", True, False),
                ),
            )
        ]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.COLUMN_RENAMED not in _kinds(facts, "A")

    def test_dissimilar_names_are_never_offered_as_a_rename(self):
        previous = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("legacy_flag", "text", True, False),
                ),
            )
        ]
        current = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("brand_new_thing", "text", True, False),
                ),
            )
        ]
        facts = compare_schemas(previous, current, rename_similarity_threshold=0.6)
        assert DriftChangeKind.COLUMN_RENAMED not in _kinds(facts, "A")

    def test_a_column_is_never_used_in_two_rename_pairs(self):
        previous = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("alpha_name", "text", True, False),
                    ColumnInfo("beta_name", "text", True, False),
                ),
            )
        ]
        current = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("alpha_name2", "text", True, False),
                ),
            )
        ]
        facts = compare_schemas(previous, current)
        renames = [f for f in facts if f.kind == DriftChangeKind.COLUMN_RENAMED]
        assert len(renames) <= 1


class TestKeysAndRelationships:
    def test_a_primary_key_change_is_reported(self):
        previous = [_table("A", (ColumnInfo("id", "int", False, True),))]
        current = [_table("A", (ColumnInfo("id", "int", False, False),))]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.PRIMARY_KEY_CHANGED in _kinds(facts, "A")

    def test_a_new_foreign_key_is_reported(self):
        previous = [_table("A", (ColumnInfo("b_id", "int", False, False),))]
        current = [
            _table(
                "A",
                (ColumnInfo("b_id", "int", False, False),),
                foreign_keys=(ForeignKeyInfo(("b_id",), "B", ("id",)),),
            )
        ]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.FOREIGN_KEY_ADDED in _kinds(facts, "A")

    def test_a_removed_foreign_key_is_reported(self):
        previous = [
            _table(
                "A",
                (ColumnInfo("b_id", "int", False, False),),
                foreign_keys=(ForeignKeyInfo(("b_id",), "B", ("id",)),),
            )
        ]
        current = [_table("A", (ColumnInfo("b_id", "int", False, False),))]
        facts = compare_schemas(previous, current)
        assert DriftChangeKind.FOREIGN_KEY_REMOVED in _kinds(facts, "A")

    def test_a_unique_constraint_added_and_removed_are_both_reported(self):
        previous = [
            _table(
                "A",
                (ColumnInfo("email", "text", False, False),),
                unique_constraints=(("old_col",),),
            )
        ]
        current = [
            _table(
                "A",
                (ColumnInfo("email", "text", False, False),),
                unique_constraints=(("email",),),
            )
        ]
        facts = compare_schemas(previous, current)
        unique_facts = [f for f in facts if f.kind == DriftChangeKind.UNIQUE_CONSTRAINT_CHANGED]
        assert len(unique_facts) == 2
        values = {(f.previous_value, f.current_value) for f in unique_facts}
        assert ("old_col", None) in values
        assert (None, "email") in values


class TestDeterminism:
    def test_repeated_comparison_of_identical_snapshots_is_empty(self):
        tables = [
            _table(
                "A",
                (ColumnInfo("id", "int", False, True), ColumnInfo("name", "text", True, False)),
                foreign_keys=(),
            )
        ]
        assert compare_schemas(tables, tables) == []

    def test_fact_ordering_is_deterministic_across_calls(self):
        previous = [_table("A", (ColumnInfo("id", "int", False, True),))]
        current = [
            _table(
                "A",
                (
                    ColumnInfo("id", "int", False, True),
                    ColumnInfo("z_col", "text", True, False),
                    ColumnInfo("a_col", "text", True, False),
                ),
            )
        ]
        first = compare_schemas(previous, current)
        second = compare_schemas(previous, current)
        assert first == second
        assert [f.column for f in first] == sorted(f.column for f in first)
