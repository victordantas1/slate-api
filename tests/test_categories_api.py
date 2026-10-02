import asyncio
import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Category, Household
from app.db.session import get_engine
from app.main import app
from app.services.categories import DEFAULT_CATEGORIES

SECRET = "test-secret-with-at-least-32-bytes!!"


class _Api:
    def __init__(self, client: TestClient, database_url: str) -> None:
        self.client = client
        self.database_url = database_url

    def household(self) -> dict[str, str]:
        """Cria uma household nova e devolve os headers de um membro dela."""

        async def _create() -> uuid.UUID:
            engine = create_async_engine(self.database_url)
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        insert(Household).values(name="Casa").returning(Household.id)
                    )
                    return result.scalar_one()
            finally:
                await engine.dispose()

        household_id = asyncio.run(_create())
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "aud": "authenticated",
                "role": "authenticated",
                "exp": int(time.time()) + 300,
                "household_id": str(household_id),
                "member_id": str(uuid.uuid4()),
            },
            SECRET,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def rows(self, category_id: str) -> list[Any]:
        async def _select() -> list[Any]:
            engine = create_async_engine(self.database_url)
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        select(Category).where(Category.id == uuid.UUID(category_id))
                    )
                    return list(result.all())
            finally:
                await engine.dispose()

        return asyncio.run(_select())


