import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.core.config import get_settings
from app.db.models import Account, Category, Commitment, Entry, Household, Member


def _database_url() -> str:
    # A fixture `alembic_config` (tests/conftest.py) aponta DATABASE_URL para o container.
    url = get_settings().database_url
    assert url is not None
    return url


class _Ctx:
    """Uma household com uma conta e uma categoria, prontas para receber commitments."""

    def __init__(self, household_id: uuid.UUID, account_id: uuid.UUID, category_id: uuid.UUID):
        self.household_id = household_id
        self.account_id = account_id
        self.category_id = category_id


async def _insert_ctx(conn: AsyncConnection, name: str = "Casa") -> _Ctx:
    household_id = (
        await conn.execute(insert(Household).values(name=name).returning(Household.id))
    ).scalar_one()
    member_id = (
        await conn.execute(
            insert(Member)
            .values(household_id=household_id, supabase_user_id=uuid.uuid4(), name="Ana")
            .returning(Member.id)
        )
    ).scalar_one()
    account_id = (
        await conn.execute(
            insert(Account)
            .values(
                household_id=household_id,
                name="Nubank",
                kind="credit_card",
                holder_kind="member",
                owner_member_id=member_id,
            )
            .returning(Account.id)
        )
    ).scalar_one()
    category_id = (
        await conn.execute(
            insert(Category)
            .values(household_id=household_id, name="Mercado", direction="expense")
            .returning(Category.id)
        )
    ).scalar_one()
    return _Ctx(household_id, account_id, category_id)


async def _insert_commitment(conn: AsyncConnection, ctx: _Ctx, **values: Any) -> uuid.UUID:
    row: dict[str, Any] = {
        "household_id": ctx.household_id,
        "account_id": ctx.account_id,
        "category_id": ctx.category_id,
        "kind": "installment",
        "description": "Geladeira",
        "purchase_date": date(2026, 8, 15),
        "total_amount": Decimal("1000.00"),
        "installment_count": 3,
    }
    row.update(values)
    result = await conn.execute(insert(Commitment).values(**row).returning(Commitment.id))
    return result.scalar_one()


async def _insert_entry(
    conn: AsyncConnection, ctx: _Ctx, commitment_id: uuid.UUID, **values: Any
) -> uuid.UUID:
    row: dict[str, Any] = {
        "household_id": ctx.household_id,
        "commitment_id": commitment_id,
        "category_id": ctx.category_id,
        "seq": 1,
        "competencia": date(2026, 9, 1),
        "amount": Decimal("333.34"),
    }
    row.update(values)
    result = await conn.execute(insert(Entry).values(**row).returning(Entry.id))
    return result.scalar_one()


async def _assert_rejected(
    conn: AsyncConnection, statement: Callable[[], Awaitable[Any]], match: str | None = None
) -> None:
    with pytest.raises(IntegrityError, match=match):
        async with conn.begin_nested():
            await statement()


async def _in_rolled_back_transaction(
    check: Callable[[AsyncConnection, _Ctx], Awaitable[None]],
) -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                await check(conn, await _insert_ctx(conn))
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def _run(alembic_config: Config, check: Callable[[AsyncConnection, _Ctx], Awaitable[None]]) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_in_rolled_back_transaction(check))


async def _check_required_references(conn: AsyncConnection, ctx: _Ctx) -> None:
    await _assert_rejected(
        conn, lambda: _insert_commitment(conn, ctx, account_id=None), match="account_id"
    )
    commitment_id = await _insert_commitment(conn, ctx)
    await _assert_rejected(
        conn,
        lambda: _insert_entry(conn, ctx, commitment_id, category_id=None),
        match="category_id",
    )
    await _assert_rejected(
        conn,
        lambda: _insert_entry(conn, ctx, None),  # type: ignore[arg-type]
        match="commitment_id",
    )


def test_account_and_category_are_required(alembic_config: Config) -> None:
    _run(alembic_config, _check_required_references)


async def _check_unique_per_commitment(conn: AsyncConnection, ctx: _Ctx) -> None:
    commitment_id = await _insert_commitment(conn, ctx)
    await _insert_entry(conn, ctx, commitment_id, seq=1, competencia=date(2026, 9, 1))
    await _insert_entry(conn, ctx, commitment_id, seq=2, competencia=date(2026, 10, 1))

    await _assert_rejected(
        conn,
        lambda: _insert_entry(conn, ctx, commitment_id, seq=3, competencia=date(2026, 9, 1)),
        match="uq_entry_commitment_id_competencia",
    )
    await _assert_rejected(
        conn,
        lambda: _insert_entry(conn, ctx, commitment_id, seq=2, competencia=date(2026, 11, 1)),
        match="uq_entry_commitment_id_seq",
    )

    # Outro commitment pode repetir seq e competência.
    other = await _insert_commitment(conn, ctx)
    await _insert_entry(conn, ctx, other, seq=1, competencia=date(2026, 9, 1))


