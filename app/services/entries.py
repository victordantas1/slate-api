"""Edição e pagamento de entries pela API.

A edição em cascata é do motor (`app.services.cascade`); aqui ficam só as regras do
endpoint em volta dele: entry `pago` é recusada em qualquer escopo e a categoria nova
precisa estar ativa na household.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Entry
from app.services.cascade import (
    EntryNotFoundError,
    PaidEntryError,
    Scope,
    _lock_commitment,
    edit_entries,
)
from app.services.lifecycle import settle_if_paid

__all__ = [
    "EntryNotFoundError",
    "EntryRuleError",
    "EntryStateError",
    "PaidEntryError",
    "confirm_entry",
    "edit_entry",
    "get_entry",
    "pay_entry",
]


class EntryRuleError(ValueError):
    """Pedido inválido para a entry (categoria, valor)."""


class EntryStateError(ValueError):
    """A entry não está no status que a operação exige."""


async def get_entry(session: AsyncSession, household_id: uuid.UUID, entry_id: uuid.UUID) -> Entry:
    entry = await session.scalar(
        select(Entry)
        .where(Entry.id == entry_id, Entry.household_id == household_id)
        .execution_options(populate_existing=True)
    )
    if entry is None:
        raise EntryNotFoundError(entry_id)
    return entry


async def edit_entry(
    session: AsyncSession,
    household_id: uuid.UUID,
    entry_id: uuid.UUID,
    *,
    scope: Scope,
    amount: Decimal | None,
    category_id: uuid.UUID | None,
) -> int:
    """Aplica a edição pelo motor e devolve quantas entries mudaram.

    O motor só recusa `pago` no escopo `this`; em `forward` e `all` ele pularia a âncora
    paga e editaria as seguintes. O endpoint recusa nos três, para quem clicou numa
    entry paga nunca ver outras mudarem.
    """
    entry = await get_entry(session, household_id, entry_id)
    if entry.status == "pago":
        raise PaidEntryError(entry_id)
    if category_id is not None:
        archived = await session.scalar(
            select(Category.archived).where(
                Category.id == category_id, Category.household_id == household_id
            )
        )
        if archived is None:
            raise EntryRuleError("categoria não encontrada")
        if archived:
            raise EntryRuleError("categoria está arquivada")
    try:
        return await edit_entries(
            session,
            household_id=household_id,
            entry_id=entry_id,
            scope=scope,
            amount=amount,
            category_id=category_id,
        )
    except (PaidEntryError, EntryNotFoundError):
        raise
    except ValueError as exc:
        raise EntryRuleError(str(exc)) from exc


async def pay_entry(
    session: AsyncSession,
    household_id: uuid.UUID,
    entry_id: uuid.UUID,
    paid_at: datetime | None = None,
) -> Entry:
    """Marca a entry como `pago` com `paid_at` (padrão: agora, no relógio do banco).

    Trava o commitment antes da entry, na mesma ordem da cascata, e no fim chama
    `settle_if_paid`: pagar a última parcela passa o commitment a `settled`.
    """
    commitment_id = await session.scalar(
        select(Entry.commitment_id).where(Entry.id == entry_id, Entry.household_id == household_id)
    )
    if commitment_id is None:
        raise EntryNotFoundError(entry_id)
    await _lock_commitment(session, household_id, commitment_id)
    entry = await session.scalar(
        select(Entry)
        .where(Entry.id == entry_id, Entry.household_id == household_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if entry is None:
        raise EntryNotFoundError(entry_id)
    if entry.status == "pago":
        raise PaidEntryError(entry_id)
    entry.status = "pago"
    entry.paid_at = paid_at if paid_at is not None else await session.scalar(select(func.now()))
    await session.flush()
    await settle_if_paid(session, household_id=household_id, commitment_id=commitment_id)
    return entry


async def confirm_entry(
    session: AsyncSession,
    household_id: uuid.UUID,
    entry_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
) -> Entry:
    """Passa uma entry `previsto` a `confirmado`, gravando a chave de idempotência.

    Trava o commitment antes da entry, como `pay_entry`. A escrita roda num savepoint:
    se a chave violar a UNIQUE `(household_id, idempotency_key)`, o `IntegrityError`
    propaga sem abortar a transação de quem chamou.
    """
    commitment_id = await session.scalar(
        select(Entry.commitment_id).where(Entry.id == entry_id, Entry.household_id == household_id)
    )
    if commitment_id is None:
        raise EntryNotFoundError(entry_id)
    await _lock_commitment(session, household_id, commitment_id)
    entry = await session.scalar(
        select(Entry)
        .where(Entry.id == entry_id, Entry.household_id == household_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if entry is None:
        raise EntryNotFoundError(entry_id)
    if entry.status != "previsto":
        raise EntryStateError(f"entry está {entry.status}, só previsto é confirmável")
    async with session.begin_nested():
        entry.status = "confirmado"
        entry.idempotency_key = idempotency_key
        await session.flush()
    return entry
