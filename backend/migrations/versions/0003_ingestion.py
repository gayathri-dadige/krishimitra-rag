"""Add document, chunk, claim and supersession ingestion tables."""

import sqlalchemy as sa
from alembic import op

revision = "0003_ingestion"
down_revision = "0002_taxonomy_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=True),
        sa.Column("doc_type", sa.String(length=80), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("version_label", sa.String(length=80), nullable=True),
        sa.Column("file_sha256", sa.String(length=128), nullable=True, unique=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("(valid_to IS NULL) OR (valid_from IS NULL) OR (valid_to >= valid_from)", name="ck_documents_validity"),
    )
    op.create_table(
        "supersessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("superseding_doc_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("superseded_doc_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("scope_crop_id", sa.Integer(), sa.ForeignKey("crops.id"), nullable=True),
        sa.Column("scope_region_id", sa.Integer(), sa.ForeignKey("regions.id"), nullable=True),
        sa.Column("scope_topic_id", sa.Integer(), sa.ForeignKey("topics.id"), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("section_path", sa.String(length=255), nullable=True),
        sa.Column("crop_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("region_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("season_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("stage_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("topics.id"), nullable=True),
        sa.Column("tagging_status", sa.String(length=30), nullable=False, server_default="auto"),
        sa.Column("tagging_confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("tsv", sa.String(), nullable=True),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        sa.CheckConstraint("page_start >= 1", name="ck_chunks_page_start"),
        sa.CheckConstraint("page_end >= page_start", name="ck_chunks_page_end"),
    )
    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("chunks.id"), nullable=False),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("topics.id"), nullable=True),
        sa.Column("action_key", sa.String(length=80), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_unit", sa.String(length=60), nullable=True),
        sa.Column("timing_day_from", sa.Integer(), nullable=True),
        sa.Column("timing_day_to", sa.Integer(), nullable=True),
        sa.Column("timing_text", sa.String(length=255), nullable=True),
        sa.Column("source_span", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
    )


def downgrade() -> None:
    op.drop_table("claims")
    op.drop_table("chunks")
    op.drop_table("supersessions")
    op.drop_table("documents")
