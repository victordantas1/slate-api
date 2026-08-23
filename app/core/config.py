from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "slate-api"
    environment: str = "development"
    database_url: str | None = None
    supabase_jwt_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
