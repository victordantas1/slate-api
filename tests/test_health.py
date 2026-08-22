import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


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
