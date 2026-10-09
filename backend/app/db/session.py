import os
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.seed import seed_taxonomy


def make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url

    if url.startswith("postgresql"):
        try:
            import psycopg  # noqa: F401

            temp_engine = create_engine(url, pool_pre_ping=True, future=True)
            with temp_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except (ModuleNotFoundError, SQLAlchemyError, OSError):
            if os.getenv("ENVIRONMENT", "development") == "production":
                raise
            url = "sqlite:///./krishimitra_local.db"

    return create_engine(url, pool_pre_ping=True, future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_local_database() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return

    with SessionLocal() as db:
        Base.metadata.create_all(bind=engine)
        table_exists = db.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='crops'")
        ).scalar()
        if table_exists is None:
            seed_taxonomy(db)
            return

        crop_count = db.execute(text("SELECT COUNT(*) FROM crops")).scalar()
        if crop_count == 0:
            seed_taxonomy(db)


initialize_local_database()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
