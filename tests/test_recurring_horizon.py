import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Commitment, Entry
from app.services.materialization import (
    AccountNotFoundError,
    create_recurring_commitment,
    extend_recurring_horizon,
)
from tests.test_materialization import _create, _Ctx, _ctx, _entries

TODAY = date(2026, 10, 2)
AMOUNT = Decimal("39.90")


@pytest.fixture
async def ctx(db_session: AsyncSession) -> _Ctx:
    return await _ctx(db_session)


async def _recurring(session: AsyncSession, ctx: _Ctx, **overrides: Any) -> Commitment:
    values: dict[str, Any] = {
        "household_id": ctx.household_id,
        "account_id": ctx.account_id,
        "category_id": ctx.category_id,
        "description": "Streaming",
        "purchase_date": date(2026, 10, 10),
        "recurring_amount": AMOUNT,
        "end_date": None,
        "today": TODAY,
    }
    values.update(overrides)
    return await create_recurring_commitment(session, **values)


async def _bare_recurring(session: AsyncSession, ctx: _Ctx, **overrides: Any) -> uuid.UUID:
    """Commitment recorrente sem entries, gravado direto na tabela."""
    values: dict[str, Any] = {
        "household_id": ctx.household_id,
        "account_id": ctx.account_id,
        "category_id": ctx.category_id,
        "kind": "recurring",
        "description": "Academia",
        "purchase_date": date(2026, 10, 10),
        "recurring_amount": AMOUNT,
    }
    values.update(overrides)
    return (
        await session.execute(insert(Commitment).values(**values).returning(Commitment.id))
    ).scalar_one()


def _snapshot(entries: list[Entry]) -> list[tuple[Any, ...]]:
    return [
        (
            e.id,
            e.seq,
            e.competencia,
            e.amount,
            e.status,
            e.source,
            e.edited_manually,
            e.category_id,
            e.created_at,
        )
        for e in entries
    ]


async def _fresh_entries(session: AsyncSession, commitment_id: uuid.UUID) -> list[Entry]:
    """Entries relidas do banco, sem o cache da identity map."""
    result = await session.scalars(
        select(Entry)
        .where(Entry.commitment_id == commitment_id)
        .order_by(Entry.seq)
        .execution_options(populate_existing=True)
    )
    return list(result)


async def test_create_materializes_until_horizon(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx)

    assert commitment.kind == "recurring"
    assert commitment.status == "active"
    entries = await _fresh_entries(db_session, commitment.id)
    # Offset 1: de novembro/2026 até outubro/2028 (hoje + 24 meses), inclusive.
    assert len(entries) == 24
    assert [e.seq for e in entries] == list(range(1, 25))
    assert entries[0].competencia == date(2026, 11, 1)
    assert entries[-1].competencia == date(2028, 10, 1)
    for entry in entries:
        assert entry.amount == AMOUNT
        assert entry.status == "previsto"
        assert entry.source == "manual"
        assert entry.edited_manually is False
        assert entry.household_id == ctx.household_id
        assert entry.category_id == ctx.category_id


async def test_invariant_3_running_n_times_equals_running_once(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment_id = await _bare_recurring(db_session, ctx)

    first = await extend_recurring_horizon(db_session, today=TODAY)
    once = _snapshot(await _fresh_entries(db_session, commitment_id))
    again = [await extend_recurring_horizon(db_session, today=TODAY) for _ in range(3)]

    assert first == 24
    assert again == [0, 0, 0]
    assert _snapshot(await _fresh_entries(db_session, commitment_id)) == once


async def test_horizon_rolls_forward_one_month(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx)
    before = _snapshot(await _fresh_entries(db_session, commitment.id))

    inserted = await extend_recurring_horizon(db_session, today=date(2026, 11, 30))

    entries = await _fresh_entries(db_session, commitment.id)
    assert inserted == 1
    assert _snapshot(entries[:-1]) == before
    assert (entries[-1].seq, entries[-1].competencia) == (25, date(2028, 11, 1))


async def test_respects_end_date(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx, end_date=date(2027, 3, 31))

    await extend_recurring_horizon(db_session, today=date(2027, 6, 1))

    entries = await _fresh_entries(db_session, commitment.id)
    # Última cobrança em março/2027; com offset 1, última competência em abril.
    assert [e.competencia for e in entries] == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
        date(2027, 3, 1),
        date(2027, 4, 1),
    ]


