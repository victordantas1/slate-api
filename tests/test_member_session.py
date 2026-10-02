import json
import time
import uuid
from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import CurrentMember
from app.core.config import get_settings
from app.db.session import apply_rls_claims, get_engine, get_member_session

DATABASE_URL = get_settings().database_url

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "requer DATABASE_URL configurada para um Postgres real; pulado até a suíte "
        "de testcontainers (issue #15) padronizar isso em todo ambiente"
    ),
)


def _member() -> CurrentMember:
    household_id, member_id = uuid.uuid4(), uuid.uuid4()
    return CurrentMember(
        member_id=member_id,
        household_id=household_id,
        claims={
            "sub": str(uuid.uuid4()),
            "role": "authenticated",
            "household_id": str(household_id),
            "member_id": str(member_id),
        },
    )


async def test_claims_are_visible_to_rls_inside_the_transaction() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    member = _member()
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await apply_rls_claims(session, member)

            raw = await session.scalar(text("SELECT current_setting('request.jwt.claims', true)"))
            household = await session.scalar(
                text("SELECT current_setting('request.jwt.claims', true)::jsonb->>'household_id'")
            )

        assert raw is not None
        assert json.loads(raw) == member.claims
        assert household == str(member.household_id)
    finally:
        await engine.dispose()


async def test_claims_do_not_leak_past_the_transaction() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                async with async_sessionmaker(bind=conn)() as session:
                    await apply_rls_claims(session, _member())

            leftover = await conn.scalar(text("SELECT current_setting('request.jwt.claims', true)"))

        assert not leftover
    finally:
        await engine.dispose()


def test_member_session_dependency_opens_transaction_with_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-secret-with-at-least-32-bytes!!"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    get_engine.cache_clear()
    member = _member()
    token = jwt.encode(
        {**member.claims, "aud": "authenticated", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    app = FastAPI()

    @app.get("/claims")
    async def claims(
        session: Annotated[AsyncSession, Depends(get_member_session)],
    ) -> dict[str, str | None]:
        household = await session.scalar(
            text("SELECT current_setting('request.jwt.claims', true)::jsonb->>'household_id'")
        )
        return {"household_id": household}

    try:
        with TestClient(app) as client:
            ok = client.get("/claims", headers={"Authorization": f"Bearer {token}"})
            anonymous = client.get("/claims")
    finally:
        get_engine.cache_clear()

    assert ok.status_code == 200
    assert ok.json() == {"household_id": str(member.household_id)}
    assert anonymous.status_code == 401
