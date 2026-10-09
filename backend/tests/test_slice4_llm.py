from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.chat.service import purge_expired_sessions
from app.core.config import Settings
from app.db.base import Base
from app.db.models import ChatSession
from app.llm.client import FakeLLMClient, get_llm_client


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_fake_llm_returns_structured_context() -> None:
    client = FakeLLMClient({"crop": "paddy", "region": "warangal"})

    assert client.extract_context("paddy in Warangal") == {
        "crop": "paddy",
        "region": "warangal",
    }


def test_fake_provider_is_selectable_from_settings() -> None:
    client = get_llm_client(
        Settings(llm_provider="fake", llm_fake_response={"season": "kharif"})
    )

    assert client is not None
    assert client.extract_context("during kharif") == {"season": "kharif"}


def test_expired_sessions_are_purged() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        db.add_all(
            [
                ChatSession(
                    id="00000000-0000-0000-0000-000000000001",
                    context={},
                    updated_at=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                ChatSession(
                    id="00000000-0000-0000-0000-000000000002",
                    context={},
                    updated_at=datetime.now(UTC) - timedelta(days=1),
                ),
            ]
        )
        db.commit()

        deleted = purge_expired_sessions(db, datetime(2020, 2, 1, tzinfo=UTC))

        assert deleted == 1
        assert db.get(ChatSession, "00000000-0000-0000-0000-000000000001") is None
        assert db.get(ChatSession, "00000000-0000-0000-0000-000000000002") is not None
