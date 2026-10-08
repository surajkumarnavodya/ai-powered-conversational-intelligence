"""AI Governance Center: governance_asset_versions, governance_events

Revision ID: c7d2e5f8a1b4
Revises: a1f5c9e3b7d2
Create Date: 2026-10-09 00:00:00.000000

Adds `identity/models.py::GovernanceAssetVersion`/`GovernanceEvent` --
Prompt 44 (`44_AI_GOVERNANCE_CENTER_CONTRACT.md`). See those models' own
docstrings for the design rationale (one row per asset version, an
append-only event log, scoped `tenant_id`, the "at most one deployed
version per asset" invariant enforced at the repository layer rather
than by a database constraint). New RBAC permission codes
(`governance.manage`/`governance.review`) need no migration of their
own -- `identity.bootstrap.seed_rbac` is idempotent and picks up
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
revision: str = "c7d2e5f8a1b4"
down_revision: str | None = "a1f5c9e3b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP_DEFAULT = sa.text("(CURRENT_TIMESTAMP)")
_JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "governance_asset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("asset_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("stage", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("config", _JSON_TYPE, nullable=False),
        sa.Column("test_results", _JSON_TYPE, nullable=True),
        sa.Column("evaluation_results", _JSON_TYPE, nullable=True),
        sa.Column("approval_notes", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("tested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deployed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["governance_asset_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evaluated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deployed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["retired_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "asset_type",
            "asset_key",
            "version",
            name="uq_governance_asset_versions_scope_version",
        ),
    )
    op.create_index(
        op.f("ix_governance_asset_versions_tenant_id"), "governance_asset_versions", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_governance_asset_versions_asset_type"),
        "governance_asset_versions",
        ["asset_type"],
    )
    op.create_index(
        op.f("ix_governance_asset_versions_asset_key"), "governance_asset_versions", ["asset_key"]
    )
    op.create_index(
        op.f("ix_governance_asset_versions_stage"), "governance_asset_versions", ["stage"]
    )

    op.create_table(
        "governance_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_version_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("from_stage", sa.String(length=16), nullable=True),
        sa.Column("to_stage", sa.String(length=16), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(length=200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload", _JSON_TYPE, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["asset_version_id"], ["governance_asset_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_governance_events_asset_version_id"), "governance_events", ["asset_version_id"]
    )
    op.create_index(op.f("ix_governance_events_tenant_id"), "governance_events", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_governance_events_tenant_id"), table_name="governance_events")
    op.drop_index(op.f("ix_governance_events_asset_version_id"), table_name="governance_events")
    op.drop_table("governance_events")
    op.drop_index(
        op.f("ix_governance_asset_versions_stage"), table_name="governance_asset_versions"
    )
    op.drop_index(
        op.f("ix_governance_asset_versions_asset_key"), table_name="governance_asset_versions"
    )
    op.drop_index(
        op.f("ix_governance_asset_versions_asset_type"), table_name="governance_asset_versions"
    )
    op.drop_index(
        op.f("ix_governance_asset_versions_tenant_id"), table_name="governance_asset_versions"
    )
    op.drop_table("governance_asset_versions")
