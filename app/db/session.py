from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings


def _engine_kwargs(settings: Settings) -> dict[str, object]:
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_recycle": settings.db_pool_recycle_seconds,
        "pool_pre_ping": True,
        "connect_args": {"statement_cache_size": settings.db_statement_cache_size},
    }


def build_async_engine(settings: Settings) -> AsyncEngine:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL não configurada")
    return create_async_engine(settings.database_url, **_engine_kwargs(settings))


@lru_cache
def get_engine() -> AsyncEngine:
    return build_async_engine(get_settings())


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
