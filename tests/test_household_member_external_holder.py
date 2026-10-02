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
from tests.test_db_migrations import REPO_ROOT

DATABASE_URL = get_settings().database_url

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "requer DATABASE_URL configurada para um Postgres real; pulado até a suíte "
        "de testcontainers (issue #15) padronizar isso em todo ambiente"
    ),
)


@pytest.fixture
def alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    assert DATABASE_URL is not None
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    return cfg


async def _insert_household(conn: AsyncConnection, name: str) -> uuid.UUID:
    result = await conn.execute(insert(Household).values(name=name).returning(Household.id))
    return result.scalar_one()


async def _assert_member_supabase_user_id_is_unique() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
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
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
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
