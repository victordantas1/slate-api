"""Materialização de commitments em entries mensais.

`single` é parcelamento de 1x (D6): passa pelo mesmo caminho de `installment`.
`recurring` é materializado num horizonte rolante de `hoje + 24 meses`.
"""

import uuid
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Commitment, Entry
from app.domain.installments import plan_installments
from app.domain.recurring import horizon_end, plan_recurring

# Linhas por INSERT: mantém os parâmetros bem abaixo do limite de 32767 do asyncpg.
_INSERT_CHUNK = 1000


class AccountNotFoundError(LookupError):
    """Conta inexistente ou de outra household."""


async def _get_offset(session: AsyncSession, household_id: uuid.UUID, account_id: uuid.UUID) -> int:
    offset = await session.scalar(
        select(Account.first_installment_offset).where(
            Account.id == account_id, Account.household_id == household_id
        )
    )
    if offset is None:
        raise AccountNotFoundError(account_id)
    return offset


async def create_installment_commitment(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    account_id: uuid.UUID,
    category_id: uuid.UUID,
    kind: str,
    description: str,
    purchase_date: date,
    total_amount: Decimal,
    installment_count: int | None,
) -> Commitment:
    """Grava o commitment e todas as suas entries, ou nada.

    Roda num savepoint dentro da transação de quem chama: uma violação de constraint
    desfaz commitment e entries juntos e propaga o `IntegrityError`, sem abortar a
    transação externa. O commit fica com quem chamou.
    """
    if kind == "single":
        installment_count = 1 if installment_count is None else installment_count
        if installment_count != 1:
            raise ValueError("single é parcelamento de 1x")
    elif kind != "installment":
        raise ValueError(f"kind {kind!r} não é materializado como parcelamento")
    if installment_count is None:
        raise ValueError("installment precisa de installment_count")

    offset = await _get_offset(session, household_id, account_id)

    plan = plan_installments(purchase_date, offset, total_amount, installment_count)

    commitment = Commitment(
        household_id=household_id,
        account_id=account_id,
        category_id=category_id,
        kind=kind,
        description=description,
        purchase_date=purchase_date,
        total_amount=total_amount,
        installment_count=installment_count,
    )
    async with session.begin_nested():
        session.add(commitment)
        await session.flush()
        await session.execute(
            insert(Entry),
            [
                {
                    "household_id": household_id,
                    "commitment_id": commitment.id,
                    "category_id": category_id,
                    "seq": p.seq,
                    "competencia": p.competencia,
                    "amount": p.amount,
                    "status": "previsto",
                    "source": "manual",
                    "edited_manually": False,
                }
                for p in plan
            ],
        )
    await session.refresh(commitment)
    return commitment


async def extend_recurring_horizon(
    session: AsyncSession,
    *,
    today: date,
    commitment_ids: Iterable[uuid.UUID] | None = None,
) -> int:
    """Materializa os recorrentes `active` até `trunc_mes(today) + 24 meses`.

    Idempotente: `ON CONFLICT (commitment_id, competencia) DO NOTHING`, então rodar N
    vezes é rodar uma, e entry existente nunca é sobrescrita. `cancelled` e `settled`
    ficam de fora. Sem `commitment_ids`, estende todos os que a sessão enxerga (sob RLS,
    os da household). Devolve quantas entries foram inseridas; o commit fica com quem
    chamou.

    O job mensal (pg_cron) roda o espelho SQL desta função,
    `slate_jobs.extend_recurring_horizon`; mudou a regra aqui, mude lá também
    (`tests/test_horizon_job.py` compara as duas).
    """
    until = horizon_end(today)
    query = (
        select(
            Commitment.id,
            Commitment.household_id,
            Commitment.category_id,
            Commitment.purchase_date,
            Commitment.recurring_amount,
            Commitment.end_date,
            Account.first_installment_offset,
        )
        .join(
            Account,
            (Account.id == Commitment.account_id)
            & (Account.household_id == Commitment.household_id),
        )
        .where(Commitment.kind == "recurring", Commitment.status == "active")
        # Trava contra uma cascata concorrente que mude `recurring_amount` entre a leitura
        # e o INSERT: o valor velho ficaria para sempre, já que o conflito é descartado.
        .with_for_update(of=Commitment)
    )
    if commitment_ids is not None:
        query = query.where(Commitment.id.in_(list(commitment_ids)))

    rows: list[dict[str, Any]] = []
    for c in (await session.execute(query)).all():
        if c.recurring_amount is None:  # garantido pela ck_commitment_recurring_amounts
            continue
        plan = plan_recurring(
            c.purchase_date,
            c.first_installment_offset,
            c.recurring_amount,
            end_date=c.end_date,
            until=until,
        )
        rows.extend(
            {
                "household_id": c.household_id,
                "commitment_id": c.id,
                "category_id": c.category_id,
                "seq": p.seq,
                "competencia": p.competencia,
                "amount": p.amount,
                "status": "previsto",
                "source": "manual",
                "edited_manually": False,
            }
            for p in plan
        )

    inserted = 0
    for start in range(0, len(rows), _INSERT_CHUNK):
        stmt = (
            pg_insert(Entry)
            .values(rows[start : start + _INSERT_CHUNK])
            .on_conflict_do_nothing(index_elements=[Entry.commitment_id, Entry.competencia])
            .returning(Entry.id)
        )
        inserted += len((await session.execute(stmt)).all())
    return inserted


async def create_recurring_commitment(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    account_id: uuid.UUID,
    category_id: uuid.UUID,
    description: str,
    purchase_date: date,
    recurring_amount: Decimal,
    end_date: date | None,
    today: date,
) -> Commitment:
    """Grava o recorrente e as entries do horizonte dele, ou nada.

    Mesmo savepoint de `create_installment_commitment`: falha desfaz commitment e
    entries juntos sem abortar a transação externa.
    """
    await _get_offset(session, household_id, account_id)

    commitment = Commitment(
        household_id=household_id,
        account_id=account_id,
        category_id=category_id,
        kind="recurring",
        description=description,
        purchase_date=purchase_date,
        recurring_amount=recurring_amount,
        end_date=end_date,
    )
    async with session.begin_nested():
        session.add(commitment)
        await session.flush()
        await extend_recurring_horizon(session, today=today, commitment_ids=[commitment.id])
    await session.refresh(commitment)
    return commitment
