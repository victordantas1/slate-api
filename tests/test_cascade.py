import random
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Category, Commitment, Entry, Household, Member
from app.domain.cascade import InvalidRescheduleError
from app.services.cascade import (
    CategoryNotFoundError,
    EntryNotFoundError,
    PaidEntryError,
    edit_entries,
    reschedule_installments,
)
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


async def _category(session: AsyncSession, household_id: uuid.UUID, name: str) -> uuid.UUID:
    return (
        await session.execute(
            insert(Category)
            .values(household_id=household_id, name=name, direction="expense")
            .returning(Category.id)
        )
    ).scalar_one()


async def _ctx(session: AsyncSession, name: str = "Casa") -> _Ctx:
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
                first_installment_offset=1,
            )
            .returning(Account.id)
        )
    ).scalar_one()
    return _Ctx(household_id, account_id, await _category(session, household_id, "Casa"))


@pytest.fixture
async def ctx(db_session: AsyncSession) -> _Ctx:
    return await _ctx(db_session)


async def _installment(
    session: AsyncSession, ctx: _Ctx, total: str = "3930.00", count: int = 10
) -> Commitment:
    return await create_installment_commitment(
        session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        kind="installment",
        description="Sofá",
        purchase_date=date(2026, 3, 10),
        total_amount=Decimal(total),
        installment_count=count,
    )


async def _entries(session: AsyncSession, commitment_id: uuid.UUID) -> list[Entry]:
    result = await session.scalars(
        select(Entry)
        .where(Entry.commitment_id == commitment_id)
        .order_by(Entry.seq)
        .execution_options(populate_existing=True)
    )
    return list(result)


async def _pay(session: AsyncSession, entry_id: uuid.UUID) -> None:
    await session.execute(
        update(Entry)
        .where(Entry.id == entry_id)
        .values(status="pago", paid_at=datetime(2026, 9, 1, tzinfo=UTC))
    )


async def _snapshot(session: AsyncSession, commitment_id: uuid.UUID, *where: Any) -> dict[Any, Any]:
    rows = await session.execute(
        select(Entry.__table__).where(Entry.commitment_id == commitment_id, *where)
    )
    return {row.id: tuple(row) for row in rows}


async def _paid(session: AsyncSession, commitment_id: uuid.UUID) -> dict[Any, Any]:
    return await _snapshot(session, commitment_id, Entry.status == "pago")


async def _manual(session: AsyncSession, commitment_id: uuid.UUID) -> dict[Any, Any]:
    return await _snapshot(session, commitment_id, Entry.edited_manually.is_(True))


async def _commitment(session: AsyncSession, commitment_id: uuid.UUID) -> Commitment:
    stored = await session.scalar(
        select(Commitment)
        .where(Commitment.id == commitment_id)
        .execution_options(populate_existing=True)
    )
    assert stored is not None
    return stored


def _amounts(entries: list[Entry]) -> list[Decimal]:
    return [e.amount for e in entries]


async def _assert_plan_consistent(session: AsyncSession, commitment_id: uuid.UUID) -> None:
    """Invariante 1 depois de qualquer cascata: soma == total, seqs e meses contíguos."""
    commitment = await _commitment(session, commitment_id)
    entries = await _entries(session, commitment_id)
    assert sum(_amounts(entries), Decimal("0")) == commitment.total_amount
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    assert len(entries) == commitment.installment_count
    for prev, cur in zip(entries, entries[1:], strict=False):
        assert (cur.competencia.year * 12 + cur.competencia.month) - (
            prev.competencia.year * 12 + prev.competencia.month
        ) == 1


# --- this ---------------------------------------------------------------------------


