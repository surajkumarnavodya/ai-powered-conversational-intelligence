"""Enterprise Workflow Automation: workflows, workflow_events

Revision ID: k5n8p1q4r7s0
Revises: j4k7l0m3n6p9
Create Date: 2026-10-09 00:00:00.000000

Adds `identity/models.py::Workflow`/`WorkflowEvent` -- Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`). See `Workflow`'s own
docstring for the design rationale (turns an already-accepted
recommendation into a governed proposed -> approval_required ->
approved -> assigned -> in_progress -> completed/rejected/expired
workflow; owners/due dates/external task references/outcome measurement
are all plain caller-supplied metadata, never a value this feature
executes). New RBAC permission codes (`workflow.review`/`workflow.manage`)
need no migration of their own -- `identity.bootstrap.seed_rbac` is
idempotent and picks up `identity/rbac.py`'s updated `SEED_PERMISSIONS`/
`SEED_ROLES` automatically on the next app startup, the same as every
prior permission addition to this table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "k5n8p1q4r7s0"
down_revision: str | None = "j4k7l0m3n6p9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP_DEFAULT = sa.text("(CURRENT_TIMESTAMP)")
_JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_system", sa.String(length=100), nullable=True),
        sa.Column("external_reference_id", sa.String(length=200), nullable=True),
        sa.Column("external_reference_url", sa.String(length=2048), nullable=True),
        sa.Column("outcome_summary", sa.Text(), nullable=True),
        sa.Column("outcome_measured_impact", _JSON_TYPE, nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
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
            ["recommendation_id"], ["recommendation_records.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflows_tenant_id"), "workflows", ["tenant_id"])
    op.create_index(op.f("ix_workflows_recommendation_id"), "workflows", ["recommendation_id"])
    op.create_index(op.f("ix_workflows_status"), "workflows", ["status"])
    op.create_index(op.f("ix_workflows_owner_user_id"), "workflows", ["owner_user_id"])
    op.create_index(op.f("ix_workflows_created_by_user_id"), "workflows", ["created_by_user_id"])

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column(
            "event_type", sa.String(length=30), nullable=False, server_default="status_change"
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(length=200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("detail", _JSON_TYPE, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_events_workflow_id"), "workflow_events", ["workflow_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_events_workflow_id"), table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_index(op.f("ix_workflows_created_by_user_id"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_owner_user_id"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_status"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_recommendation_id"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_tenant_id"), table_name="workflows")
    op.drop_table("workflows")
