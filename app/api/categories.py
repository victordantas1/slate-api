import uuid
from collections.abc import Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentMember, get_current_member
from app.db.session import get_member_session
from app.services import categories as service

router = APIRouter(prefix="/categories", tags=["categories"])

Direction = Literal["expense", "income"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    direction: Direction
    archived: bool
    created_at: datetime


class CategoryCreate(BaseModel):
    name: Name
    parent_id: uuid.UUID | None = None
    # Sem direction, a subcategoria herda a do pai e a raiz vira `expense`.
    direction: Direction | None = None


class CategoryUpdate(BaseModel):
    name: Name | None = None
    parent_id: uuid.UUID | None = None
    archived: bool | None = None


class SeedOut(BaseModel):
    created: int


async def _call[T](work: Coroutine[Any, Any, T]) -> T:
    try:
        return await work
    except service.CategoryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoria não encontrada") from exc
    except service.CategoryConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except service.CategoryRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    member: Member, session: Session, include_archived: bool = False
) -> list[CategoryOut]:
    rows = await service.list_categories(
        session, member.household_id, include_archived=include_archived
    )
    return [CategoryOut.model_validate(row) for row in rows]


@router.post("/seed", response_model=SeedOut)
async def seed_categories(member: Member, session: Session) -> SeedOut:
    created = await service.seed_default_categories(session, member.household_id)
    return SeedOut(created=created)


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(body: CategoryCreate, member: Member, session: Session) -> CategoryOut:
    category = await _call(
        service.create_category(
            session,
            member.household_id,
            name=body.name,
            direction=body.direction,
            parent_id=body.parent_id,
        )
    )
    return CategoryOut.model_validate(category)


@router.get("/{category_id}", response_model=CategoryOut)
async def get_category(category_id: uuid.UUID, member: Member, session: Session) -> CategoryOut:
    category = await _call(service.get_category(session, member.household_id, category_id))
    return CategoryOut.model_validate(category)


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: uuid.UUID, body: CategoryUpdate, member: Member, session: Session
) -> CategoryOut:
    changes = body.model_dump(exclude_unset=True)
    for key in ("name", "archived"):
        if key in changes and changes[key] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{key} não aceita null")
    category = await _call(
        service.update_category(session, member.household_id, category_id, changes)
    )
    return CategoryOut.model_validate(category)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_category(category_id: uuid.UUID, member: Member, session: Session) -> Response:
    """Arquiva: não há delete físico de categoria, para entries nunca perderem a FK."""
    await _call(service.archive_category(session, member.household_id, category_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
