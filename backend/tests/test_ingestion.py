from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.db.base import Base
from app.db.models import Crop, Region, Season, Topic
from app.db.session import get_db
from app.ingestion.service import ingest_document


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_ingest_document_creates_document_chunk_and_claims() -> None:
    SessionLocal = _session_factory()

    with SessionLocal() as db:
        crop = Crop(code="paddy", name="Paddy", aliases={"labels": {"en": "Paddy"}, "aliases": {"en": ["paddy"]}})
        season = Season(code="kharif", name="Kharif", aliases={"labels": {"en": "Kharif"}, "aliases": {"en": ["kharif"]}})
        region = Region(code="nalgonda", name="Nalgonda", level="district", aliases={"labels": {"en": "Nalgonda"}, "aliases": {"en": ["nalgonda"]}})
        topic = Topic(code="fertilizer_schedule", name="Fertilizer Schedule", aliases={"labels": {"en": "Fertilizer Schedule"}, "aliases": {"en": ["fertilizer"]}}, required_slots=["crop", "region", "season"])
        db.add_all([crop, season, region, topic])
        db.commit()

        document = ingest_document(
            db,
            {
                "title": "Paddy nutrient schedule",
                "publisher": "ICAR",
                "doc_type": "guideline",
                "source_url": "https://example.com/paddy",
                "language": "en",
                "publication_date": "2024-01-15",
                "valid_from": "2024-01-01",
                "valid_to": "2025-12-31",
                "version_label": "v2",
                "file_sha256": "abc123",
                "chunks": [
                    {
                        "ordinal": 1,
                        "text": "Apply 40 kg/ha nitrogen at 30-45 days after transplanting.",
                        "page_start": 4,
                        "page_end": 4,
                        "section_path": "4.1 Fertilizer",
                        "crop_ids": [crop.id],
                        "region_ids": [region.id],
                        "season_ids": [season.id],
                        "stage_ids": [],
                        "topic_code": "fertilizer_schedule",
                        "claims": [
                            {
                                "topic_code": "fertilizer_schedule",
                                "action_key": "apply",
                                "value_num": 40,
                                "value_unit": "kg/ha",
                                "timing_day_from": 30,
                                "timing_day_to": 45,
                                "source_span": "40 kg/ha",
                            }
                        ],
                    }
                ],
                "supersessions": [
                    {
                        "superseded_doc_title": "Older paddy nutrient schedule",
                        "scope_crop_code": "paddy",
                        "note": "Superseded by updated schedule",
                    }
                ],
            },
        )

        assert document.title == "Paddy nutrient schedule"
        assert document.chunks[0].text.startswith("Apply 40 kg/ha")
        assert document.chunks[0].claims[0].value_num == 40
        assert document.superseded_by is not None


def test_chunk_endpoint_returns_document_detail() -> None:
    SessionLocal = _session_factory()

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with SessionLocal() as db:
            crop = Crop(code="maize", name="Maize", aliases={"labels": {"en": "Maize"}, "aliases": {"en": ["maize"]}})
            topic = Topic(code="irrigation", name="Irrigation", aliases={"labels": {"en": "Irrigation"}, "aliases": {"en": ["irrigation"]}}, required_slots=["crop", "region"])
            db.add_all([crop, topic])
            db.commit()
            document = ingest_document(
                db,
                {
                    "title": "Maize irrigation note",
                    "publisher": "KVK",
                    "doc_type": "note",
                    "source_url": "https://example.com/maize",
                    "language": "en",
                    "publication_date": "2024-02-01",
                    "valid_from": "2024-01-01",
                    "valid_to": "2024-12-31",
                    "version_label": "v1",
                    "file_sha256": "def456",
                    "chunks": [
                        {
                            "ordinal": 1,
                            "text": "Irrigate every 7 days during flowering.",
                            "page_start": 7,
                            "page_end": 7,
                            "section_path": "7.2 Irrigation",
                            "crop_ids": [crop.id],
                            "region_ids": [],
                            "season_ids": [],
                            "stage_ids": [],
                            "topic_code": "irrigation",
                            "claims": [],
                        }
                    ],
                },
            )

        with SessionLocal() as db:
            persisted_document = db.get(type(document), document.id)
            chunk_id = persisted_document.chunks[0].id

        client = TestClient(app)
        response = client.get(f"/chunks/{chunk_id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["text"].startswith("Irrigate every 7 days")
        assert payload["document"]["title"] == "Maize irrigation note"
    finally:
        app.dependency_overrides.clear()
