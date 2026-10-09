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
    llm_provider: str = "deterministic"
    llm_model: str = "claude-3-5-haiku-latest"
    anthropic_api_key: str | None = None
    llm_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    llm_fake_response: dict[str, str] = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_prefix="",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
