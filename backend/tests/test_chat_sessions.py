from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.chat.service import answer_question
from app.db.base import Base
from app.db.models import ChatSession, Chunk, Crop, Document, GrowthStage, Region, Season, Topic


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_natural_language_context_is_extracted_and_answered() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        crop = Crop(code="paddy", name="Paddy", aliases={"labels": {"en": "Paddy"}, "aliases": {"en": ["paddy"]}})
        season = Season(code="kharif", name="Kharif", aliases={"labels": {"en": "Kharif"}, "aliases": {"en": ["kharif"]}})
        region = Region(code="warangal", name="Warangal", level="district", aliases={"labels": {"en": "Warangal"}, "aliases": {"en": ["warangal"]}})
        topic = Topic(
            code="fertilizer_schedule",
            name="Fertilizer schedule",
            aliases={"labels": {"en": "Fertilizer schedule"}, "aliases": {"en": ["fertilizer"]}},
            required_slots=["season", "growth_stage"],
        )
        db.add_all([crop, season, region, topic])
        db.flush()
        stage = GrowthStage(
            crop_id=crop.id,
            code="tillering",
            name="Tillering",
            ordinal=1,
            day_from=21,
            day_to=45,
            reference_event="transplanting",
            aliases={"labels": {"en": "Tillering"}, "aliases": {"en": ["tillering"]}},
        )
        db.add(stage)
        db.flush()
        document = Document(
            title="Paddy guide",
            publisher="KVK",
            doc_type="guideline",
            source_url="https://example.com/paddy",
            language="en",
            publication_date=date(2024, 1, 1),
            valid_from=date(2024, 1, 1),
            valid_to=date(2099, 1, 1),
            version_label="v1",
            file_sha256="natural-language-test",
        )
        db.add(document)
        db.flush()
        db.add(
            Chunk(
                document_id=document.id,
                ordinal=1,
                text="Apply the fertilizer during tillering.",
                page_start=1,
                page_end=1,
                section_path="Fertilizer",
                crop_ids=[crop.id],
                region_ids=[region.id],
                season_ids=[season.id],
                stage_ids=[stage.id],
                topic_id=topic.id,
            )
        )
        db.commit()

        response = answer_question(
            db,
            "What fertilizer should I use for paddy in Warangal during tillering in kharif?",
            "2024-06-01",
            None,
        )

        assert response["type"] == "answer"
        assert response["verified"] is True
        assert response["citations"][0]["document_title"] == "Paddy guide"


def test_clarification_is_persisted_and_merged_across_turns() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        crop = Crop(code="paddy", name="Paddy", aliases={"labels": {}, "aliases": {"en": ["paddy"]}})
        region = Region(code="warangal", name="Warangal", level="district", aliases={"labels": {}, "aliases": {"en": ["warangal"]}})
        season = Season(code="kharif", name="Kharif", aliases={"labels": {}, "aliases": {"en": ["kharif"]}})
        topic = Topic(code="irrigation", name="Irrigation", aliases={"labels": {}, "aliases": {"en": ["irrigation"]}}, required_slots=["season", "growth_stage"])
        db.add_all([crop, region, season, topic])
        db.flush()
        db.add(
            GrowthStage(
                crop_id=crop.id,
                code="tillering",
                name="Tillering",
                ordinal=1,
                day_from=21,
                day_to=45,
                reference_event="transplanting",
                aliases={"labels": {}, "aliases": {"en": ["tillering"]}},
            )
        )
        db.commit()

        first = answer_question(db, "paddy irrigation in Warangal", None, None)
        assert first["type"] == "clarify"
        session_id = str(first["session_id"])
        assert first["clarification"]["slot"] == "season"
        assert db.get(ChatSession, session_id) is not None

        second = answer_question(db, "kharif", None, session_id)
        assert second["type"] == "clarify"
        assert second["clarification"]["slot"] == "stage"
        third = answer_question(db, "tillering", None, session_id)
        assert third["type"] == "abstain"
        assert third["session_id"] == session_id
