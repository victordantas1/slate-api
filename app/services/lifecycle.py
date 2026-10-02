"""Fim de vida de um commitment: cancelar, encerrar recorrente e quitar.

O histórico permanece verdadeiro: entry `pago` nunca é apagada nem alterada aqui
(invariante 2). Cancelar a assinatura hoje não apaga o que foi pago em agosto.
"""

import uuid
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Commitment, Entry
from app.domain.recurring import last_competencia
from app.services.cascade import CommitmentStateError, _lock_commitment
from app.services.materialization import _get_offset, extend_recurring_horizon


def _require_active(commitment: Commitment) -> None:
    if commitment.status != "active":
        raise CommitmentStateError(f"commitment está {commitment.status}")


async def cancel_commitment(
    session: AsyncSession, *, household_id: uuid.UUID, commitment_id: uuid.UUID
) -> int:
    """Apaga as entries `previsto` e passa o commitment a `cancelled`.

    `pago` e `confirmado` ficam. `total_amount` continua o valor contratado. Só
    commitment `active` é cancelável. Roda num savepoint; devolve quantas entries saíram
    e o commit fica com quem chamou.
    """
    commitment = await _lock_commitment(session, household_id, commitment_id)
    _require_active(commitment)
    async with session.begin_nested():
        removed = await session.execute(
            delete(Entry)
            .where(Entry.commitment_id == commitment.id, Entry.status == "previsto")
            .returning(Entry.id)
        )
        commitment.status = "cancelled"
        await session.flush()
    return len(removed.all())


async def end_recurring(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    commitment_id: uuid.UUID,
    end_date: date,
    today: date,
) -> int:
    """Grava `end_date` num recorrente `active` e apaga as entries depois do fim.

    A última competência segue `plan_recurring`: `trunc_mes(end_date) +
    first_installment_offset`. Depois dela saem as entries que não estão `pago` nem
    `edited_manually`. Um fim posterior ao antigo estende o horizonte até ele. No fim,
    `settle_if_paid`. Roda num savepoint; devolve quantas entries saíram.
    """
    commitment = await _lock_commitment(session, household_id, commitment_id)
    if commitment.kind != "recurring":
        raise CommitmentStateError("só recorrente é encerrado; use o cancelamento")
    _require_active(commitment)
    if end_date < commitment.purchase_date:
        raise ValueError("end_date precisa ser >= purchase_date")
    offset = await _get_offset(session, household_id, commitment.account_id)
    last = last_competencia(end_date, offset)

    async with session.begin_nested():
        removed = await session.execute(
            delete(Entry)
            .where(
                Entry.commitment_id == commitment.id,
                Entry.competencia > last,
                Entry.status != "pago",
                Entry.edited_manually.is_(False),
            )
            .returning(Entry.id)
        )
        commitment.end_date = end_date
        await session.flush()
        await extend_recurring_horizon(session, today=today, commitment_ids=[commitment.id])
    await settle_if_paid(session, household_id=household_id, commitment_id=commitment.id)
    return len(removed.all())


async def settle_if_paid(
    session: AsyncSession, *, household_id: uuid.UUID, commitment_id: uuid.UUID
) -> bool:
    """Passa um commitment `active` a `settled` quando não resta nada a pagar.

    Nada a pagar: tem entries, todas `pago`, e o plano acabou. Parcelamento e avulso
    acabam nas próprias entries; recorrente precisa de `end_date` e de a última
    competência já estar materializada. Devolve se transicionou.
    """
    commitment = await _lock_commitment(session, household_id, commitment_id)
    if commitment.status != "active":
        return False
    if commitment.kind == "recurring" and commitment.end_date is None:
        return False

    count, unpaid, newest = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Entry.status != "pago"),
                func.max(Entry.competencia),
            ).where(Entry.commitment_id == commitment.id)
        )
    ).one()
    if count == 0 or unpaid > 0:
        return False
    if commitment.kind == "recurring" and commitment.end_date is not None:
        offset = await _get_offset(session, household_id, commitment.account_id)
        if newest < last_competencia(commitment.end_date, offset):
            return False

    commitment.status = "settled"
    await session.flush()
    return True
