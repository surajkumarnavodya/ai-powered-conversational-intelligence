"""semantic_findings: restore the created_at/updated_at defaults and widen kind

Revision ID: m6q9r2s5t8u1
Revises: k5n8p1q4r7s0
Create Date: 2026-10-09 00:00:00.000000

Two real bugs in `semantic_findings`, both found by running the Prompt 51
executive briefing end to end against the **real PostgreSQL identity
database** rather than the SQLite fixture the test suite uses. Both were
invisible to every test: `Base.metadata.create_all` builds the table from
`identity/models.py`, which is correct, so only a database built by
`a1f5c9e3b7d2` (the migration) is affected -- which is every real
deployment.

**1. `created_at`/`updated_at` were `NOT NULL` with no default.**
`identity.models.SemanticFinding` declares both via `_created_at()`/
`_updated_at()`, which carry `server_default=func.now()`, so the ORM omits
them from every INSERT and lets the database fill them. `a1f5c9e3b7d2`
created the columns without that default (the only table in this schema
missing it -- `users`, `recommendation_records`, `semantic_catalog_entries`,
`workflows`, `governance_asset_versions` and `evaluation_runs` all have
`CURRENT_TIMESTAMP`), so every insert raised
`NotNullViolation: null value in column "created_at"`.

The effect was total: **no semantic finding could be written at all**, which
silently disabled the shared review queue for all three of its producers --
semantic intelligence (Prompt 35), schema drift (Prompt 43) and KPI
monitoring (Prompt 50) -- and therefore two of the executive briefing's four
sources (Prompt 51). The `NULL` backfill below is defensive only; no row can
exist with a null `created_at`, because the insert that would have created it
failed.

**2. `kind` was `String(32)`, too short for schema drift's own kinds.**
`schema_drift.findings` emits `schema_drift_column_nullability_changed` (39
characters) and `schema_drift_unique_constraint_changed` (38), both of which
PostgreSQL rejects outright while SQLite silently ignores the length. This
was already disclosed as a known latent bug in
`50_PROACTIVE_AI_ANALYST_CONTRACT.md` and in CLAUDE.md's Prompt 50 section;
widening the column to 64 is the fix, and `kpi_monitoring.findings
.KIND_MAX_LENGTH`'s import-time guard moves with it so a future kind that
would overflow still fails fast rather than at the first write.

Both changes are additive-in-effect: widening a varchar and adding a default
can neither reject an existing row nor change one. The downgrade restores the
original shape, but refuses to narrow `kind` back to 32 if any stored kind is
already longer -- silently truncating a governed review item's own type would
be worse than failing the downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m6q9r2s5t8u1"
down_revision: str | None = "k5n8p1q4r7s0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Defensive: no such row can exist (the failing insert never committed),
    # but a backfill before re-asserting NOT NULL costs nothing and makes the
    # migration safe on a database in any state. CURRENT_TIMESTAMP rather
    # than now() so the statement is valid on SQLite too -- see the
    # batch_alter_table note below.
    op.execute(
        "UPDATE semantic_findings "
        "SET created_at = COALESCE(created_at, last_seen_at, CURRENT_TIMESTAMP), "
        "updated_at = COALESCE(updated_at, last_seen_at, CURRENT_TIMESTAMP) "
        "WHERE created_at IS NULL OR updated_at IS NULL"
    )
    # `batch_alter_table` rather than three bare `alter_column` calls: on
    # PostgreSQL (production) it emits exactly the same ALTER statements,
    # while on SQLite it falls back to copy-and-move. SQLite cannot ALTER a
    # column at all, so without batch mode this migration would raise
    # `NotImplementedError` there -- which matters because
    # `tests/test_identity_migration_parity.py` runs the whole chain against
    # SQLite to catch precisely the model-versus-migration drift this
    # migration exists to repair.
    with op.batch_alter_table("semantic_findings") as batch:
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.func.now(),
        )
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.func.now(),
        )
        batch.alter_column(
            "kind",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    too_long = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM semantic_findings WHERE length(kind) > 32"))
        .scalar()
    )
    if too_long:
        raise RuntimeError(
            f"{too_long} stored finding(s) have a kind longer than 32 characters; narrowing "
            "the column would truncate a governed review item's own type. Decide those "
            "findings or delete them before downgrading."
        )
    with op.batch_alter_table("semantic_findings") as batch:
        batch.alter_column(
            "kind",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