@pytest.fixture
def api(migrated_postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    monkeypatch.setenv("DATABASE_URL", migrated_postgres_url)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    get_engine.cache_clear()
    try:
        with TestClient(app) as client:
            yield _Api(client, migrated_postgres_url)
    finally:
        get_engine.cache_clear()


def _create(api: _Api, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = api.client.post("/categories", json=body, headers=headers)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def _names(api: _Api, headers: dict[str, str], **params: Any) -> list[str]:
    response = api.client.get("/categories", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return [c["name"] for c in response.json()]


def test_seed_is_idempotent_and_per_household(api: _Api) -> None:
    casa = api.household()

    first = api.client.post("/categories/seed", headers=casa)
    second = api.client.post("/categories/seed", headers=casa)

    assert first.status_code == 200
    assert first.json() == {"created": len(DEFAULT_CATEGORIES)}
    assert second.json() == {"created": 0}
    listed = api.client.get("/categories", headers=casa).json()
    assert sorted(c["name"] for c in listed) == sorted(DEFAULT_CATEGORIES)
    assert all(c["parent_id"] is None and c["direction"] == "expense" for c in listed)

    outra = api.household()
    assert api.client.post("/categories/seed", headers=outra).json() == {
        "created": len(DEFAULT_CATEGORIES)
    }


def test_seed_runs_only_once_even_after_renaming_a_default(api: _Api) -> None:
    casa = api.household()
    api.client.post("/categories/seed", headers=casa)
    mercado = next(
        c for c in api.client.get("/categories", headers=casa).json() if c["name"] == "Mercado"
    )
    api.client.patch(f"/categories/{mercado['id']}", json={"name": "Feira"}, headers=casa)

    assert api.client.post("/categories/seed", headers=casa).json() == {"created": 0}
    assert "Mercado" not in _names(api, casa)


def test_seed_skips_household_that_already_has_categories(api: _Api) -> None:
    casa = api.household()
    _create(api, casa, name="Moradia")

    assert api.client.post("/categories/seed", headers=casa).json() == {"created": 0}
    assert _names(api, casa) == ["Moradia"]


def test_third_level_is_rejected(api: _Api) -> None:
    casa = api.household()
    root = _create(api, casa, name="Moradia")
    child = _create(api, casa, name="Aluguel", parent_id=root["id"])

    grandchild = api.client.post(
        "/categories", json={"name": "Multa", "parent_id": child["id"]}, headers=casa
    )
    other_root = _create(api, casa, name="Casa")
    move_parent_under = api.client.patch(
        f"/categories/{root['id']}", json={"parent_id": other_root["id"]}, headers=casa
    )

    assert child["parent_id"] == root["id"]
    assert grandchild.status_code == 422
    assert move_parent_under.status_code == 422


def test_child_inherits_direction_and_rejects_mismatch(api: _Api) -> None:
    casa = api.household()
    salario = _create(api, casa, name="Salário", direction="income")

    child = _create(api, casa, name="13º", parent_id=salario["id"])
    mismatch = api.client.post(
        "/categories",
        json={"name": "Bônus", "parent_id": salario["id"], "direction": "expense"},
        headers=casa,
    )

    assert child["direction"] == "income"
    assert mismatch.status_code == 422


def test_archive_keeps_the_row_and_hides_it_from_default_listing(api: _Api) -> None:
    casa = api.household()
    root = _create(api, casa, name="Lazer")
    child = _create(api, casa, name="Cinema", parent_id=root["id"])

    archived = api.client.delete(f"/categories/{root['id']}", headers=casa)

    assert archived.status_code == 204
    assert _names(api, casa) == []
    assert sorted(_names(api, casa, include_archived="true")) == ["Cinema", "Lazer"]
    by_id = api.client.get(f"/categories/{root['id']}", headers=casa)
    assert by_id.status_code == 200
    assert by_id.json()["archived"] is True
    assert api.client.get(f"/categories/{child['id']}", headers=casa).json()["archived"] is True
    # A linha continua no banco: entries que apontam para ela não perdem a FK.
    assert len(api.rows(root["id"])) == 1

    under_archived = api.client.post(
        "/categories", json={"name": "Teatro", "parent_id": root["id"]}, headers=casa
    )
    assert under_archived.status_code == 422

    restored = api.client.patch(f"/categories/{root['id']}", json={"archived": False}, headers=casa)
    assert restored.status_code == 200
    assert restored.json()["archived"] is False
    assert _names(api, casa) == ["Lazer"]


def test_crud_roundtrip(api: _Api) -> None:
    casa = api.household()
    created = _create(api, casa, name="  Saúde  ")
    other = _create(api, casa, name="Farmácia")

    assert created["name"] == "Saúde"
    assert created["direction"] == "expense"
    assert created["archived"] is False

    renamed = api.client.patch(
        f"/categories/{created['id']}", json={"name": "Saúde e bem-estar"}, headers=casa
    )
    moved = api.client.patch(
        f"/categories/{other['id']}", json={"parent_id": created["id"]}, headers=casa
    )
    back_to_root = api.client.patch(
        f"/categories/{other['id']}", json={"parent_id": None}, headers=casa
    )

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Saúde e bem-estar"
    assert moved.status_code == 200
    assert moved.json()["parent_id"] == created["id"]
    assert back_to_root.json()["parent_id"] is None


def test_duplicate_name_on_same_level_is_conflict(api: _Api) -> None:
    casa = api.household()
    _create(api, casa, name="Mercado")
    duplicate = api.client.post("/categories", json={"name": "Mercado"}, headers=casa)

    assert duplicate.status_code == 409


def test_other_household_is_not_visible(api: _Api) -> None:
    casa, outra = api.household(), api.household()
    mine = _create(api, casa, name="Mercado")

    assert api.client.get(f"/categories/{mine['id']}", headers=outra).status_code == 404
    assert (
        api.client.patch(f"/categories/{mine['id']}", json={"name": "X"}, headers=outra).status_code
        == 404
    )
    assert api.client.delete(f"/categories/{mine['id']}", headers=outra).status_code == 404
    assert (
        api.client.post(
            "/categories", json={"name": "Filha", "parent_id": mine["id"]}, headers=outra
        ).status_code
        == 422
    )
    assert _names(api, outra) == []


def test_requires_token(api: _Api) -> None:
    assert api.client.get("/categories").status_code == 401
    assert api.client.post("/categories/seed").status_code == 401


def test_invalid_payload_is_rejected(api: _Api) -> None:
    casa = api.household()
    assert api.client.post("/categories", json={"name": "   "}, headers=casa).status_code == 422
    assert (
        api.client.post(
            "/categories", json={"name": "X", "direction": "transfer"}, headers=casa
        ).status_code
        == 422
    )
