"""Criação, consultas e remoção de commitments.

`create_commitment` é o único caminho de escrita de commitment novo: a API manual e o
ingest do FinCoach passam por ele. Saldo devedor e mês de quitação são agregados das
entries não pagas (D10), calculados na consulta e nunca gravados.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Category, Commitment, Entry
from app.services.materialization import create_installment_commitment


class CommitmentNotFoundError(LookupError):
    pass


class CommitmentRuleError(ValueError):
    """Conta ou categoria do pedido inválida para a household."""


class ActiveCommitment(NamedTuple):
    commitment: Commitment
    outstanding_balance: Decimal
    payoff_month: date | None


async def ensure_references(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    account_id: uuid.UUID,
    category_id: uuid.UUID,
) -> None:
    """Conta e categoria precisam existir na household e não estar arquivadas."""
    account_archived = await session.scalar(
        select(Account.archived).where(
            Account.id == account_id, Account.household_id == household_id
        )
    )
    if account_archived is None:
        raise CommitmentRuleError("conta não encontrada")
    if account_archived:
        raise CommitmentRuleError("conta está arquivada")
    category_archived = await session.scalar(
        select(Category.archived).where(
            Category.id == category_id, Category.household_id == household_id
        )
    )
    if category_archived is None:
        raise CommitmentRuleError("categoria não encontrada")
    if category_archived:
        raise CommitmentRuleError("categoria está arquivada")


async def create_commitment(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    account_id: uuid.UUID,
    category_id: uuid.UUID,
    kind: str,
    description: str,
    purchase_date: date,
    total_amount: Decimal,
    installment_count: int | None,
    entry_status: str = "previsto",
    source: str = "manual",
    idempotency_key: str | None = None,
) -> Commitment:
    """Valida conta e categoria e materializa o commitment com todas as entries."""
    await ensure_references(session, household_id, account_id=account_id, category_id=category_id)
    return await create_installment_commitment(
        session,
        household_id=household_id,
        account_id=account_id,
        category_id=category_id,
        kind=kind,
        description=description,
        purchase_date=purchase_date,
        total_amount=total_amount,
        installment_count=installment_count,
        entry_status=entry_status,
        source=source,
        idempotency_key=idempotency_key,
    )


async def list_entries(session: AsyncSession, commitment_id: uuid.UUID) -> Sequence[Entry]:
    return (
        await session.scalars(
            select(Entry).where(Entry.commitment_id == commitment_id).order_by(Entry.seq)
        )
    ).all()


async def list_active(session: AsyncSession, household_id: uuid.UUID) -> list[ActiveCommitment]:
    unpaid = (
        select(
            Entry.commitment_id,
            func.sum(Entry.amount).label("balance"),
            func.max(Entry.competencia).label("payoff"),
        )
        .where(Entry.household_id == household_id, Entry.status != "pago")
        .group_by(Entry.commitment_id)
        .subquery()
    )
    rows = await session.execute(
        select(Commitment, unpaid.c.balance, unpaid.c.payoff)
        .outerjoin(unpaid, unpaid.c.commitment_id == Commitment.id)
        .where(Commitment.household_id == household_id, Commitment.status == "active")
        .order_by(Commitment.created_at, Commitment.id)
    )
    result = []
    for commitment, balance, payoff in rows.tuples():
        # Recorrente sem fim não quita: a maior competência é só o fim do horizonte.
        if commitment.kind == "recurring" and commitment.end_date is None:
            payoff = None
        result.append(ActiveCommitment(commitment, balance or Decimal("0.00"), payoff))
    return result


async def delete_commitment(
    session: AsyncSession, household_id: uuid.UUID, commitment_id: uuid.UUID
) -> None:
    """Remove o commitment e, pela FK em cascata, todas as entries."""
    deleted = await session.scalar(
        delete(Commitment)
        .where(Commitment.id == commitment_id, Commitment.household_id == household_id)
        .returning(Commitment.id)
    )
    if deleted is None:
        raise CommitmentNotFoundError(commitment_id)
