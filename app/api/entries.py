import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.commitments import EntryOut
from app.core.auth import CurrentMember, get_current_member
from app.db.session import get_member_session
from app.services import entries as service
from app.services.cascade import Scope

router = APIRouter(prefix="/entries", tags=["entries"])

Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Entry não encontrada"},
    409: {"description": "Entry já está paga"},
}


class EntryUpdate(BaseModel):
    amount: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    category_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _check_any(self) -> Self:
        if self.amount is None and self.category_id is None:
            raise ValueError("informe amount e/ou category_id")
        return self


class EntryUpdateOut(BaseModel):
    affected: int = Field(description="Quantas entries a edição alterou")
    entry: EntryOut


class EntryPay(BaseModel):
    paid_at: AwareDatetime | None = None


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Entry não encontrada")


def _paid() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "Entry paga não é editável")


@router.patch("/{entry_id}", response_model=EntryUpdateOut, responses=_ERRORS)
async def update_entry(
    entry_id: uuid.UUID,
    body: EntryUpdate,
    member: Member,
    session: Session,
    scope: Annotated[Scope, Query(description="this, forward ou all; sem padrão")],
) -> EntryUpdateOut:
    """Edita `amount` e/ou `category_id` com a cascata do motor.

    - `this`: só esta entry, que passa a `edited_manually` e deixa de seguir o commitment.
    - `forward`: esta e as seguintes do commitment, menos pagas e editadas à mão.
    - `all`: todas do commitment, menos pagas e editadas à mão.

    Em `forward` e `all` o commitment também muda, para o horizonte futuro herdar.
    """
    try:
        affected = await service.edit_entry(
            session,
            member.household_id,
            entry_id,
            scope=scope,
            amount=body.amount,
            category_id=body.category_id,
        )
    except service.EntryNotFoundError as exc:
        raise _not_found() from exc
    except service.PaidEntryError as exc:
        raise _paid() from exc
    except service.EntryRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    entry = await service.get_entry(session, member.household_id, entry_id)
    return EntryUpdateOut(affected=affected, entry=EntryOut.model_validate(entry))


@router.post("/{entry_id}/pay", response_model=EntryOut, responses=_ERRORS)
async def pay_entry(
    entry_id: uuid.UUID, member: Member, session: Session, body: EntryPay | None = None
) -> EntryOut:
    """Marca a entry como `pago`. Sem `paid_at`, usa o horário do banco."""
    paid_at: datetime | None = body.paid_at if body is not None else None
    try:
        entry = await service.pay_entry(session, member.household_id, entry_id, paid_at)
    except service.EntryNotFoundError as exc:
        raise _not_found() from exc
    except service.PaidEntryError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Entry já está paga") from exc
    return EntryOut.model_validate(entry)
