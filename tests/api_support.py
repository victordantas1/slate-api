"""Apoio aos testes HTTP de entries e mês, sobre o `_Api` dos testes de commitments."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Account, Category, Entry
from app.db.session import get_engine
from app.main import app
from tests.test_commitments_api import SECRET, _Api, _Household


@contextmanager
def open_api(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    """Mesmo ciclo do fixture `api` dos commitments, apagando as households no fim."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    get_engine.cache_clear()
    try:
        with TestClient(app) as client:
            test_api = _Api(client, database_url)
            try:
                yield test_api
            finally:
                test_api.cleanup()
    finally:
        get_engine.cache_clear()


def create(api: _Api, household: _Household, **overrides: Any) -> dict[str, Any]:
    response = api.client.post(
        "/commitments", json=household.body(**overrides), headers=household.headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def category(
    api: _Api,
    household: _Household,
    name: str,
    direction: str = "expense",
    *,
    archived: bool = False,
) -> uuid.UUID:
    household_id = household_of(api, household)

    async def _create(conn: AsyncConnection) -> uuid.UUID:
        return (
            await conn.execute(
                insert(Category)
                .values(
                    household_id=household_id, name=name, direction=direction, archived=archived
                )
                .returning(Category.id)
            )
        ).scalar_one()

    return api._run(_create)


def account(api: _Api, household: _Household, name: str, kind: str = "checking") -> uuid.UUID:
    household_id = household_of(api, household)

    async def _create(conn: AsyncConnection) -> uuid.UUID:
        member_id = await conn.scalar(
            select(Account.owner_member_id).where(Account.id == household.account_id)
        )
        return (
            await conn.execute(
                insert(Account)
                .values(
                    household_id=household_id,
                    name=name,
                    kind=kind,
                    holder_kind="member",
                    owner_member_id=member_id,
                    first_installment_offset=0,
                )
                .returning(Account.id)
            )
        ).scalar_one()

    return api._run(_create)


def household_of(api: _Api, household: _Household) -> uuid.UUID:
    async def _get(conn: AsyncConnection) -> uuid.UUID:
        return (
            await conn.execute(
                select(Account.household_id).where(Account.id == household.account_id)
            )
        ).scalar_one()

    return api._run(_get)


def entry_row(api: _Api, entry_id: str) -> dict[str, Any]:
    async def _get(conn: AsyncConnection) -> dict[str, Any]:
        row = (
            (await conn.execute(select(Entry).where(Entry.id == uuid.UUID(entry_id))))
            .mappings()
            .one()
        )
        return dict(row)

    return api._run(_get)
