from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.db.base import Base
from app.db.models import Chunk, Claim, Crop, Document, GrowthStage, Region, Season, Topic
from app.db.session import get_db


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_healthz_is_live() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"


def test_taxonomy_requires_supported_language() -> None:
    response = TestClient(app).get("/api/v1/taxonomy?lang=fr")
    assert response.status_code == 422


def test_documented_taxonomy_route_returns_localized_options() -> None:
    response = TestClient(app).get("/taxonomy?lang=te")
    assert response.status_code == 200
    assert response.json()["language"] == "te"
    assert response.json()["crops"][0]["label"] == "వరి"


def test_chat_returns_grounded_answer_for_given_context() -> None:
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
            crop = Crop(code="paddy", name="Paddy", aliases={"labels": {"en": "Paddy"}, "aliases": {"en": ["paddy"]}})
            season = Season(code="kharif", name="Kharif", aliases={"labels": {"en": "Kharif"}, "aliases": {"en": ["kharif"]}})
            region = Region(code="warangal", name="Warangal", level="district", aliases={"labels": {"en": "Warangal"}, "aliases": {"en": ["warangal"]}})
            topic = Topic(code="fertilizer_schedule", name="Fertilizer Schedule", aliases={"labels": {"en": "Fertilizer Schedule"}, "aliases": {"en": ["fertilizer"]}}, required_slots=["season", "growth_stage"])
            stage = GrowthStage(crop_id=1, code="tillering", name="Tillering", ordinal=2, day_from=21, day_to=45, reference_event="transplanting", aliases={"labels": {"en": "Tillering"}, "aliases": {"en": ["tillering"]}})
            db.add_all([crop, season, region, topic, stage])
            db.commit()
            doc = Document(
                title="Warangal paddy schedule",
                publisher="KVK",
                doc_type="guideline",
                source_url="https://example.com/warangal-paddy",
                language="en",
                publication_date=date(2024, 3, 1),
                valid_from=date(2024, 1, 1),
                valid_to=date(2099, 12, 31),
                version_label="v1",
                file_sha256="answer-doc",
            )
            db.add(doc)
            db.flush()
            chunk = Chunk(
                document_id=doc.id,
                ordinal=1,
                text="Apply 40 kg/ha nitrogen at 30-45 days after transplanting in Warangal.",
                page_start=8,
                page_end=8,
                section_path="8.1 Fertilizer",
                crop_ids=[crop.id],
                region_ids=[region.id],
                season_ids=[season.id],
                stage_ids=[stage.id],
                topic_id=topic.id,
            )
            db.add(chunk)
            db.flush()
            db.add(Claim(chunk_id=chunk.id, topic_id=topic.id, action_key="apply", value_num=40.0, value_unit="kg/ha", timing_day_from=30, timing_day_to=45, source_span="40 kg/ha"))
            db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/v1/chat",
            json={
                "message": "Crop: paddy | Region: Warangal | Season: kharif | Stage: tillering | Topic: fertilizer_schedule"
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "answer"
        assert "40 kg/ha" in payload["answer"]
        assert payload["citations"][0]["document_title"] == "Warangal paddy schedule"
    finally:
        app.dependency_overrides.clear()


def test_chat_abstains_when_required_context_is_missing() -> None:
    response = TestClient(app).post(
        "/api/v1/chat",
        json={"message": "Crop: paddy | Region: Warangal"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] in {"clarify", "abstain"}
