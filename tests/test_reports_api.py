import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import event, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reports import current_month
from app.db.models import Category, Entry
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
    app.dependency_overrides[current_month] = lambda: date(2026, 12, 1)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_member_session, None)
        app.dependency_overrides.pop(current_month, None)


@pytest.fixture
async def statements(db_session: AsyncSession) -> AsyncIterator[list[str]]:
    """SQL executado na conexão do teste, a partir do momento em que a fixture entra."""
    executed: list[str] = []
    conn = (await db_session.connection()).sync_connection
    assert conn is not None

    def _record(*args: Any) -> None:
        executed.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    yield executed
    event.remove(conn, "before_cursor_execute", _record)


async def _income_category(
    session: AsyncSession, ctx: HouseholdCtx, name: str = "Salário"
) -> uuid.UUID:
    result = await session.execute(
        insert(Category)
        .values(household_id=ctx.household_id, name=name, direction="income")
        .returning(Category.id)
    )
    category_id: uuid.UUID = result.scalar_one()
    return category_id


async def _expense_category(session: AsyncSession, ctx: HouseholdCtx, name: str) -> uuid.UUID:
    result = await session.execute(
        insert(Category)
        .values(household_id=ctx.household_id, name=name, direction="expense")
        .returning(Category.id)
    )
    category_id: uuid.UUID = result.scalar_one()
    return category_id


async def _single(
    session: AsyncSession, ctx: HouseholdCtx, category_id: uuid.UUID, purchase: date, amount: str
) -> None:
    """Avulso de 1x; com offset 1 a competência é o mês seguinte a `purchase`."""
    await make_commitment(
        session,
        ctx,
        kind="single",
        description="Avulso",
        category_id=category_id,
        purchase_date=purchase,
        total_amount=Decimal(amount),
        installment_count=1,
    )


async def _pay(session: AsyncSession, commitment_id: uuid.UUID, competencia: date) -> None:
    await session.execute(
        update(Entry)
        .where(Entry.commitment_id == commitment_id, Entry.competencia == competencia)
        .values(status="pago", paid_at=datetime.now(UTC))
    )


def _point(body: list[dict[str, Any]], month: str) -> dict[str, Any]:
    return next(p for p in body if p["competencia"] == f"{month}-01")


# --- debt-curve -------------------------------------------------------------------


async def test_debt_curve_counts_only_unpaid_installments(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    # Parcelas de 300 em 2026-12, 2027-01 e 2027-02; a primeira paga.
    geladeira = await make_commitment(db_session, household, total_amount=Decimal("900.00"))
    await _pay(db_session, geladeira.id, date(2026, 12, 1))
    # Avulso não é parcelamento: fica fora mesmo sem pagar.
    await _single(db_session, household, household.category_id, date(2026, 12, 10), "50.00")

    response = await api.get(
        "/reports/debt-curve", params={"months": 4}, headers=_headers(household.household_id)
    )

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"competencia": "2026-12-01", "amount": "0.00"},
        {"competencia": "2027-01-01", "amount": "300.00"},
        {"competencia": "2027-02-01", "amount": "300.00"},
        {"competencia": "2027-03-01", "amount": "0.00"},
    ]


async def test_debt_curve_defaults_to_24_months_from_current_month(
    api: httpx.AsyncClient, household: HouseholdCtx
) -> None:
    response = await api.get("/reports/debt-curve", headers=_headers(household.household_id))

    body = response.json()
    assert len(body) == 24
    assert body[0]["competencia"] == "2026-12-01"
    assert body[-1]["competencia"] == "2028-11-01"


