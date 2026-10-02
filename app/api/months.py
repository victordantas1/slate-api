import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.accounts import AccountKind
from app.api.categories import Direction
from app.api.commitments import EntryStatus, Kind, Source
from app.core.auth import CurrentMember, get_current_member
from app.db.session import get_member_session
from app.services import months as service

router = APIRouter(prefix="/months", tags=["months"])

Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]


class TotalsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    income: Decimal = Field(description="Entradas: entries de categoria `income`")
    expense: Decimal = Field(description="Saídas: entries de categoria `expense`")
    balance: Decimal = Field(description="income - expense")
    installments: Decimal = Field(
        description="Total comprometido em parcelas: saídas de commitments `installment`"
    )


class MonthEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    commitment_id: uuid.UUID
    description: str
    commitment_kind: Kind
    installment_count: int | None
    category_id: uuid.UUID
    category_name: str
    direction: Direction
    seq: int
    competencia: date
    amount: Decimal
    status: EntryStatus
    paid_at: datetime | None
    source: Source
    edited_manually: bool


class MonthAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    account_name: str
    account_kind: AccountKind
    totals: TotalsOut
    entries: list[MonthEntryOut]


class MonthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    month: str = Field(description="Competência no formato yyyy-mm")
    totals: TotalsOut
    accounts: list[MonthAccountOut]


@router.get("/{month}", response_model=MonthOut)
async def get_month(
    month: Annotated[
        str, Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Competência, yyyy-mm")
    ],
    member: Member,
    session: Session,
) -> MonthOut:
    """Entries da competência agrupadas por conta, com entradas, saídas, saldo e o total
    comprometido em parcelas, no mês e por conta. Mês sem lançamento devolve listas
    vazias e totais zerados."""
    year, number = (int(part) for part in month.split("-"))
    if year < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ano inválido")
    view = await service.get_month(session, member.household_id, date(year, number, 1))
    return MonthOut(
        month=month,
        totals=TotalsOut.model_validate(view.totals),
        accounts=[MonthAccountOut.model_validate(a) for a in view.accounts],
    )
