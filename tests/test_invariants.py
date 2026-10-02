"""As invariantes do motor como testes de primeira classe, contra Postgres real.

1. Soma das parcelas: as entries de um parcelamento somam exatamente o `total_amount`,
   uma por parcela, com competências consecutivas.
2. Imutabilidade de `pago`: nenhuma operação do motor altera uma entry paga.
3. Idempotência do horizonte: rodar o horizonte de recorrentes N vezes == 1 vez. Entra
   com o horizonte (#11), que é a operação que ela descreve.

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
from sqlalchemy import Row, delete, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Commitment, Entry
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


# Toda operação do motor que grava entries entra aqui. O horizonte de recorrentes (#11)
# e a cascata de edição (#12) acrescentam as suas.
ENGINE_OPERATIONS: dict[str, EngineOperation] = {
    "materializa-outro-parcelamento": _materialize_same_account,
    "materializa-single": _materialize_single_same_month,
    "materializacao-que-falha": _failed_materialization,
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
    before = await paid_snapshot(db_session, household.household_id)
    assert len(before) == 3

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
