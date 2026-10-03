import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import jwt
import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Entry, ExternalHolder
from app.db.session import get_member_session
from app.main import app
from tests.factories import HouseholdCtx, make_commitment, make_household

SECRET = "test-secret-with-at-least-32-bytes!!"


def _headers(household_id: uuid.UUID) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "household_id": str(household_id),
            "member_id": str(uuid.uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def api(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_member_session] = _session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_member_session, None)


async def _holder(session: AsyncSession, ctx: HouseholdCtx, name: str) -> uuid.UUID:
    result = await session.execute(
        insert(ExternalHolder)
        .values(household_id=ctx.household_id, name=name)
        .returning(ExternalHolder.id)
    )
    holder_id: uuid.UUID = result.scalar_one()
    return holder_id


async def _external_account(
    session: AsyncSession, ctx: HouseholdCtx, holder_id: uuid.UUID, *, archived: bool = False
) -> uuid.UUID:
    result = await session.execute(
        insert(Account)
        .values(
            household_id=ctx.household_id,
            name=f"Cartão {uuid.uuid4().hex[:6]}",
            kind="credit_card",
            holder_kind="external",
            external_holder_id=holder_id,
            archived=archived,
        )
        .returning(Account.id)
    )
    account_id: uuid.UUID = result.scalar_one()
    return account_id


async def _pay(session: AsyncSession, commitment_id: uuid.UUID, competencia: date) -> None:
    await session.execute(
        update(Entry)
        .where(Entry.commitment_id == commitment_id, Entry.competencia == competencia)
        .values(status="pago", paid_at=datetime.now(UTC))
    )


async def test_balance_sums_unpaid_entries_per_holder(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    vo = await _holder(db_session, household, "Vó")
    tio = await _holder(db_session, household, "Tio")
    cartao_vo = await _external_account(db_session, household, vo)
    arquivado_vo = await _external_account(db_session, household, vo, archived=True)
    cartao_tio = await _external_account(db_session, household, tio)
    # 3x de 300 no cartão da Vó, a primeira já reembolsada.
    geladeira = await make_commitment(
        db_session, household, account_id=cartao_vo, total_amount=Decimal("900.00")
    )
    await _pay(db_session, geladeira.id, date(2026, 12, 1))
    # Conta arquivada ainda deve.
    await make_commitment(
        db_session,
        household,
        account_id=arquivado_vo,
        kind="single",
        total_amount=Decimal("45.50"),
        installment_count=1,
    )
    # Tudo do Tio já foi reembolsado.
    tv = await make_commitment(
        db_session,
        household,
        account_id=cartao_tio,
        kind="single",
        total_amount=Decimal("100.00"),
        installment_count=1,
    )
    await _pay(db_session, tv.id, date(2026, 12, 1))
    # Conta do próprio membro não é dívida com titular externo.
    await make_commitment(db_session, household, total_amount=Decimal("700.00"))

    response = await api.get("/external-holders/balance", headers=_headers(household.household_id))

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"id": str(tio), "name": "Tio", "balance": "0.00"},
        {"id": str(vo), "name": "Vó", "balance": "645.50"},
    ]


async def test_holder_without_accounts_returns_zero(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    vo = await _holder(db_session, household, "Vó")

    response = await api.get("/external-holders/balance", headers=_headers(household.household_id))

    assert response.json() == [{"id": str(vo), "name": "Vó", "balance": "0.00"}]


async def test_balance_only_lists_own_household(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    other = await make_household(db_session, "Outra")
    holder = await _holder(db_session, other, "Vó")
    account = await _external_account(db_session, other, holder)
    await make_commitment(db_session, other, account_id=account, total_amount=Decimal("90.00"))

    response = await api.get("/external-holders/balance", headers=_headers(household.household_id))

    assert response.status_code == 200
    assert response.json() == []


async def test_balance_requires_token(api: httpx.AsyncClient) -> None:
    response = await api.get("/external-holders/balance")

    assert response.status_code == 401


async def test_no_transfer_table_exists(db_session: AsyncSession) -> None:
    # O reembolso é a entry marcada como `pago`; uma tabela de transferência contaria o
    # mesmo dinheiro duas vezes.
    tables = await db_session.scalars(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )

    assert not [name for name in tables if "transfer" in name]
