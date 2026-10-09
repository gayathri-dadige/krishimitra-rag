from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Crop, GrowthStage, Region, Season, Topic


@dataclass(frozen=True)
class StageMatch:
    code: str
    name: str
    reference_event: str


def _localized(value: str, aliases: dict[str, Any], language: str) -> str:
    labels = aliases.get("labels", {})
    return str(labels.get(language) or labels.get("en") or value)


def resolve_alias(items: list[Any], query: str, language: str = "en") -> Any | None:
    normalized = query.strip().casefold()
    for item in items:
        if normalized == item.code.casefold() or normalized == item.name.casefold():
            return item
        aliases = item.aliases.get("aliases", {})
        candidates = aliases.get(language, []) + aliases.get("en", [])
        if any(normalized == str(alias).casefold() for alias in candidates):
            return item
    return None


def stage_from_days(
    db: Session, crop_code: str, days: int, reference_event: str
) -> StageMatch | None:
    stages = db.scalars(
        select(GrowthStage)
        .join(GrowthStage.crop)
        .where(Crop.code == crop_code, GrowthStage.reference_event == reference_event)
        .order_by(GrowthStage.ordinal)
    ).all()
    return stage_from_ranges(stages, days, reference_event)


def stage_from_ranges(stages: list[GrowthStage], days: int, reference_event: str) -> StageMatch | None:
    for stage in stages:
        if stage.reference_event == reference_event and stage.day_from <= days <= stage.day_to:
            return StageMatch(stage.code, stage.name, stage.reference_event)
    return None


def taxonomy_payload(db: Session, language: str) -> dict[str, Any]:
    crops = db.scalars(select(Crop).options(joinedload(Crop.growth_stages))).unique().all()
    seasons = db.scalars(select(Season).order_by(Season.id)).all()
    regions = db.scalars(select(Region).order_by(Region.level, Region.name)).all()
    topics = db.scalars(select(Topic).order_by(Topic.id)).all()
    return {
        "language": language,
        "crops": [
            {
                "code": crop.code,
                "label": _localized(crop.name, crop.aliases, language),
                "stages": [
                    {
                        "code": stage.code,
                        "label": _localized(stage.name, stage.aliases, language),
                        "day_from": stage.day_from,
                        "day_to": stage.day_to,
                        "reference_event": stage.reference_event,
                    }
                    for stage in sorted(crop.growth_stages, key=lambda item: item.ordinal)
                ],
            }
            for crop in crops
        ],
        "seasons": [
            {"code": season.code, "label": _localized(season.name, season.aliases, language)}
            for season in seasons
        ],
        "regions": [
            {
                "code": region.code,
                "label": _localized(region.name, region.aliases, language),
                "level": region.level,
                "parent_code": next(
                    (parent.code for parent in regions if parent.id == region.parent_id), None
                ),
            }
            for region in regions
        ],
        "topics": [
            {
                "code": topic.code,
                "label": _localized(topic.name, topic.aliases, language),
                "required_slots": topic.required_slots,
            }
            for topic in topics
        ],
    }
