"""Ingest de lançamentos externos (FinCoach) em lote.

Não há escrita própria aqui: entry nova sai de `commitments.create_commitment`, o mesmo
serviço do `POST /commitments`, e o casamento sai de `entries.confirm_entry`. Este
módulo só decide, item a item, qual dos dois chamar, e garante a idempotência pela
UNIQUE `(household_id, idempotency_key)` da entry.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Commitment, Entry
from app.services import commitments, entries

Outcome = Literal["created", "matched", "existing"]
SOURCE = "fincoach"


@dataclass(frozen=True)
class IngestItem:
    idempotency_key: str
    match_entry_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    description: str | None = None
    purchase_date: date | None = None
    amount: Decimal | None = None


class IngestResult(NamedTuple):
    idempotency_key: str
    outcome: Outcome
    entry: Entry


class IngestItemError(Exception):
    """Falha de um item do lote; `index` é a posição dele no lote."""

    def __init__(self, index: int, cause: Exception) -> None:
        super().__init__(f"entries[{index}]: {cause}")
        self.index = index
        self.cause = cause


async def _find_by_key(session: AsyncSession, household_id: uuid.UUID, key: str) -> Entry | None:
    entry: Entry | None = await session.scalar(
        select(Entry)
        .where(Entry.household_id == household_id, Entry.idempotency_key == key)
        .execution_options(populate_existing=True)
    )
    return entry


async def _write(session: AsyncSession, household_id: uuid.UUID, item: IngestItem) -> IngestResult:
    key = item.idempotency_key
    if item.match_entry_id is not None:
        entry = await entries.confirm_entry(
            session, household_id, item.match_entry_id, idempotency_key=key
        )
        return IngestResult(key, "matched", entry)

    assert item.account_id is not None and item.category_id is not None
    assert item.description is not None and item.purchase_date is not None
    assert item.amount is not None
    commitment = await commitments.create_commitment(
        session,
        household_id,
        account_id=item.account_id,
        category_id=item.category_id,
        kind="single",
        description=item.description,
        purchase_date=item.purchase_date,
        total_amount=item.amount,
        installment_count=1,
        entry_status="confirmado",
        source=SOURCE,
        idempotency_key=key,
    )
    (entry,) = await commitments.list_entries(session, commitment.id)
    return IngestResult(key, "created", entry)


async def ingest_one(
    session: AsyncSession, household_id: uuid.UUID, item: IngestItem
) -> IngestResult:
    key = item.idempotency_key
    existing = await _find_by_key(session, household_id, key)
    if existing is not None:
        if item.match_entry_id is not None and existing.id != item.match_entry_id:
            raise entries.EntryStateError("idempotency_key já usada em outra entry")
        return IngestResult(key, "existing", existing)
    try:
        return await _write(session, household_id, item)
    except (IntegrityError, entries.EntryStateError):
        # Outra requisição gravou a mesma chave entre a busca e a escrita: na criação a
        # UNIQUE estoura (num savepoint, então a transação segue viva); no casamento a
        # entry já chega `confirmado`. Nos dois casos a entry dela é a resposta.
        existing = await _find_by_key(session, household_id, key)
        if existing is None:
            raise
        return IngestResult(key, "existing", existing)


async def _lock_matched_commitments(
    session: AsyncSession, household_id: uuid.UUID, items: Sequence[IngestItem]
) -> None:
    """Trava de uma vez, em ordem de id, os commitments das entries a casar.

    Sem isso cada item travaria o seu na ordem do lote, e dois lotes com os mesmos
    commitments em ordens diferentes se esperariam até o Postgres abortar um deles.
    """
    match_ids = {i.match_entry_id for i in items if i.match_entry_id is not None}
    if not match_ids:
        return
    commitment_ids = select(Entry.commitment_id).where(
        Entry.household_id == household_id, Entry.id.in_(match_ids)
    )
    await session.execute(
        select(Commitment.id)
        .where(Commitment.household_id == household_id, Commitment.id.in_(commitment_ids))
        .order_by(Commitment.id)
        .with_for_update()
    )


async def ingest_entries(
    session: AsyncSession, household_id: uuid.UUID, items: Sequence[IngestItem]
) -> list[IngestResult]:
    """Processa o lote em ordem, na transação de quem chamou.

    Qualquer falha de item vira `IngestItemError` e o chamador desfaz o lote inteiro;
    reenviar depois é seguro, porque o que entrou volta como `existing`.
    """
    await _lock_matched_commitments(session, household_id, items)
    results = []
    for index, item in enumerate(items):
        try:
            results.append(await ingest_one(session, household_id, item))
        except (entries.EntryNotFoundError, ValueError) as exc:
            raise IngestItemError(index, exc) from exc
    return results
