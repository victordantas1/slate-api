import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.categories import Direction
from app.core.auth import CurrentMember, get_current_member
from app.db.session import get_member_session
from app.services import reports as service

router = APIRouter(prefix="/reports", tags=["reports"])

Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
# Fuso do household: o mês corrente vira à meia-noite de Brasília, não de UTC.
HOUSEHOLD_TZ = ZoneInfo("America/Sao_Paulo")


def current_month() -> date:
    return datetime.now(HOUSEHOLD_TZ).date().replace(day=1)


Today = Annotated[date, Depends(current_month)]


def _parse_month(value: str) -> date:
    year, number = (int(part) for part in value.split("-"))
    if year < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ano inválido")
    return date(year, number, 1)


def _start(value: str | None, today: date) -> date:
    return today if value is None else _parse_month(value)


StartMonth = Annotated[
    str | None,
    Query(pattern=MONTH_PATTERN, description="Primeira competência, yyyy-mm. Padrão: mês atual"),
]


class DebtPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    competencia: date
    amount: Decimal = Field(description="Soma das parcelas não pagas da competência")


class IncomePointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    competencia: date
    installments: Decimal = Field(description="Saídas de commitments `installment`")
    income: Decimal = Field(description="Entries de categoria `income`")
    ratio: Decimal | None = Field(
        description="installments / income, com 4 casas; nulo quando não há entrada no mês"
    )


class CategoryDeltaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: uuid.UUID
    name: str
    direction: Direction
    parent_id: uuid.UUID | None
    current: Decimal = Field(description="Total da categoria no mês")
    baseline_average: Decimal = Field(
        description="Média dos meses anteriores; mês sem lançamento conta como zero"
    )
    delta: Decimal = Field(description="current - baseline_average")
    delta_pct: Decimal | None = Field(
        description="delta / baseline_average, com 4 casas; nulo quando a média é zero"
    )


class CategoryDeltaReportOut(BaseModel):
    month: date
    baseline: int
    categories: list[CategoryDeltaOut]


@router.get("/debt-curve", response_model=list[DebtPointOut])
async def debt_curve(
    member: Member,
    session: Session,
    today: Today,
    months: Annotated[int, Query(ge=1, le=120)] = 24,
    start: StartMonth = None,
) -> list[service.DebtPoint]:
    """Quanto falta pagar de parcelamentos em cada mês futuro. Conta só entries de
    commitment `installment` que não estão `pago`; mês sem parcela vem com zero."""
    return await service.debt_curve(
        session, household_id=member.household_id, start=_start(start, today), months=months
    )


@router.get("/committed-income", response_model=list[IncomePointOut])
async def committed_income(
    member: Member,
    session: Session,
    today: Today,
    months: Annotated[int, Query(ge=1, le=120)] = 12,
    start: StartMonth = None,
) -> list[service.IncomePoint]:
    """Fração das entradas de cada mês comprometida com parcelas."""
    return await service.committed_income(
        session, household_id=member.household_id, start=_start(start, today), months=months
    )


@router.get("/category-delta", response_model=CategoryDeltaReportOut)
async def category_delta(
    member: Member,
    session: Session,
    today: Today,
    month: Annotated[
        str | None,
        Query(pattern=MONTH_PATTERN, description="Competência, yyyy-mm. Padrão: mês atual"),
    ] = None,
    baseline: Annotated[int, Query(ge=1, le=24)] = 3,
) -> CategoryDeltaReportOut:
    """Total de cada categoria no mês contra a média dos `baseline` meses anteriores."""
    target = _start(month, today)
    rows = await service.category_delta(
        session, household_id=member.household_id, month=target, baseline=baseline
    )
    return CategoryDeltaReportOut(
        month=target,
        baseline=baseline,
        categories=[CategoryDeltaOut.model_validate(row) for row in rows],
    )