async def test_this_updates_only_the_entry_and_marks_it(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx)
    entries = await _entries(db_session, commitment.id)

    changed = await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[3].id,
        scope="this",
        amount=Decimal("134.00"),
    )

    assert changed == 1
    after = await _entries(db_session, commitment.id)
    assert (
        _amounts(after) == [Decimal("393.00")] * 3 + [Decimal("134.00")] + [Decimal("393.00")] * 6
    )
    assert [e.edited_manually for e in after] == [False] * 3 + [True] + [False] * 6
    await _assert_plan_consistent(db_session, commitment.id)


async def test_this_refuses_a_paid_entry(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _installment(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)
    paid = await _paid(db_session, commitment.id)
    other = await _category(db_session, ctx.household_id, "Outra")

    with pytest.raises(PaidEntryError):
        await edit_entries(
            db_session,
            household_id=ctx.household_id,
            entry_id=entries[0].id,
            scope="this",
            amount=Decimal("1.00"),
            category_id=other,
        )

    assert await _paid(db_session, commitment.id) == paid


async def test_this_can_override_category_only(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _installment(db_session, ctx, count=3, total="300.00")
    entries = await _entries(db_session, commitment.id)
    other = await _category(db_session, ctx.household_id, "Outra")

    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[1].id,
        scope="this",
        category_id=other,
    )

    after = await _entries(db_session, commitment.id)
    assert [e.category_id for e in after] == [ctx.category_id, other, ctx.category_id]
    assert (await _commitment(db_session, commitment.id)).category_id == ctx.category_id


# --- forward / all ------------------------------------------------------------------


async def test_forward_filters_competencia_manual_and_paid(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)
    await _pay(db_session, entries[6].id)
    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[7].id,
        scope="this",
        amount=Decimal("134.00"),
    )
    paid = await _paid(db_session, commitment.id)
    manual = await _manual(db_session, commitment.id)

    changed = await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[4].id,
        scope="forward",
        amount=Decimal("400.00"),
    )

    # Seqs 5..10, menos o 7 (pago) e o 8 (edited_manually).
    assert changed == 4
    after = await _entries(db_session, commitment.id)
    d = Decimal
    assert _amounts(after) == [
        *[d("393.00")] * 4,
        d("400.00"),
        d("400.00"),
        d("393.00"),
        d("134.00"),
        d("400.00"),
        d("400.00"),
    ]
    assert await _paid(db_session, commitment.id) == paid
    assert await _manual(db_session, commitment.id) == manual
    await _assert_plan_consistent(db_session, commitment.id)


async def test_all_ignores_competencia_but_keeps_filters(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx, count=4, total="400.00")
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)
    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[1].id,
        scope="this",
        amount=Decimal("50.00"),
    )
    paid = await _paid(db_session, commitment.id)
    manual = await _manual(db_session, commitment.id)
    other = await _category(db_session, ctx.household_id, "Outra")

    changed = await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[3].id,
        scope="all",
        amount=Decimal("120.00"),
        category_id=other,
    )

    assert changed == 2
    after = await _entries(db_session, commitment.id)
    assert _amounts(after) == [
        Decimal("100.00"),
        Decimal("50.00"),
        Decimal("120.00"),
        Decimal("120.00"),
    ]
    assert [e.category_id for e in after] == [ctx.category_id, ctx.category_id, other, other]
    assert await _paid(db_session, commitment.id) == paid
    assert await _manual(db_session, commitment.id) == manual
    stored = await _commitment(db_session, commitment.id)
    assert stored.category_id == other
    assert stored.total_amount == Decimal("390.00")


async def test_recurring_forward_changes_the_horizon(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await create_recurring_commitment(
        db_session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        description="Internet",
        purchase_date=date(2026, 9, 5),
        recurring_amount=Decimal("99.90"),
        end_date=None,
        today=TODAY,
    )
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)
    paid = await _paid(db_session, commitment.id)

    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[2].id,
        scope="forward",
        amount=Decimal("119.90"),
    )
    inserted = await extend_recurring_horizon(
        db_session, today=date(2027, 1, 2), commitment_ids=[commitment.id]
    )

    assert inserted == 3
    after = await _entries(db_session, commitment.id)
    assert _amounts(after)[:2] == [Decimal("99.90")] * 2
    assert set(_amounts(after)[2:]) == {Decimal("119.90")}
    assert (await _commitment(db_session, commitment.id)).recurring_amount == Decimal("119.90")
    assert (await _commitment(db_session, commitment.id)).total_amount is None
    assert await _paid(db_session, commitment.id) == paid


