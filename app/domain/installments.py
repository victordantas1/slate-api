"""Competência e divisão de parcelas.

Regras puras do motor: sem HTTP e sem banco.
"""

from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import NamedTuple

CENT = Decimal("0.01")


def competencia(purchase_date: date, first_installment_offset: int, seq: int) -> date:
    """Mês de competência da parcela `seq` (1-based), sempre no dia 1.

    competencia(seq) = trunc_mes(purchase_date) + offset meses + (seq - 1) meses
    """
    if first_installment_offset < 0:
        raise ValueError("first_installment_offset precisa ser >= 0")
    if seq < 1:
        raise ValueError("seq precisa ser >= 1")

    month_index = purchase_date.month - 1 + first_installment_offset + seq - 1
    return date(purchase_date.year + month_index // 12, month_index % 12 + 1, 1)


def seq_from_competencia(purchase_date: date, first_installment_offset: int, month: date) -> int:
    """Inversa de `competencia`: o `seq` (1-based) da parcela que cai em `month`.

    O seq sai da competência, nunca de um contador, para que a mesma competência de um
    commitment sempre tenha o mesmo seq (inclusive no horizonte do recorrente).
    """
    if month.day != 1:
        raise ValueError("competência precisa ser o dia 1 do mês")
    first = competencia(purchase_date, first_installment_offset, 1)
    seq = (month.year - first.year) * 12 + month.month - first.month + 1
    if seq < 1:
        raise ValueError("competência anterior à primeira parcela")
    return seq


def split_installments(total: Decimal, count: int) -> list[Decimal]:
    """Divide `total` em `count` parcelas iguais para baixo, resíduo na primeira.

    R$ 1.000 em 3x → 333,34 / 333,33 / 333,33, como os emissores brasileiros.
    A soma das parcelas é sempre exatamente `total`.
    """
    if count < 1:
        raise ValueError("count precisa ser >= 1")
    if total <= 0:
        raise ValueError("total precisa ser > 0")
    if total != total.quantize(CENT):
        raise ValueError("total precisa ter no máximo 2 casas decimais")

    base = (total / count).quantize(CENT, rounding=ROUND_DOWN)
    first = total - base * (count - 1)
    return [first.quantize(CENT)] + [base] * (count - 1)


class PlannedInstallment(NamedTuple):
    seq: int
    competencia: date
    amount: Decimal


def plan_installments(
    purchase_date: date, first_installment_offset: int, total: Decimal, count: int
) -> list[PlannedInstallment]:
    """As `count` parcelas de um parcelamento (ou `single`, com `count=1`)."""
    plan = []
    for n, amount in enumerate(split_installments(total, count), start=1):
        month = competencia(purchase_date, first_installment_offset, n)
        seq = seq_from_competencia(purchase_date, first_installment_offset, month)
        plan.append(PlannedInstallment(seq, month, amount))
    return plan
