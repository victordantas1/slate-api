import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.core.config import get_settings
from app.db.models import Account, ExternalHolder, Household, Member


def _database_url() -> str:
    # A fixture `alembic_config` (tests/conftest.py) aponta DATABASE_URL para o container.
    url = get_settings().database_url
    assert url is not None
    return url


class _Holders:
    def __init__(self, household_id: uuid.UUID, member_id: uuid.UUID, external_id: uuid.UUID):
        self.household_id = household_id
        self.member_id = member_id
        self.external_id = external_id


async def _insert_holders(conn: AsyncConnection) -> _Holders:
    household_id = (
        await conn.execute(insert(Household).values(name="Casa").returning(Household.id))
    ).scalar_one()
    member_id = (
        await conn.execute(
            insert(Member)
            .values(household_id=household_id, supabase_user_id=uuid.uuid4(), name="Ana")
            .returning(Member.id)
        )
    ).scalar_one()
    external_id = (
        await conn.execute(
            insert(ExternalHolder)
            .values(household_id=household_id, name="Vó")
            .returning(ExternalHolder.id)
        )
    ).scalar_one()
    return _Holders(household_id, member_id, external_id)


async def _insert_account(conn: AsyncConnection, **values: Any) -> uuid.UUID:
    result = await conn.execute(insert(Account).values(**values).returning(Account.id))
    return result.scalar_one()


async def _assert_rejected(conn: AsyncConnection, **values: Any) -> None:
    with pytest.raises(IntegrityError):
        async with conn.begin_nested():
            await _insert_account(conn, **values)


async def _in_rolled_back_transaction(
    check: Callable[[AsyncConnection, _Holders], Awaitable[None]],
) -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                await check(conn, await _insert_holders(conn))
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


async def _check_member_holder(conn: AsyncConnection, h: _Holders) -> None:
    base = {"household_id": h.household_id, "name": "Conta", "kind": "checking"}
    await _insert_account(conn, **base, holder_kind="member", owner_member_id=h.member_id)
    await _assert_rejected(conn, **base, holder_kind="member")
    await _assert_rejected(
        conn,
        **base,
        holder_kind="external",
        owner_member_id=h.member_id,
        external_holder_id=h.external_id,
    )


def test_member_holder_requires_owner_member_id(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_in_rolled_back_transaction(_check_member_holder))


async def _check_external_holder(conn: AsyncConnection, h: _Holders) -> None:
    base = {"household_id": h.household_id, "name": "Cartão da Vó", "kind": "credit_card"}
    await _insert_account(conn, **base, holder_kind="external", external_holder_id=h.external_id)
    await _assert_rejected(conn, **base, holder_kind="external")
    await _assert_rejected(
        conn,
        **base,
        holder_kind="member",
        owner_member_id=h.member_id,
        external_holder_id=h.external_id,
    )


def test_external_holder_requires_external_holder_id(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_in_rolled_back_transaction(_check_external_holder))


async def _check_first_installment_offset(conn: AsyncConnection, h: _Holders) -> None:
    base = {
        "household_id": h.household_id,
        "name": "Cartão",
        "kind": "credit_card",
        "holder_kind": "member",
        "owner_member_id": h.member_id,
    }
    account_id = await _insert_account(conn, **base)
    row = (await conn.execute(select(Account).where(Account.id == account_id))).one()
    assert row.first_installment_offset == 1
    assert row.archived is False
    await _assert_rejected(conn, **base, first_installment_offset=None)


def test_first_installment_offset_defaults_to_one_and_is_not_null(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_in_rolled_back_transaction(_check_first_installment_offset))


async def _check_enums_and_day_ranges(conn: AsyncConnection, h: _Holders) -> None:
    base = {
        "household_id": h.household_id,
        "name": "Conta",
        "kind": "credit_card",
        "holder_kind": "member",
        "owner_member_id": h.member_id,
    }
    await _assert_rejected(conn, **{**base, "kind": "savings"})
    await _assert_rejected(conn, **{**base, "holder_kind": "other"})
    await _assert_rejected(conn, **base, closing_day=0)
    await _assert_rejected(conn, **base, due_day=32)
    await _insert_account(conn, **base, closing_day=31, due_day=1)


def test_kind_holder_kind_and_days_reject_invalid_values(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_in_rolled_back_transaction(_check_enums_and_day_ranges))


async def _table_names() -> set[str]:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
    finally:
        await engine.dispose()


def test_account_migration_downgrade_runs(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    # Revisão anterior à de account, fixa: `-1` passa a reverter quem vier depois dela.
    command.downgrade(alembic_config, "019fca530b88")
    tables = asyncio.run(_table_names())
    assert "account" not in tables
    assert {"household", "member", "external_holder"} <= tables
    command.upgrade(alembic_config, "head")
    assert "account" in asyncio.run(_table_names())