async def test_debt_curve_start_param(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    await make_commitment(db_session, household, total_amount=Decimal("900.00"))

    response = await api.get(
        "/reports/debt-curve",
        params={"months": 2, "start": "2027-02"},
        headers=_headers(household.household_id),
    )

    assert [p["amount"] for p in response.json()] == ["300.00", "0.00"]


async def test_debt_curve_ignores_other_household(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    other = await make_household(db_session, "Outra")
    await make_commitment(db_session, other, total_amount=Decimal("900.00"))

    response = await api.get(
        "/reports/debt-curve", params={"months": 3}, headers=_headers(household.household_id)
    )

    assert {p["amount"] for p in response.json()} == {"0.00"}


# --- committed-income -------------------------------------------------------------


async def test_committed_income_ratio(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    salario = await _income_category(db_session, household)
    geladeira = await make_commitment(db_session, household, total_amount=Decimal("900.00"))
    # Paga também conta: é o que o mês comprometeu.
    await _pay(db_session, geladeira.id, date(2026, 12, 1))
    await _single(db_session, household, salario, date(2026, 11, 5), "1200.00")
    await _single(db_session, household, salario, date(2026, 12, 5), "4000.00")
    # Avulso de despesa não é parcela.
    await _single(db_session, household, household.category_id, date(2026, 11, 20), "80.00")

    response = await api.get(
        "/reports/committed-income", params={"months": 2}, headers=_headers(household.household_id)
    )

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "competencia": "2026-12-01",
            "installments": "300.00",
            "income": "1200.00",
            "ratio": "0.2500",
        },
        {
            "competencia": "2027-01-01",
            "installments": "300.00",
            "income": "4000.00",
            "ratio": "0.0750",
        },
    ]


async def test_committed_income_without_income_has_null_ratio(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    await make_commitment(db_session, household, total_amount=Decimal("900.00"))

    response = await api.get(
        "/reports/committed-income", params={"months": 4}, headers=_headers(household.household_id)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert _point(body, "2027-01") == {
        "competencia": "2027-01-01",
        "installments": "300.00",
        "income": "0.00",
        "ratio": None,
    }
    assert _point(body, "2027-03")["ratio"] is None
    assert len(body) == 4


# --- category-delta ---------------------------------------------------------------


async def test_category_delta_against_baseline_average(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    mercado = await _expense_category(db_session, household, "Mercado")
    # Competências 2026-09, 2026-10 e 2026-11 (baseline) e 2026-12 (mês).
    for purchase, amount in [
        (date(2026, 8, 3), "300.00"),
        (date(2026, 9, 3), "600.00"),
        (date(2026, 11, 3), "500.00"),
    ]:
        await _single(db_session, household, mercado, purchase, amount)
    # Fora da janela: não entra na média.
    await _single(db_session, household, mercado, date(2026, 7, 3), "9999.00")

    response = await api.get(
        "/reports/category-delta",
        params={"month": "2026-12", "baseline": 3},
        headers=_headers(household.household_id),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month"] == "2026-12-01"
    assert body["baseline"] == 3
    assert body["categories"] == [
        {
            "category_id": str(mercado),
            "name": "Mercado",
            "direction": "expense",
            "parent_id": None,
            "current": "500.00",
            "baseline_average": "300.00",
            "delta": "200.00",
            "delta_pct": "0.6667",
        }
    ]


async def test_category_delta_without_history_has_zero_baseline(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    await _single(db_session, household, household.category_id, date(2026, 11, 3), "120.00")

    response = await api.get(
        "/reports/category-delta",
        params={"month": "2026-12"},
        headers=_headers(household.household_id),
    )

    [category] = response.json()["categories"]
    assert category["current"] == "120.00"
    assert category["baseline_average"] == "0.00"
    assert category["delta"] == "120.00"
    assert category["delta_pct"] is None


async def test_category_delta_lists_category_missing_this_month(
    api: httpx.AsyncClient, db_session: AsyncSession, household: HouseholdCtx
) -> None:
    await _single(db_session, household, household.category_id, date(2026, 9, 3), "90.00")

    response = await api.get(
        "/reports/category-delta",
        params={"month": "2026-12", "baseline": 3},
        headers=_headers(household.household_id),
    )

    [category] = response.json()["categories"]
    assert category["current"] == "0.00"
    assert category["baseline_average"] == "30.00"
    assert category["delta"] == "-30.00"
    assert category["delta_pct"] == "-1.0000"


async def test_category_delta_defaults_to_current_month(
    api: httpx.AsyncClient, household: HouseholdCtx
) -> None:
    response = await api.get("/reports/category-delta", headers=_headers(household.household_id))

    assert response.json() == {"month": "2026-12-01", "baseline": 3, "categories": []}


# --- comum ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/reports/debt-curve",
        "/reports/committed-income",
        "/reports/category-delta?month=2026-12",
    ],
)
async def test_each_report_is_one_query(
    api: httpx.AsyncClient,
    db_session: AsyncSession,
    household: HouseholdCtx,
    statements: list[str],
    url: str,
) -> None:
    salario = await _income_category(db_session, household)
    await make_commitment(db_session, household, total_amount=Decimal("900.00"))
    await _single(db_session, household, salario, date(2026, 11, 5), "1200.00")
    statements.clear()

    response = await api.get(url, headers=_headers(household.household_id))

    assert response.status_code == 200, response.text
    assert len(statements) == 1, statements


@pytest.mark.parametrize(
    "url",
    [
        "/reports/debt-curve?start=2026-13",
        "/reports/debt-curve?months=0",
        "/reports/debt-curve?months=121",
        "/reports/committed-income?start=26-01",
        "/reports/category-delta?month=0000-01",
        "/reports/category-delta?baseline=0",
    ],
)
async def test_invalid_params_return_422(
    api: httpx.AsyncClient, household: HouseholdCtx, url: str
) -> None:
    response = await api.get(url, headers=_headers(household.household_id))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "url", ["/reports/debt-curve", "/reports/committed-income", "/reports/category-delta"]
)
async def test_requires_token(api: httpx.AsyncClient, url: str) -> None:
    response = await api.get(url)

    assert response.status_code == 401


def test_current_month_is_first_day() -> None:
    assert current_month().day == 1
