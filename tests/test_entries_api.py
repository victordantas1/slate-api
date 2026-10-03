import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Commitment
from tests.api_support import category, create, entry_row, open_api
from tests.test_commitments_api import _Api


@pytest.fixture
def api(migrated_postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    with open_api(migrated_postgres_url, monkeypatch) as test_api:
        yield test_api


def _patch(
    api: _Api, headers: dict[str, str], entry_id: str, body: dict[str, Any], scope: str | None
) -> Any:
    params = {} if scope is None else {"scope": scope}
    return api.client.patch(f"/entries/{entry_id}", params=params, json=body, headers=headers)


def _amounts(commitment: dict[str, Any], api: _Api) -> list[Decimal]:
    return [Decimal(str(entry_row(api, e["id"])["amount"])) for e in commitment["entries"]]


def test_patch_without_scope_is_422_and_changes_nothing(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household)
    entry = commitment["entries"][0]

    response = _patch(api, household.headers, entry["id"], {"amount": "10.00"}, None)

    assert response.status_code == 422
    assert entry_row(api, entry["id"])["amount"] == Decimal(entry["amount"])


def test_patch_rejects_unknown_scope(api: _Api) -> None:
    household = api.household()
    entry = create(api, household)["entries"][0]

    response = _patch(api, household.headers, entry["id"], {"amount": "10.00"}, "everything")

    assert response.status_code == 422


@pytest.mark.parametrize("scope", ["this", "forward", "all"])
def test_patch_on_paid_entry_is_409_in_every_scope(api: _Api, scope: str) -> None:
    household = api.household()
    commitment = create(api, household)
    first, *rest = commitment["entries"]
    api.pay(first["id"])

    response = _patch(api, household.headers, first["id"], {"amount": "10.00"}, scope)

    assert response.status_code == 409
    for entry in rest:
        assert entry_row(api, entry["id"])["amount"] == Decimal(entry["amount"])


def test_patch_this_reports_one_affected_and_marks_edited(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household)
    entry = commitment["entries"][1]

    response = _patch(api, household.headers, entry["id"], {"amount": "400.00"}, "this")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["affected"] == 1
    assert body["entry"]["id"] == entry["id"]
    assert body["entry"]["amount"] == "400.00"
    assert body["entry"]["edited_manually"] is True


def test_patch_forward_reports_affected_count(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household, total_amount="1200.00", installment_count=4)
    entries = commitment["entries"]

    response = _patch(api, household.headers, entries[1]["id"], {"amount": "250.00"}, "forward")

    assert response.status_code == 200, response.text
    assert response.json()["affected"] == 3
    assert _amounts(commitment, api) == [Decimal("300.00")] + [Decimal("250.00")] * 3


def test_patch_all_skips_paid_and_reports_affected_count(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household, total_amount="1200.00", installment_count=4)
    entries = commitment["entries"]
    api.pay(entries[0]["id"])

    response = _patch(api, household.headers, entries[2]["id"], {"amount": "250.00"}, "all")

    assert response.status_code == 200, response.text
    assert response.json()["affected"] == 3
    assert _amounts(commitment, api)[0] == Decimal("300.00")


def test_category_override_this_does_not_touch_commitment(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household)
    other = category(api, household, "Mercado")
    entry = commitment["entries"][0]

    response = _patch(api, household.headers, entry["id"], {"category_id": str(other)}, "this")

    assert response.status_code == 200, response.text
    assert response.json()["entry"]["category_id"] == str(other)
    active = api.client.get("/commitments/active", headers=household.headers).json()
    assert active[0]["category_id"] == str(household.category_id)
    for sibling in commitment["entries"][1:]:
        assert entry_row(api, sibling["id"])["category_id"] == household.category_id


def test_patch_rejects_category_of_other_household_or_archived(api: _Api) -> None:
    household = api.household()
    stranger = api.household()
    entry = create(api, household)["entries"][0]
    archived = category(api, household, "Velha", archived=True)

    for category_id in (stranger.category_id, archived):
        response = _patch(
            api, household.headers, entry["id"], {"category_id": str(category_id)}, "this"
        )
        assert response.status_code == 422, response.text


@pytest.mark.parametrize("body", [{}, {"amount": "-1.00"}, {"amount": "1.001"}])
def test_patch_rejects_invalid_body(api: _Api, body: dict[str, Any]) -> None:
    household = api.household()
    entry = create(api, household)["entries"][0]

    assert _patch(api, household.headers, entry["id"], body, "this").status_code == 422


def test_patch_entry_of_other_household_is_404(api: _Api) -> None:
    household = api.household()
    stranger = api.household()
    entry = create(api, household)["entries"][0]

    response = _patch(api, stranger.headers, entry["id"], {"amount": "1.00"}, "this")

    assert response.status_code == 404


def test_pay_sets_status_and_paid_at(api: _Api) -> None:
    household = api.household()
    entry = create(api, household)["entries"][0]

    response = api.client.post(f"/entries/{entry['id']}/pay", headers=household.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pago"
    assert body["paid_at"] is not None
    stored = entry_row(api, entry["id"])
    assert stored["status"] == "pago"
    assert stored["paid_at"] is not None


def test_pay_accepts_explicit_paid_at(api: _Api) -> None:
    household = api.household()
    entry = create(api, household)["entries"][0]

    response = api.client.post(
        f"/entries/{entry['id']}/pay",
        json={"paid_at": "2026-12-10T12:00:00Z"},
        headers=household.headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["paid_at"].startswith("2026-12-10T12:00:00")


def test_pay_twice_is_409(api: _Api) -> None:
    household = api.household()
    entry = create(api, household)["entries"][0]
    url = f"/entries/{entry['id']}/pay"

    assert api.client.post(url, headers=household.headers).status_code == 200
    assert api.client.post(url, headers=household.headers).status_code == 409


def test_pay_entry_of_other_household_is_404(api: _Api) -> None:
    household = api.household()
    stranger = api.household()
    entry = create(api, household)["entries"][0]

    response = api.client.post(f"/entries/{entry['id']}/pay", headers=stranger.headers)

    assert response.status_code == 404
    assert entry_row(api, entry["id"])["status"] == "previsto"


def test_entries_endpoints_require_token(api: _Api) -> None:
    household = api.household()
    entry = create(api, household)["entries"][0]

    assert (
        api.client.patch(
            f"/entries/{entry['id']}", params={"scope": "this"}, json={"amount": "1.00"}
        ).status_code
        == 401
    )
    assert api.client.post(f"/entries/{entry['id']}/pay").status_code == 401


def _commitment_status(api: _Api, commitment_id: str) -> str:
    async def _get(conn: AsyncConnection) -> str:
        return (
            await conn.execute(
                select(Commitment.status).where(Commitment.id == uuid.UUID(commitment_id))
            )
        ).scalar_one()

    return api._run(_get)


def test_paying_last_installment_settles_commitment(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household)
    *first, last = commitment["entries"]

    for entry in first:
        response = api.client.post(f"/entries/{entry['id']}/pay", headers=household.headers)
        assert response.status_code == 200, response.text
    assert _commitment_status(api, commitment["id"]) == "active"

    response = api.client.post(f"/entries/{last['id']}/pay", headers=household.headers)

    assert response.status_code == 200, response.text
    assert _commitment_status(api, commitment["id"]) == "settled"
