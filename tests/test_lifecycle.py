import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Category, Commitment, Entry, Household, Member
from app.services.cascade import (
    CommitmentNotFoundError,
    CommitmentStateError,
    reschedule_installments,
)
from app.services.lifecycle import cancel_commitment, end_recurring, settle_if_paid
from app.services.materialization import (
    create_installment_commitment,
    create_recurring_commitment,
    extend_recurring_horizon,
)

TODAY = date(2026, 10, 2)


class _Ctx:
    def __init__(self, household_id: uuid.UUID, account_id: uuid.UUID, category_id: uuid.UUID):
        self.household_id = household_id
        self.account_id = account_id
        self.category_id = category_id


@pytest.fixture
async def ctx(db_session: AsyncSession) -> _Ctx:
    household_id = (
        await db_session.execute(insert(Household).values(name="Casa").returning(Household.id))
    ).scalar_one()
    member_id = (
        await db_session.execute(
            insert(Member)
            .values(household_id=household_id, supabase_user_id=uuid.uuid4(), name="Ana")
            .returning(Member.id)
        )
    ).scalar_one()
    account_id = (
        await db_session.execute(
            insert(Account)
            .values(
                household_id=household_id,
                name="Nubank",
                kind="credit_card",
                holder_kind="member",
                owner_member_id=member_id,
                first_installment_offset=1,
            )
            .returning(Account.id)
        )
    ).scalar_one()
    category_id = (
        await db_session.execute(
            insert(Category)
            .values(household_id=household_id, name="Casa", direction="expense")
            .returning(Category.id)
        )
    ).scalar_one()
    return _Ctx(household_id, account_id, category_id)


async def _installment(session: AsyncSession, ctx: _Ctx, count: int = 10) -> Commitment:
    return await create_installment_commitment(
        session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        kind="installment",
        description="Sofá",
        purchase_date=date(2026, 3, 10),
        total_amount=Decimal("393.00") * count,
        installment_count=count,
    )


async def _recurring(session: AsyncSession, ctx: _Ctx, end_date: date | None = None) -> Commitment:
    # Compra em 2026-01-10, offset 1: primeira competência 2026-02, horizonte até 2028-10.
    return await create_recurring_commitment(
        session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        description="Streaming",
        purchase_date=date(2026, 1, 10),
        recurring_amount=Decimal("55.90"),
        end_date=end_date,
        today=TODAY,
    )


async def _entries(session: AsyncSession, commitment_id: uuid.UUID) -> list[Entry]:
    result = await session.scalars(
        select(Entry)
        .where(Entry.commitment_id == commitment_id)
        .order_by(Entry.seq)
        .execution_options(populate_existing=True)
    )
    return list(result)


async def _set(session: AsyncSession, entry_ids: list[uuid.UUID], **values: Any) -> None:
    await session.execute(update(Entry).where(Entry.id.in_(entry_ids)).values(**values))


async def _pay(session: AsyncSession, entries: list[Entry]) -> None:
    await _set(
        session, [e.id for e in entries], status="pago", paid_at=datetime(2026, 9, 1, tzinfo=UTC)
    )


async def _snapshot(session: AsyncSession, commitment_id: uuid.UUID, *where: Any) -> dict[Any, Any]:
    rows = await session.execute(
        select(Entry.__table__).where(Entry.commitment_id == commitment_id, *where)
    )
    return {row.id: tuple(row) for row in rows}


async def _status(session: AsyncSession, commitment_id: uuid.UUID) -> str:
    stored = await session.scalar(
        select(Commitment)
        .where(Commitment.id == commitment_id)
        .execution_options(populate_existing=True)
    )
    assert stored is not None
    return stored.status


# --- cancelar -----------------------------------------------------------------------


async def test_cancel_keeps_paid_entries_and_removes_forecasts(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[:3])
    paid = await _snapshot(db_session, commitment.id, Entry.status == "pago")

    removed = await cancel_commitment(
        db_session, household_id=ctx.household_id, commitment_id=commitment.id
    )

    assert removed == 7
    assert await _snapshot(db_session, commitment.id) == paid
    assert await _status(db_session, commitment.id) == "cancelled"


async def test_cancel_keeps_confirmed_and_drops_edited_forecasts(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx, count=4)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[:1])
    await _set(db_session, [entries[1].id], status="confirmado")
    await _set(db_session, [entries[2].id], edited_manually=True, amount=Decimal("100.00"))
    kept = await _snapshot(db_session, commitment.id, Entry.status != "previsto")

    removed = await cancel_commitment(
        db_session, household_id=ctx.household_id, commitment_id=commitment.id
    )

    assert removed == 2
    assert await _snapshot(db_session, commitment.id) == kept


