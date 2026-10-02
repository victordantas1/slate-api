import json
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.auth import CurrentMember, get_current_member
from app.core.config import Settings, get_settings


def _engine_kwargs(settings: Settings) -> dict[str, object]:
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_recycle": settings.db_pool_recycle_seconds,
        "pool_pre_ping": True,
        "connect_args": {"statement_cache_size": settings.db_statement_cache_size},
    }


def build_async_engine(settings: Settings) -> AsyncEngine:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL não configurada")
    return create_async_engine(settings.database_url, **_engine_kwargs(settings))


@lru_cache
def get_engine() -> AsyncEngine:
    return build_async_engine(get_settings())


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def apply_rls_claims(session: AsyncSession, member: CurrentMember) -> None:
    # Mesmo GUC que o PostgREST usa e que `auth.jwt()` lê no Supabase. `is_local`
    # limita o valor à transação: com o pooler em transaction mode, um valor de
    # sessão vazaria para a próxima requisição que pegasse a mesma conexão.
    await session.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"),
        {"claims": json.dumps(member.claims)},
    )


async def get_member_session(
    member: Annotated[CurrentMember, Depends(get_current_member)],
) -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session, session.begin():
        await apply_rls_claims(session, member)
        yield session