@pytest.mark.parametrize("status", ["cancelled", "settled"])
async def test_ignores_cancelled_and_settled(
    db_session: AsyncSession, ctx: _Ctx, status: str
) -> None:
    commitment_id = await _bare_recurring(db_session, ctx, status=status)

    inserted = await extend_recurring_horizon(db_session, today=TODAY)

    assert inserted == 0
    assert await _entries(db_session, commitment_id) == []


async def test_inactive_keeps_existing_entries(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx)
    before = _snapshot(await _fresh_entries(db_session, commitment.id))
    await db_session.execute(
        update(Commitment).where(Commitment.id == commitment.id).values(status="cancelled")
    )

    assert await extend_recurring_horizon(db_session, today=date(2027, 10, 2)) == 0
    assert _snapshot(await _fresh_entries(db_session, commitment.id)) == before


async def test_ignores_installments(db_session: AsyncSession, ctx: _Ctx) -> None:
    installment = await _create(db_session, ctx)
    before = _snapshot(await _fresh_entries(db_session, installment.id))

    assert await extend_recurring_horizon(db_session, today=TODAY) == 0
    assert _snapshot(await _fresh_entries(db_session, installment.id)) == before


async def test_never_overwrites_existing_entry(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx)
    edited = (await _fresh_entries(db_session, commitment.id))[2]
    await db_session.execute(
        update(Entry)
        .where(Entry.id == edited.id)
        .values(
            amount=Decimal("45.00"),
            status="pago",
            paid_at=datetime(2027, 1, 5, tzinfo=UTC),
            edited_manually=True,
        )
    )
    # O valor do commitment muda depois: entries existentes continuam como estão.
    await db_session.execute(
        update(Commitment)
        .where(Commitment.id == commitment.id)
        .values(recurring_amount=Decimal("49.90"))
    )
    before = _snapshot(await _fresh_entries(db_session, commitment.id))

    inserted = await extend_recurring_horizon(db_session, today=date(2026, 11, 2))

    entries = await _fresh_entries(db_session, commitment.id)
    assert inserted == 1
    assert _snapshot(entries[:-1]) == before
    assert (entries[2].amount, entries[2].status) == (Decimal("45.00"), "pago")
    assert entries[-1].amount == Decimal("49.90")


async def test_extend_can_be_scoped_to_commitments(db_session: AsyncSession, ctx: _Ctx) -> None:
    target = await _bare_recurring(db_session, ctx)
    other = await _bare_recurring(db_session, ctx, description="Internet")

    inserted = await extend_recurring_horizon(db_session, today=TODAY, commitment_ids=[target])

    assert inserted == 24
    assert len(await _entries(db_session, target)) == 24
    assert await _entries(db_session, other) == []


async def test_create_rejects_account_of_other_household(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    other = await _ctx(db_session, name="Outra casa")

    with pytest.raises(AccountNotFoundError):
        await _recurring(db_session, ctx, account_id=other.account_id)


async def test_create_respects_offset_zero(db_session: AsyncSession) -> None:
    ctx = await _ctx(db_session, offset=0)

    commitment = await _recurring(db_session, ctx)

    entries = await _fresh_entries(db_session, commitment.id)
    assert entries[0].competencia == date(2026, 10, 1)
    assert entries[-1].competencia == date(2028, 10, 1)
    assert len(entries) == 25


async def test_create_with_future_start_past_horizon_has_no_entries(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _recurring(db_session, ctx, purchase_date=date(2029, 1, 10))

    assert await _entries(db_session, commitment.id) == []
    stored = await db_session.scalar(select(Commitment).where(Commitment.id == commitment.id))
    assert stored is not None
