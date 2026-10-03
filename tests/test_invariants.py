"""As invariantes do motor como testes de primeira classe, contra Postgres real.

1. Soma das parcelas: as entries de um parcelamento somam exatamente o `total_amount`,
   uma por parcela, com competências consecutivas.
2. Imutabilidade de `pago`: nenhuma operação do motor altera uma entry paga.
3. Idempotência do horizonte: rodar o horizonte de recorrentes N vezes == 1 vez.

As invariantes são verificadas no banco (SQL sobre o que foi gravado), não no plano em
memória: é o que a tela de mês vai ler.
"""

import random
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Commitment, Entry
from app.domain.cascade import InvalidRescheduleError
from app.services.cascade import PaidEntryError, Scope, edit_entries, reschedule_installments
from app.services.lifecycle import cancel_commitment, end_recurring
from app.services.materialization import create_recurring_commitment, extend_recurring_horizon
from tests.factories import HouseholdCtx, entries_of, make_commitment, make_household

MAX_AMOUNT = Decimal("9999999999.99")  # Numeric(12, 2)
MAX_INSTALLMENTS = 32767  # SmallInteger


async def installment_sum_violations(
    session: AsyncSession, commitment_ids: Sequence[uuid.UUID]
) -> list[Row[Any]]:
    """Commitments cujas entries não somam o total, ou não são uma por parcela.

    Vazio == invariante 1 satisfeita para todos os `commitment_ids`.
    """
    totals = (
        select(
            Entry.commitment_id,
            func.sum(Entry.amount).label("soma"),
            func.count().label("n"),
            func.min(Entry.seq).label("seq_min"),
            func.max(Entry.seq).label("seq_max"),
            func.min(Entry.competencia).label("primeira"),
            func.max(Entry.competencia).label("ultima"),
        )
        .group_by(Entry.commitment_id)
        .subquery()
    )
    months_between = (
        func.extract("year", totals.c.ultima) * 12 + func.extract("month", totals.c.ultima)
    ) - (func.extract("year", totals.c.primeira) * 12 + func.extract("month", totals.c.primeira))
    query = (
        select(Commitment.id, Commitment.total_amount, totals.c.soma, totals.c.n)
        .outerjoin(totals, totals.c.commitment_id == Commitment.id)
        .where(Commitment.id.in_(commitment_ids))
        .where(
            totals.c.soma.is_(None)
            | (totals.c.soma != Commitment.total_amount)
            | (totals.c.n != Commitment.installment_count)
            | (totals.c.seq_min != 1)
            | (totals.c.seq_max != Commitment.installment_count)
            | (months_between != Commitment.installment_count - 1)
        )
    )
    return list((await session.execute(query)).all())


ADVERSARIAL = [
    # O menor valor possível, sozinho e espalhado: parcelas de zero centavo.
    (Decimal("0.01"), 1),
    (Decimal("0.01"), 2),
    (Decimal("0.01"), 12),
    (Decimal("0.02"), 3),
    # Dízimas: o resíduo vai todo para a primeira parcela.
    (Decimal("0.10"), 3),
    (Decimal("1.00"), 3),
    (Decimal("100.00"), 3),
    (Decimal("1000.00"), 3),
    (Decimal("100.00"), 7),
    (Decimal("999.99"), 11),
    (Decimal("1234.57"), 13),
    (Decimal("10.00"), 97),
    (Decimal("0.07"), 360),
    # O teto da coluna, em contagens que não dividem.
    (MAX_AMOUNT, 1),
    (MAX_AMOUNT, 7),
    (MAX_AMOUNT, 12),
    (MAX_AMOUNT - Decimal("0.01"), 9),
]


@pytest.mark.parametrize(("total", "count"), ADVERSARIAL, ids=lambda v: str(v))
async def test_invariant_1_installments_sum_to_total(
    db_session: AsyncSession, household: HouseholdCtx, total: Decimal, count: int
) -> None:
    commitment = await make_commitment(
        db_session, household, total_amount=total, installment_count=count
    )

    assert await installment_sum_violations(db_session, [commitment.id]) == []
    amounts = [e.amount for e in await entries_of(db_session, commitment.id)]
    # Resíduo na primeira, as demais iguais e nunca maiores que ela.
    assert len(set(amounts[1:])) <= 1
    assert all(a <= amounts[0] for a in amounts)
    assert amounts[0] - min(amounts) < Decimal("0.01") * count


