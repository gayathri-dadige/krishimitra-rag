"""Create foundation taxonomy tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = postgresql.JSONB()
    op.create_table(
        "crops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("aliases", json_type, nullable=False),
    )
    op.create_table(
        "seasons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("aliases", json_type, nullable=False),
    )
    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("regions.id")),
        sa.Column("aliases", json_type, nullable=False),
        sa.CheckConstraint("level IN ('state', 'zone', 'district')", name="ck_regions_level"),
    )
    op.create_table(
        "growth_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("crop_id", sa.Integer(), sa.ForeignKey("crops.id"), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("day_from", sa.Integer(), nullable=False),
        sa.Column("day_to", sa.Integer(), nullable=False),
        sa.Column("reference_event", sa.String(30), nullable=False),
        sa.Column("aliases", json_type, nullable=False),
        sa.UniqueConstraint("crop_id", "code", name="uq_growth_stages_crop_code"),
        sa.CheckConstraint("day_from >= 0 AND day_to >= day_from", name="ck_growth_stages_days"),
        sa.CheckConstraint(
            "reference_event IN ('transplanting', 'sowing')",
            name="ck_growth_stages_reference_event",
        ),
    )
    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("aliases", json_type, nullable=False),
        sa.Column("required_slots", postgresql.ARRAY(sa.String()), nullable=False),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_overlapping_growth_stages()
        RETURNS trigger AS $fn$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM growth_stages
                WHERE crop_id = NEW.crop_id
                  AND reference_event = NEW.reference_event
                  AND id <> COALESCE(NEW.id, -1)
                  AND int4range(day_from, day_to, '[]') && int4range(NEW.day_from, NEW.day_to, '[]')
            ) THEN
                RAISE EXCEPTION 'overlapping growth stage range for crop % and event %', NEW.crop_id, NEW.reference_event;
            END IF;
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER growth_stages_no_overlap
        BEFORE INSERT OR UPDATE ON growth_stages
        FOR EACH ROW EXECUTE FUNCTION reject_overlapping_growth_stages();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS growth_stages_no_overlap ON growth_stages")
    op.execute("DROP FUNCTION IF EXISTS reject_overlapping_growth_stages()")
    op.drop_table("growth_stages")
    op.drop_table("topics")
    op.drop_table("regions")
    op.drop_table("seasons")
    op.drop_table("crops")
