"""AI Evaluation Benchmarking platform: evaluation_runs, evaluation_case_reviews

Revision ID: h2j5k8l1m4n7
Revises: c7d2e5f8a1b4
Create Date: 2026-10-09 00:00:00.000000

Adds `identity/models.py::EvaluationRun`/`EvaluationCaseReview` --
Prompt 46 (`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`). See those
models' own docstrings for the design rationale (one persisted row per
evaluation run, an independent append-only human-review table, scoped
`tenant_id`, a deliberately loose string-only link to a governed asset
version rather than a foreign key onto `governance_asset_versions`).
New RBAC permission codes (`eval.manage`/`eval.review`) need no
migration of their own -- `identity.bootstrap.seed_rbac` is idempotent
and picks up `identity/rbac.py`'s updated `SEED_PERMISSIONS`/
`SEED_ROLES` automatically on the next app startup, the same as every
prior permission addition to this table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "h2j5k8l1m4n7"
down_revision: str | None = "c7d2e5f8a1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP_DEFAULT = sa.text("(CURRENT_TIMESTAMP)")
_JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("dataset_version", sa.String(length=128), nullable=False),
        sa.Column("governed_asset_type", sa.String(length=32), nullable=True),
        sa.Column("governed_asset_key", sa.String(length=200), nullable=True),
        sa.Column("governed_asset_version", sa.Integer(), nullable=True),
        sa.Column("summary", _JSON_TYPE, nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=True),
        sa.Column("total_passed", sa.Integer(), nullable=True),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.Column("cost_estimate", sa.Float(), nullable=True),
        sa.Column("release_gate_verdict", sa.String(length=16), nullable=True),
        sa.Column("release_gate_reasons", _JSON_TYPE, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("triggered_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evaluation_runs_tenant_id"), "evaluation_runs", ["tenant_id"])
    op.create_index(op.f("ix_evaluation_runs_source"), "evaluation_runs", ["source"])

    op.create_table(
        "evaluation_case_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("case_name", sa.String(length=300), nullable=False),
        sa.Column("original_verdict", sa.Boolean(), nullable=False),
        sa.Column("reviewed_verdict", sa.Boolean(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_evaluation_case_reviews_tenant_id"), "evaluation_case_reviews", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_evaluation_case_reviews_tenant_id"), table_name="evaluation_case_reviews"
    )
    op.drop_table("evaluation_case_reviews")
    op.drop_index(op.f("ix_evaluation_runs_source"), table_name="evaluation_runs")
    op.drop_index(op.f("ix_evaluation_runs_tenant_id"), table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
