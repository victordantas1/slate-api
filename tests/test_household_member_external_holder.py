import asyncio
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.core.config import get_settings
from app.db.models import ExternalHolder, Household, Member


def _database_url() -> str:
    # A fixture `alembic_config` (tests/conftest.py) aponta DATABASE_URL para o container.
    url = get_settings().database_url
    assert url is not None
    return url


async def _insert_household(conn: AsyncConnection, name: str) -> uuid.UUID:
    result = await conn.execute(insert(Household).values(name=name).returning(Household.id))
    return result.scalar_one()


async def _assert_member_supabase_user_id_is_unique() -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                household_id = await _insert_household(conn, "Casa 1")
                shared_supabase_user_id = uuid.uuid4()
                await conn.execute(
                    insert(Member).values(
                        household_id=household_id,
                        supabase_user_id=shared_supabase_user_id,
                        name="Primeiro membro",
                    )
                )
                with pytest.raises(IntegrityError):
                    async with conn.begin_nested():
                        await conn.execute(
                            insert(Member).values(
                                household_id=household_id,
                                supabase_user_id=shared_supabase_user_id,
                                name="Segundo membro",
                            )
                        )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def test_member_supabase_user_id_is_unique(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_assert_member_supabase_user_id_is_unique())


async def _assert_external_holder_name_is_unique_per_household() -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                household_a = await _insert_household(conn, "Casa A")
                await conn.execute(
                    insert(ExternalHolder).values(household_id=household_a, name="Vó")
                )
                with pytest.raises(IntegrityError):
                    async with conn.begin_nested():
                        await conn.execute(
                            insert(ExternalHolder).values(household_id=household_a, name="Vó")
                        )

                household_b = await _insert_household(conn, "Casa B")
                await conn.execute(
                    insert(ExternalHolder).values(household_id=household_b, name="Vó")
                )
                rows = (
                    await conn.execute(select(ExternalHolder).where(ExternalHolder.name == "Vó"))
                ).all()
                assert {row.household_id for row in rows} == {household_a, household_b}
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def test_external_holder_name_is_unique_per_household(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_assert_external_holder_name_is_unique_per_household())


def test_migration_downgrade_runs(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "-1")
    command.upgrade(alembic_config, "head")
