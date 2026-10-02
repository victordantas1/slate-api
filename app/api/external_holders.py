import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentMember, get_current_member
from app.db.models import Account, ExternalHolder
from app.db.session import get_member_session

router = APIRouter(prefix="/external-holders", tags=["external-holders"])

HolderName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]


class ExternalHolderIn(BaseModel):
    name: HolderName


class ExternalHolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


def _duplicate_name() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "Já existe um titular com esse nome")


async def _get_owned(session: AsyncSession, member: CurrentMember, id: uuid.UUID) -> ExternalHolder:
    # Titular de outra household responde 404, para não revelar que existe.
    holder = await session.scalar(
        select(ExternalHolder).where(
            ExternalHolder.id == id, ExternalHolder.household_id == member.household_id
        )
    )
    if holder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Titular não encontrado")
    return holder


@router.get("", response_model=list[ExternalHolderOut])
async def list_external_holders(member: Member, session: Session) -> list[ExternalHolder]:
    result = await session.scalars(
        select(ExternalHolder)
        .where(ExternalHolder.household_id == member.household_id)
        .order_by(ExternalHolder.name)
    )
    return list(result)


@router.post("", response_model=ExternalHolderOut, status_code=status.HTTP_201_CREATED)
async def create_external_holder(
    body: ExternalHolderIn, member: Member, session: Session
) -> ExternalHolder:
    holder = ExternalHolder(household_id=member.household_id, name=body.name)
    # O savepoint isola a violação de unicidade sem abortar a transação da requisição.
    try:
        async with session.begin_nested():
            session.add(holder)
    except IntegrityError as exc:
        raise _duplicate_name() from exc
    await session.refresh(holder)
    return holder


@router.get("/{holder_id}", response_model=ExternalHolderOut)
async def get_external_holder(
    holder_id: uuid.UUID, member: Member, session: Session
) -> ExternalHolder:
    return await _get_owned(session, member, holder_id)


@router.patch("/{holder_id}", response_model=ExternalHolderOut)
async def update_external_holder(
    holder_id: uuid.UUID, body: ExternalHolderIn, member: Member, session: Session
) -> ExternalHolder:
    holder = await _get_owned(session, member, holder_id)
    try:
        async with session.begin_nested():
            holder.name = body.name
    except IntegrityError as exc:
        await session.refresh(holder)
        raise _duplicate_name() from exc
    return holder


@router.delete("/{holder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_external_holder(
    holder_id: uuid.UUID, member: Member, session: Session
) -> Response:
    holder = await _get_owned(session, member, holder_id)
    # Conta arquivada também conta: a FK de account é RESTRICT.
    linked = await session.scalar(select(exists().where(Account.external_holder_id == holder.id)))
    if linked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Titular tem conta vinculada")
    await session.delete(holder)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
