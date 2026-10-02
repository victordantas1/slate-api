import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, insert, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.core.config import get_settings
from app.db.models import Category, Household


def _database_url() -> str:
    # A fixture `alembic_config` (tests/conftest.py) aponta DATABASE_URL para o container.
    url = get_settings().database_url
    assert url is not None
    return url


async def _insert_household(conn: AsyncConnection, name: str = "Casa") -> uuid.UUID:
    result = await conn.execute(insert(Household).values(name=name).returning(Household.id))
    return result.scalar_one()


async def _insert_category(conn: AsyncConnection, **values: Any) -> uuid.UUID:
    values.setdefault("direction", "expense")
    result = await conn.execute(insert(Category).values(**values).returning(Category.id))
    return result.scalar_one()


async def _assert_rejected(
    conn: AsyncConnection,
    statement: Callable[[], Awaitable[Any]],
    error: type[Exception] = DBAPIError,
    match: str | None = None,
) -> None:
    with pytest.raises(error, match=match):
        async with conn.begin_nested():
            await statement()


async def _in_rolled_back_transaction(
    check: Callable[[AsyncConnection, uuid.UUID], Awaitable[None]],
) -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                await check(conn, await _insert_household(conn))
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def _run(
    alembic_config: Config, check: Callable[[AsyncConnection, uuid.UUID], Awaitable[None]]
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_in_rolled_back_transaction(check))


async def _check_third_level(conn: AsyncConnection, household_id: uuid.UUID) -> None:
    root = await _insert_category(conn, household_id=household_id, name="Moradia")
    child = await _insert_category(conn, household_id=household_id, name="Aluguel", parent_id=root)

    await _assert_rejected(
        conn,
        lambda: _insert_category(conn, household_id=household_id, name="Multa", parent_id=child),
        match="terceiro nível",
    )

    other_root = await _insert_category(conn, household_id=household_id, name="Lazer")
    await _assert_rejected(
        conn,
        lambda: conn.execute(
            update(Category).where(Category.id == root).values(parent_id=other_root)
        ),
        match="terceiro nível",
    )

    leaf = await _insert_category(
        conn, household_id=household_id, name="Cinema", parent_id=other_root
    )
    await _assert_rejected(
        conn,
        lambda: conn.execute(update(Category).where(Category.id == leaf).values(parent_id=child)),
        match="terceiro nível",
    )

    await _assert_rejected(
        conn,
        lambda: conn.execute(
            update(Category).where(Category.id == other_root).values(parent_id=other_root)
        ),
        error=IntegrityError,
    )


def test_third_level_is_rejected(alembic_config: Config) -> None:
    _run(alembic_config, _check_third_level)


async def _check_child_matches_parent(conn: AsyncConnection, household_id: uuid.UUID) -> None:
    root = await _insert_category(conn, household_id=household_id, name="Moradia")
    other_household = await _insert_household(conn, "Outra casa")

    await _assert_rejected(
        conn,
        lambda: _insert_category(
            conn, household_id=other_household, name="Aluguel", parent_id=root
        ),
        match="household",
    )
    await _assert_rejected(
        conn,
        lambda: _insert_category(
            conn, household_id=household_id, name="Salário", parent_id=root, direction="income"
        ),
        match="direction",
    )

    await _insert_category(conn, household_id=household_id, name="Aluguel", parent_id=root)
    await _assert_rejected(
        conn,
        lambda: conn.execute(
            update(Category).where(Category.id == root).values(direction="income")
        ),
        match="direction",
    )


def test_child_must_match_parent_household_and_direction(alembic_config: Config) -> None:
    _run(alembic_config, _check_child_matches_parent)


async def _check_no_physical_delete(conn: AsyncConnection, household_id: uuid.UUID) -> None:
    category_id = await _insert_category(conn, household_id=household_id, name="Moradia")

    await _assert_rejected(
        conn,
        lambda: conn.execute(delete(Category).where(Category.id == category_id)),
        match="archived",
    )

    await conn.execute(update(Category).where(Category.id == category_id).values(archived=True))
    archived = (
        await conn.execute(select(Category.archived).where(Category.id == category_id))
    ).scalar_one()
    assert archived is True


def test_physical_delete_is_rejected_but_archive_works(alembic_config: Config) -> None:
    _run(alembic_config, _check_no_physical_delete)


async def _check_household_cascade(conn: AsyncConnection, household_id: uuid.UUID) -> None:
    root = await _insert_category(conn, household_id=household_id, name="Moradia")
    await _insert_category(conn, household_id=household_id, name="Aluguel", parent_id=root)

    await conn.execute(delete(Household).where(Household.id == household_id))

    remaining = (
        await conn.execute(select(Category.id).where(Category.household_id == household_id))
    ).all()
    assert remaining == []


def test_household_delete_cascades_to_categories(alembic_config: Config) -> None:
    _run(alembic_config, _check_household_cascade)


async def _check_unique_name(conn: AsyncConnection, household_id: uuid.UUID) -> None:
    root = await _insert_category(conn, household_id=household_id, name="Moradia")
    await _assert_rejected(
        conn,
        lambda: _insert_category(conn, household_id=household_id, name="Moradia"),
        error=IntegrityError,
    )

    await _insert_category(conn, household_id=household_id, name="Outros", parent_id=root)
    await _assert_rejected(
        conn,
        lambda: _insert_category(conn, household_id=household_id, name="Outros", parent_id=root),
        error=IntegrityError,
    )

    other_root = await _insert_category(conn, household_id=household_id, name="Lazer")
    await _insert_category(conn, household_id=household_id, name="Outros", parent_id=other_root)
    await _insert_category(conn, household_id=household_id, name="Outros")

    other_household = await _insert_household(conn, "Outra casa")
    await _insert_category(conn, household_id=other_household, name="Moradia")


def test_name_is_unique_per_household_and_parent(alembic_config: Config) -> None:
    _run(alembic_config, _check_unique_name)


async def _check_direction(conn: AsyncConnection, household_id: uuid.UUID) -> None:
    await _insert_category(conn, household_id=household_id, name="Moradia", direction="expense")
    await _insert_category(conn, household_id=household_id, name="Salário", direction="income")
    await _assert_rejected(
        conn,
        lambda: _insert_category(
            conn, household_id=household_id, name="Transferência", direction="transfer"
        ),
        error=IntegrityError,
    )


def test_direction_rejects_unknown_values(alembic_config: Config) -> None:
    _run(alembic_config, _check_direction)


async def _schema_objects() -> tuple[set[str], set[str]]:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            tables = set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
            functions = set(
                (
                    await conn.execute(
                        text(
                            "SELECT proname FROM pg_proc "
                            "WHERE pronamespace = 'public'::regnamespace"
                        )
                    )
                ).scalars()
            )
            return tables, functions
    finally:
        await engine.dispose()


def test_category_migration_downgrade_runs(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    tables, functions = asyncio.run(_schema_objects())
    assert "category" in tables
    assert {"category_enforce_hierarchy", "category_prevent_delete"} <= functions

    # Revisão anterior à de category (account), fixa para não depender de quem é head.
    command.downgrade(alembic_config, "7bedea4f2832")
    tables, functions = asyncio.run(_schema_objects())
    assert "category" not in tables
    assert {"household", "account"} <= tables
    assert not {"category_enforce_hierarchy", "category_prevent_delete"} & functions

    command.upgrade(alembic_config, "head")
    assert "category" in asyncio.run(_schema_objects())[0]
