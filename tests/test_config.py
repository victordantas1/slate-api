import pytest

from app.core.config import get_settings


def test_db_pool_settings_have_conservative_defaults() -> None:
    settings = get_settings()

    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 5
    assert settings.db_pool_recycle_seconds == 1800
    assert settings.db_statement_cache_size == 0


def test_db_pool_settings_are_configurable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "10")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "2")

    settings = get_settings()

    assert settings.db_pool_size == 10
    assert settings.db_max_overflow == 2
