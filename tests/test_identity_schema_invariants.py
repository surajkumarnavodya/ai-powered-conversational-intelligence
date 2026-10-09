"""Invariants the identity schema must hold, across the ORM models, the
Alembic migrations, and every package that writes into them.

**Why this file exists.** Running the Prompt 51 executive briefing against
the real PostgreSQL identity database found two bugs in
`semantic_findings` that the entire test suite was structurally unable to
see, because every test builds its schema with
`Base.metadata.create_all` (which reads `identity/models.py`, and is
correct) while every real deployment builds it from the migrations (which
were not):

1. `created_at`/`updated_at` were created `NOT NULL` with no
   `server_default`, while the model declares one. The ORM therefore omits
   both columns from every INSERT and lets the database fill them --
   so every insert raised `NotNullViolation` and **no semantic finding
   could be written at all**, silently disabling the shared review queue
   for semantic intelligence (Prompt 35), schema drift (Prompt 43) and KPI
   monitoring (Prompt 50).
2. `kind` was `String(32)`, but `schema_drift.findings` emits kinds of 38
   and 39 characters. PostgreSQL rejects those; SQLite silently ignores a
   VARCHAR length, so every test passed.

Both are repaired by migration `m6q9r2s5t8u1`. The tests below are the
guard that keeps either from coming back, and they catch the whole bug
*family* rather than these two instances: a future table whose timestamp
columns omit the default, or a future finding kind that overflows the
column, fails here instead of in production.

Running the full migration chain against SQLite and diffing it against
the models would be the most complete check, but it is not available: an
earlier, already-applied migration (`b7e2d4a1c9f0`) uses constraint
operations SQLite cannot perform outside batch mode, and this project's
own convention is never to hand-edit an applied migration. These static
checks cover the same two failure modes without that dependency.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from identity.models import Base, SemanticFinding
from semantic.intelligence.risk import _BASE as RISK_BASE

from kpi_monitoring.findings import KIND_MAX_LENGTH as KPI_KIND_MAX_LENGTH
from kpi_monitoring.models import ChangeKind
from schema_drift.findings import _KIND_BY_CHANGE
from schema_drift.findings import KIND_MAX_LENGTH as DRIFT_KIND_MAX_LENGTH

MIGRATIONS_DIR = pathlib.Path("identity/migrations/versions")

#: Timestamp columns whose value the ORM never supplies, so the database
#: must. Any `create_table` that declares one without a `server_default`
#: produces a table the ORM cannot insert into.
_ORM_FILLED_TIMESTAMPS = ("created_at", "updated_at")

#: The one historical violation, left in place rather than hand-edited
#: (this project never rewrites an applied migration) and repaired
#: forward by `m6q9r2s5t8u1`. Listed explicitly so the invariant below
#: stays enforced for every other migration, and so this exception is
#: documented rather than silently tolerated.
_KNOWN_MISSING_DEFAULTS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("a1f5c9e3b7d2_semantic_intelligence.py", "semantic_findings", "created_at"),
        ("a1f5c9e3b7d2_semantic_intelligence.py", "semantic_findings", "updated_at"),
    }
)


def _created_table_timestamp_columns() -> list[tuple[str, str, str, bool]]:
    """Every `(migration, table, column, declares_server_default)` for a
    timestamp column in an `op.create_table(...)` call.

    Parsed from the real migration source rather than executed, so this
    needs no database of any kind.
    """
    found: list[tuple[str, str, str, bool]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                continue
            table = str(node.args[0].value)
            for arg in node.args[1:]:
                if not (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "Column"
                    and arg.args
                    and isinstance(arg.args[0], ast.Constant)
                ):
                    continue
                column = str(arg.args[0].value)
                if column not in _ORM_FILLED_TIMESTAMPS:
                    continue
                has_default = any(kw.arg == "server_default" for kw in arg.keywords)
                found.append((path.name, table, column, has_default))
    return found


class TestMigrationsMatchWhatTheOrmExpects:
    def test_the_scan_actually_finds_timestamp_columns(self):
        """Guards every assertion below from passing vacuously if the
        migrations directory moves or the parse silently returns nothing."""
        found = _created_table_timestamp_columns()
        assert len(found) >= 20, f"only found {len(found)} timestamp column(s)"
        assert len({table for _, table, _, _ in found}) >= 10

    def test_every_created_timestamp_column_declares_a_server_default(self):
        """The bug that stopped every `semantic_findings` insert.

        A column the ORM never populates and the database never defaults
        is a column no insert can satisfy -- and the failure only appears
        against a migration-built database, which is every real one.
        """
        offenders = [
            (migration, table, column)
            for migration, table, column, has_default in _created_table_timestamp_columns()
            if not has_default and (migration, table, column) not in _KNOWN_MISSING_DEFAULTS
        ]
        assert offenders == [], (
            "these migrations create an ORM-filled timestamp column with no server_default, "
            f"so no ORM insert into that table can succeed: {offenders}"
        )

    def test_the_known_violation_is_still_exactly_what_it_was(self):
        """If `a1f5c9e3b7d2` is ever corrected in place, this allowlist
        must shrink with it -- otherwise it silently starts excusing a
        column that no longer needs excusing."""
        actual = {
            (migration, table, column)
            for migration, table, column, has_default in _created_table_timestamp_columns()
            if not has_default
        }
        assert actual == set(_KNOWN_MISSING_DEFAULTS)

    def test_a_repair_migration_exists_for_the_known_violation(self):
        """The allowlist above is only honest because the gap is repaired
        forward. This pins that the repair is still present."""
        repair = MIGRATIONS_DIR / "m6q9r2s5t8u1_semantic_findings_defaults.py"
        assert repair.exists()
        source = repair.read_text(encoding="utf-8")
        assert "server_default=sa.func.now()" in source
        assert 'batch.alter_column(\n            "created_at"' in source
        assert 'batch.alter_column(\n            "updated_at"' in source

    def test_models_declare_a_server_default_on_every_orm_filled_timestamp(self):
        """The model side of the same invariant: if a model ever stops
        declaring the default, the migrations above would be the only
        thing filling it, and a `create_all`-built test database would
        start failing instead."""
        offenders = [
            f"{table.name}.{column.name}"
            for table in Base.metadata.tables.values()
            for column in table.columns
            if column.name in _ORM_FILLED_TIMESTAMPS and column.server_default is None
        ]
        assert offenders == [], offenders


class TestFindingKindFitsItsColumn:
    def _column_length(self) -> int:
        length = SemanticFinding.__table__.c.kind.type.length
        assert length is not None
        return int(length)

    def test_the_restated_limits_match_the_real_column(self):
        """`kpi_monitoring` and `schema_drift` deliberately never import
        `identity` (both assert that structurally in their own security
        tests), so each restates the column's limit as a constant. That
        makes drift possible, which is exactly what happened -- this is
        the test that makes the duplication safe."""
        actual = self._column_length()
        assert actual == KPI_KIND_MAX_LENGTH, (
            f"kpi_monitoring.findings.KIND_MAX_LENGTH is {KPI_KIND_MAX_LENGTH} but "
            f"SemanticFinding.kind is String({actual})"
        )
        assert actual == DRIFT_KIND_MAX_LENGTH, (
            f"schema_drift.findings.KIND_MAX_LENGTH is {DRIFT_KIND_MAX_LENGTH} but "
            f"SemanticFinding.kind is String({actual})"
        )

    def test_every_kind_any_producer_can_emit_fits_the_column(self):
        """Covers all three producers of the shared queue at once,
        including the two schema-drift kinds (38 and 39 characters) that
        PostgreSQL rejected while SQLite ignored the length."""
        limit = self._column_length()
        kinds = {
            *(kind.value for kind in ChangeKind),
            *_KIND_BY_CHANGE.values(),
            *RISK_BASE.keys(),
        }
        too_long = sorted(kind for kind in kinds if len(kind) > limit)
        assert too_long == [], f"kind(s) longer than String({limit}): {too_long}"

    def test_the_previously_overflowing_kinds_are_still_covered(self):
        """A regression pin on the two concrete values that broke, so a
        future narrowing of the column fails here with a recognizable
        message rather than only in the generic check above."""
        limit = self._column_length()
        for kind in (
            "schema_drift_column_nullability_changed",
            "schema_drift_unique_constraint_changed",
        ):
            assert kind in _KIND_BY_CHANGE.values(), f"{kind} is no longer a real drift kind"
            assert len(kind) <= limit, f"{kind} ({len(kind)} chars) no longer fits String({limit})"

    @pytest.mark.parametrize("producer", ["kpi_monitoring", "schema_drift"])
    def test_each_producer_asserts_its_own_kinds_at_import_time(self, producer):
        """Both guards run at import, so a bad kind fails fast rather
        than at the first write. Importing the module is the assertion."""
        module = __import__(f"{producer}.findings", fromlist=["_assert_kinds_fit"])
        module._assert_kinds_fit()