def test_competencia_and_seq_are_unique_per_commitment(alembic_config: Config) -> None:
    _run(alembic_config, _check_unique_per_commitment)


async def _check_idempotency_key(conn: AsyncConnection, ctx: _Ctx) -> None:
    first = await _insert_commitment(conn, ctx)
    second = await _insert_commitment(conn, ctx)
    await _insert_entry(conn, ctx, first, idempotency_key="fc-1", source="fincoach")
    await _assert_rejected(
        conn,
        lambda: _insert_entry(conn, ctx, second, idempotency_key="fc-1", source="fincoach"),
        match="uq_entry_household_id_idempotency_key",
    )

    # Lançamento manual não tem chave: NULL repetido passa.
    await _insert_entry(conn, ctx, first, seq=2, competencia=date(2026, 10, 1))
    await _insert_entry(conn, ctx, second, seq=2, competencia=date(2026, 10, 1))

    # A mesma chave em outra household passa.
    other_ctx = await _insert_ctx(conn, name="Outra casa")
    other_commitment = await _insert_commitment(conn, other_ctx)
    await _insert_entry(conn, other_ctx, other_commitment, idempotency_key="fc-1")


def test_idempotency_key_is_unique_per_household(alembic_config: Config) -> None:
    _run(alembic_config, _check_idempotency_key)


async def _check_kind_amounts(conn: AsyncConnection, ctx: _Ctx) -> None:
    await _insert_commitment(
        conn,
        ctx,
        kind="recurring",
        description="Streaming",
        total_amount=None,
        installment_count=None,
        recurring_amount=Decimal("39.90"),
        end_date=date(2027, 8, 1),
    )
    await _insert_commitment(
        conn, ctx, kind="single", total_amount=Decimal("80.00"), installment_count=1
    )

    recurring = {
        "kind": "recurring",
        "total_amount": None,
        "installment_count": None,
        "recurring_amount": Decimal("39.90"),
    }
    rejected: list[tuple[dict[str, Any], str]] = [
        ({**recurring, "total_amount": Decimal("100.00")}, "ck_commitment_recurring_amounts"),
        ({**recurring, "installment_count": 12}, "ck_commitment_recurring_amounts"),
        ({**recurring, "recurring_amount": None}, "ck_commitment_recurring_amounts"),
        ({"total_amount": None}, "ck_commitment_recurring_amounts"),
        ({"installment_count": None}, "ck_commitment_recurring_amounts"),
        ({"recurring_amount": Decimal("10.00")}, "ck_commitment_recurring_amounts"),
        ({"kind": "single", "installment_count": 2}, "ck_commitment_single_is_one_installment"),
        ({"kind": "loan"}, "ck_commitment_kind"),
        ({"total_amount": Decimal("0")}, "ck_commitment_total_amount_positive"),
        ({"installment_count": 0}, "ck_commitment_installment_count_positive"),
        ({"end_date": date(2027, 1, 1)}, "ck_commitment_end_date_only_recurring"),
        ({**recurring, "end_date": date(2026, 1, 1)}, "ck_commitment_end_date_after_start"),
        ({"status": "paused"}, "ck_commitment_status"),
        (
            {**recurring, "recurring_amount": Decimal("0")},
            "ck_commitment_recurring_amount_positive",
        ),
    ]
    for values, constraint in rejected:
        await _assert_rejected(
            conn, partial(_insert_commitment, conn, ctx, **values), match=constraint
        )


def test_recurring_iff_total_amount_is_null(alembic_config: Config) -> None:
    _run(alembic_config, _check_kind_amounts)


async def _check_competencia_first_day(conn: AsyncConnection, ctx: _Ctx) -> None:
    commitment_id = await _insert_commitment(conn, ctx)
    await _assert_rejected(
        conn,
        lambda: _insert_entry(conn, ctx, commitment_id, competencia=date(2026, 9, 15)),
        match="ck_entry_competencia_first_day",
    )
    await _insert_entry(conn, ctx, commitment_id, competencia=date(2026, 9, 1))


def test_competencia_must_be_first_day_of_month(alembic_config: Config) -> None:
    _run(alembic_config, _check_competencia_first_day)