@pytest.mark.parametrize(
    ("purchase_date", "offset"),
    [
        (date(2026, 12, 31), 1),  # vira o ano já na primeira parcela
        (date(2028, 2, 29), 0),  # bissexto
        (date(2026, 1, 31), 2),
        (date(2026, 11, 1), 12),
    ],
    ids=str,
)
async def test_invariant_1_holds_across_calendar_edges(
    db_session: AsyncSession, purchase_date: date, offset: int
) -> None:
    household = await make_household(db_session, offset=offset)
    commitment = await make_commitment(
        db_session,
        household,
        purchase_date=purchase_date,
        total_amount=Decimal("1000.00"),
        installment_count=13,
    )

    assert await installment_sum_violations(db_session, [commitment.id]) == []


async def test_invariant_1_holds_at_the_installment_count_ceiling(
    db_session: AsyncSession, household: HouseholdCtx
) -> None:
    commitment = await make_commitment(
        db_session, household, total_amount=MAX_AMOUNT, installment_count=MAX_INSTALLMENTS
    )

    assert await installment_sum_violations(db_session, [commitment.id]) == []


async def test_invariant_1_holds_for_random_adversarial_batch(
    db_session: AsyncSession, household: HouseholdCtx
) -> None:
    # Semente fixa: a mesma bateria em toda rodada, para uma falha ser reproduzível.
    rng = random.Random(15)
    ids = []
    for _ in range(150):
        cents = int(10 ** rng.uniform(0, 12)) or 1
        total = min(Decimal(cents) / 100, MAX_AMOUNT)
        count = rng.choice([rng.randint(1, 24), rng.randint(25, 120)])
        commitment = await make_commitment(
            db_session, household, total_amount=total, installment_count=count
        )
        ids.append(commitment.id)

    assert await installment_sum_violations(db_session, ids) == []


async def test_invariant_1_check_catches_a_broken_split(
    db_session: AsyncSession, household: HouseholdCtx
) -> None:
    # Controle: a consulta da invariante enxerga um centavo a mais e uma parcela a menos.
    off_by_cent = await make_commitment(db_session, household)
    missing = await make_commitment(db_session, household)
    await db_session.execute(
        update(Entry)
        .where(Entry.commitment_id == off_by_cent.id, Entry.seq == 1)
        .values(amount=Entry.amount + Decimal("0.01"))
    )
    await db_session.execute(delete(Entry).where(Entry.commitment_id == missing.id, Entry.seq == 3))

    violations = await installment_sum_violations(db_session, [off_by_cent.id, missing.id])

    assert {row.id for row in violations} == {off_by_cent.id, missing.id}


async def test_invariant_1_rejects_totals_the_column_cannot_hold(
    db_session: AsyncSession, household: HouseholdCtx
) -> None:
    before = await db_session.scalar(select(func.count()).select_from(Commitment))

    with pytest.raises(ValueError):
        await make_commitment(db_session, household, total_amount=Decimal("10.005"))
    with pytest.raises(DBAPIError, match="numeric field overflow"):
        await make_commitment(db_session, household, total_amount=MAX_AMOUNT + Decimal("0.01"))

    assert await db_session.scalar(select(func.count()).select_from(Commitment)) == before


# --- Invariante 2 -------------------------------------------------------------------

EngineOperation = Callable[[AsyncSession, HouseholdCtx], Awaitable[object]]


async def _materialize_same_account(session: AsyncSession, household: HouseholdCtx) -> None:
    await make_commitment(session, household, installment_count=24)


async def _materialize_single_same_month(session: AsyncSession, household: HouseholdCtx) -> None:
    await make_commitment(session, household, kind="single", installment_count=1)


async def _failed_materialization(session: AsyncSession, household: HouseholdCtx) -> None:
    other = await make_household(session, name="Outra casa")
    with pytest.raises(IntegrityError):
        await make_commitment(session, household, category_id=other.category_id)


TODAY = date(2026, 10, 2)


async def _create_recurring(
    session: AsyncSession, household: HouseholdCtx, **overrides: Any
) -> Commitment:
    values: dict[str, Any] = {
        "household_id": household.household_id,
        "account_id": household.account_id,
        "category_id": household.category_id,
        "description": "Streaming",
        "purchase_date": date(2026, 10, 10),
        "recurring_amount": Decimal("39.90"),
        "end_date": None,
        "today": TODAY,
    }
    values.update(overrides)
    return await create_recurring_commitment(session, **values)


async def _materialize_recurring(session: AsyncSession, household: HouseholdCtx) -> None:
    await _create_recurring(session, household)


async def _extend_horizon(session: AsyncSession, household: HouseholdCtx) -> None:
    await extend_recurring_horizon(session, today=TODAY)
    await extend_recurring_horizon(session, today=date(2027, 10, 2))


# As operações abaixo agem sobre os dois commitments que o teste da invariante 2 monta,
# com entries pagas no meio do escopo de cada uma.


