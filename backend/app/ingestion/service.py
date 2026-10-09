from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Claim, Crop, Document, Region, Season, Supersession, Topic


class IngestionError(ValueError):
    """Raised when an ingestion manifest is invalid."""


def _as_date(value: Any, field_name: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise IngestionError(f"{field_name} must be a ISO date string") from exc
    raise IngestionError(f"{field_name} must be a date or ISO date string")


def _resolve_model_ids(
    db: Session,
    model: type[Crop | Region | Season],
    values: Sequence[int | str] | None,
    lookup_key: str,
) -> list[int]:
    if not values:
        return []

    resolved: list[int] = []
    for raw_value in values:
        if isinstance(raw_value, int):
            resolved.append(raw_value)
            continue
        if not isinstance(raw_value, str):
            raise IngestionError(f"{lookup_key} values must be ints or strings")
        row = db.scalar(select(model).where(getattr(model, lookup_key) == raw_value))
        if row is None:
            raise IngestionError(f"Unknown {model.__tablename__[:-1]} reference: {raw_value}")
        resolved.append(row.id)
    return resolved


def _resolve_topic(db: Session, code: str | None) -> Topic | None:
    if code is None:
        return None
    topic = db.scalar(select(Topic).where(Topic.code == code))
    if topic is None:
        raise IngestionError(f"Unknown topic code: {code}")
    return topic


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    required_fields = [
        "title",
        "publisher",
        "doc_type",
        "source_url",
        "language",
        "publication_date",
        "valid_from",
        "valid_to",
        "version_label",
        "file_sha256",
    ]
    for field in required_fields:
        if field not in manifest:
            raise IngestionError(f"Manifest field '{field}' is required")

    if manifest["valid_from"] and manifest["valid_to"]:
        valid_from = _as_date(manifest["valid_from"], "valid_from")
        valid_to = _as_date(manifest["valid_to"], "valid_to")
        if valid_to < valid_from:
            raise IngestionError("valid_to must be later than or equal to valid_from")

    if not isinstance(manifest.get("chunks"), list):
        raise IngestionError("The manifest requires a 'chunks' list")


def _ensure_document(
    db: Session,
    manifest: Mapping[str, Any],
    publication_date: date,
) -> Document:
    file_sha256 = manifest.get("file_sha256")
    existing = None if not file_sha256 else db.scalar(select(Document).where(Document.file_sha256 == file_sha256))
    if existing is not None:
        return existing

    document = Document(
        title=str(manifest["title"]),
        publisher=str(manifest.get("publisher") or ""),
        doc_type=str(manifest.get("doc_type") or "guideline"),
        source_url=str(manifest.get("source_url") or ""),
        language=str(manifest.get("language") or "en"),
        publication_date=publication_date,
        valid_from=_as_date(manifest["valid_from"], "valid_from") if manifest.get("valid_from") else None,
        valid_to=_as_date(manifest["valid_to"], "valid_to") if manifest.get("valid_to") else None,
        version_label=str(manifest.get("version_label") or ""),
        file_sha256=str(file_sha256) if file_sha256 else None,
    )
    db.add(document)
    db.flush()
    return document


def _create_document_links(db: Session, document: Document, manifest: Mapping[str, Any]) -> None:
    for item in manifest.get("supersessions") or []:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("superseded_doc_title"))
        superseded_doc = db.scalar(select(Document).where(Document.title == title))
        if superseded_doc is None:
            superseded_doc = Document(
                title=title,
                publisher="legacy-import",
                doc_type="legacy",
                source_url="",
                language="en",
                publication_date=datetime.now(tz=UTC).date(),
                valid_from=None,
                valid_to=None,
                version_label="legacy",
                file_sha256=None,
            )
            db.add(superseded_doc)
            db.flush()

        existing = db.scalar(
            select(Supersession).where(
                Supersession.superseding_doc_id == document.id,
                Supersession.superseded_doc_id == superseded_doc.id,
            )
        )
        if existing is not None:
            continue

        crop_code = item.get("scope_crop_code")
        crop = db.scalar(select(Crop).where(Crop.code == crop_code)) if crop_code else None
        record = Supersession(
            superseding_doc_id=document.id,
            superseded_doc_id=superseded_doc.id,
            scope_crop_id=crop.id if crop else None,
            note=str(item.get("note") or ""),
        )
        db.add(record)


