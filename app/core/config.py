from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "slate-api"
    environment: str = "development"
    database_url: str | None = None
    supabase_jwt_secret: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_recycle_seconds: int = 1800
    db_statement_cache_size: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
