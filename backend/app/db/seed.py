import os
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Crop, GrowthStage, Region, Season, Topic


def _localized(value: dict[str, Any]) -> dict[str, Any]:
    return {"labels": value["labels"], "aliases": value["aliases"]}


def validate_taxonomy(data: dict[str, Any]) -> None:
    for crop in data["crops"]:
        for event in {stage["reference_event"] for stage in crop["stages"]}:
            stages = sorted(
                (stage for stage in crop["stages"] if stage["reference_event"] == event),
                key=lambda stage: stage["day_from"],
            )
            for previous, current in pairwise(stages):
                if current["day_from"] <= previous["day_to"]:
                    raise ValueError(f"overlapping stages for {crop['code']} ({event})")
    region_codes = {region["code"] for region in data["regions"]}
    for region in data["regions"]:
        if region["parent"] is not None and region["parent"] not in region_codes:
            raise ValueError(f"unknown parent region: {region['parent']}")


def seed_taxonomy(db: Session, path: Path | None = None) -> None:
    seed_path = path or Path(
        os.getenv(
            "TAXONOMY_PATH",
            str(Path(__file__).resolve().parents[3] / "data" / "taxonomy" / "taxonomy.yml"),
        )
    )
    with seed_path.open(encoding="utf-8") as file:
        data: dict[str, Any] = yaml.safe_load(file)
    validate_taxonomy(data)

    crops: dict[str, Crop] = {}
    for item in data["crops"]:
        crop = db.scalar(select(Crop).where(Crop.code == item["code"])) or Crop(code=item["code"])
        crop.name = item["name"]
        crop.aliases = _localized(item)
        db.add(crop)
        db.flush()
        crops[crop.code] = crop
        for stage_data in item["stages"]:
            stage = db.scalar(
                select(GrowthStage).where(
                    GrowthStage.crop_id == crop.id, GrowthStage.code == stage_data["code"]
                )
            ) or GrowthStage(crop_id=crop.id, code=stage_data["code"])
            for key in ("name", "ordinal", "day_from", "day_to", "reference_event"):
                setattr(stage, key, stage_data[key])
            stage.aliases = _localized(stage_data)
            db.add(stage)

    for item in data["seasons"]:
        season = db.scalar(select(Season).where(Season.code == item["code"])) or Season(code=item["code"])
        season.name = item["name"]
        season.aliases = _localized(item)
        db.add(season)

    regions: dict[str, Region] = {}
    for item in data["regions"]:
        region = db.scalar(select(Region).where(Region.code == item["code"])) or Region(code=item["code"])
        region.name = item["name"]
        region.level = item["level"]
        region.aliases = _localized(item)
        db.add(region)
        db.flush()
        regions[region.code] = region
    for item in data["regions"]:
        regions[item["code"]].parent_id = regions[item["parent"]].id if item["parent"] else None

    for item in data["topics"]:
        topic = db.scalar(select(Topic).where(Topic.code == item["code"])) or Topic(code=item["code"])
        topic.name = item["name"]
        topic.aliases = _localized(item)
        topic.required_slots = item["required_slots"]
        db.add(topic)
    db.commit()


if __name__ == "__main__":
    from app.db.session import SessionLocal

    seed_taxonomy(SessionLocal())
