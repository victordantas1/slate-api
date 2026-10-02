import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Account, ExternalHolder, Household, Member
from app.db.session import get_engine, get_member_session
from app.main import create_app

SECRET = "test-secret-with-at-least-32-bytes!!"


@dataclass(frozen=True)
class Tenant:
    id: uuid.UUID
    member_id: uuid.UUID
    external_id: uuid.UUID
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def _household(session: AsyncSession) -> Tenant:
    household_id = (
        await session.execute(insert(Household).values(name="Casa").returning(Household.id))
    ).scalar_one()
    member_id = (
        await session.execute(
            insert(Member)
            .values(household_id=household_id, supabase_user_id=uuid.uuid4(), name="Ana")
            .returning(Member.id)
        )
    ).scalar_one()
    external_id = (
        await session.execute(
            insert(ExternalHolder)
            # Nome único: o teste sob RLS commita, e outros testes buscam "Vó" no banco.
            .values(household_id=household_id, name=f"Titular {uuid.uuid4().hex[:8]}")
            .returning(ExternalHolder.id)
        )
    ).scalar_one()
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "household_id": str(household_id),
            "member_id": str(member_id),
        },
        SECRET,
        algorithm="HS256",
    )
    return Tenant(household_id, member_id, external_id, token)


@pytest.fixture
async def api(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_member_session] = _session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def home(db_session: AsyncSession) -> Tenant:
    return await _household(db_session)


def _member_account(h: Tenant, **extra: Any) -> dict[str, Any]:
    return {
        "name": "Nubank",
        "kind": "credit_card",
        "holder_kind": "member",
        "owner_member_id": str(h.member_id),
        "closing_day": 3,
        "due_day": 10,
        **extra,
    }