async def test_recurring_forward_refuses_zero(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await create_recurring_commitment(
        db_session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        description="Internet",
        purchase_date=date(2026, 9, 5),
        recurring_amount=Decimal("99.90"),
        end_date=None,
        today=TODAY,
    )
    entries = await _entries(db_session, commitment.id)

    with pytest.raises(ValueError):
        await edit_entries(
            db_session,
            household_id=ctx.household_id,
            entry_id=entries[0].id,
            scope="all",
            amount=Decimal("0.00"),
        )


async def test_other_household_is_not_found(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _installment(db_session, ctx, count=2, total="20.00")
    entries = await _entries(db_session, commitment.id)
    stranger = await _ctx(db_session, "Vizinhos")

    with pytest.raises(EntryNotFoundError):
        await edit_entries(
            db_session,
            household_id=stranger.household_id,
            entry_id=entries[0].id,
            scope="all",
            amount=Decimal("1.00"),
        )
    with pytest.raises(CategoryNotFoundError):
        await edit_entries(
            db_session,
            household_id=ctx.household_id,
            entry_id=entries[0].id,
            scope="all",
            category_id=stranger.category_id,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": "sometimes", "amount": Decimal("1.00")},
        {"scope": "all"},
        {"scope": "all", "amount": Decimal("-1.00")},
        {"scope": "all", "amount": Decimal("1.001")},
        {"scope": "all", "amount": Decimal("NaN")},
        {"scope": "all", "amount": Decimal("10000000000.00")},
    ],
)
async def test_invalid_edit_is_refused(
    db_session: AsyncSession, ctx: _Ctx, kwargs: dict[str, Any]
) -> None:
    commitment = await _installment(db_session, ctx, count=2, total="20.00")
    entries = await _entries(db_session, commitment.id)

    with pytest.raises(ValueError):
        await edit_entries(
            db_session, household_id=ctx.household_id, entry_id=entries[0].id, **kwargs
        )


async def test_refused_edit_leaves_database_and_session_untouched(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    single = await create_installment_commitment(
        db_session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        kind="single",
        description="Cadeira",
        purchase_date=date(2026, 3, 10),
        total_amount=Decimal("80.00"),
        installment_count=1,
    )
    (entry,) = await _entries(db_session, single.id)
    before = await _snapshot(db_session, single.id)

    with pytest.raises(ValueError):
        await edit_entries(
            db_session,
            household_id=ctx.household_id,
            entry_id=entry.id,
            scope="this",
            amount=Decimal("0.00"),
        )

    assert entry.amount == Decimal("80.00")
    assert entry.edited_manually is False
    assert await _snapshot(db_session, single.id) == before
    assert (await _commitment(db_session, single.id)).total_amount == Decimal("80.00")


# --- caso real ----------------------------------------------------------------------


async def test_renegotiated_installment_survives_parent_edits(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    """Parcela de 393 renegociada para 134 sobrevive a edições posteriores do pai."""
    commitment = await _installment(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)
    await _pay(db_session, entries[1].id)
    renegotiated = entries[4]
    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=renegotiated.id,
        scope="this",
        amount=Decimal("134.00"),
    )
    paid = await _paid(db_session, commitment.id)
    manual = await _manual(db_session, commitment.id)
    other = await _category(db_session, ctx.household_id, "Móveis")

    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[2].id,
        scope="forward",
        amount=Decimal("410.00"),
    )
    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[9].id,
        scope="all",
        category_id=other,
    )
    await reschedule_installments(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        installment_count=12,
        total_amount=Decimal("3930.00"),
    )

    assert await _manual(db_session, commitment.id) == manual
    assert await _paid(db_session, commitment.id) == paid
    after = await _entries(db_session, commitment.id)
    assert after[4].amount == Decimal("134.00")
    assert after[4].edited_manually is True
    await _assert_plan_consistent(db_session, commitment.id)


