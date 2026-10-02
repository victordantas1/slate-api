import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.db.models import Account, Category, Commitment, Entry, Household, Member
from app.db.session import get_engine
from app.main import app

SECRET = "test-secret-with-at-least-32-bytes!!"


class _Household:
    def __init__(
        self, headers: dict[str, str], account_id: uuid.UUID, category_id: uuid.UUID
    ) -> None:
        self.headers = headers
        self.account_id = account_id
        self.category_id = category_id

    def body(self, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "account_id": str(self.account_id),
            "category_id": str(self.category_id),
            "kind": "installment",
            "description": "Geladeira",
            "purchase_date": "2026-11-15",
            "total_amount": "1000.00",
            "installment_count": 3,
        }
        body.update(overrides)
        return body


class _Api:
    def __init__(self, client: TestClient, database_url: str) -> None:
        self.client = client
        self.database_url = database_url
        self.household_ids: list[uuid.UUID] = []

    def _run[T](self, work: Callable[[AsyncConnection], Awaitable[T]]) -> T:
        async def _go() -> T:
            engine = create_async_engine(self.database_url)
            try:
                async with engine.begin() as conn:
                    return await work(conn)
            finally:
                await engine.dispose()

        return asyncio.run(_go())

    def household(self) -> _Household:
        """Household nova com uma conta (offset 1) e uma categoria, e o token de um membro."""

        async def _create(conn: AsyncConnection) -> tuple[uuid.UUID, ...]:
            household_id = (
                await conn.execute(insert(Household).values(name="Casa").returning(Household.id))
            ).scalar_one()
            member_id = (
                await conn.execute(
                    insert(Member)
                    .values(household_id=household_id, supabase_user_id=uuid.uuid4(), name="Ana")
                    .returning(Member.id)
                )
            ).scalar_one()
            account_id = (
                await conn.execute(
                    insert(Account)
                    .values(
                        household_id=household_id,
                        name="Nubank",
                        kind="credit_card",
                        holder_kind="member",
                        owner_member_id=member_id,
                        first_installment_offset=1,
                    )
                    .returning(Account.id)
                )
            ).scalar_one()
            category_id = (
                await conn.execute(
                    insert(Category)
                    .values(household_id=household_id, name="Casa", direction="expense")
                    .returning(Category.id)
                )
            ).scalar_one()
            return household_id, member_id, account_id, category_id

        household_id, member_id, account_id, category_id = self._run(_create)
        self.household_ids.append(household_id)
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "aud": "authenticated",
                "role": "authenticated",
                "exp": int(time.time()) + 300,
                "household_id": str(household_id),
                "member_id": str(member_id),
            },
            SECRET,
            algorithm="HS256",
        )
        return _Household({"Authorization": f"Bearer {token}"}, account_id, category_id)

    def pay(self, entry_id: str) -> None:
        async def _pay(conn: AsyncConnection) -> None:
            await conn.execute(
                update(Entry)
                .where(Entry.id == uuid.UUID(entry_id))
                .values(status="pago", paid_at=datetime.now(UTC))
            )

        self._run(_pay)

    def archive_category(self, category_id: uuid.UUID) -> None:
        async def _archive(conn: AsyncConnection) -> None:
            await conn.execute(
                update(Category).where(Category.id == category_id).values(archived=True)
            )

        self._run(_archive)

    def counts(self, commitment_id: str) -> tuple[int, int]:
        """(commitments, entries) gravados com esse id de commitment."""
        cid = uuid.UUID(commitment_id)

        async def _count(conn: AsyncConnection) -> tuple[int, int]:
            commitments = await conn.scalar(
                select(func.count()).select_from(Commitment).where(Commitment.id == cid)
            )
            entries = await conn.scalar(
                select(func.count()).select_from(Entry).where(Entry.commitment_id == cid)
            )
            return commitments or 0, entries or 0

        return self._run(_count)

    def cleanup(self) -> None:
        """Apaga as households criadas (e tudo delas, em cascata).

        A API commita de verdade no Postgres compartilhado da sessão de testes; sem isso
        as entries daqui vazariam para testes que leem a tabela `entry` inteira.
        """
        if not self.household_ids:
            return
        ids = list(self.household_ids)

        async def _delete(conn: AsyncConnection) -> None:
            await conn.execute(delete(Household).where(Household.id.in_(ids)))

        self._run(_delete)

    def set_status(self, commitment_id: str, status: str) -> None:
        async def _set(conn: AsyncConnection) -> None:
            await conn.execute(
                update(Commitment)
                .where(Commitment.id == uuid.UUID(commitment_id))
                .values(status=status)
            )

        self._run(_set)


