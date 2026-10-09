"""Enterprise BI integrations: bi_connectors

Revision ID: j4k7l0m3n6p9
Revises: h2j5k8l1m4n7
Create Date: 2026-10-09 00:00:00.000000

Adds `identity/models.py::BiConnector` -- Prompt 48
(`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`). See that model's own
docstring for the design rationale (no connection secret stored, a
deliberately loose string reference to a governed semantic-catalog
metric rather than a copy of its formula, scoped `tenant_id`). New RBAC
permission code (`bi_integration.manage`) needs no migration of its own
-- `identity.bootstrap.seed_rbac` is idempotent and picks up
`identity/rbac.py`'s updated `SEED_PERMISSIONS`/`SEED_ROLES`
automatically on the next app startup, the same as every prior
permission addition to this table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "j4k7l0m3n6p9"
down_revision: str | None = "h2j5k8l1m4n7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP_DEFAULT = sa.text("(CURRENT_TIMESTAMP)")
_JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "bi_connectors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("database_id", sa.String(length=100), nullable=False),
        sa.Column("governed_metric_concept_key", sa.String(length=200), nullable=True),
        sa.Column("governed_metric_version", sa.Integer(), nullable=True),
        sa.Column("push_config", _JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="never_refreshed"),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_row_count", sa.Integer(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bi_connectors_tenant_id"), "bi_connectors", ["tenant_id"])
    op.create_index(op.f("ix_bi_connectors_target_type"), "bi_connectors", ["target_type"])


def downgrade() -> None:
    op.drop_index(op.f("ix_bi_connectors_target_type"), table_name="bi_connectors")
    op.drop_index(op.f("ix_bi_connectors_tenant_id"), table_name="bi_connectors")
    op.drop_table("bi_connectors")
