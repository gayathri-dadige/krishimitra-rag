from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.db.models import (
    ChatMessage,
    ChatSession,
    Chunk,
    Crop,
    GrowthStage,
    Region,
    Season,
    Supersession,
    Topic,
)
from app.llm.client import get_llm_client

_CONTEXT_KEYS = ("crop", "region", "season", "stage", "topic")
_SLOT_ALIASES = {"growth_stage": "stage", "days_after_event": "stage"}
_CLARIFICATION_ORDER = ("crop", "region", "season", "stage", "topic")
_DAY_PATTERN = re.compile(
    r"\b(?P<days>\d{1,3})\s+days?\s+after\s+(?P<event>transplanting|sowing)\b",
    re.IGNORECASE,
)


def _normalize_token(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _match_taxonomy_item(items: list[Any], value: str) -> Any | None:
    normalized = _normalize_token(value)
    for item in items:
        candidates = {item.code.casefold(), item.name.casefold()}
        aliases = getattr(item, "aliases", {}) or {}
        alias_values = aliases.get("aliases", {}) if isinstance(aliases, dict) else {}
        for language_aliases in alias_values.values():
            if isinstance(language_aliases, list):
                candidates.update(_normalize_token(str(alias)) for alias in language_aliases)
        if normalized in candidates:
            return item
    return None


def _extract_context(message: str) -> dict[str, str]:
    context: dict[str, str] = {}
    for part in re.split(r"[|;\n]+", message):
        if not part.strip():
            continue
        if "=" in part:
            left, right = part.split("=", 1)
        elif ":" in part:
            left, right = part.split(":", 1)
        else:
            continue
        key = _SLOT_ALIASES.get(left.strip().casefold(), left.strip().casefold())
        if key in _CONTEXT_KEYS:
            context[key] = right.strip()
    return context


def _extract_natural_context(
    db: Session, message: str, base_context: dict[str, str] | None = None
) -> dict[str, str]:
    """Resolve taxonomy aliases appearing in ordinary English sentences.

    This deterministic extractor is the offline-safe implementation. A live LLM
    can be placed in front of it later, but its output must still pass this resolver.
    """
    context = {**(base_context or {}), **_extract_context(message)}
    groups = (
        ("crop", db.scalars(select(Crop)).all()),
        ("region", db.scalars(select(Region)).all()),
        ("season", db.scalars(select(Season)).all()),
        ("topic", db.scalars(select(Topic)).all()),
    )
    normalized_message = _normalize_token(message)
    for key, items in groups:
        for item in items:
            aliases = [item.code, item.name]
            raw_aliases = (item.aliases or {}).get("aliases", {})
            for values in raw_aliases.values():
                aliases.extend(str(value) for value in values)
            if any(_normalize_token(alias) in normalized_message for alias in aliases):
                context.setdefault(key, item.code)
                break

    day_match = _DAY_PATTERN.search(message)
    if day_match and "crop" in context:
        stages = db.scalars(
            select(GrowthStage)
            .join(GrowthStage.crop)
            .where(
                Crop.code == context["crop"],
                GrowthStage.reference_event == day_match.group("event").casefold(),
            )
            .order_by(GrowthStage.ordinal)
        ).all()
        days = int(day_match.group("days"))
        stage = next((item for item in stages if item.day_from <= days <= item.day_to), None)
        if stage is not None:
            context["stage"] = stage.code
    if "stage" not in context and "crop" in context:
        stages = db.scalars(
            select(GrowthStage)
            .join(GrowthStage.crop)
            .where(Crop.code == context["crop"])
            .order_by(GrowthStage.ordinal)
        ).all()
        for stage in stages:
            aliases = [stage.code, stage.name]
            raw_aliases = (stage.aliases or {}).get("aliases", {})
            for values in raw_aliases.values():
                aliases.extend(str(value) for value in values)
            if any(_normalize_token(alias) in normalized_message for alias in aliases):
                context["stage"] = stage.code
                break
    return context


def _resolve_context(
    db: Session, payload: dict[str, str]
) -> tuple[Crop | None, Region | None, Season | None, GrowthStage | None, Topic | None]:
    crop = _match_taxonomy_item(db.scalars(select(Crop)).all(), payload["crop"]) if payload.get("crop") else None
    region = _match_taxonomy_item(db.scalars(select(Region)).all(), payload["region"]) if payload.get("region") else None
    season = _match_taxonomy_item(db.scalars(select(Season)).all(), payload["season"]) if payload.get("season") else None
    topic = _match_taxonomy_item(db.scalars(select(Topic)).all(), payload["topic"]) if payload.get("topic") else None
    stage = None
    if payload.get("stage"):
        stages = db.scalars(select(GrowthStage).order_by(GrowthStage.ordinal)).all()
        stage = _match_taxonomy_item(stages, payload["stage"])
    return crop, region, season, stage, topic


def _new_session(db: Session, session_id: str) -> ChatSession:
    try:
        UUID(session_id)
    except ValueError as exc:
        raise ValueError("session_id must be a UUID") from exc
    session = ChatSession(id=session_id, context={})
    db.add(session)
    db.flush()
    return session


def _is_expired(session: ChatSession, now: datetime) -> bool:
    updated_at = session.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at < now - timedelta(days=get_settings().retention_days)


def _get_session(db: Session, session_id: str | None) -> ChatSession:
    if session_id is None:
        return _new_session(db, str(uuid4()))
    try:
        UUID(session_id)
    except ValueError as exc:
        raise ValueError("session_id must be a UUID") from exc
    session = db.get(ChatSession, session_id)
    if session is not None and _is_expired(session, datetime.now(UTC)):
        db.delete(session)
        db.commit()
        return _new_session(db, session_id)
    return session or _new_session(db, session_id)


def purge_expired_sessions(db: Session, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=get_settings().retention_days)
    sessions = db.scalars(select(ChatSession)).all()
    expired = []
    for session in sessions:
        updated_at = session.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if updated_at < cutoff:
            expired.append(session)
    for session in expired:
        db.delete(session)
    db.commit()
    return len(expired)


def _tag_matches(ids: list[int] | None, value_id: int | None) -> bool:
    return value_id is None or not ids or value_id in ids


def _matches_chunk(
    chunk: Chunk,
    crop: Crop | None,
    region: Region | None,
    season: Season | None,
    stage: GrowthStage | None,
    topic: Topic | None,
) -> bool:
    return (
        _tag_matches(chunk.crop_ids, crop.id if crop else None)
        and _tag_matches(chunk.region_ids, region.id if region else None)
        and _tag_matches(chunk.season_ids, season.id if season else None)
        and _tag_matches(chunk.stage_ids, stage.id if stage else None)
        and (topic is None or chunk.topic_id in (None, topic.id))
    )


def _is_superseded(
    db: Session,
    document_id: int,
    as_of: date,
    crop: Crop | None,
    region: Region | None,
    topic: Topic | None,
) -> bool:
    links = db.scalars(
        select(Supersession)
        .options(joinedload(Supersession.superseding_document))
        .where(Supersession.superseded_doc_id == document_id)
    ).all()
    for link in links:
        superseding = link.superseding_document
        if superseding.valid_from and superseding.valid_from > as_of:
            continue
        if superseding.valid_to and superseding.valid_to < as_of:
            continue
        if link.scope_crop_id not in (None, crop.id if crop else None):
            continue
        if link.scope_region_id not in (None, region.id if region else None):
            continue
        if link.scope_topic_id not in (None, topic.id if topic else None):
            continue
        return True
    return False


def _required_slots(topic: Topic | None) -> list[str]:
    required = ["crop", "region"]
    if topic is None:
        required.append("topic")
    else:
        for slot in topic.required_slots:
            normalized = _SLOT_ALIASES.get(slot, slot)
            if normalized not in required:
                required.append(normalized)
    return [slot for slot in _CLARIFICATION_ORDER if slot in required]


def _clarification_options(db: Session, slot: str, crop: Crop | None) -> list[dict[str, str]]:
    if slot == "crop":
        items = db.scalars(select(Crop).order_by(Crop.id)).all()
    elif slot == "region":
        items = db.scalars(select(Region).order_by(Region.name)).all()
    elif slot == "season":
        items = db.scalars(select(Season).order_by(Season.id)).all()
    elif slot == "topic":
        items = db.scalars(select(Topic).order_by(Topic.id)).all()
    elif slot == "stage" and crop is not None:
        items = db.scalars(
            select(GrowthStage).where(GrowthStage.crop_id == crop.id).order_by(GrowthStage.ordinal)
        ).all()
    else:
        items = []
    return [{"value": item.code, "label": item.name} for item in items]


def _as_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now(UTC).date()


def _citation(chunk: Chunk) -> dict[str, Any]:
    document = chunk.document
    return {
        "chunk_id": chunk.id,
        "document_title": document.title if document else "Unknown document",
        "publisher": document.publisher if document else None,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section": chunk.section_path,
        "section_path": chunk.section_path,
        "publication_date": document.publication_date.isoformat() if document else None,
        "valid_from": document.valid_from.isoformat() if document and document.valid_from else None,
        "valid_to": document.valid_to.isoformat() if document and document.valid_to else None,
        "version_label": document.version_label if document else None,
    }


def _lexical_score(message: str, chunk: Chunk) -> float:
    query_terms = set(re.findall(r"[a-z0-9]+", message.casefold()))
    text_terms = set(re.findall(r"[a-z0-9]+", chunk.text.casefold()))
    return float(len(query_terms & text_terms)) / max(len(query_terms), 1)


def answer_question(
    db: Session, message: str, as_of_date: str | None, session_id: str | None
) -> dict[str, object]:
    session = _get_session(db, session_id)
    incoming = _extract_natural_context(db, message, session.context or {})
    missing_before_llm = {"crop", "region", "season", "stage", "topic"} - incoming.keys()
    if missing_before_llm:
        llm = get_llm_client(get_settings())
        if llm is not None:
            incoming = {**incoming, **llm.extract_context(message)}
    merged = {**(session.context or {}), **incoming}
    crop, region, season, stage, topic = _resolve_context(db, merged)
    previous_context = session.context or {}
    for key, resolved in (
        ("crop", crop),
        ("region", region),
        ("season", season),
        ("stage", stage),
        ("topic", topic),
    ):
        if key in merged and resolved is None:
            if key in previous_context:
                merged[key] = previous_context[key]
            else:
                merged.pop(key)
    crop, region, season, stage, topic = _resolve_context(db, merged)
    if crop:
        merged["crop"] = crop.code
    if region:
        merged["region"] = region.code
    if season:
        merged["season"] = season.code
    if stage:
        merged["stage"] = stage.code
    if topic:
        merged["topic"] = topic.code
        session.last_topic_code = topic.code

    missing = [
        slot for slot in _required_slots(topic)
        if {"crop": crop, "region": region, "season": season, "stage": stage, "topic": topic}[slot] is None
    ]
    db.add(ChatMessage(session_id=session.id, role="user", content=message))
    session.context = merged
    session.context_version += 1
    session.clarification_rounds += 1 if missing else 0
    if missing and session.clarification_rounds > 3:
        response: dict[str, object] = {
            "session_id": session.id,
            "type": "abstain",
            "language": "en",
            "message": "I could not complete the question after three clarification rounds.",
            "abstain_reason": "too_many_clarifications",
            "citations": [],
            "trace_id": str(uuid4()),
        }
    elif missing:
        slot = missing[0]
        response = {
            "session_id": session.id,
            "type": "clarify",
            "response_type": "clarification",
            "language": "en",
            "message": f"Which {slot.replace('_', ' ')} should I use?",
            "context": merged,
            "clarification": {
                "slot": slot,
                "options": _clarification_options(db, slot, crop),
            },
            "citations": [],
            "trace_id": str(uuid4()),
        }
    else:
        session.clarification_rounds = 0
        as_of = _as_date(as_of_date)
        chunks = db.scalars(
            select(Chunk)
            .options(joinedload(Chunk.document), joinedload(Chunk.claims))
            .where(Chunk.document_id.isnot(None))
        ).unique().all()
        matches: list[tuple[float, Chunk]] = []
        for chunk in chunks:
            document = chunk.document
            if document is None or (document.valid_from and document.valid_from > as_of) or (
                document.valid_to and document.valid_to < as_of
            ):
                continue
            if _is_superseded(db, document.id, as_of, crop, region, topic):
                continue
            if _matches_chunk(chunk, crop, region, season, stage, topic):
                matches.append((_lexical_score(message, chunk) + len(chunk.claims) * 0.01, chunk))
        matches.sort(key=lambda item: (item[0], item[1].id), reverse=True)
        if not matches:
            response = {
                "session_id": session.id,
                "type": "abstain",
                "language": "en",
                "message": "I could not find reliable evidence for this context.",
                "abstain_reason": "no_evidence",
                "citations": [],
                "trace_id": str(uuid4()),
            }
        else:
            evidence = [chunk for _, chunk in matches[:8]]
            best = evidence[0]
            citations = [_citation(chunk) for chunk in evidence[:3]]
            answer = (
                f"For {crop.name} in {region.name}, the most relevant guidance is: {best.text}."
            )
            response = {
                "session_id": session.id,
                "type": "answer",
                "language": "en",
                "message": answer,
                "answer": answer,
                "statements": [{"text": answer, "chunk_ids": [best.id]}],
                "citations": citations,
                "verified": best.id == citations[0]["chunk_id"],
                "trace_id": str(uuid4()),
            }
    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            content=str(response.get("message", "")),
            response_type=str(response["type"]),
        )
    )
    db.commit()
    return response
