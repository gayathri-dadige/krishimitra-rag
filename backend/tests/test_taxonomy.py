from pathlib import Path

import pytest
import yaml

from app.db.seed import validate_taxonomy
from app.taxonomy.service import resolve_alias, stage_from_ranges


def taxonomy_data() -> dict:
    path = Path(__file__).resolve().parents[2] / "data" / "taxonomy" / "taxonomy.yml"
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def test_seed_taxonomy_has_mvp_entities() -> None:
    data = taxonomy_data()
    assert {item["code"] for item in data["crops"]} == {"paddy", "cotton", "maize"}
    assert len(data["seasons"]) == 3
    assert {item["level"] for item in data["regions"]} == {"state", "zone", "district"}
    assert len(data["topics"]) == 4


def test_invalid_overlapping_stage_ranges_are_rejected() -> None:
    data = taxonomy_data()
    data["crops"][0]["stages"][1]["day_from"] = 20
    with pytest.raises(ValueError, match="overlapping stages"):
        validate_taxonomy(data)


def test_invalid_region_parent_is_rejected() -> None:
    data = taxonomy_data()
    data["regions"][1]["parent"] = "missing"
    with pytest.raises(ValueError, match="unknown parent"):
        validate_taxonomy(data)


def test_alias_and_day_range_resolution() -> None:
    data = taxonomy_data()
    crop_data = data["crops"][0]
    crop = type("CropValue", (), {
        "code": crop_data["code"],
        "name": crop_data["name"],
        "aliases": {"aliases": crop_data["aliases"]},
    })()
    assert resolve_alias([crop], "వరి", "te") is crop
    stages = [
        type("StageValue", (), stage_data)()
        for stage_data in crop_data["stages"]
    ]
    match = stage_from_ranges(stages, 35, "transplanting")
    assert match is not None
    assert match.code == "tillering"
    assert stage_from_ranges(stages, 500, "transplanting") is None
