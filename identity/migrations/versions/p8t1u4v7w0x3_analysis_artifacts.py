"""Analysis artifact workspace: analysis_artifacts

Revision ID: p8t1u4v7w0x3
Revises: m6q9r2s5t8u1
Create Date: 2026-10-10 00:00:00.000000

Adds `identity/models.py::AnalysisArtifact` -- see
`docs/conversational-intelligence-ui.md`. See that model's own
docstring for the design rationale: an artifact references the turn's
already-persisted `ai_outputs.metadata.result_snapshot` rather than
copying it, keeps only the three facts staleness needs about it, and owns
nothing but the rows a *refresh* produced. There is deliberately no
freshness/staleness column -- see `artifacts/staleness.py`.

No new RBAC permission code is introduced by this feature at all (the
base-role `ask`/`execute_sql` vocabulary already draws the distinction it
needs -- see `artifacts/policy.py`), so there is nothing for
`identity.bootstrap.seed_rbac` to pick up either.

`created_at`/`updated_at` carry `server_default` here, matching the ORM
model's own `server_default=func.now()` -- the exact invariant
`tests/test_identity_schema_invariants.py` pins after migration
`a1f5c9e3b7d2` shipped without it and made `semantic_findings`
unwritable against a real PostgreSQL database.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "p8t1u4v7w0x3"
down_revision: str | None = "m6q9r2s5t8u1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP_DEFAULT = sa.text("(CURRENT_TIMESTAMP)")
_JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "analysis_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_id", sa.Uuid(), nullable=True),
        sa.Column("ai_output_id", sa.Uuid(), nullable=True),
        sa.Column("artifact_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("database_id", sa.String(length=100), nullable=True),
        sa.Column("source_sql", sa.Text(), nullable=True),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("origin_row_count", sa.Integer(), nullable=True),
        sa.Column("origin_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_refresh_outcome",
            sa.String(length=20),
            nullable=False,
            server_default="never_refreshed",
        ),
        sa.Column("last_refresh_error", sa.Text(), nullable=True),
        sa.Column("last_refresh_row_count", sa.Integer(), nullable=True),
        sa.Column("last_refresh_fingerprint", sa.String(length=64), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", _JSON_TYPE, nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ai_output_id"], ["ai_outputs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_artifacts_tenant_id"), "analysis_artifacts", ["tenant_id"])
    op.create_index(
        op.f("ix_analysis_artifacts_owner_user_id"), "analysis_artifacts", ["owner_user_id"]
    )
    op.create_index(
        op.f("ix_analysis_artifacts_conversation_id"), "analysis_artifacts", ["conversation_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_analysis_artifacts_conversation_id"), table_name="analysis_artifacts")
    op.drop_index(op.f("ix_analysis_artifacts_owner_user_id"), table_name="analysis_artifacts")
    op.drop_index(op.f("ix_analysis_artifacts_tenant_id"), table_name="analysis_artifacts")
    op.drop_table("analysis_artifacts")
