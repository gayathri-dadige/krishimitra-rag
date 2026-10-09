from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "KrishiMitra"
    environment: str = "development"
    database_url: str = "sqlite:///./krishimitra_local.db"
    cors_origins: list[str] = Field(default_factory=list)
    log_level: str = "INFO"
    retention_days: int = Field(default=30, ge=1, le=365)

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_prefix="",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
