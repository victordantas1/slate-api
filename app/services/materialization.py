"""Materialização de commitments em entries mensais.

`single` é parcelamento de 1x (D6): passa pelo mesmo caminho de `installment`.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Commitment, Entry
from app.domain.installments import plan_installments


class AccountNotFoundError(LookupError):
    """Conta inexistente ou de outra household."""


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

    offset = await session.scalar(
        select(Account.first_installment_offset).where(
            Account.id == account_id, Account.household_id == household_id
        )
    )
    if offset is None:
        raise AccountNotFoundError(account_id)

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
