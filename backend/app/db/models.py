from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Crop(Base):
    __tablename__ = "crops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    growth_stages: Mapped[list[GrowthStage]] = relationship(back_populates="crop")


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parent: Mapped[Region | None] = relationship(remote_side="Region.id", back_populates="children")
    children: Mapped[list[Region]] = relationship(back_populates="parent")

    __table_args__ = (
        CheckConstraint("level IN ('state', 'zone', 'district')", name="ck_regions_level"),
    )


class GrowthStage(Base):
    __tablename__ = "growth_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crop_id: Mapped[int] = mapped_column(ForeignKey("crops.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    day_from: Mapped[int] = mapped_column(Integer, nullable=False)
    day_to: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_event: Mapped[str] = mapped_column(String(30), nullable=False)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    crop: Mapped[Crop] = relationship(back_populates="growth_stages")

    __table_args__ = (
        UniqueConstraint("crop_id", "code", name="uq_growth_stages_crop_code"),
        CheckConstraint("day_from >= 0 AND day_to >= day_from", name="ck_growth_stages_days"),
        CheckConstraint(
            "reference_event IN ('transplanting', 'sowing')",
            name="ck_growth_stages_reference_event",
        ),
    )


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    required_slots: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    clarification_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_topic_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    response_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    session: Mapped[ChatSession] = relationship(back_populates="messages")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    publication_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    version_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document")
    superseded_by: Mapped[list[Supersession]] = relationship(
        back_populates="superseding_document",
        foreign_keys="Supersession.superseding_doc_id",
        cascade="all, delete-orphan",
    )
    superseded_documents: Mapped[list[Supersession]] = relationship(
        back_populates="superseded_document",
        foreign_keys="Supersession.superseded_doc_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "(valid_to IS NULL) OR (valid_from IS NULL) OR (valid_to >= valid_from)",
            name="ck_documents_validity",
        ),
    )


class Supersession(Base):
    __tablename__ = "supersessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    superseding_doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    superseded_doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    scope_crop_id: Mapped[int | None] = mapped_column(ForeignKey("crops.id"), nullable=True)
    scope_region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    scope_topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    superseding_document: Mapped[Document] = relationship(
        back_populates="superseded_by",
        foreign_keys=[superseding_doc_id],
        primaryjoin="Supersession.superseding_doc_id == Document.id",
    )
    superseded_document: Mapped[Document] = relationship(
        back_populates="superseded_documents",
        foreign_keys=[superseded_doc_id],
        primaryjoin="Supersession.superseded_doc_id == Document.id",
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    crop_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    region_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    season_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    stage_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    tagging_status: Mapped[str] = mapped_column(String(30), nullable=False, default="auto")
    tagging_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    tsv: Mapped[str | None] = mapped_column(String, nullable=True)
    document: Mapped[Document] = relationship(back_populates="chunks")
    claims: Mapped[list[Claim]] = relationship(back_populates="chunk", cascade="all, delete-orphan")
    topic: Mapped[Topic | None] = relationship(foreign_keys=[topic_id])

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        CheckConstraint("page_start >= 1", name="ck_chunks_page_start"),
        CheckConstraint("page_end >= page_start", name="ck_chunks_page_end"),
    )


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id"), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    action_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_unit: Mapped[str | None] = mapped_column(String(60), nullable=True)
    timing_day_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timing_day_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timing_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_span: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    chunk: Mapped[Chunk] = relationship(back_populates="claims")
    topic: Mapped[Topic | None] = relationship(foreign_keys=[topic_id])
