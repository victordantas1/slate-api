import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentMember, get_current_member
from app.db.models import Account
from app.db.session import get_member_session
from app.services import accounts as service

router = APIRouter(prefix="/accounts", tags=["accounts"])

AccountKind = Literal["checking", "credit_card", "store_credit"]
HolderKind = Literal["member", "external"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Day = Annotated[int, Field(ge=1, le=31)]
# Mesmo piso de `app.domain.installments.competencia`; o teto é o do SMALLINT.
Offset = Annotated[int, Field(ge=0, le=32767)]


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Name
    kind: AccountKind
    holder_kind: HolderKind
    owner_member_id: uuid.UUID | None = None
    external_holder_id: uuid.UUID | None = None
    closing_day: Day | None = None
    due_day: Day | None = None
    first_installment_offset: Offset = 1


class AccountUpdate(BaseModel):
    """Só os campos enviados são alterados. `kind` não é editável: `extra="forbid"`."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    holder_kind: HolderKind | None = None
    owner_member_id: uuid.UUID | None = None
    external_holder_id: uuid.UUID | None = None
    closing_day: Day | None = None
    due_day: Day | None = None
    first_installment_offset: Offset | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def _required_fields_are_not_null(self) -> "AccountUpdate":
        for field in ("name", "holder_kind", "first_installment_offset", "archived"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} não pode ser null")
        return self


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    household_id: uuid.UUID
    name: str
    kind: AccountKind
    holder_kind: HolderKind
    owner_member_id: uuid.UUID | None
    external_holder_id: uuid.UUID | None
    closing_day: int | None
    due_day: int | None
    first_installment_offset: int
    archived: bool
    created_at: datetime


Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")


def _invalid(exc: service.InvalidAccountError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.get("", response_model=list[AccountOut])
async def list_accounts(
    member: Member, session: Session, include_archived: bool = False
) -> Sequence[Account]:
    return await service.list_accounts(
        session, member.household_id, include_archived=include_archived
    )


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(payload: AccountCreate, member: Member, session: Session) -> Account:
    try:
        return await service.create_account(session, member.household_id, payload.model_dump())
    except service.InvalidAccountError as exc:
        raise _invalid(exc) from exc


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: uuid.UUID, member: Member, session: Session) -> Account:
    try:
        return await service.get_account(session, member.household_id, account_id)
    except service.AccountNotFoundError as exc:
        raise _not_found() from exc


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: uuid.UUID, payload: AccountUpdate, member: Member, session: Session
) -> Account:
    try:
        return await service.update_account(
            session, member.household_id, account_id, payload.model_dump(exclude_unset=True)
        )
    except service.AccountNotFoundError as exc:
        raise _not_found() from exc
    except service.InvalidAccountError as exc:
        raise _invalid(exc) from exc
