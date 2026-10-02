from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.session import get_engine
from app.services.months import month_query
from tests.api_support import account, category, create, household_of, open_api
from tests.test_commitments_api import _Api, _Household


@pytest.fixture
def api(migrated_postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    with open_api(migrated_postgres_url, monkeypatch) as test_api:
        yield test_api


def _month(api: _Api, household: _Household, month: str) -> dict[str, Any]:
    response = api.client.get(f"/months/{month}", headers=household.headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _populate(api: _Api, household: _Household) -> None:
    """Dezembro/2026 com parcelamento e avulso no cartão e salário e mercado na corrente.

    O cartão tem offset 1 (compra de novembro cai em dezembro); a corrente, offset 0.
    """
    checking = account(api, household, "Conta corrente")
    salary = category(api, household, "Salário", "income")
    groceries = category(api, household, "Mercado")
    create(api, household)  # 1000.00 em 3x: 333.34 em dezembro
    create(
        api,
        household,
        kind="single",
        description="Fone",
        total_amount="150.00",
        installment_count=None,
    )
    create(
        api,
        household,
        kind="single",
        description="Salário",
        account_id=str(checking),
        category_id=str(salary),
        purchase_date="2026-12-05",
        total_amount="5000.00",
        installment_count=None,
    )
    create(
        api,
        household,
        kind="single",
        description="Feira",
        account_id=str(checking),
        category_id=str(groceries),
        purchase_date="2026-12-07",
        total_amount="420.50",
        installment_count=None,
    )


def test_month_groups_entries_by_account_with_totals(api: _Api) -> None:
    household = api.household()
    _populate(api, household)

    body = _month(api, household, "2026-12")

    assert body["month"] == "2026-12"
    assert body["totals"] == {
        "income": "5000.00",
        "expense": "903.84",
        "balance": "4096.16",
        "installments": "333.34",
    }
    accounts = {a["account_name"]: a for a in body["accounts"]}
    assert accounts.keys() == {"Nubank", "Conta corrente"}
    card = accounts["Nubank"]
    assert card["account_kind"] == "credit_card"
    assert sorted(e["description"] for e in card["entries"]) == ["Fone", "Geladeira"]
    assert card["totals"] == {
        "income": "0.00",
        "expense": "483.34",
        "balance": "-483.34",
        "installments": "333.34",
    }
    installment = next(e for e in card["entries"] if e["description"] == "Geladeira")
    assert installment["seq"] == 1
    assert installment["installment_count"] == 3
    assert installment["commitment_kind"] == "installment"
    assert installment["direction"] == "expense"
    assert installment["competencia"] == "2026-12-01"
    checking = accounts["Conta corrente"]
    assert checking["totals"]["income"] == "5000.00"
    assert checking["totals"]["expense"] == "420.50"
    assert checking["totals"]["installments"] == "0.00"


def test_totals_match_sum_of_returned_entries(api: _Api) -> None:
    household = api.household()
    _populate(api, household)

    body = _month(api, household, "2026-12")

    entries = [e for a in body["accounts"] for e in a["entries"]]
    income = sum((Decimal(e["amount"]) for e in entries if e["direction"] == "income"), Decimal(0))
    expense = sum(
        (Decimal(e["amount"]) for e in entries if e["direction"] == "expense"), Decimal(0)
    )
    installments = sum(
        (
            Decimal(e["amount"])
            for e in entries
            if e["direction"] == "expense" and e["commitment_kind"] == "installment"
        ),
        Decimal(0),
    )
    assert Decimal(body["totals"]["income"]) == income
    assert Decimal(body["totals"]["expense"]) == expense
    assert Decimal(body["totals"]["balance"]) == income - expense
    assert Decimal(body["totals"]["installments"]) == installments
    for group in body["accounts"]:
        assert Decimal(group["totals"]["expense"]) == sum(
            (Decimal(e["amount"]) for e in group["entries"] if e["direction"] == "expense"),
            Decimal(0),
        )


def test_entry_category_override_moves_entry_between_directions(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household, kind="single", total_amount="80.00", installment_count=None)
    refund = category(api, household, "Reembolso", "income")
    entry = commitment["entries"][0]
    response = api.client.patch(
        f"/entries/{entry['id']}",
        params={"scope": "this"},
        json={"category_id": str(refund)},
        headers=household.headers,
    )
    assert response.status_code == 200, response.text

    body = _month(api, household, "2026-12")

    assert body["totals"]["income"] == "80.00"
    assert body["totals"]["expense"] == "0.00"
    assert body["accounts"][0]["entries"][0]["category_name"] == "Reembolso"


def test_empty_month_returns_empty_structure(api: _Api) -> None:
    household = api.household()
    create(api, household)

    body = _month(api, household, "2030-01")

    assert body == {
        "month": "2030-01",
        "totals": {"income": "0.00", "expense": "0.00", "balance": "0.00", "installments": "0.00"},
        "accounts": [],
    }


def test_month_shows_only_own_household(api: _Api) -> None:
    household = api.household()
    stranger = api.household()
    _populate(api, stranger)

    assert _month(api, household, "2026-12")["accounts"] == []


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "26-12", "2026-00", "0000-01", "dez"])
def test_invalid_month_is_422(api: _Api, month: str) -> None:
    household = api.household()

    response = api.client.get(f"/months/{month}", headers=household.headers)

    assert response.status_code == 422


def test_month_requires_token(api: _Api) -> None:
    assert api.client.get("/months/2026-12").status_code == 401


def test_month_runs_a_single_query(api: _Api) -> None:
    household = api.household()
    _populate(api, household)
    statements: list[str] = []

    def _record(*args: Any) -> None:
        statements.append(args[2])

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        body = _month(api, household, "2026-12")
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert sum(len(a["entries"]) for a in body["accounts"]) == 4
    # Fora o SET ROLE e o set_config das claims, que toda sessão autenticada roda.
    queries = [
        s for s in statements if not s.startswith("SET LOCAL ROLE") and "set_config" not in s
    ]
    assert len(queries) == 1, queries


def test_month_query_uses_household_competencia_index(api: _Api) -> None:
    household = api.household()
    for _ in range(5):
        create(api, household, total_amount="3600.00", installment_count=360)
    household_id = household_of(api, household)

    async def _explain(conn: AsyncConnection) -> str:
        sql = month_query(household_id, date(2026, 12, 1)).compile(
            dialect=conn.dialect, compile_kwargs={"literal_binds": True}
        )
        await conn.execute(text("ANALYZE entry"))
        # Com o volume do teste o planner pode preferir seq scan ou entrar em entry pelo
        # commitment num nested loop, o que depende das estatísticas do banco compartilhado.
        # Desligar os dois isola a pergunta do critério: qual índice serve o filtro de entry.
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        await conn.execute(text("SET LOCAL enable_nestloop = off"))
        rows = await conn.execute(text(f"EXPLAIN {sql}"))
        return "\n".join(r[0] for r in rows)

    plan = api._run(_explain)

    assert "ix_entry_household_id_competencia on entry" in plan, plan