async def _setup_commitment(
    session: AsyncSession, household: HouseholdCtx, kind: str
) -> Commitment:
    query = select(Commitment).where(
        Commitment.household_id == household.household_id, Commitment.kind == kind
    )
    return (await session.scalars(query)).one()


async def _entry_id(session: AsyncSession, commitment_id: uuid.UUID, seq: int) -> uuid.UUID:
    query = select(Entry.id).where(Entry.commitment_id == commitment_id, Entry.seq == seq)
    return (await session.scalars(query)).one()


async def _new_category(session: AsyncSession, household: HouseholdCtx) -> uuid.UUID:
    category_id: uuid.UUID = (
        await session.execute(
            insert(Category)
            .values(household_id=household.household_id, name="Lazer", direction="expense")
            .returning(Category.id)
        )
    ).scalar_one()
    return category_id


async def _edit(
    session: AsyncSession, household: HouseholdCtx, kind: str, seq: int, scope: Scope
) -> None:
    commitment = await _setup_commitment(session, household, kind)
    await edit_entries(
        session,
        household_id=household.household_id,
        entry_id=await _entry_id(session, commitment.id, seq),
        scope=scope,
        amount=Decimal("77.70"),
        category_id=await _new_category(session, household),
    )


async def _edit_this(session: AsyncSession, household: HouseholdCtx) -> None:
    await _edit(session, household, "installment", 3, "this")


async def _edit_this_paid_refused(session: AsyncSession, household: HouseholdCtx) -> None:
    with pytest.raises(PaidEntryError):
        await _edit(session, household, "installment", 2, "this")


async def _edit_forward_installment(session: AsyncSession, household: HouseholdCtx) -> None:
    # Âncora paga: o `forward` parte dela, mas não a altera.
    await _edit(session, household, "installment", 2, "forward")


async def _edit_all_installment(session: AsyncSession, household: HouseholdCtx) -> None:
    await _edit(session, household, "installment", 3, "all")


async def _edit_forward_recurring(session: AsyncSession, household: HouseholdCtx) -> None:
    await _edit(session, household, "recurring", 1, "forward")
    await extend_recurring_horizon(session, today=date(2027, 10, 2))


async def _edit_all_recurring(session: AsyncSession, household: HouseholdCtx) -> None:
    await _edit(session, household, "recurring", 2, "all")


async def _reschedule(session: AsyncSession, household: HouseholdCtx, **changes: Any) -> None:
    commitment = await _setup_commitment(session, household, "installment")
    await reschedule_installments(
        session, household_id=household.household_id, commitment_id=commitment.id, **changes
    )


async def _reschedule_shrink(session: AsyncSession, household: HouseholdCtx) -> None:
    # Encolhe até a última paga (seq 4) e muda o total: as livres 5 e 6 saem.
    await _reschedule(session, household, installment_count=4, total_amount=Decimal("900.00"))


async def _reschedule_grow(session: AsyncSession, household: HouseholdCtx) -> None:
    await _reschedule(session, household, installment_count=12, total_amount=Decimal("2000.00"))


async def _reschedule_refused(session: AsyncSession, household: HouseholdCtx) -> None:
    with pytest.raises(InvalidRescheduleError):
        await _reschedule(session, household, installment_count=3)


async def _cancel(session: AsyncSession, household: HouseholdCtx, kind: str) -> None:
    commitment = await _setup_commitment(session, household, kind)
    await cancel_commitment(
        session, household_id=household.household_id, commitment_id=commitment.id
    )


async def _cancel_installment(session: AsyncSession, household: HouseholdCtx) -> None:
    await _cancel(session, household, "installment")


async def _cancel_recurring(session: AsyncSession, household: HouseholdCtx) -> None:
    await _cancel(session, household, "recurring")
    await extend_recurring_horizon(session, today=date(2027, 10, 2))


async def _end_recurring(session: AsyncSession, household: HouseholdCtx, end_date: date) -> None:
    commitment = await _setup_commitment(session, household, "recurring")
    await end_recurring(
        session,
        household_id=household.household_id,
        commitment_id=commitment.id,
        end_date=end_date,
        today=TODAY,
    )


async def _end_recurring_before_paid(session: AsyncSession, household: HouseholdCtx) -> None:
    # Fim no mês da compra: a paga de seq 3 fica depois da última competência.
    await _end_recurring(session, household, date(2026, 10, 10))


async def _end_recurring_later(session: AsyncSession, household: HouseholdCtx) -> None:
    await _end_recurring(session, household, date(2028, 6, 30))


