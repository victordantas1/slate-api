import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentMember, get_current_member
from app.db.session import get_member_session
from app.services import commitments as service
from app.services.materialization import AccountNotFoundError, create_installment_commitment

router = APIRouter(prefix="/commitments", tags=["commitments"])

Kind = Literal["installment", "recurring", "single"]
CommitmentStatus = Literal["active", "cancelled", "settled"]
EntryStatus = Literal["previsto", "confirmado", "pago"]
Source = Literal["manual", "fincoach"]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]


class CommitmentCreate(BaseModel):
    account_id: uuid.UUID
    category_id: uuid.UUID
    kind: Kind
    description: Description
    purchase_date: date
    total_amount: Money | None = None
    # Opcional em `single`, que é sempre 1x.
    installment_count: Annotated[int, Field(ge=1, le=360)] | None = None
    recurring_amount: Money | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "recurring":
            # Materialização do recorrente é a #11; o enum já é o do banco.
            raise ValueError("recurring ainda não é suportado")
        if self.total_amount is None:
            raise ValueError(f"{self.kind} precisa de total_amount")
        if self.recurring_amount is not None or self.end_date is not None:
            raise ValueError("recurring_amount e end_date são só de recurring")
        if self.kind == "installment" and self.installment_count is None:
            raise ValueError("installment precisa de installment_count")
        if self.kind == "single" and self.installment_count not in (None, 1):
            raise ValueError("single é parcelamento de 1x")
        return self


class EntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category_id: uuid.UUID
    seq: int
    competencia: date
    amount: Decimal
    status: EntryStatus
    paid_at: datetime | None
    source: Source
    edited_manually: bool


class CommitmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    category_id: uuid.UUID
    kind: Kind
    description: str
    purchase_date: date
    total_amount: Decimal | None
    installment_count: int | None
    recurring_amount: Decimal | None
    end_date: date | None
    status: CommitmentStatus
    created_at: datetime


class CommitmentWithEntries(CommitmentOut):
    entries: list[EntryOut]


class ActiveCommitmentOut(CommitmentOut):
    # Derivados das entries não pagas (D10), nunca colunas.
    outstanding_balance: Decimal
    payoff_month: date | None


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


@router.post("", response_model=CommitmentWithEntries, status_code=status.HTTP_201_CREATED)
async def create_commitment(
    body: CommitmentCreate, member: Member, session: Session
) -> CommitmentWithEntries:
    """Cria o commitment e materializa todas as entries na mesma transação."""
    assert body.total_amount is not None
    try:
        await service.ensure_references(
            session,
            member.household_id,
            account_id=body.account_id,
            category_id=body.category_id,
        )
        commitment = await create_installment_commitment(
            session,
            household_id=member.household_id,
            account_id=body.account_id,
            category_id=body.category_id,
            kind=body.kind,
            description=body.description,
            purchase_date=body.purchase_date,
            total_amount=body.total_amount,
            installment_count=body.installment_count,
        )
    except service.CommitmentRuleError as exc:
        raise _unprocessable(str(exc)) from exc
    except AccountNotFoundError as exc:
        raise _unprocessable("conta não encontrada") from exc
    entries = await service.list_entries(session, commitment.id)
    return CommitmentWithEntries.model_validate(
        {
            **CommitmentOut.model_validate(commitment).model_dump(),
            "entries": [EntryOut.model_validate(e) for e in entries],
        }
    )


@router.get("/active", response_model=list[ActiveCommitmentOut])
async def list_active_commitments(member: Member, session: Session) -> list[ActiveCommitmentOut]:
    rows = await service.list_active(session, member.household_id)
    return [
        ActiveCommitmentOut(
            **CommitmentOut.model_validate(row.commitment).model_dump(),
            outstanding_balance=row.outstanding_balance,
            payoff_month=row.payoff_month,
        )
        for row in rows
    ]


@router.delete(
    "/{commitment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={501: {"description": "keep_paid=true ainda não implementado"}},
)
async def delete_commitment(
    commitment_id: uuid.UUID, member: Member, session: Session, keep_paid: bool = False
) -> Response:
    """Sem `keep_paid`, apaga o commitment e todas as entries, inclusive as pagas.

    `keep_paid=true` é o cancelamento (preserva as pagas, remove as previstas), que
    depende do motor de cancelamento e por ora responde 501 sem alterar nada.
    """
    if keep_paid:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "keep_paid=true ainda não é suportado")
    try:
        await service.delete_commitment(session, member.household_id, commitment_id)
    except service.CommitmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commitment não encontrado") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
