"""Visão do mês: a tela inicial do app.

Uma única consulta lê as entries da competência pelo índice
`ix_entry_household_id_competencia`, com commitment, conta e categoria no mesmo
SELECT. Agrupamento e totais saem em Python das próprias linhas devolvidas, então os
totais sempre conferem com a soma das entries da resposta.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Row, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Category, Commitment, Entry

ZERO = Decimal("0.00")

# entry, descrição, kind e parcelas do commitment, conta (id, nome, kind), categoria
# (nome, direction).
MonthRow = tuple[Entry, str, str, int | None, uuid.UUID, str, str, str, str]


@dataclass
class Totals:
    income: Decimal = ZERO
    expense: Decimal = ZERO
    installments: Decimal = ZERO

    @property
    def balance(self) -> Decimal:
        return self.income - self.expense

    def add(self, *, amount: Decimal, direction: str, commitment_kind: str) -> None:
        if direction == "income":
            self.income += amount
        else:
            self.expense += amount
            # Comprometido em parcelas: saídas de parcelamentos, não avulsos nem recorrentes.
            if commitment_kind == "installment":
                self.installments += amount


@dataclass
class MonthEntry:
    id: uuid.UUID
    commitment_id: uuid.UUID
    description: str
    commitment_kind: str
    installment_count: int | None
    category_id: uuid.UUID
    category_name: str
    direction: str
    seq: int
    competencia: date
    amount: Decimal
    status: str
    paid_at: datetime | None
    source: str
    edited_manually: bool


@dataclass
class MonthAccount:
    account_id: uuid.UUID
    account_name: str
    account_kind: str
    totals: Totals = field(default_factory=Totals)
    entries: list[MonthEntry] = field(default_factory=list)


@dataclass
class MonthView:
    month: date
    totals: Totals = field(default_factory=Totals)
    accounts: list[MonthAccount] = field(default_factory=list)


def month_query(household_id: uuid.UUID, month: date) -> Select[MonthRow]:
    return (
        select(
            Entry,
            Commitment.description,
            Commitment.kind,
            Commitment.installment_count,
            Commitment.account_id,
            Account.name,
            Account.kind,
            Category.name,
            Category.direction,
        )
        # Joins pela PK: as FKs compostas já garantem a mesma household.
        .join(Commitment, Commitment.id == Entry.commitment_id)
        .join(Account, Account.id == Commitment.account_id)
        .join(Category, Category.id == Entry.category_id)
        .where(Entry.household_id == household_id, Entry.competencia == month)
        .order_by(Account.name, Account.id, Commitment.description, Entry.commitment_id)
    )


def _build(month: date, rows: list[Row[MonthRow]]) -> MonthView:
    view = MonthView(month=month)
    by_account: dict[uuid.UUID, MonthAccount] = {}
    for (
        entry,
        description,
        commitment_kind,
        installment_count,
        account_id,
        account_name,
        account_kind,
        category_name,
        direction,
    ) in rows:
        group = by_account.get(account_id)
        if group is None:
            group = MonthAccount(account_id, account_name, account_kind)
            by_account[account_id] = group
            view.accounts.append(group)
        group.entries.append(
            MonthEntry(
                id=entry.id,
                commitment_id=entry.commitment_id,
                description=description,
                commitment_kind=commitment_kind,
                installment_count=installment_count,
                category_id=entry.category_id,
                category_name=category_name,
                direction=direction,
                seq=entry.seq,
                competencia=entry.competencia,
                amount=entry.amount,
                status=entry.status,
                paid_at=entry.paid_at,
                source=entry.source,
                edited_manually=entry.edited_manually,
            )
        )
        for totals in (group.totals, view.totals):
            totals.add(amount=entry.amount, direction=direction, commitment_kind=commitment_kind)
    return view


async def get_month(session: AsyncSession, household_id: uuid.UUID, month: date) -> MonthView:
    """Entries da competência `month` agrupadas por conta, com os totais."""
    rows = list((await session.execute(month_query(household_id, month))).all())
    return _build(month, rows)