async def _check_entry_values(conn: AsyncConnection, ctx: _Ctx) -> None:
    commitment_id = await _insert_commitment(conn, ctx)
    now = datetime.now(UTC)

    rejected: list[tuple[dict[str, Any], str]] = [
        ({"status": "pago"}, "ck_entry_paid_at_iff_pago"),
        ({"paid_at": now}, "ck_entry_paid_at_iff_pago"),
        ({"status": "atrasado"}, "ck_entry_status"),
        ({"source": "planilha"}, "ck_entry_source"),
        ({"seq": 0}, "ck_entry_seq_positive"),
        ({"amount": Decimal("-1.00")}, "ck_entry_amount_not_negative"),
    ]
    for values, constraint in rejected:
        await _assert_rejected(
            conn, partial(_insert_entry, conn, ctx, commitment_id, **values), match=constraint
        )

    entry_id = await _insert_entry(conn, ctx, commitment_id, amount=Decimal("0.00"))
    row = (
        await conn.execute(
            select(Entry.status, Entry.source, Entry.edited_manually).where(Entry.id == entry_id)
        )
    ).one()
    assert tuple(row) == ("previsto", "manual", False)
    await _insert_entry(
        conn, ctx, commitment_id, seq=2, competencia=date(2026, 10, 1), status="pago", paid_at=now
    )


def test_entry_status_source_and_paid_at(alembic_config: Config) -> None:
    _run(alembic_config, _check_entry_values)


async def _check_household_coherence(conn: AsyncConnection, ctx: _Ctx) -> None:
    other = await _insert_ctx(conn, name="Outra casa")
    fk = "foreign key"

    await _assert_rejected(
        conn, lambda: _insert_commitment(conn, ctx, account_id=other.account_id), match=fk
    )
    await _assert_rejected(
        conn, lambda: _insert_commitment(conn, ctx, category_id=other.category_id), match=fk
    )

    commitment_id = await _insert_commitment(conn, ctx)
    await _assert_rejected(
        conn,
        lambda: _insert_entry(conn, ctx, commitment_id, category_id=other.category_id),
        match=fk,
    )
    # Entry com household forjada não pendura em commitment de outra household.
    await _assert_rejected(
        conn,
        lambda: _insert_entry(
            conn, other, commitment_id, category_id=other.category_id, idempotency_key="x"
        ),
        match=fk,
    )


def test_household_must_match_across_references(alembic_config: Config) -> None:
    _run(alembic_config, _check_household_coherence)


async def _check_deletes(conn: AsyncConnection, ctx: _Ctx) -> None:
    commitment_id = await _insert_commitment(conn, ctx)
    entry_id = await _insert_entry(conn, ctx, commitment_id)

    # Conta e categoria em uso não somem.
    await _assert_rejected(
        conn, lambda: conn.execute(delete(Account).where(Account.id == ctx.account_id))
    )

    # Delete de commitment leva as entries.
    await conn.execute(delete(Commitment).where(Commitment.id == commitment_id))
    assert (await conn.execute(select(Entry.id).where(Entry.id == entry_id))).first() is None

    # Delete de household leva tudo, apesar das FKs NO ACTION para conta e categoria.
    commitment_id = await _insert_commitment(conn, ctx)
    await _insert_entry(conn, ctx, commitment_id)
    await conn.execute(delete(Household).where(Household.id == ctx.household_id))
    for table in (Commitment, Entry, Account, Category):
        remaining = await conn.execute(
            select(table.id).where(table.household_id == ctx.household_id)
        )
        assert remaining.first() is None


def test_deletes_cascade_and_restrict(alembic_config: Config) -> None:
    _run(alembic_config, _check_deletes)


async def _indexes() -> dict[str, list[str | None]]:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            found = await conn.run_sync(lambda sync: inspect(sync).get_indexes("entry"))
    finally:
        await engine.dispose()
    return {ix["name"]: list(ix["column_names"]) for ix in found if ix["name"]}


def test_entry_indexes_exist(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    columns = list(asyncio.run(_indexes()).values())
    assert ["household_id", "competencia"] in columns
    assert ["commitment_id"] in columns
    assert ["household_id", "category_id", "competencia"] in columns


async def _schema_objects() -> tuple[set[str], set[str]]:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            tables = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            constraints = await conn.execute(text("SELECT conname FROM pg_constraint"))
            return {r[0] for r in tables}, {r[0] for r in constraints}
    finally:
        await engine.dispose()


async def _tables() -> set[str]:
    return (await _schema_objects())[0]


COMPOSITE_FK_TARGETS = {"uq_account_id_household_id", "uq_category_id_household_id"}


def test_commitment_entry_migration_downgrade_runs(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    tables, constraints = asyncio.run(_schema_objects())
    assert {"commitment", "entry"} <= tables
    assert COMPOSITE_FK_TARGETS <= constraints

    # Revisão anterior (category), fixa para não depender de quem é head.
    command.downgrade(alembic_config, "9fd2e8412e8f")
    tables, constraints = asyncio.run(_schema_objects())
    assert not {"commitment", "entry"} & tables
    assert not COMPOSITE_FK_TARGETS & constraints
    assert {"account", "category"} <= tables

    command.upgrade(alembic_config, "head")
    assert {"commitment", "entry"} <= asyncio.run(_tables())
