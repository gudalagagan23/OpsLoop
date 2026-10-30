from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from environment variables prefixed OPSLOOP_."""

    model_config = SettingsConfigDict(env_prefix="OPSLOOP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://opsloop:opsloop@localhost:55432/opsloop"


@lru_cache
def get_settings() -> Settings:
    return Settings()