async def _create(api: httpx.AsyncClient, h: Tenant, **extra: Any) -> dict[str, Any]:
    response = await api.post("/accounts", json=_member_account(h, **extra), headers=h.headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_requires_token(api: httpx.AsyncClient) -> None:
    assert (await api.get("/accounts")).status_code == 401
    assert (await api.post("/accounts", json={})).status_code == 401


async def test_create_member_account(api: httpx.AsyncClient, home: Tenant) -> None:
    body = await _create(api, home)

    assert body["household_id"] == str(home.id)
    assert body["holder_kind"] == "member"
    assert body["owner_member_id"] == str(home.member_id)
    assert body["external_holder_id"] is None
    assert body["first_installment_offset"] == 1
    assert body["archived"] is False


async def test_create_external_account(api: httpx.AsyncClient, home: Tenant) -> None:
    body = await _create(
        api,
        home,
        holder_kind="external",
        owner_member_id=None,
        external_holder_id=str(home.external_id),
        first_installment_offset=0,
    )

    assert body["holder_kind"] == "external"
    assert body["external_holder_id"] == str(home.external_id)
    assert body["owner_member_id"] is None
    assert body["first_installment_offset"] == 0


async def test_household_id_in_body_is_ignored(
    api: httpx.AsyncClient, home: Tenant, db_session: AsyncSession
) -> None:
    other = await _household(db_session)
    body = await _create(api, home, household_id=str(other.id))

    assert body["household_id"] == str(home.id)


@pytest.mark.parametrize(
    "payload",
    [
        {"holder_kind": "external", "owner_member_id": None},
        {"holder_kind": "external", "owner_member_id": None, "external_holder_id": None},
        {"holder_kind": "member", "owner_member_id": None},
    ],
    ids=["external-sem-holder", "external-holder-null", "member-sem-owner"],
)
async def test_holder_without_its_id_is_rejected(
    api: httpx.AsyncClient, home: Tenant, payload: dict[str, Any]
) -> None:
    response = await api.post(
        "/accounts", json=_member_account(home, **payload), headers=home.headers
    )

    assert response.status_code == 422


async def test_external_without_external_holder_is_rejected(
    api: httpx.AsyncClient, home: Tenant
) -> None:
    payload = _member_account(home, holder_kind="external")
    del payload["owner_member_id"]

    response = await api.post("/accounts", json=payload, headers=home.headers)

    assert response.status_code == 422
    assert "external_holder_id" in response.text


async def test_both_holders_are_rejected(api: httpx.AsyncClient, home: Tenant) -> None:
    response = await api.post(
        "/accounts",
        json=_member_account(home, external_holder_id=str(home.external_id)),
        headers=home.headers,
    )

    assert response.status_code == 422


async def test_holder_from_other_household_is_rejected(
    api: httpx.AsyncClient, home: Tenant, db_session: AsyncSession
) -> None:
    other = await _household(db_session)

    as_member = await api.post(
        "/accounts",
        json=_member_account(home, owner_member_id=str(other.member_id)),
        headers=home.headers,
    )
    as_external = await api.post(
        "/accounts",
        json=_member_account(
            home,
            holder_kind="external",
            owner_member_id=None,
            external_holder_id=str(other.external_id),
        ),
        headers=home.headers,
    )

    assert as_member.status_code == 422
    assert as_external.status_code == 422


@pytest.mark.parametrize(
    "extra",
    [
        {"first_installment_offset": -1},
        {"closing_day": 0},
        {"due_day": 32},
        {"kind": "savings"},
        {"name": ""},
    ],
    ids=["offset-negativo", "closing-0", "due-32", "kind-invalido", "nome-vazio"],
)
async def test_invalid_fields_are_rejected(
    api: httpx.AsyncClient, home: Tenant, extra: dict[str, Any]
) -> None:
    response = await api.post(
        "/accounts", json=_member_account(home, **extra), headers=home.headers
    )

    assert response.status_code == 422


async def test_list_and_get(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)

    listed = await api.get("/accounts", headers=home.headers)
    fetched = await api.get(f"/accounts/{created['id']}", headers=home.headers)

    assert listed.status_code == 200
    assert [a["id"] for a in listed.json()] == [created["id"]]
    assert fetched.status_code == 200
    assert fetched.json() == created


async def test_other_household_account_is_not_visible(
    api: httpx.AsyncClient, home: Tenant, db_session: AsyncSession
) -> None:
    other = await _household(db_session)
    created = await _create(api, other)
    path = f"/accounts/{created['id']}"

    assert (await api.get("/accounts", headers=home.headers)).json() == []
    assert (await api.get(path, headers=home.headers)).status_code == 404
    patched = await api.patch(path, json={"name": "Minha"}, headers=home.headers)
    assert patched.status_code == 404
    assert (await api.get(path, headers=other.headers)).json()["name"] == "Nubank"


async def test_unknown_account_is_404(api: httpx.AsyncClient, home: Tenant) -> None:
    response = await api.get(f"/accounts/{uuid.uuid4()}", headers=home.headers)

    assert response.status_code == 404


async def test_first_installment_offset_is_editable(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)
    path = f"/accounts/{created['id']}"

    response = await api.patch(path, json={"first_installment_offset": 2}, headers=home.headers)

    assert response.status_code == 200
    assert response.json()["first_installment_offset"] == 2
    assert (await api.get(path, headers=home.headers)).json()["first_installment_offset"] == 2


async def test_negative_offset_is_rejected(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)

    response = await api.patch(
        f"/accounts/{created['id']}", json={"first_installment_offset": -1}, headers=home.headers
    )

    assert response.status_code == 422


async def test_patch_updates_only_given_fields(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)

    response = await api.patch(
        f"/accounts/{created['id']}",
        json={"name": "Nubank Ana", "closing_day": None},
        headers=home.headers,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["name"] == "Nubank Ana"
    assert body["closing_day"] is None
    assert body["due_day"] == 10
    assert body["owner_member_id"] == str(home.member_id)


@pytest.mark.parametrize("field", ["name", "first_installment_offset", "archived"])
async def test_patch_rejects_null_for_required_fields(
    api: httpx.AsyncClient, home: Tenant, field: str
) -> None:
    created = await _create(api, home)

    response = await api.patch(
        f"/accounts/{created['id']}", json={field: None}, headers=home.headers
    )

    assert response.status_code == 422


async def test_kind_is_not_editable(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)

    response = await api.patch(
        f"/accounts/{created['id']}", json={"kind": "checking"}, headers=home.headers
    )

    assert response.status_code == 422


async def test_patch_switches_holder_to_external(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)

    response = await api.patch(
        f"/accounts/{created['id']}",
        json={"holder_kind": "external", "external_holder_id": str(home.external_id)},
        headers=home.headers,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["holder_kind"] == "external"
    assert body["external_holder_id"] == str(home.external_id)
    assert body["owner_member_id"] is None


async def test_patch_to_external_without_holder_is_rejected(
    api: httpx.AsyncClient, home: Tenant
) -> None:
    created = await _create(api, home)
    path = f"/accounts/{created['id']}"

    response = await api.patch(path, json={"holder_kind": "external"}, headers=home.headers)

    assert response.status_code == 422
    assert "external_holder_id" in response.text
    assert (await api.get(path, headers=home.headers)).json()["holder_kind"] == "member"


async def test_patch_clearing_owner_is_rejected(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)

    response = await api.patch(
        f"/accounts/{created['id']}", json={"owner_member_id": None}, headers=home.headers
    )

    assert response.status_code == 422


async def test_patch_holder_of_opposite_kind_is_rejected(
    api: httpx.AsyncClient, home: Tenant
) -> None:
    created = await _create(api, home)

    response = await api.patch(
        f"/accounts/{created['id']}",
        json={"external_holder_id": str(home.external_id)},
        headers=home.headers,
    )

    assert response.status_code == 422


async def test_archive_hides_from_default_list(api: httpx.AsyncClient, home: Tenant) -> None:
    created = await _create(api, home)
    path = f"/accounts/{created['id']}"

    archived = await api.patch(path, json={"archived": True}, headers=home.headers)
    default = await api.get("/accounts", headers=home.headers)
    everything = await api.get("/accounts?include_archived=true", headers=home.headers)
    restored = await api.patch(path, json={"archived": False}, headers=home.headers)

    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert default.json() == []
    assert [a["id"] for a in everything.json()] == [created["id"]]
    assert restored.json()["archived"] is False


async def test_delete_is_not_allowed_and_account_survives(
    api: httpx.AsyncClient, home: Tenant, db_session: AsyncSession
) -> None:
    created = await _create(api, home)

    response = await api.delete(f"/accounts/{created['id']}", headers=home.headers)

    assert response.status_code == 405
    still_there = await db_session.scalar(
        select(Account.id).where(Account.id == uuid.UUID(created["id"]))
    )
    assert still_there is not None


def test_crud_through_rls_session(
    migrated_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem override: a sessão real roda como `slate_app`, sob as policies de RLS."""

    async def _seed() -> Tenant:
        engine = create_async_engine(migrated_postgres_url)
        try:
            async with engine.begin() as conn:
                return await _household(AsyncSession(bind=conn))
        finally:
            await engine.dispose()

    monkeypatch.setenv("DATABASE_URL", migrated_postgres_url)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    get_engine.cache_clear()
    try:
        home, other = asyncio.run(_seed()), asyncio.run(_seed())
        with TestClient(create_app()) as client:
            created = client.post("/accounts", json=_member_account(home), headers=home.headers)
            path = f"/accounts/{created.json()['id']}"
            patched = client.patch(path, json={"first_installment_offset": 0}, headers=home.headers)
            foreign = client.post(
                "/accounts",
                json=_member_account(home, owner_member_id=str(other.member_id)),
                headers=home.headers,
            )
            hidden = client.get(path, headers=other.headers)
    finally:
        get_engine.cache_clear()

    assert created.status_code == 201, created.text
    assert patched.json()["first_installment_offset"] == 0
    assert foreign.status_code == 422
    assert hidden.status_code == 404
