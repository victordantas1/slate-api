import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Category, Commitment, Entry, Household, Member
from app.domain.installments import PlannedInstallment
from app.services import materialization
from app.services.materialization import AccountNotFoundError, create_installment_commitment


class _Ctx:
    def __init__(self, household_id: uuid.UUID, account_id: uuid.UUID, category_id: uuid.UUID):
        self.household_id = household_id
        self.account_id = account_id
        self.category_id = category_id


async def _ctx(session: AsyncSession, name: str = "Casa", offset: int = 1) -> _Ctx:
    household_id = (
        await session.execute(insert(Household).values(name=name).returning(Household.id))
    ).scalar_one()
    member_id = (
        await session.execute(
            insert(Member)
            .values(household_id=household_id, supabase_user_id=uuid.uuid4(), name="Ana")
            .returning(Member.id)
        )
    ).scalar_one()
    account_id = (
        await session.execute(
            insert(Account)
            .values(
                household_id=household_id,
                name="Nubank",
                kind="credit_card",
                holder_kind="member",
                owner_member_id=member_id,
                first_installment_offset=offset,
            )
            .returning(Account.id)
        )
    ).scalar_one()
    category_id = (
        await session.execute(
            insert(Category)
            .values(household_id=household_id, name="Casa", direction="expense")
            .returning(Category.id)
        )
    ).scalar_one()
    return _Ctx(household_id, account_id, category_id)


@pytest.fixture
async def ctx(db_session: AsyncSession) -> _Ctx:
    return await _ctx(db_session)


async def _create(session: AsyncSession, ctx: _Ctx, **overrides: Any) -> Commitment:
    values: dict[str, Any] = {
        "household_id": ctx.household_id,
        "account_id": ctx.account_id,
        "category_id": ctx.category_id,
        "kind": "installment",
        "description": "Geladeira",
        "purchase_date": date(2026, 11, 15),
        "total_amount": Decimal("1000.00"),
        "installment_count": 3,
    }
    values.update(overrides)
    return await create_installment_commitment(session, **values)


async def _entries(session: AsyncSession, commitment_id: uuid.UUID) -> list[Entry]:
    result = await session.scalars(
        select(Entry).where(Entry.commitment_id == commitment_id).order_by(Entry.seq)
    )
    return list(result)


async def _count(session: AsyncSession, model: type[Commitment] | type[Entry]) -> int:
    return (await session.scalar(select(func.count()).select_from(model))) or 0


async def test_installment_creates_commitment_and_entries(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _create(db_session, ctx)

    stored = await db_session.get(Commitment, commitment.id)
    assert stored is not None
    assert stored.status == "active"
    entries = await _entries(db_session, commitment.id)
    assert [(e.seq, e.competencia, e.amount) for e in entries] == [
        (1, date(2026, 12, 1), Decimal("333.34")),
        (2, date(2027, 1, 1), Decimal("333.33")),
        (3, date(2027, 2, 1), Decimal("333.33")),
    ]


async def test_entries_are_born_previsto_manual_untouched(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _create(db_session, ctx, installment_count=12)

    entries = await _entries(db_session, commitment.id)
    assert len(entries) == 12
    for entry in entries:
        await db_session.refresh(entry)
        assert entry.status == "previsto"
        assert entry.source == "manual"
        assert entry.edited_manually is False
        assert entry.paid_at is None
        assert entry.idempotency_key is None
        assert entry.household_id == ctx.household_id
        assert entry.category_id == ctx.category_id


async def test_single_produces_exactly_one_entry(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _create(
        db_session,
        ctx,
        kind="single",
        total_amount=Decimal("59.90"),
        installment_count=1,
    )

    entries = await _entries(db_session, commitment.id)
    assert [(e.seq, e.competencia, e.amount) for e in entries] == [
        (1, date(2026, 12, 1), Decimal("59.90"))
    ]


async def test_single_defaults_to_one_installment(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _create(db_session, ctx, kind="single", installment_count=None)

    assert commitment.installment_count == 1
    assert len(await _entries(db_session, commitment.id)) == 1


async def test_offset_comes_from_account(db_session: AsyncSession) -> None:
    ctx = await _ctx(db_session, offset=0)
    commitment = await _create(db_session, ctx, installment_count=2)

    entries = await _entries(db_session, commitment.id)
    assert [e.competencia for e in entries] == [date(2026, 11, 1), date(2026, 12, 1)]


async def test_entry_constraint_failure_rolls_back_everything(
    db_session: AsyncSession, ctx: _Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A última parcela viola `amount >= 0`: o commitment já foi inserido quando ela falha.
    def broken_plan(*args: Any) -> list[PlannedInstallment]:
        return [
            PlannedInstallment(1, date(2026, 12, 1), Decimal("1000.00")),
            PlannedInstallment(2, date(2027, 1, 1), Decimal("-1.00")),
        ]

    monkeypatch.setattr(materialization, "plan_installments", broken_plan)
    before = (await _count(db_session, Commitment), await _count(db_session, Entry))

    with pytest.raises(IntegrityError, match="ck_entry_amount_not_negative"):
        await _create(db_session, ctx, installment_count=2)

    assert (await _count(db_session, Commitment), await _count(db_session, Entry)) == before


async def test_commitment_constraint_failure_rolls_back_and_session_survives(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    other = await _ctx(db_session, name="Outra casa")
    before = (await _count(db_session, Commitment), await _count(db_session, Entry))

    # Categoria de outra household: a FK composta recusa.
    with pytest.raises(IntegrityError, match="fk_commitment_category_id_category"):
        await _create(db_session, ctx, category_id=other.category_id)

    assert (await _count(db_session, Commitment), await _count(db_session, Entry)) == before
    # A transação externa segue utilizável depois do rollback do savepoint.
    commitment = await _create(db_session, ctx)
    assert len(await _entries(db_session, commitment.id)) == 3


async def test_account_of_other_household_is_not_found(db_session: AsyncSession, ctx: _Ctx) -> None:
    other = await _ctx(db_session, name="Outra casa")

    with pytest.raises(AccountNotFoundError):
        await _create(db_session, ctx, account_id=other.account_id)


@pytest.mark.parametrize(
    ("kind", "count"),
    [("recurring", None), ("single", 2), ("installment", None), ("nope", 3)],
)
async def test_rejects_what_is_not_installment_or_single(
    db_session: AsyncSession, ctx: _Ctx, kind: str, count: int | None
) -> None:
    before = await _count(db_session, Commitment)

    with pytest.raises(ValueError):
        await _create(db_session, ctx, kind=kind, installment_count=count)

    assert await _count(db_session, Commitment) == before
