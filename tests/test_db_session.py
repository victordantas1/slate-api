from typing import Any

import pytest

from app.core.config import Settings
from app.db.session import _engine_kwargs, build_async_engine


def _settings(**overrides: object) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://user:pass@localhost:5432/db",
        "db_pool_size": 7,
        "db_max_overflow": 3,
        "db_pool_recycle_seconds": 900,
        "db_statement_cache_size": 0,
    }
    base.update(overrides)
    return Settings(**base)


def test_engine_kwargs_maps_settings_to_pool_config() -> None:
    kwargs = _engine_kwargs(_settings())

    assert kwargs["pool_size"] == 7
    assert kwargs["max_overflow"] == 3
    assert kwargs["pool_recycle"] == 900
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["connect_args"] == {"statement_cache_size": 0}


def test_build_async_engine_raises_without_database_url() -> None:
    settings = _settings(database_url=None)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_async_engine(settings)


async def test_build_async_engine_returns_engine_with_configured_url() -> None:
    settings = _settings()

    engine = build_async_engine(settings)
    try:
        assert str(engine.url) == "postgresql+asyncpg://user:***@localhost:5432/db"
    finally:
        await engine.dispose()
