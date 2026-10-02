from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.installments import (
    PlannedInstallment,
    competencia,
    plan_installments,
    seq_from_competencia,
    split_installments,
)

CENT = Decimal("0.01")

totals = st.integers(min_value=1, max_value=10_000_000_00).map(lambda cents: Decimal(cents) * CENT)
counts = st.integers(min_value=1, max_value=48)


@pytest.mark.parametrize(
    ("total", "count"),
    [
        (Decimal("1000"), 3),
        (Decimal("0.01"), 7),
        (Decimal("999.99"), 6),
        (Decimal("100"), 1),
    ],
)
def test_split_sum_equals_total(total: Decimal, count: int) -> None:
    parts = split_installments(total, count)

    assert len(parts) == count
    assert sum(parts, Decimal(0)) == total


def test_split_puts_residue_on_first_installment() -> None:
    assert split_installments(Decimal("1000"), 3) == [
        Decimal("333.34"),
        Decimal("333.33"),
        Decimal("333.33"),
    ]


def test_split_with_fewer_cents_than_installments() -> None:
    assert split_installments(Decimal("0.01"), 7) == [Decimal("0.01")] + [Decimal("0.00")] * 6


@pytest.mark.parametrize(
    ("total", "count"),
    [
        (Decimal("0"), 3),
        (Decimal("-10"), 2),
        (Decimal("10.001"), 2),
        (Decimal("10"), 0),
    ],
)
def test_split_rejects_invalid_input(total: Decimal, count: int) -> None:
    with pytest.raises(ValueError):
        split_installments(total, count)


@given(total=totals, count=counts)
def test_split_sum_invariant_property(total: Decimal, count: int) -> None:
    parts = split_installments(total, count)

    assert len(parts) == count
    assert sum(parts, Decimal(0)) == total


@given(total=totals, count=counts)
def test_split_shape_property(total: Decimal, count: int) -> None:
    first, *rest = split_installments(total, count)

    assert all(p == p.quantize(CENT) for p in [first, *rest])
    assert all(p == rest[0] for p in rest)
    assert all(p >= 0 for p in rest)
    if rest:
        assert Decimal(0) <= first - rest[0] < count * CENT


def test_competencia_first_installment_without_offset() -> None:
    assert competencia(date(2026, 3, 17), 0, 1) == date(2026, 3, 1)


def test_competencia_applies_offset_and_sequence() -> None:
    assert competencia(date(2026, 3, 17), 1, 1) == date(2026, 4, 1)
    assert competencia(date(2026, 3, 17), 1, 3) == date(2026, 6, 1)


def test_competencia_year_rollover() -> None:
    purchase = date(2026, 12, 20)

    assert [competencia(purchase, 1, seq) for seq in (1, 2, 3)] == [
        date(2027, 1, 1),
        date(2027, 2, 1),
        date(2027, 3, 1),
    ]
    assert competencia(purchase, 0, 2) == date(2027, 1, 1)
    assert competencia(date(2026, 11, 30), 0, 14) == date(2027, 12, 1)


@pytest.mark.parametrize(("offset", "seq"), [(-1, 1), (0, 0)])
def test_competencia_rejects_invalid_input(offset: int, seq: int) -> None:
    with pytest.raises(ValueError):
        competencia(date(2026, 1, 1), offset, seq)


@given(
    purchase=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31)),
    offset=st.integers(min_value=0, max_value=3),
    seq=st.integers(min_value=1, max_value=48),
)
def test_competencia_property(purchase: date, offset: int, seq: int) -> None:
    result = competencia(purchase, offset, seq)

    assert result.day == 1
    months = (result.year - purchase.year) * 12 + (result.month - purchase.month)
    assert months == offset + seq - 1


purchase_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31))
offsets = st.integers(min_value=0, max_value=3)


@given(purchase_dates, offsets, st.integers(min_value=1, max_value=480))
def test_seq_from_competencia_inverts_competencia(
    purchase_date: date, offset: int, seq: int
) -> None:
    month = competencia(purchase_date, offset, seq)
    assert seq_from_competencia(purchase_date, offset, month) == seq


def test_seq_from_competencia_across_year_boundary() -> None:
    # Compra em dezembro, primeira parcela no mês seguinte.
    assert seq_from_competencia(date(2026, 12, 20), 1, date(2027, 1, 1)) == 1
    assert seq_from_competencia(date(2026, 12, 20), 1, date(2027, 12, 1)) == 12


@pytest.mark.parametrize(
    "month",
    [date(2026, 9, 15), date(2026, 8, 1)],  # fora do dia 1 / antes da primeira parcela
)
def test_seq_from_competencia_rejects_invalid_month(month: date) -> None:
    with pytest.raises(ValueError):
        seq_from_competencia(date(2026, 8, 15), 1, month)


def test_plan_installments_example() -> None:
    assert plan_installments(date(2026, 11, 15), 1, Decimal("1000"), 3) == [
        PlannedInstallment(1, date(2026, 12, 1), Decimal("333.34")),
        PlannedInstallment(2, date(2027, 1, 1), Decimal("333.33")),
        PlannedInstallment(3, date(2027, 2, 1), Decimal("333.33")),
    ]


def test_plan_single_is_one_installment() -> None:
    assert plan_installments(date(2026, 8, 15), 0, Decimal("59.90"), 1) == [
        PlannedInstallment(1, date(2026, 8, 1), Decimal("59.90"))
    ]


@given(purchase_dates, offsets, totals, counts)
def test_plan_installments_properties(
    purchase_date: date, offset: int, total: Decimal, count: int
) -> None:
    plan = plan_installments(purchase_date, offset, total, count)

    assert len(plan) == count
    assert sum((p.amount for p in plan), Decimal(0)) == total
    assert [p.seq for p in plan] == list(range(1, count + 1))
    assert plan[0].competencia == competencia(purchase_date, offset, 1)
    for p in plan:
        assert p.competencia.day == 1
        assert seq_from_competencia(purchase_date, offset, p.competencia) == p.seq
    for prev, cur in zip(plan, plan[1:], strict=False):
        months = (cur.competencia.year - prev.competencia.year) * 12
        assert months + cur.competencia.month - prev.competencia.month == 1
