from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.db.session import get_engine

router = APIRouter()

DatabaseStatus = Literal["ok", "unavailable", "not_configured"]
HealthStatus = Literal["ok", "degraded"]


class HealthResponse(BaseModel):
    status: HealthStatus
    environment: str
    database: DatabaseStatus


async def get_database_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DatabaseStatus:
    # O keepalive depende deste SELECT 1: é ele que gera atividade no Postgres e
    # impede o Supabase de pausar o projeto por inatividade.
    if not settings.database_url:
        return "not_configured"
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return "unavailable"
    return "ok"


@router.get("/health", response_model=HealthResponse)
def get_health(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[DatabaseStatus, Depends(get_database_status)],
) -> HealthResponse:
    if database == "unavailable":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="degraded", environment=settings.environment, database=database
        )
    return HealthResponse(status="ok", environment=settings.environment, database=database)