# --- installment_count ----------------------------------------------------------------


@pytest.mark.parametrize("new_count", [12, 6, 10, 4])
async def test_reschedule_preserves_paid_entries(
    db_session: AsyncSession, ctx: _Ctx, new_count: int
) -> None:
    commitment = await _installment(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    for entry in entries[:3]:
        await _pay(db_session, entry.id)
    paid = await _paid(db_session, commitment.id)

    stored = await reschedule_installments(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        installment_count=new_count,
    )

    assert stored.installment_count == new_count
    assert stored.total_amount == Decimal("3930.00")
    assert await _paid(db_session, commitment.id) == paid
    after = await _entries(db_session, commitment.id)
    assert after[0].competencia == entries[0].competencia
    # 3930 - 3*393 = 2751 redividido entre as que não estão pagas.
    free = after[3:]
    assert sum(_amounts(free), Decimal("0")) == Decimal("2751.00")
    for entry in free:
        assert entry.status == "previsto"
        assert entry.edited_manually is False
    await _assert_plan_consistent(db_session, commitment.id)


async def test_reschedule_changes_total(db_session: AsyncSession, ctx: _Ctx) -> None:
    commitment = await _installment(db_session, ctx, count=3, total="1000.00")
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)

    await reschedule_installments(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        total_amount=Decimal("1200.00"),
    )

    after = await _entries(db_session, commitment.id)
    assert _amounts(after) == [Decimal("333.34"), Decimal("433.33"), Decimal("433.33")]
    await _assert_plan_consistent(db_session, commitment.id)


async def test_reschedule_keeps_manual_entries_and_resplits_the_rest(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    commitment = await _installment(db_session, ctx)
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)
    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[4].id,
        scope="this",
        amount=Decimal("134.00"),
    )
    paid = await _paid(db_session, commitment.id)
    manual = await _manual(db_session, commitment.id)

    await reschedule_installments(
        db_session,
        household_id=ctx.household_id,
        commitment_id=commitment.id,
        installment_count=8,
        total_amount=Decimal("3000.00"),
    )

    assert await _paid(db_session, commitment.id) == paid
    assert await _manual(db_session, commitment.id) == manual
    after = await _entries(db_session, commitment.id)
    # 3000 - 393 - 134 = 2473 em 6 livres: 412,20 + 5 x 412,16.
    free = [e.amount for e in after if e.seq not in (1, 5)]
    assert free == [Decimal("412.20")] + [Decimal("412.16")] * 5
    await _assert_plan_consistent(db_session, commitment.id)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"installment_count": 2}, InvalidRescheduleError),
        ({"total_amount": Decimal("700.00")}, InvalidRescheduleError),
        ({"installment_count": 40000}, ValueError),
        ({"total_amount": Decimal("10000000000.00")}, ValueError),
    ],
)
async def test_reschedule_refuses_plans_that_break_locked_entries(
    db_session: AsyncSession, ctx: _Ctx, kwargs: dict[str, Any], error: type[ValueError]
) -> None:
    commitment = await _installment(db_session, ctx, count=4, total="1000.00")
    entries = await _entries(db_session, commitment.id)
    await _pay(db_session, entries[0].id)
    await _pay(db_session, entries[2].id)
    await edit_entries(
        db_session,
        household_id=ctx.household_id,
        entry_id=entries[1].id,
        scope="this",
        amount=Decimal("250.00"),
    )
    before = await _snapshot(db_session, commitment.id)

    with pytest.raises(error):
        await reschedule_installments(
            db_session, household_id=ctx.household_id, commitment_id=commitment.id, **kwargs
        )

    assert await _snapshot(db_session, commitment.id) == before


