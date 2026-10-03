import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Commitment, Entry
from app.services import commitments
from tests.api_support import create, entry_row, household_of, open_api
from tests.test_commitments_api import _Api, _Household


@pytest.fixture
def api(migrated_postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    with open_api(migrated_postgres_url, monkeypatch) as test_api:
        yield test_api


def _item(household: _Household, key: str | None, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "account_id": str(household.account_id),
        "category_id": str(household.category_id),
        "description": "Padaria",
        "purchase_date": "2026-11-15",
        "amount": "23.50",
    }
    if key is not None:
        item["idempotency_key"] = key
    item.update(overrides)
    return item


def _ingest(api: _Api, household: _Household, *items: dict[str, Any]) -> Any:
    return api.client.post(
        "/ingest/entries", json={"entries": list(items)}, headers=household.headers
    )


def _counts(api: _Api, household: _Household) -> tuple[int, int]:
    """(commitments, entries) da household."""
    household_id = household_of(api, household)

    async def _count(conn: AsyncConnection) -> tuple[int, int]:
        c = await conn.scalar(
            select(func.count())
            .select_from(Commitment)
            .where(Commitment.household_id == household_id)
        )
        e = await conn.scalar(
            select(func.count()).select_from(Entry).where(Entry.household_id == household_id)
        )
        return c or 0, e or 0

    return api._run(_count)


def test_missing_idempotency_key_is_422(api: _Api) -> None:
    household = api.household()

    response = _ingest(api, household, _item(household, None))

    assert response.status_code == 422
    assert _counts(api, household) == (0, 0)


def test_empty_idempotency_key_is_422(api: _Api) -> None:
    household = api.household()

    assert _ingest(api, household, _item(household, "")).status_code == 422


def test_create_without_create_fields_is_422(api: _Api) -> None:
    household = api.household()

    response = _ingest(api, household, {"idempotency_key": "k1", "amount": "10.00"})

    assert response.status_code == 422
    assert "account_id" in response.text


def test_ingest_creates_confirmed_fincoach_entry(api: _Api) -> None:
    household = api.household()

    response = _ingest(api, household, _item(household, "fc-1"))

    assert response.status_code == 200, response.text
    (result,) = response.json()["results"]
    assert result["idempotency_key"] == "fc-1"
    assert result["outcome"] == "created"
    entry = result["entry"]
    assert entry["amount"] == "23.50"
    assert entry["status"] == "confirmado"
    assert entry["source"] == "fincoach"
    row = entry_row(api, entry["id"])
    assert row["idempotency_key"] == "fc-1"
    assert _counts(api, household) == (1, 1)


def test_resending_batch_returns_existing_without_duplicating(api: _Api) -> None:
    household = api.household()
    batch = [_item(household, "fc-1"), _item(household, "fc-2", amount="99.90")]

    first = _ingest(api, household, *batch)
    second = _ingest(api, household, *batch)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert [r["outcome"] for r in first.json()["results"]] == ["created", "created"]
    assert [r["outcome"] for r in second.json()["results"]] == ["existing", "existing"]
    assert [r["entry"]["id"] for r in second.json()["results"]] == [
        r["entry"]["id"] for r in first.json()["results"]
    ]
    assert _counts(api, household) == (2, 2)


def test_duplicate_key_inside_batch_creates_once(api: _Api) -> None:
    household = api.household()

    response = _ingest(api, household, _item(household, "fc-1"), _item(household, "fc-1"))

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [r["outcome"] for r in results] == ["created", "existing"]
    assert results[0]["entry"]["id"] == results[1]["entry"]["id"]
    assert _counts(api, household) == (1, 1)


def test_keys_are_scoped_per_household(api: _Api) -> None:
    ours, theirs = api.household(), api.household()

    a = _ingest(api, ours, _item(ours, "fc-1"))
    b = _ingest(api, theirs, _item(theirs, "fc-1"))

    assert a.status_code == b.status_code == 200
    assert b.json()["results"][0]["outcome"] == "created"
    assert a.json()["results"][0]["entry"]["id"] != b.json()["results"][0]["entry"]["id"]


def test_match_confirms_existing_entry(api: _Api) -> None:
    household = api.household()
    commitment = create(api, household)
    target = commitment["entries"][1]

    response = _ingest(api, household, {"idempotency_key": "fc-1", "match_entry_id": target["id"]})

    assert response.status_code == 200, response.text
    (result,) = response.json()["results"]
    assert result["outcome"] == "matched"
    assert result["entry"]["id"] == target["id"]
    assert result["entry"]["status"] == "confirmado"
    row = entry_row(api, target["id"])
    assert row["status"] == "confirmado"
    assert row["idempotency_key"] == "fc-1"
    assert row["amount"] == Decimal(target["amount"])
    # Casar não cria nada: continua o commitment manual com as mesmas 3 entries.
    assert _counts(api, household) == (1, 3)

    again = _ingest(api, household, {"idempotency_key": "fc-1", "match_entry_id": target["id"]})
    assert again.status_code == 200, again.text
    assert again.json()["results"][0]["outcome"] == "existing"
    assert again.json()["results"][0]["entry"]["id"] == target["id"]


def test_reused_key_on_other_match_is_409(api: _Api) -> None:
    household = api.household()
    first, second, _ = create(api, household)["entries"]
    ok = _ingest(api, household, {"idempotency_key": "fc-1", "match_entry_id": first["id"]})
    assert ok.status_code == 200, ok.text

    response = _ingest(api, household, {"idempotency_key": "fc-1", "match_entry_id": second["id"]})

    assert response.status_code == 409
    assert entry_row(api, second["id"])["status"] == "previsto"


def test_match_paid_entry_is_409_and_rolls_back_batch(api: _Api) -> None:
    household = api.household()
    target = create(api, household)["entries"][0]
    api.pay(target["id"])

    response = _ingest(
        api,
        household,
        _item(household, "fc-new"),
        {"idempotency_key": "fc-1", "match_entry_id": target["id"]},
    )

    assert response.status_code == 409
    assert "entries[1]" in response.json()["detail"]
    # O item 0 válido também foi desfeito.
    assert _counts(api, household) == (1, 3)
    assert entry_row(api, target["id"])["idempotency_key"] is None


def test_match_entry_of_other_household_is_404(api: _Api) -> None:
    ours, theirs = api.household(), api.household()
    target = create(api, theirs)["entries"][0]

    response = _ingest(api, ours, {"idempotency_key": "fc-1", "match_entry_id": target["id"]})

    assert response.status_code == 404
    assert entry_row(api, target["id"])["status"] == "previsto"


def test_match_unknown_entry_is_404(api: _Api) -> None:
    household = api.household()

    response = _ingest(
        api, household, {"idempotency_key": "fc-1", "match_entry_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404


def test_ingest_rejects_archived_category_like_manual_api(api: _Api) -> None:
    household = api.household()
    api.archive_category(household.category_id)

    manual = api.client.post("/commitments", json=household.body(), headers=household.headers)
    ingested = _ingest(api, household, _item(household, "fc-1"))

    assert manual.status_code == ingested.status_code == 422
    assert "categoria está arquivada" in manual.text
    assert "categoria está arquivada" in ingested.text


def test_ingest_and_manual_api_share_write_service(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    household = api.household()
    calls: list[dict[str, Any]] = []
    original = commitments.create_commitment

    async def spy(*args: Any, **kwargs: Any) -> Commitment:
        calls.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(commitments, "create_commitment", spy)

    create(api, household)
    response = _ingest(api, household, _item(household, "fc-1"))

    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert "source" not in calls[0]
    assert calls[1]["source"] == "fincoach"


def test_concurrent_requests_with_same_key_create_once(api: _Api) -> None:
    household = api.household()

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: _ingest(api, household, _item(household, "fc-1")), range(4))
        )

    assert [r.status_code for r in responses] == [200] * 4, [r.text for r in responses]
    outcomes = sorted(r.json()["results"][0]["outcome"] for r in responses)
    assert outcomes == ["created", "existing", "existing", "existing"]
    assert len({r.json()["results"][0]["entry"]["id"] for r in responses}) == 1
    assert _counts(api, household) == (1, 1)
