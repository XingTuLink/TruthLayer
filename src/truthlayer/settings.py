"""Application settings loaded from environment variables.

Phase 0 only needs the database URL. The Master API Key (TRUTHLAYER_API_KEY)
becomes active in Phase 1 (#68) and is intentionally not consumed yet.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRUTHLAYER_", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres@localhost:5432/truthlayer"


settings = Settings()