def _chunk_from_manifest(db: Session, document: Document, chunk_payload: Mapping[str, Any]) -> Chunk:
    topic = _resolve_topic(db, str(chunk_payload.get("topic_code"))) if chunk_payload.get("topic_code") else None
    chunk = Chunk(
        document_id=document.id,
        ordinal=int(chunk_payload["ordinal"]),
        text=str(chunk_payload["text"]),
        page_start=int(chunk_payload.get("page_start", 1)),
        page_end=int(chunk_payload.get("page_end", 1)),
        section_path=str(chunk_payload.get("section_path") or ""),
        crop_ids=_resolve_model_ids(db, Crop, chunk_payload.get("crop_ids") or [], "code"),
        region_ids=_resolve_model_ids(db, Region, chunk_payload.get("region_ids") or [], "code"),
        season_ids=_resolve_model_ids(db, Season, chunk_payload.get("season_ids") or [], "code"),
        stage_ids=list(chunk_payload.get("stage_ids") or []),
        topic_id=topic.id if topic else None,
        tagging_status=str(chunk_payload.get("tagging_status") or "auto"),
        tagging_confidence=float(chunk_payload.get("tagging_confidence") or 1.0),
        embedding=list(chunk_payload.get("embedding") or []),
    )
    db.add(chunk)
    db.flush()

    for claim_payload in chunk_payload.get("claims") or []:
        if not isinstance(claim_payload, Mapping):
            continue
        claim_topic = _resolve_topic(db, str(claim_payload.get("topic_code"))) if claim_payload.get("topic_code") else topic
        claim = Claim(
            chunk_id=chunk.id,
            topic_id=claim_topic.id if claim_topic else None,
            action_key=str(claim_payload.get("action_key") or "unknown"),
            value_num=float(claim_payload["value_num"]) if claim_payload.get("value_num") is not None else None,
            value_unit=str(claim_payload.get("value_unit")) if claim_payload.get("value_unit") else None,
            timing_day_from=int(claim_payload["timing_day_from"]) if claim_payload.get("timing_day_from") is not None else None,
            timing_day_to=int(claim_payload["timing_day_to"]) if claim_payload.get("timing_day_to") is not None else None,
            timing_text=str(claim_payload.get("timing_text")) if claim_payload.get("timing_text") else None,
            source_span=str(claim_payload.get("source_span")) if claim_payload.get("source_span") else None,
            confidence=float(claim_payload.get("confidence") or 1.0),
        )
        db.add(claim)

    return chunk


def ingest_document(db: Session, manifest: Mapping[str, Any]) -> Document:
    _validate_manifest(manifest)
    publication_date = _as_date(manifest["publication_date"], "publication_date")
    document = _ensure_document(db, manifest, publication_date)
    document.title = str(manifest["title"])
    document.publisher = str(manifest.get("publisher") or "")
    document.doc_type = str(manifest.get("doc_type") or "guideline")
    document.source_url = str(manifest.get("source_url") or "")
    document.language = str(manifest.get("language") or "en")
    document.publication_date = publication_date
    document.valid_from = _as_date(manifest["valid_from"], "valid_from") if manifest.get("valid_from") else None
    document.valid_to = _as_date(manifest["valid_to"], "valid_to") if manifest.get("valid_to") else None
    document.version_label = str(manifest.get("version_label") or "")
    document.file_sha256 = str(manifest.get("file_sha256") or document.file_sha256)
    db.flush()

    existing_chunk_ids = {chunk.ordinal for chunk in document.chunks}
    for raw_chunk in manifest.get("chunks") or []:
        if not isinstance(raw_chunk, Mapping):
            continue
        if raw_chunk.get("ordinal") in existing_chunk_ids:
            continue
        _chunk_from_manifest(db, document, raw_chunk)

    _create_document_links(db, document, manifest)
    db.commit()
    db.refresh(document)
    return document
