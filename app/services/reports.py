"""Relatórios: cada um é uma única consulta agregada no Postgres, sem laço em Python.

Os meses da série saem de `generate_series`, então mês sem lançamento aparece com
zero em vez de sumir. As definições seguem a tela de mês: entrada é entry de
categoria `income`; parcela é entry de commitment `installment`.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    Date,
    and_,
    cast,
    func,
    literal,
    literal_column,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Subquery

from app.db.models import Category, Commitment, Entry

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class DebtPoint:
    competencia: date
    amount: Decimal


@dataclass(frozen=True)
class IncomePoint:
    competencia: date
    installments: Decimal
    income: Decimal
    ratio: Decimal | None


@dataclass(frozen=True)
class CategoryDelta:
    category_id: uuid.UUID
    name: str
    direction: str
    parent_id: uuid.UUID | None
    current: Decimal
    baseline_average: Decimal
    delta: Decimal
    delta_pct: Decimal | None


def _months_after(start: date, months: int) -> ColumnElement[date]:
    """`start + months` meses, calculado no banco (sem estouro de `date` em Python)."""
    shifted = cast(literal(start, Date), TIMESTAMP) + func.make_interval(0, months)
    return cast(shifted, Date)


def _month_series(start: date, months: int) -> Subquery:
    """Subquery com uma linha por competência, de `start` até `start + months - 1`."""
    first = cast(literal(start, Date), TIMESTAMP)
    last = first + func.make_interval(0, months - 1)
    series = func.generate_series(first, last, literal_column("interval '1 month'"))
    return select(cast(series, Date).label("competencia")).subquery("months")


async def debt_curve(
    session: AsyncSession, *, household_id: uuid.UUID, start: date, months: int
) -> list[DebtPoint]:
    """Soma das parcelas ainda não pagas de cada competência a partir de `start`."""
    series = _month_series(start, months)
    unpaid_installments = Entry.__table__.join(
        Commitment.__table__,
        and_(Commitment.id == Entry.commitment_id, Commitment.kind == "installment"),
    )
    stmt = (
        select(series.c.competencia, func.coalesce(func.sum(Entry.amount), ZERO))
        .select_from(
            series.outerjoin(
                unpaid_installments,
                and_(
                    Entry.competencia == series.c.competencia,
                    Entry.household_id == household_id,
                    Entry.status != "pago",
                ),
            )
        )
        .group_by(series.c.competencia)
        .order_by(series.c.competencia)
    )
    rows = await session.execute(stmt)
    return [DebtPoint(competencia, amount) for competencia, amount in rows]


async def committed_income(
    session: AsyncSession, *, household_id: uuid.UUID, start: date, months: int
) -> list[IncomePoint]:
    """Parcelas do mês divididas pelas entradas do mês, para cada competência.

    Parcela é saída de commitment `installment`, paga ou não: é o que o mês compromete.
    Sem entrada no mês, a razão é nula em vez de dividir por zero.
    """
    series = _month_series(start, months)
    entries = Entry.__table__.join(Commitment.__table__, Commitment.id == Entry.commitment_id).join(
        Category.__table__, Category.id == Entry.category_id
    )
    is_installment = and_(Category.direction == "expense", Commitment.kind == "installment")
    installments = func.coalesce(func.sum(Entry.amount).filter(is_installment), ZERO)
    income = func.coalesce(func.sum(Entry.amount).filter(Category.direction == "income"), ZERO)
    ratio = func.round(installments / func.nullif(income, 0), 4)
    stmt = (
        select(series.c.competencia, installments, income, ratio)
        .select_from(
            series.outerjoin(
                entries,
                and_(
                    Entry.competencia == series.c.competencia,
                    Entry.household_id == household_id,
                ),
            )
        )
        .group_by(series.c.competencia)
        .order_by(series.c.competencia)
    )
    rows = await session.execute(stmt)
    return [IncomePoint(*row) for row in rows]


async def category_delta(
    session: AsyncSession, *, household_id: uuid.UUID, month: date, baseline: int
) -> list[CategoryDelta]:
    """Total de cada categoria em `month` contra a média dos `baseline` meses anteriores.

    A média divide pelo número de meses da janela, contando mês sem lançamento como
    zero. Categoria sem histórico tem média zero e `delta_pct` nulo; categoria que só
    aparece na janela entra com total zero no mês.
    """
    current = func.coalesce(func.sum(Entry.amount).filter(Entry.competencia == month), ZERO)
    history = func.coalesce(func.sum(Entry.amount).filter(Entry.competencia < month), ZERO)
    average = func.round(history / baseline, 2)
    delta = current - average
    delta_pct = func.round(delta / func.nullif(average, 0), 4)
    stmt = (
        select(
            Category.id,
            Category.name,
            Category.direction,
            Category.parent_id,
            current,
            average,
            delta,
            delta_pct,
        )
        .select_from(Entry)
        .join(Category, Category.id == Entry.category_id)
        .where(
            Entry.household_id == household_id,
            Entry.competencia >= _months_after(month, -baseline),
            Entry.competencia <= month,
        )
        .group_by(Category.id)
        .order_by(func.abs(delta).desc(), Category.name, Category.id)
    )
    rows = await session.execute(stmt)
    return [CategoryDelta(*row) for row in rows]
