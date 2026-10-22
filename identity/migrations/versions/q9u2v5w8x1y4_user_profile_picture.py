"""User profile picture: users.avatar_image / avatar_media_type / avatar_updated_at

Revision ID: q9u2v5w8x1y4
Revises: p8t1u4v7w0x3
Create Date: 2026-10-22 00:00:00.000000

Adds the three columns behind `identity/models.py::User`'s profile
picture. See that model's own comments for why the image lives on the
account row rather than on disk.

**All three are nullable with no server default, deliberately.** An
account without a picture is the normal state, not a missing value: the
frontend falls back to the default icon on NULL (`ChatMessage.tsx`), and
`UserOut.avatar_url` is None. A default here would have to invent a
picture, which is the opposite of what NULL means.

`avatar_updated_at` is **not** one of the ORM-filled timestamps
`tests/test_identity_schema_invariants.py` requires a `server_default`
for. That invariant covers `created_at`/`updated_at` columns on
`create_table`, which the ORM omits from every INSERT and the database
must therefore fill. This column is set by the application at the exact
moment a picture is stored (`identity.repositories.users
.set_profile_picture`) and is NULL at every other time, so a database
default would be actively wrong rather than merely absent -- it would
claim a picture had been set.

Reversible in full: `downgrade` drops the three columns, which discards
stored pictures. That is the correct behaviour for this feature (the
pictures are a derived, re-uploadable convenience, not a record of
anything) and is stated here rather than left to be discovered.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q9u2v5w8x1y4"
down_revision: str | None = "p8t1u4v7w0x3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_image", sa.LargeBinary(), nullable=True))
    op.add_column("users", sa.Column("avatar_media_type", sa.String(length=32), nullable=True))
    op.add_column(
        "users",
        sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "avatar_media_type")
    op.drop_column("users", "avatar_image")
