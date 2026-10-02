"""Cascata de edição de commitments e entries.

Invariante 2: entry `pago` nunca muda em nenhuma operação daqui. `forward` e `all`
também pulam entry `edited_manually`, que é como uma edição pontual (`this`) sobrevive
a edições posteriores do commitment pai.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal, get_args

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Commitment, Entry
from app.domain.cascade import resplit_installments
from app.domain.installments import CENT, competencia
from app.services.materialization import _get_offset

Scope = Literal["this", "forward", "all"]
SCOPES: tuple[Scope, ...] = get_args(Scope)

# Linhas por INSERT: mantém os parâmetros bem abaixo do limite de 32767 do asyncpg.
_INSERT_CHUNK = 1000


class EntryNotFoundError(LookupError):
    """Entry inexistente ou de outra household."""


class CommitmentNotFoundError(LookupError):
    """Commitment inexistente ou de outra household."""


class CategoryNotFoundError(LookupError):
    """Categoria inexistente ou de outra household."""


class PaidEntryError(ValueError):
    """Entry `pago` não é editável."""


async def _lock_commitment(
    session: AsyncSession, household_id: uuid.UUID, commitment_id: uuid.UUID
) -> Commitment:
    # FOR UPDATE serializa cascatas e recálculos do mesmo commitment.
    commitment = await session.scalar(
        select(Commitment)
        .where(Commitment.id == commitment_id, Commitment.household_id == household_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if commitment is None:
        raise CommitmentNotFoundError(commitment_id)
    return commitment


async def _sync_total_amount(session: AsyncSession, commitment: Commitment) -> None:
    """Parcelamento e avulso: `total_amount` é a soma das entries (invariante 1)."""
    if commitment.kind == "recurring":
        return
    total = await session.scalar(
        select(func.sum(Entry.amount)).where(Entry.commitment_id == commitment.id)
    )
    if total is None or total <= 0:
        raise ValueError("a soma das parcelas precisa ser > 0")
    commitment.total_amount = total


async def edit_entries(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    entry_id: uuid.UUID,
    scope: Scope,
    amount: Decimal | None = None,
    category_id: uuid.UUID | None = None,
) -> int:
    """Aplica `amount` e/ou `category_id` a partir da entry `entry_id`.

    - `this`: só a entry, e liga `edited_manually`. Entry `pago` é recusada.
    - `forward`: entries do commitment com `competencia >= X`, `NOT edited_manually` e
      `status <> 'pago'`, onde X é a competência da entry âncora.
    - `all`: idem, sem filtro de competência.

    `forward` e `all` também gravam o valor novo no commitment (`category_id`, e
    `recurring_amount` no recorrente), para o horizonte futuro herdar a edição. Em
    parcelamento e avulso, `total_amount` passa a ser a soma das entries em qualquer
    escopo. Roda num savepoint; devolve quantas entries mudaram e o commit fica com quem
    chamou.
    """
    if scope not in SCOPES:
        raise ValueError(f"scope inválido: {scope!r}")
    if amount is None and category_id is None:
        raise ValueError("nada a editar: informe amount e/ou category_id")
    if amount is not None and (amount < 0 or amount != amount.quantize(CENT)):
        raise ValueError("amount precisa ser >= 0 com no máximo 2 casas decimais")

    commitment_id = await session.scalar(
        select(Entry.commitment_id).where(Entry.id == entry_id, Entry.household_id == household_id)
    )
    if commitment_id is None:
        raise EntryNotFoundError(entry_id)
    commitment = await _lock_commitment(session, household_id, commitment_id)
    anchor = await session.scalar(
        select(Entry)
        .where(Entry.id == entry_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if anchor is None:
        raise EntryNotFoundError(entry_id)

    if scope == "this" and anchor.status == "pago":
        raise PaidEntryError(entry_id)
    if category_id is not None:
        found = await session.scalar(
            select(Category.id).where(
                Category.id == category_id, Category.household_id == household_id
            )
        )
        if found is None:
            raise CategoryNotFoundError(category_id)
    if amount is not None and scope != "this" and commitment.kind == "recurring" and amount <= 0:
        raise ValueError("recurring_amount precisa ser > 0")

    values: dict[str, Any] = {}
    if amount is not None:
        values["amount"] = amount
    if category_id is not None:
        values["category_id"] = category_id

    async with session.begin_nested():
        if scope == "this":
            stmt = (
                update(Entry)
                .where(Entry.id == anchor.id)
                .values(**values, edited_manually=True)
                .returning(Entry.id)
            )
        else:
            stmt = (
                update(Entry)
                .where(
                    Entry.commitment_id == commitment.id,
                    Entry.edited_manually.is_(False),
                    Entry.status != "pago",
                )
                .values(**values)
                .returning(Entry.id)
            )
            if scope == "forward":
                stmt = stmt.where(Entry.competencia >= anchor.competencia)
            if category_id is not None:
                commitment.category_id = category_id
            if amount is not None and commitment.kind == "recurring":
                commitment.recurring_amount = amount
        changed = len((await session.execute(stmt)).all())
        if amount is not None:
            await _sync_total_amount(session, commitment)
        await session.flush()
    return changed


def _shift_month(month: date, months: int) -> date:
    index = month.year * 12 + month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


async def reschedule_installments(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    commitment_id: uuid.UUID,
    installment_count: int | None = None,
    total_amount: Decimal | None = None,
) -> Commitment:
    """Recalcula a divisão de um parcelamento com nova contagem e/ou novo total.

    É o `all` aplicado ao plano: entries `pago` e `edited_manually` ficam como estão, e
    `total - soma(travadas)` é redividido entre os outros seqs de `1..installment_count`.
    Seqs livres além da nova contagem são apagados; seqs novos nascem `previsto`.
    Recusa (`InvalidRescheduleError`) contagem abaixo de uma travada ou total que não as
    cobre. Roda num savepoint; o commit fica com quem chamou.
    """
    commitment = await _lock_commitment(session, household_id, commitment_id)
    if commitment.kind == "recurring":
        raise ValueError("recorrente não tem divisão de parcelas")
    count = commitment.installment_count if installment_count is None else installment_count
    total = commitment.total_amount if total_amount is None else total_amount
    if count is None or total is None:  # garantido pela ck_commitment_recurring_amounts
        raise ValueError("parcelamento sem installment_count ou total_amount")
    if commitment.kind == "single" and count != 1:
        raise ValueError("single é parcelamento de 1x")

    entries = list(
        await session.scalars(
            select(Entry)
            .where(Entry.commitment_id == commitment.id)
            .order_by(Entry.seq)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    locked = {e.seq: e.amount for e in entries if e.status == "pago" or e.edited_manually}
    free = resplit_installments(total, count, locked)

    # A competência dos seqs novos sai das entries existentes, não do offset atual da
    # conta, que pode ter mudado depois da compra.
    if entries:
        first = _shift_month(entries[0].competencia, 1 - entries[0].seq)
    else:
        offset = await _get_offset(session, household_id, commitment.account_id)
        first = competencia(commitment.purchase_date, offset, 1)

    by_seq = {e.seq: e for e in entries}
    updates = [
        {"id": by_seq[seq].id, "amount": value}
        for seq, value in free.items()
        if seq in by_seq and by_seq[seq].amount != value
    ]
    inserts = [
        {
            "household_id": commitment.household_id,
            "commitment_id": commitment.id,
            "category_id": commitment.category_id,
            "seq": seq,
            "competencia": _shift_month(first, seq - 1),
            "amount": value,
            "status": "previsto",
            "source": "manual",
            "edited_manually": False,
        }
        for seq, value in free.items()
        if seq not in by_seq
    ]

    async with session.begin_nested():
        # resplit_installments já garantiu que nenhuma travada passa de `count`.
        await session.execute(
            delete(Entry).where(Entry.commitment_id == commitment.id, Entry.seq > count)
        )
        if updates:
            await session.execute(update(Entry), updates)
        for start in range(0, len(inserts), _INSERT_CHUNK):
            await session.execute(insert(Entry), inserts[start : start + _INSERT_CHUNK])
        commitment.installment_count = count
        commitment.total_amount = total
        await session.flush()
    await session.refresh(commitment)
    return commitment
