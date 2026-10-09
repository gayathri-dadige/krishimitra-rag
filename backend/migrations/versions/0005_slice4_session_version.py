"""Add optimistic session context versioning."""

import sqlalchemy as sa
from alembic import op

revision = "0005_slice4_session_version"
down_revision = "0004_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("context_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "context_version")
