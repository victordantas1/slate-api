"""Fixtures de dados para os testes contra Postgres: household, conta, categoria e
commitments materializados pelo motor.

Tudo é gravado como dono das tabelas (sem RLS), dentro da transação do `db_session`.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.models import Account, Category, Commitment, Entry, Household, Member
from app.services.materialization import create_installment_commitment


@dataclass(frozen=True)
class HouseholdCtx:
    """Uma household com um membro, um cartão e uma categoria de despesa."""

    household_id: uuid.UUID
    member_id: uuid.UUID
    account_id: uuid.UUID
    category_id: uuid.UUID


async def _returning_id(session: AsyncSession, model: type[Base], **values: Any) -> uuid.UUID:
    result = await session.execute(insert(model).values(**values).returning(model.id))  # type: ignore[attr-defined]
    value: uuid.UUID = result.scalar_one()
    return value


async def make_household(
    session: AsyncSession, name: str = "Casa", *, offset: int = 1
) -> HouseholdCtx:
    """`offset` é o `first_installment_offset` do cartão."""
    household_id = await _returning_id(session, Household, name=name)
    member_id = await _returning_id(
        session, Member, household_id=household_id, supabase_user_id=uuid.uuid4(), name="Ana"
    )
    account_id = await _returning_id(
        session,
        Account,
        household_id=household_id,
        name="Nubank",
        kind="credit_card",
        holder_kind="member",
        owner_member_id=member_id,
        first_installment_offset=offset,
    )
    category_id = await _returning_id(
        session, Category, household_id=household_id, name="Casa", direction="expense"
    )
    return HouseholdCtx(household_id, member_id, account_id, category_id)


async def make_commitment(session: AsyncSession, ctx: HouseholdCtx, **overrides: Any) -> Commitment:
    """Um parcelamento materializado pelo motor; `overrides` troca qualquer argumento."""
    values: dict[str, Any] = {
        "household_id": ctx.household_id,
        "account_id": ctx.account_id,
        "category_id": ctx.category_id,
        "kind": "installment",
        "description": "Geladeira",
        "purchase_date": date(2026, 11, 15),
        "total_amount": Decimal("1000.00"),
        "installment_count": 3,
    }
    values.update(overrides)
    return await create_installment_commitment(session, **values)


async def entries_of(session: AsyncSession, commitment_id: uuid.UUID) -> list[Entry]:
    result = await session.scalars(
        select(Entry).where(Entry.commitment_id == commitment_id).order_by(Entry.seq)
    )
    return list(result)
