import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SLATE_WEB = "https://slate-web.example.com"


def _preflight(client: TestClient, origin: str) -> dict[str, str]:
    response = client.options(
        "/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    return dict(response.headers)


def _client_with_origins(monkeypatch: pytest.MonkeyPatch, origins: str) -> TestClient:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origins)
    get_settings.cache_clear()
    return TestClient(create_app())


def test_cors_allows_configured_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_origins(monkeypatch, SLATE_WEB)

    headers = _preflight(client, SLATE_WEB)

    assert headers.get("access-control-allow-origin") == SLATE_WEB


def test_cors_rejects_other_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_origins(monkeypatch, SLATE_WEB)

    headers = _preflight(client, "https://evil.example.com")

    assert "access-control-allow-origin" not in headers


def test_cors_accepts_comma_separated_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_origins(monkeypatch, f"{SLATE_WEB}, http://localhost:5173")

    headers = _preflight(client, "http://localhost:5173")

    assert headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_allows_no_origin_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_origins(monkeypatch, "")

    headers = _preflight(client, SLATE_WEB)

    assert "access-control-allow-origin" not in headers
