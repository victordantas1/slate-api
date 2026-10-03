import uuid
from datetime import date
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.commitments import Description, EntryOut, Money
from app.core.auth import CurrentMember, get_current_member
from app.db.session import get_member_session
from app.services import entries
from app.services import ingest as service

router = APIRouter(prefix="/ingest", tags=["ingest"])

Member = Annotated[CurrentMember, Depends(get_current_member)]
Session = Annotated[AsyncSession, Depends(get_member_session)]
IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=200)]

MAX_BATCH = 500


class IngestEntryIn(BaseModel):
    idempotency_key: IdempotencyKey = Field(
        description="Chave do lançamento no FinCoach; reenviar a mesma chave não duplica"
    )
    match_entry_id: uuid.UUID | None = Field(
        default=None,
        description="Entry `previsto` a confirmar em vez de criar uma nova; com ela, os "
        "campos de criação são ignorados",
    )
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    description: Description | None = None
    purchase_date: date | None = None
    amount: Money | None = None

    @model_validator(mode="after")
    def _check_create_fields(self) -> Self:
        if self.match_entry_id is None:
            missing = [
                name
                for name in ("account_id", "category_id", "description", "purchase_date", "amount")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"sem match_entry_id, informe {', '.join(missing)}")
        return self


class IngestBatch(BaseModel):
    entries: Annotated[list[IngestEntryIn], Field(min_length=1, max_length=MAX_BATCH)]


class IngestResultOut(BaseModel):
    idempotency_key: str
    outcome: Literal["created", "matched", "existing"] = Field(
        description="created: entry nova; matched: entry existente confirmada; "
        "existing: a chave já tinha entrado e nada foi gravado"
    )
    entry: EntryOut


class IngestBatchOut(BaseModel):
    results: list[IngestResultOut]


@router.post(
    "/entries",
    response_model=IngestBatchOut,
    responses={
        404: {"description": "match_entry_id não encontrada"},
        409: {"description": "match_entry_id não está previsto"},
    },
)
async def ingest_entries(body: IngestBatch, member: Member, session: Session) -> IngestBatchOut:
    """Recebe lançamentos do FinCoach em lote, de forma idempotente.

    Toda entry criada aqui é gravada com `source='fincoach'`; a origem é a própria rota.

    Cada item cria uma entry `confirmado` (um commitment `single`, pelo mesmo serviço do
    `POST /commitments`) ou, com `match_entry_id`, confirma uma entry `previsto`. Uma
    chave que já entrou devolve a entry dela sem gravar nada. O lote é atômico: um item
    inválido devolve o erro com o índice dele e nenhum item é gravado.
    """
    items = [
        service.IngestItem(
            idempotency_key=e.idempotency_key,
            match_entry_id=e.match_entry_id,
            account_id=e.account_id,
            category_id=e.category_id,
            description=e.description,
            purchase_date=e.purchase_date,
            amount=e.amount,
        )
        for e in body.entries
    ]
    try:
        results = await service.ingest_entries(session, member.household_id, items)
    except service.IngestItemError as exc:
        if isinstance(exc.cause, entries.EntryNotFoundError):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc.cause, entries.EntryStateError):
            code = status.HTTP_409_CONFLICT
        else:
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(exc)) from exc
    return IngestBatchOut(
        results=[
            IngestResultOut(
                idempotency_key=r.idempotency_key,
                outcome=r.outcome,
                entry=EntryOut.model_validate(r.entry),
            )
            for r in results
        ]
    )
