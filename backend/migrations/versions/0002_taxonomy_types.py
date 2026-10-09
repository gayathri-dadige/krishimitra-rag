"""Align taxonomy JSON and array columns with the data model."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_taxonomy_types"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "crops",
        "aliases",
        existing_nullable=False,
        type_=postgresql.JSONB(),
        postgresql_using="aliases::jsonb",
    )
    op.alter_column(
        "seasons",
        "aliases",
        existing_nullable=False,
        type_=postgresql.JSONB(),
        postgresql_using="aliases::jsonb",
    )
    op.alter_column(
        "regions",
        "aliases",
        existing_nullable=False,
        type_=postgresql.JSONB(),
        postgresql_using="aliases::jsonb",
    )
    op.alter_column(
        "growth_stages",
        "aliases",
        existing_nullable=False,
        type_=postgresql.JSONB(),
        postgresql_using="aliases::jsonb",
    )
    op.alter_column(
        "topics",
        "aliases",
        existing_nullable=False,
        type_=postgresql.JSONB(),
        postgresql_using="aliases::jsonb",
    )
    op.add_column("topics", sa.Column("required_slots_new", postgresql.ARRAY(postgresql.TEXT())))
    op.execute(
        "UPDATE topics SET required_slots_new = ARRAY(SELECT jsonb_array_elements_text(required_slots::jsonb))"
    )
    op.drop_column("topics", "required_slots")
    op.alter_column("topics", "required_slots_new", new_column_name="required_slots", nullable=False)


def downgrade() -> None:
    op.add_column("topics", sa.Column("required_slots_old", postgresql.JSON()))
    op.execute("UPDATE topics SET required_slots_old = to_json(required_slots)")
    op.drop_column("topics", "required_slots")
    op.alter_column("topics", "required_slots_old", new_column_name="required_slots", nullable=False)
    for table in ("topics", "growth_stages", "regions", "seasons", "crops"):
        op.alter_column(
            table,
            "aliases",
            existing_nullable=False,
            type_=postgresql.JSON(),
            postgresql_using="aliases::json",
        )