async def test_cancelled_recurring_is_not_materialized_again(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _recurring(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[:8])  # 2026-02 a 2026-09
    paid = await _snapshot(db_session, commitment.id, Entry.status == "pago")

    await cancel_commitment(db_session, household_id=ctx.household_id, commitment_id=commitment.id)
    inserted = await extend_recurring_horizon(
        db_session, today=date(2027, 10, 2), commitment_ids=[commitment.id]
    )

    assert inserted == 0
    assert await _snapshot(db_session, commitment.id) == paid


async def test_cancel_refuses_a_commitment_that_is_not_active(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx)
    await cancel_commitment(db_session, household_id=ctx.household_id, commitment_id=commitment.id)

    with pytest.raises(CommitmentStateError):
        await cancel_commitment(
            db_session, household_id=ctx.household_id, commitment_id=commitment.id
        )


async def test_cancel_of_other_household_is_not_found(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _installment(db_session, ctx)

    with pytest.raises(CommitmentNotFoundError):
        await cancel_commitment(db_session, household_id=uuid.uuid4(), commitment_id=commitment.id)
    assert await _status(db_session, commitment.id) == "active"


async def test_reschedule_refuses_a_cancelled_commitment(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx)
    await cancel_commitment(db_session, household_id=ctx.household_id, commitment_id=commitment.id)

    with pytest.raises(CommitmentStateError):
        await reschedule_installments(
            db_session,
            household_id=ctx.household_id,
            commitment_id=commitment.id,
            installment_count=12,
        )
    assert await _entries(db_session, commitment.id) == []


# --- encerrar -----------------------------------------------------------------------


async def test_end_keeps_paid_and_edited_future_entries(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _recurring(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    by_month = {e.competencia: e for e in entries}
    # Pago adiantado e editado à mão, ambos depois do fim.
    await _pay(db_session, [by_month[date(2027, 3, 1)]])
    await _set(
        db_session, [by_month[date(2027, 5, 1)].id], edited_manually=True, amount=Decimal("1.00")
    )
    await _set(db_session, [by_month[date(2027, 6, 1)].id], status="confirmado")
    protected = await _snapshot(
        db_session,
        commitment.id,
        (Entry.status == "pago") | Entry.edited_manually.is_(True),
    )
    before_end = await _snapshot(db_session, commitment.id, Entry.competencia <= date(2026, 12, 1))

    # Última cobrança em 2026-11: última competência 2026-12 (offset 1).
    removed = await end_recurring(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        end_date=date(2026, 11, 20),
        today=TODAY,
    )

    after = await _snapshot(db_session, commitment.id)
    assert after == before_end | protected
    assert removed == len(entries) - len(after)
    stored = await db_session.get(Commitment, commitment.id, populate_existing=True)
    assert stored is not None
    assert stored.end_date == date(2026, 11, 20)
    assert stored.status == "active"


async def test_end_later_materializes_the_new_months(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx, end_date=date(2026, 5, 10))
    assert (await _entries(db_session, commitment.id))[-1].competencia == date(2026, 6, 1)

    removed = await end_recurring(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        end_date=date(2026, 8, 10),
        today=TODAY,
    )

    entries = await _entries(db_session, commitment.id)
    assert removed == 0
    assert [e.competencia.month for e in entries] == [2, 3, 4, 5, 6, 7, 8, 9]


async def test_end_refuses_installment_and_inactive(db_session: AsyncSession, ctx: _Ctx) -> None:
    installment = await _installment(db_session, ctx)
    with pytest.raises(CommitmentStateError):
        await end_recurring(
            db_session,
            household_id=ctx.household_id,
            commitment_id=installment.id,
            end_date=date(2026, 12, 1),
            today=TODAY,
        )

    recurring = await _recurring(db_session, ctx)
    await cancel_commitment(db_session, household_id=ctx.household_id, commitment_id=recurring.id)
    with pytest.raises(CommitmentStateError):
        await end_recurring(
            db_session,
            household_id=ctx.household_id,
            commitment_id=recurring.id,
            end_date=date(2026, 12, 1),
            today=TODAY,
        )


async def test_end_refuses_end_before_purchase(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx)
    count = len(await _entries(db_session, commitment.id))

    with pytest.raises(ValueError, match="end_date"):
        await end_recurring(
            db_session,
            household_id=ctx.household_id,
            commitment_id=commitment.id,
            end_date=date(2025, 12, 31),
            today=TODAY,
        )
    assert len(await _entries(db_session, commitment.id)) == count


# --- settled ------------------------------------------------------------------------


async def test_installment_with_every_entry_paid_settles(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx, count=3)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[:2])

    assert not await settle_if_paid(
        db_session, household_id=ctx.household_id, commitment_id=commitment.id
    )
    assert await _status(db_session, commitment.id) == "active"

    await _pay(db_session, entries[2:])
    assert await settle_if_paid(
        db_session, household_id=ctx.household_id, commitment_id=commitment.id
    )
    assert await _status(db_session, commitment.id) == "settled"


async def test_reschedule_down_to_the_paid_entries_settles(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx, count=4)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[:2])

    await reschedule_installments(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        installment_count=2,
        total_amount=Decimal("786.00"),
    )

    assert await _status(db_session, commitment.id) == "settled"


async def test_recurring_without_end_never_settles(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx)
    await _pay(db_session, await _entries(db_session, commitment.id))

    assert not await settle_if_paid(
        db_session, household_id=ctx.household_id, commitment_id=commitment.id
    )
    assert await _status(db_session, commitment.id) == "active"


async def test_ending_a_fully_paid_recurring_settles(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _recurring(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[:8])  # 2026-02 a 2026-09

    await end_recurring(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        end_date=date(2026, 8, 15),
        today=TODAY,
    )

    assert len(await _entries(db_session, commitment.id)) == 8
    assert await _status(db_session, commitment.id) == "settled"


async def test_ended_recurring_not_yet_materialized_does_not_settle(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    # Fim em 2030: o horizonte (até 2028-10) ainda não chegou à última competência.
    commitment = await _recurring(db_session, ctx, end_date=date(2030, 1, 10))
    await _pay(db_session, await _entries(db_session, commitment.id))

    assert not await settle_if_paid(
        db_session, household_id=ctx.household_id, commitment_id=commitment.id
    )


async def test_cancelled_commitment_does_not_settle(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _installment(db_session, ctx, count=2)
    await _pay(db_session, (await _entries(db_session, commitment.id))[:1])
    await cancel_commitment(db_session, household_id=ctx.household_id, commitment_id=commitment.id)

    assert not await settle_if_paid(
        db_session, household_id=ctx.household_id, commitment_id=commitment.id
    )
    assert await _status(db_session, commitment.id) == "cancelled"
