import time
import uuid
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, ExternalHolder, Household
from app.db.session import get_member_session
from app.main import app

SECRET = "test-secret-with-at-least-32-bytes!!"


async def _household(session: AsyncSession, name: str = "Casa") -> uuid.UUID:
    result = await session.execute(insert(Household).values(name=name).returning(Household.id))
    return result.scalar_one()


def _headers(household_id: uuid.UUID) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "household_id": str(household_id),
            "member_id": str(uuid.uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def api(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    # O AsyncClient roda no mesmo event loop da `db_session`; o TestClient usaria outro.
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_member_session] = _session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_member_session, None)


@pytest.fixture
async def household(db_session: AsyncSession) -> uuid.UUID:
    return await _household(db_session)


async def _create(api: httpx.AsyncClient, household: uuid.UUID, name: str) -> dict[str, str]:
    response = await api.post("/external-holders", json={"name": name}, headers=_headers(household))
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


async def test_create_returns_holder(api: httpx.AsyncClient, household: uuid.UUID) -> None:
    response = await api.post(
        "/external-holders", json={"name": "  Vó  "}, headers=_headers(household)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Vó"
    assert uuid.UUID(body["id"])
    assert "created_at" in body
    assert "household_id" not in body


async def test_create_duplicate_name_returns_409(
    api: httpx.AsyncClient, household: uuid.UUID
) -> None:
    await _create(api, household, "Vó")

    response = await api.post("/external-holders", json={"name": "Vó"}, headers=_headers(household))

    assert response.status_code == 409


async def test_same_name_in_other_household_is_allowed(
    api: httpx.AsyncClient, household: uuid.UUID, db_session: AsyncSession
) -> None:
    other = await _household(db_session, "Outra")
    await _create(api, household, "Vó")

    response = await api.post("/external-holders", json={"name": "Vó"}, headers=_headers(other))

    assert response.status_code == 201


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
async def test_invalid_name_returns_422(
    api: httpx.AsyncClient, household: uuid.UUID, name: str
) -> None:
    response = await api.post("/external-holders", json={"name": name}, headers=_headers(household))

    assert response.status_code == 422


async def test_household_id_in_body_is_ignored(
    api: httpx.AsyncClient, household: uuid.UUID, db_session: AsyncSession
) -> None:
    other = await _household(db_session, "Outra")

    created = await api.post(
        "/external-holders",
        json={"name": "Vó", "household_id": str(other)},
        headers=_headers(household),
    )

    stored = await db_session.scalar(
        select(ExternalHolder.household_id).where(ExternalHolder.id == created.json()["id"])
    )
    assert stored == household


async def test_list_only_returns_own_household(
    api: httpx.AsyncClient, household: uuid.UUID, db_session: AsyncSession
) -> None:
    other = await _household(db_session, "Outra")
    await _create(api, household, "Vó")
    await _create(api, household, "Tio")
    await _create(api, other, "Estranho")

    response = await api.get("/external-holders", headers=_headers(household))

    assert response.status_code == 200
    assert [h["name"] for h in response.json()] == ["Tio", "Vó"]


async def test_get_returns_holder(api: httpx.AsyncClient, household: uuid.UUID) -> None:
    created = await _create(api, household, "Vó")

    response = await api.get(f"/external-holders/{created['id']}", headers=_headers(household))

    assert response.status_code == 200
    assert response.json() == created


async def test_other_household_holder_is_404(
    api: httpx.AsyncClient, household: uuid.UUID, db_session: AsyncSession
) -> None:
    other = await _household(db_session, "Outra")
    created = await _create(api, other, "Vó")
    url = f"/external-holders/{created['id']}"
    headers = _headers(household)

    assert (await api.get(url, headers=headers)).status_code == 404
    assert (await api.patch(url, json={"name": "X"}, headers=headers)).status_code == 404
    assert (await api.delete(url, headers=headers)).status_code == 404
    still = await api.get(url, headers=_headers(other))
    assert still.json()["name"] == "Vó"


async def test_unknown_holder_is_404(api: httpx.AsyncClient, household: uuid.UUID) -> None:
    response = await api.get(f"/external-holders/{uuid.uuid4()}", headers=_headers(household))

    assert response.status_code == 404


async def test_rename(api: httpx.AsyncClient, household: uuid.UUID) -> None:
    created = await _create(api, household, "Vó")

    response = await api.patch(
        f"/external-holders/{created['id']}", json={"name": "Vovó"}, headers=_headers(household)
    )

    assert response.status_code == 200
    assert response.json() == {**created, "name": "Vovó"}


async def test_rename_to_existing_name_returns_409(
    api: httpx.AsyncClient, household: uuid.UUID
) -> None:
    await _create(api, household, "Vó")
    tio = await _create(api, household, "Tio")

    response = await api.patch(
        f"/external-holders/{tio['id']}", json={"name": "Vó"}, headers=_headers(household)
    )

    assert response.status_code == 409
    current = await api.get(f"/external-holders/{tio['id']}", headers=_headers(household))
    assert current.json()["name"] == "Tio"


async def test_delete(api: httpx.AsyncClient, household: uuid.UUID) -> None:
    created = await _create(api, household, "Vó")
    url = f"/external-holders/{created['id']}"

    response = await api.delete(url, headers=_headers(household))

    assert response.status_code == 204
    assert (await api.get(url, headers=_headers(household))).status_code == 404


@pytest.mark.parametrize("archived", [False, True])
async def test_delete_with_linked_account_returns_409(
    api: httpx.AsyncClient, household: uuid.UUID, db_session: AsyncSession, archived: bool
) -> None:
    created = await _create(api, household, "Vó")
    await db_session.execute(
        insert(Account).values(
            household_id=household,
            name="Cartão da Vó",
            kind="credit_card",
            holder_kind="external",
            external_holder_id=uuid.UUID(created["id"]),
            closing_day=1,
            due_day=10,
            archived=archived,
        )
    )
    url = f"/external-holders/{created['id']}"

    response = await api.delete(url, headers=_headers(household))

    assert response.status_code == 409
    assert (await api.get(url, headers=_headers(household))).status_code == 200


async def test_requires_token(api: httpx.AsyncClient) -> None:
    assert (await api.get("/external-holders")).status_code == 401
    assert (await api.post("/external-holders", json={"name": "Vó"})).status_code == 401
