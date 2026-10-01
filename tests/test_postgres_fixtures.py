from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


async def test_db_engine_reaches_postgres_17(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()

    assert int(version) >= 170000


async def test_db_session_starts_on_migrated_schema(db_session: AsyncSession) -> None:
    applied = (
        await db_session.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL"))
    ).scalar_one()

    assert applied is True


async def test_db_session_rolls_back_between_tests_part_1(db_session: AsyncSession) -> None:
    await db_session.execute(text("CREATE TABLE _isolation_probe (id int)"))
    await db_session.commit()


async def test_db_session_rolls_back_between_tests_part_2(db_session: AsyncSession) -> None:
    exists = (
        await db_session.execute(text("SELECT to_regclass('_isolation_probe') IS NOT NULL"))
    ).scalar_one()

    assert exists is False
