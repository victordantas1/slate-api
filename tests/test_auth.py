import base64
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.auth import CurrentMember, get_current_member

SECRET = "test-secret-with-at-least-32-bytes!!"
HOUSEHOLD_ID = uuid.uuid4()
MEMBER_ID = uuid.uuid4()


class _Body(BaseModel):
    household_id: uuid.UUID | None = None


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    def me(member: Annotated[CurrentMember, Depends(get_current_member)]) -> dict[str, str]:
        return {"household_id": str(member.household_id), "member_id": str(member.member_id)}

    @app.post("/echo")
    def echo(
        body: _Body, member: Annotated[CurrentMember, Depends(get_current_member)]
    ) -> dict[str, str]:
        return {"household_id": str(member.household_id)}

    return app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with TestClient(_app()) as test_client:
        yield test_client


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "role": "authenticated",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "household_id": str(HOUSEHOLD_ID),
        "member_id": str(MEMBER_ID),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _token(secret: str = SECRET, **overrides: Any) -> str:
    return jwt.encode(_claims(**overrides), secret, algorithm="HS256")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_valid_token_exposes_household_and_member_from_claims(client: TestClient) -> None:
    response = client.get("/me", headers=_bearer(_token()))

    assert response.status_code == 200
    assert response.json() == {"household_id": str(HOUSEHOLD_ID), "member_id": str(MEMBER_ID)}


def test_missing_token_returns_401(client: TestClient) -> None:
    response = client.get("/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_non_bearer_scheme_returns_401(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert response.status_code == 401


def test_garbage_token_returns_401(client: TestClient) -> None:
    response = client.get("/me", headers=_bearer("not-a-jwt"))

    assert response.status_code == 401


def test_expired_token_returns_401(client: TestClient) -> None:
    token = _token(exp=datetime.now(UTC) - timedelta(seconds=1))

    response = client.get("/me", headers=_bearer(token))

    assert response.status_code == 401


def test_token_without_exp_returns_401(client: TestClient) -> None:
    claims = _claims()
    del claims["exp"]
    token = jwt.encode(claims, SECRET, algorithm="HS256")

    response = client.get("/me", headers=_bearer(token))

    assert response.status_code == 401


def test_token_signed_with_other_secret_returns_401(client: TestClient) -> None:
    token = _token(secret="another-secret-with-at-least-32-bytes")

    response = client.get("/me", headers=_bearer(token))

    assert response.status_code == 401


def test_tampered_payload_with_original_signature_returns_401(client: TestClient) -> None:
    header, _, signature = _token().split(".")
    forged = _b64(
        {
            **_claims(exp=int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())),
            "household_id": str(uuid.uuid4()),
        }
    )

    response = client.get("/me", headers=_bearer(f"{header}.{forged}.{signature}"))

    assert response.status_code == 401


def test_alg_none_token_returns_401(client: TestClient) -> None:
    claims = _claims(exp=int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()))
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}."

    response = client.get("/me", headers=_bearer(token))

    assert response.status_code == 401


def test_wrong_audience_returns_401(client: TestClient) -> None:
    response = client.get("/me", headers=_bearer(_token(aud="anon")))

    assert response.status_code == 401


@pytest.mark.parametrize("claim", ["household_id", "member_id"])
def test_missing_tenant_claim_returns_401(client: TestClient, claim: str) -> None:
    claims = _claims()
    del claims[claim]
    token = jwt.encode(claims, SECRET, algorithm="HS256")

    response = client.get("/me", headers=_bearer(token))

    assert response.status_code == 401


def test_non_uuid_household_claim_returns_401(client: TestClient) -> None:
    response = client.get("/me", headers=_bearer(_token(household_id="household-a")))

    assert response.status_code == 401


def test_household_id_in_body_is_ignored(client: TestClient) -> None:
    forged = uuid.uuid4()

    response = client.post("/echo", headers=_bearer(_token()), json={"household_id": str(forged)})

    assert response.status_code == 200
    assert response.json() == {"household_id": str(HOUSEHOLD_ID)}


def test_missing_secret_is_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")

    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.get("/me", headers=_bearer(_token()))

    assert response.status_code == 500