# Toda operação do motor que grava entries entra aqui.
ENGINE_OPERATIONS: dict[str, EngineOperation] = {
    "materializa-outro-parcelamento": _materialize_same_account,
    "materializa-single": _materialize_single_same_month,
    "materializacao-que-falha": _failed_materialization,
    "materializa-recorrente": _materialize_recurring,
    "estende-horizonte": _extend_horizon,
    "edita-this": _edit_this,
    "edita-this-paga-recusada": _edit_this_paid_refused,
    "edita-forward-parcelamento": _edit_forward_installment,
    "edita-all-parcelamento": _edit_all_installment,
    "edita-forward-recorrente": _edit_forward_recurring,
    "edita-all-recorrente": _edit_all_recurring,
    "recalcula-parcelas-encolhe": _reschedule_shrink,
    "recalcula-parcelas-cresce": _reschedule_grow,
    "recalculo-recusado": _reschedule_refused,
    "cancela-parcelamento": _cancel_installment,
    "cancela-recorrente": _cancel_recurring,
    "encerra-recorrente-antes-da-paga": _end_recurring_before_paid,
    "encerra-recorrente-depois": _end_recurring_later,
}


async def _pay(session: AsyncSession, commitment_id: uuid.UUID, seqs: Sequence[int]) -> None:
    await session.execute(
        update(Entry)
        .where(Entry.commitment_id == commitment_id, Entry.seq.in_(seqs))
        .values(status="pago", paid_at=func.now())
    )


async def paid_snapshot(session: AsyncSession, household_id: uuid.UUID) -> list[Row[Any]]:
    """Todas as colunas de todas as entries pagas da household, em ordem estável."""
    query = (
        select(Entry.__table__)
        .where(Entry.household_id == household_id, Entry.status == "pago")
        .order_by(Entry.id)
    )
    return list((await session.execute(query)).all())


@pytest.mark.parametrize("operation", ENGINE_OPERATIONS.values(), ids=ENGINE_OPERATIONS.keys())
async def test_invariant_2_engine_never_touches_paid_entries(
    db_session: AsyncSession, household: HouseholdCtx, operation: EngineOperation
) -> None:
    commitment = await make_commitment(db_session, household, installment_count=6)
    await _pay(db_session, commitment.id, [1, 2, 4])
    recurring = await _create_recurring(db_session, household)
    await _pay(db_session, recurring.id, [1, 3])
    before = await paid_snapshot(db_session, household.household_id)
    assert len(before) == 5

    await operation(db_session, household)

    assert await paid_snapshot(db_session, household.household_id) == before


async def test_invariant_2_snapshot_sees_any_change_to_a_paid_entry(
    db_session: AsyncSession, household: HouseholdCtx
) -> None:
    # Controle: sem isto, um snapshot que não lê o banco passaria no teste acima.
    commitment = await make_commitment(db_session, household)
    await _pay(db_session, commitment.id, [1])
    before = await paid_snapshot(db_session, household.household_id)

    await db_session.execute(
        update(Entry)
        .where(Entry.commitment_id == commitment.id, Entry.seq == 1)
        .values(edited_manually=True)
    )

    assert await paid_snapshot(db_session, household.household_id) != before


# --- Invariante 3 -------------------------------------------------------------------


async def household_entries(session: AsyncSession, household_id: uuid.UUID) -> list[Row[Any]]:
    """Todas as colunas de todas as entries da household, em ordem estável."""
    query = select(Entry.__table__).where(Entry.household_id == household_id).order_by(Entry.id)
    return list((await session.execute(query)).all())


@pytest.mark.parametrize("runs", [2, 5])
async def test_invariant_3_horizon_n_times_equals_once(
    db_session: AsyncSession, household: HouseholdCtx, runs: int
) -> None:
    # Household mista: recorrentes com e sem fim, um pago, um editado, um cancelado e um
    # parcelamento, que o horizonte não deve tocar.
    open_ended = await _create_recurring(db_session, household)
    await _create_recurring(
        db_session, household, description="Academia", end_date=date(2027, 6, 30)
    )
    cancelled = await _create_recurring(db_session, household, description="Revista")
    await db_session.execute(
        update(Commitment).where(Commitment.id == cancelled.id).values(status="cancelled")
    )
    await make_commitment(db_session, household, installment_count=10)
    await _pay(db_session, open_ended.id, [1])
    await db_session.execute(
        update(Entry)
        .where(Entry.commitment_id == open_ended.id, Entry.seq == 2)
        .values(amount=Decimal("19.90"), edited_manually=True)
    )
    later = date(2027, 3, 15)

    first = await extend_recurring_horizon(db_session, today=later)
    once = await household_entries(db_session, household.household_id)
    again = [await extend_recurring_horizon(db_session, today=later) for _ in range(runs - 1)]

    assert first > 0  # o horizonte andou: o teste não é vacuamente idempotente
    assert again == [0] * (runs - 1)
    assert await household_entries(db_session, household.household_id) == once
