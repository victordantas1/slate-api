from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.installments import PlannedInstallment, seq_from_competencia
from app.domain.recurring import HORIZON_MONTHS, horizon_end, plan_recurring

AMOUNT = Decimal("39.90")

days = st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 12, 31))
offsets = st.integers(min_value=0, max_value=3)


def test_horizon_is_24_months() -> None:
    assert HORIZON_MONTHS == 24


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 2), date(2028, 10, 1)),
        (date(2026, 1, 31), date(2028, 1, 1)),
        (date(2026, 12, 1), date(2028, 12, 1)),
    ],
)
def test_horizon_end_is_first_day_24_months_ahead(today: date, expected: date) -> None:
    assert horizon_end(today) == expected


def test_plan_runs_until_horizon_inclusive() -> None:
    plan = plan_recurring(date(2026, 10, 10), 1, AMOUNT, end_date=None, until=date(2027, 2, 1))

    assert plan == [
        PlannedInstallment(1, date(2026, 11, 1), AMOUNT),
        PlannedInstallment(2, date(2026, 12, 1), AMOUNT),
        PlannedInstallment(3, date(2027, 1, 1), AMOUNT),
        PlannedInstallment(4, date(2027, 2, 1), AMOUNT),
    ]


def test_plan_starts_in_purchase_month_with_offset_zero() -> None:
    plan = plan_recurring(date(2026, 10, 10), 0, AMOUNT, end_date=None, until=date(2026, 11, 1))

    assert [p.competencia for p in plan] == [date(2026, 10, 1), date(2026, 11, 1)]


def test_plan_stops_at_end_date_shifted_by_offset() -> None:
    # Última cobrança em janeiro/2027; com offset 1, a última competência é fevereiro.
    plan = plan_recurring(
        date(2026, 10, 10), 1, AMOUNT, end_date=date(2027, 1, 20), until=date(2028, 10, 1)
    )

    assert [p.competencia for p in plan] == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]


def test_end_date_in_purchase_month_yields_one_entry() -> None:
    plan = plan_recurring(
        date(2026, 10, 10), 1, AMOUNT, end_date=date(2026, 10, 31), until=date(2028, 10, 1)
    )

    assert plan == [PlannedInstallment(1, date(2026, 11, 1), AMOUNT)]


def test_plan_is_empty_when_first_competencia_is_past_horizon() -> None:
    assert plan_recurring(date(2030, 1, 1), 1, AMOUNT, end_date=None, until=date(2028, 10, 1)) == []


def test_until_must_be_first_day() -> None:
    with pytest.raises(ValueError):
        plan_recurring(date(2026, 10, 10), 1, AMOUNT, end_date=None, until=date(2027, 2, 2))


@given(purchase=days, offset=offsets, today=days, end=st.none() | days)
def test_plan_properties(purchase: date, offset: int, today: date, end: date | None) -> None:
    if end is not None and end < purchase:
        end = purchase
    until = horizon_end(today)

    plan = plan_recurring(purchase, offset, AMOUNT, end_date=end, until=until)

    assert [p.seq for p in plan] == list(range(1, len(plan) + 1))
    for prev, cur in zip(plan, plan[1:], strict=False):
        months = (cur.competencia.year - prev.competencia.year) * 12
        assert months + cur.competencia.month - prev.competencia.month == 1
    for p in plan:
        assert p.competencia.day == 1
        assert p.competencia <= until
        assert p.amount == AMOUNT
        assert seq_from_competencia(purchase, offset, p.competencia) == p.seq
    if end is not None and plan:
        last_charge = (end.year - purchase.year) * 12 + end.month - purchase.month + 1
        assert len(plan) <= last_charge


@given(purchase=days, offset=offsets, today=days)
def test_later_horizon_extends_earlier_plan(purchase: date, offset: int, today: date) -> None:
    earlier = plan_recurring(purchase, offset, AMOUNT, end_date=None, until=horizon_end(today))
    month_later = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    later = plan_recurring(purchase, offset, AMOUNT, end_date=None, until=horizon_end(month_later))

    assert later[: len(earlier)] == earlier
    assert len(later) - len(earlier) in (0, 1)
