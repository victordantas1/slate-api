from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.domain.cascade import InvalidRescheduleError, resplit_installments
from app.domain.installments import CENT, split_installments

cents = st.integers(min_value=1, max_value=1_000_000_00).map(lambda c: Decimal(c) * CENT)


def test_without_locked_matches_split_installments() -> None:
    assert resplit_installments(Decimal("1000.00"), 3, {}) == dict(
        zip([1, 2, 3], split_installments(Decimal("1000.00"), 3), strict=True)
    )


def test_locked_seqs_are_kept_out_and_rest_is_split() -> None:
    # 10x de 393 com a 1ª paga e a 4ª renegociada para 134.
    locked = {1: Decimal("393.00"), 4: Decimal("134.00")}
    free = resplit_installments(Decimal("3930.00"), 10, locked)

    assert sorted(free) == [2, 3, 5, 6, 7, 8, 9, 10]
    assert sum(free.values()) + sum(locked.values()) == Decimal("3930.00")
    # 3403 / 8 = 425,375 → 425,37 em cada, resíduo de 0,04 no primeiro livre.
    assert free[2] == Decimal("425.41")
    assert all(free[s] == Decimal("425.37") for s in [3, 5, 6, 7, 8, 9, 10])


def test_all_locked_needs_exact_total() -> None:
    locked = {1: Decimal("10.00"), 2: Decimal("20.00")}
    assert resplit_installments(Decimal("30.00"), 2, locked) == {}
    with pytest.raises(InvalidRescheduleError):
        resplit_installments(Decimal("31.00"), 2, locked)


def test_locked_beyond_count_is_refused() -> None:
    with pytest.raises(InvalidRescheduleError):
        resplit_installments(Decimal("100.00"), 3, {5: Decimal("10.00")})


@pytest.mark.parametrize("total", [Decimal("50.00"), Decimal("60.00")])
def test_total_not_above_locked_sum_with_free_seqs_is_refused(total: Decimal) -> None:
    with pytest.raises(InvalidRescheduleError):
        resplit_installments(total, 3, {1: Decimal("60.00")})


@pytest.mark.parametrize(
    ("total", "count"),
    [(Decimal("0.00"), 1), (Decimal("1.001"), 1), (Decimal("10.00"), 0)],
)
def test_invalid_total_or_count(total: Decimal, count: int) -> None:
    with pytest.raises(ValueError):
        resplit_installments(total, count, {})


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[Decimal, int, dict[int, Decimal]]:
    count = draw(st.integers(min_value=1, max_value=60))
    locked_seqs = draw(st.sets(st.integers(min_value=1, max_value=count), max_size=count))
    locked = {s: draw(cents) for s in locked_seqs}
    if len(locked_seqs) == count:
        total = sum(locked.values(), Decimal("0"))
    else:
        total = sum(locked.values(), Decimal("0")) + draw(cents)
    return total, count, locked


@given(scenarios())
def test_resplit_properties(scenario: tuple[Decimal, int, dict[int, Decimal]]) -> None:
    total, count, locked = scenario
    free = resplit_installments(total, count, locked)

    assert set(free) == set(range(1, count + 1)) - set(locked)
    assert sum(free.values(), Decimal("0")) + sum(locked.values(), Decimal("0")) == total
    assert all(v >= 0 and v == v.quantize(CENT) for v in free.values())
    if free:
        # Iguais para baixo, resíduo só no primeiro livre.
        first, *rest = sorted(free)
        assert len({free[s] for s in rest}) <= 1
        assert all(free[first] >= free[s] for s in rest)


@given(scenarios(), st.integers(min_value=1, max_value=60))
def test_shrinking_below_a_locked_seq_is_refused(
    scenario: tuple[Decimal, int, dict[int, Decimal]], new_count: int
) -> None:
    total, _, locked = scenario
    assume(locked and new_count < max(locked))
    with pytest.raises(InvalidRescheduleError):
        resplit_installments(total, new_count, locked)