async def test_reschedule_refuses_single_and_recurring(db_session: AsyncSession, ctx: _Ctx) -> None:
    single = await create_installment_commitment(
        db_session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        kind="single",
        description="Cadeira",
        purchase_date=date(2026, 3, 10),
        total_amount=Decimal("80.00"),
        installment_count=1,
    )
    recurring = await create_recurring_commitment(
        db_session,
        household_id=ctx.household_id,
        account_id=ctx.account_id,
        category_id=ctx.category_id,
        description="Internet",
        purchase_date=date(2026, 9, 5),
        recurring_amount=Decimal("99.90"),
        end_date=None,
        today=TODAY,
    )

    with pytest.raises(ValueError):
        await reschedule_installments(
            db_session,
            household_id=ctx.household_id,
            commitment_id=single.id,
            installment_count=2,
        )
    with pytest.raises(ValueError):
        await reschedule_installments(
            db_session,
            household_id=ctx.household_id,
            commitment_id=recurring.id,
            installment_count=2,
        )
    stored = await reschedule_installments(
        db_session,
        household_id=ctx.household_id,
        commitment_id=single.id,
        total_amount=Decimal("90.00"),
    )
    assert stored.total_amount == Decimal("90.00")
    await _assert_plan_consistent(db_session, single.id)


# --- bateria aleatória ------------------------------------------------------------------


async def test_random_operations_never_touch_paid_entries(
    db_session: AsyncSession, ctx: _Ctx
) -> None:
    """Invariantes 1 e 2 sob uma sequência aleatória de cascatas, pagamentos e
    recálculos. Semente fixa: hypothesis não combina com fixtures async por teste."""
    rng = random.Random(12)
    categories = [ctx.category_id, await _category(db_session, ctx.household_id, "Outra")]
    commitment = await _installment(db_session, ctx, total="5000.00", count=12)
    applied: Counter[str] = Counter()

    for _ in range(150):
        entries = await _entries(db_session, commitment.id)
        paid = await _paid(db_session, commitment.id)
        manual = await _manual(db_session, commitment.id)
        anchor = rng.choice(entries)
        op = rng.choice(["this", "forward", "all", "pay", "reschedule"])
        try:
            if op == "pay":
                await _pay(db_session, anchor.id)
                paid = await _paid(db_session, commitment.id)
            elif op == "reschedule":
                await reschedule_installments(
                    db_session,
                    household_id=ctx.household_id,
                    commitment_id=commitment.id,
                    installment_count=rng.randint(1, 18),
                    total_amount=Decimal(rng.randint(1_000_00, 9_000_00)) / 100,
                )
            else:
                await edit_entries(
                    db_session,
                    household_id=ctx.household_id,
                    entry_id=anchor.id,
                    scope=op,  # type: ignore[arg-type]
                    amount=Decimal(rng.randint(0, 900_00)) / 100,
                    category_id=rng.choice([None, *categories]),
                )
                if op == "this":
                    manual = await _manual(db_session, commitment.id)
            applied[op] += 1
        except ValueError:  # PaidEntryError, InvalidRescheduleError, soma zerada
            pass

        assert await _paid(db_session, commitment.id) == paid
        if op in ("forward", "all"):
            assert await _manual(db_session, commitment.id) == manual
        elif op == "reschedule":
            # O recálculo pode apagar seqs além da nova contagem, mas só livres.
            assert await _manual(db_session, commitment.id) == manual
        await _assert_plan_consistent(db_session, commitment.id)

    # A bateria só prova algo se cada operação de fato rodou várias vezes.
    assert all(applied[op] >= 5 for op in ("this", "forward", "all", "pay", "reschedule"))
