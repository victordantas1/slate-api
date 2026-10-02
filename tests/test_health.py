import pytest
from fastapi.testclient import TestClient

from app.api.health import DatabaseStatus, get_database_status
from app.core.config import get_settings
from app.db.session import get_engine
from app.main import app


def test_get_settings_cache_does_not_leak_across_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "leaked")

    assert get_settings().environment == "leaked"


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reflects_configured_environment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["environment"] == "staging"


def test_health_reports_database_not_configured_without_url(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["database"] == "not_configured"


def test_health_reports_database_ok_when_probe_succeeds(client: TestClient) -> None:
    async def probe_ok() -> DatabaseStatus:
        return "ok"

    app.dependency_overrides[get_database_status] = probe_ok
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_database_status)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development", "database": "ok"}


def test_health_returns_503_when_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Porta 1 recusa a conexão na hora: exercita o SELECT 1 de verdade, sem Postgres.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@127.0.0.1:1/db")
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        response = client.get("/health")
    finally:
        get_engine.cache_clear()

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"