@pytest.fixture
def api(migrated_postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    monkeypatch.setenv("DATABASE_URL", migrated_postgres_url)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    get_engine.cache_clear()
    try:
        with TestClient(app) as client:
            test_api = _Api(client, migrated_postgres_url)
            try:
                yield test_api
            finally:
                test_api.cleanup()
    finally:
        get_engine.cache_clear()


def _create(api: _Api, household: _Household, **overrides: Any) -> dict[str, Any]:
    response = api.client.post(
        "/commitments", json=household.body(**overrides), headers=household.headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _active(api: _Api, household: _Household) -> dict[str, dict[str, Any]]:
    response = api.client.get("/commitments/active", headers=household.headers)
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()}


# POST


def test_post_installment_returns_materialized_entries(api: _Api) -> None:
    household = api.household()

    body = _create(api, household)

    assert body["kind"] == "installment"
    assert body["status"] == "active"
    assert body["installment_count"] == 3
    assert Decimal(body["total_amount"]) == Decimal("1000.00")
    entries = body["entries"]
    assert [e["seq"] for e in entries] == [1, 2, 3]
    assert [e["competencia"] for e in entries] == ["2026-12-01", "2027-01-01", "2027-02-01"]
    assert sum(Decimal(e["amount"]) for e in entries) == Decimal("1000.00")
    assert {e["status"] for e in entries} == {"previsto"}
    assert {e["category_id"] for e in entries} == {str(household.category_id)}
    assert api.counts(body["id"]) == (1, 3)


def test_post_single_has_one_entry_with_full_amount(api: _Api) -> None:
    household = api.household()

    body = _create(api, household, kind="single", installment_count=None, total_amount="89.90")

    assert body["installment_count"] == 1
    assert [Decimal(e["amount"]) for e in body["entries"]] == [Decimal("89.90")]


@pytest.mark.parametrize(
    "overrides",
    [
        {"installment_count": None},
        {"kind": "single", "installment_count": 2},
        {"total_amount": None},
        {"total_amount": "0"},
        {"total_amount": "10.001"},
        {"installment_count": 0},
        {"description": "   "},
        {
            "kind": "recurring",
            "total_amount": None,
            "installment_count": None,
            "recurring_amount": "39.90",
        },
        {"recurring_amount": "39.90"},
        {"end_date": "2027-12-01"},
    ],
)
def test_post_rejects_invalid_body(api: _Api, overrides: dict[str, Any]) -> None:
    household = api.household()

    response = api.client.post(
        "/commitments", json=household.body(**overrides), headers=household.headers
    )

    assert response.status_code == 422, response.text


def test_post_rejects_account_and_category_of_other_household(api: _Api) -> None:
    mine, other = api.household(), api.household()

    for field, value in (("account_id", other.account_id), ("category_id", other.category_id)):
        response = api.client.post(
            "/commitments", json=mine.body(**{field: str(value)}), headers=mine.headers
        )
        assert response.status_code == 422, (field, response.text)


def test_post_rejects_archived_category(api: _Api) -> None:
    household = api.household()
    api.archive_category(household.category_id)

    response = api.client.post("/commitments", json=household.body(), headers=household.headers)

    assert response.status_code == 422


# GET /commitments/active


def test_active_derives_balance_and_payoff_from_unpaid_entries(api: _Api) -> None:
    household = api.household()
    created = _create(api, household)

    item = _active(api, household)[created["id"]]
    assert Decimal(item["outstanding_balance"]) == Decimal("1000.00")
    assert item["payoff_month"] == "2027-02-01"

    api.pay(created["entries"][0]["id"])
    item = _active(api, household)[created["id"]]
    paid = Decimal(created["entries"][0]["amount"])
    assert Decimal(item["outstanding_balance"]) == Decimal("1000.00") - paid
    assert item["payoff_month"] == "2027-02-01"

    # Pagar a última parcela antes das outras puxa a quitação para a maior não paga.
    api.pay(created["entries"][2]["id"])
    item = _active(api, household)[created["id"]]
    assert Decimal(item["outstanding_balance"]) == Decimal(created["entries"][1]["amount"])
    assert item["payoff_month"] == "2027-01-01"

    api.pay(created["entries"][1]["id"])
    item = _active(api, household)[created["id"]]
    assert Decimal(item["outstanding_balance"]) == Decimal("0")
    assert item["payoff_month"] is None


def test_active_lists_only_active_commitments_of_own_household(api: _Api) -> None:
    mine, other = api.household(), api.household()
    kept = _create(api, mine)
    cancelled = _create(api, mine)
    api.set_status(cancelled["id"], "cancelled")
    foreign = _create(api, other)

    ids = _active(api, mine).keys()

    assert kept["id"] in ids
    assert cancelled["id"] not in ids
    assert foreign["id"] not in ids


def test_balance_is_not_a_stored_column() -> None:
    columns = set(Commitment.__table__.columns.keys())

    assert not columns & {"outstanding_balance", "payoff_month", "balance"}


# DELETE


def test_delete_removes_commitment_and_entries(api: _Api) -> None:
    household = api.household()
    created = _create(api, household)

    response = api.client.delete(f"/commitments/{created['id']}", headers=household.headers)

    assert response.status_code == 204
    assert api.counts(created["id"]) == (0, 0)
    assert created["id"] not in _active(api, household)


def test_delete_of_other_household_is_404(api: _Api) -> None:
    mine, other = api.household(), api.household()
    foreign = _create(api, other)

    response = api.client.delete(f"/commitments/{foreign['id']}", headers=mine.headers)

    assert response.status_code == 404
    assert api.counts(foreign["id"]) == (1, 3)


def test_delete_keep_paid_is_not_implemented_yet(api: _Api) -> None:
    household = api.household()
    created = _create(api, household)

    response = api.client.delete(
        f"/commitments/{created['id']}", params={"keep_paid": "true"}, headers=household.headers
    )

    assert response.status_code == 501
    assert api.counts(created["id"]) == (1, 3)


def test_endpoints_require_token(api: _Api) -> None:
    assert api.client.get("/commitments/active").status_code == 401
    assert api.client.post("/commitments", json={}).status_code == 401
    assert api.client.delete(f"/commitments/{uuid.uuid4()}").status_code == 401
