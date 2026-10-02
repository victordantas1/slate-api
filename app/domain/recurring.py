"""Horizonte rolante de recorrentes.

Regras puras do motor: sem HTTP e sem banco. A competência e o seq seguem as mesmas
funções do parcelamento, para a mesma competência de um commitment ter sempre o mesmo seq.
"""

from datetime import date
from decimal import Decimal

from app.domain.installments import PlannedInstallment, competencia, seq_from_competencia

HORIZON_MONTHS = 24


def _add_months(month: date, months: int) -> date:
    index = month.year * 12 + month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def horizon_end(today: date, months: int = HORIZON_MONTHS) -> date:
    """Última competência materializada: `trunc_mes(today) + months`, inclusiva."""
    return _add_months(today.replace(day=1), months)


def plan_recurring(
    purchase_date: date,
    first_installment_offset: int,
    amount: Decimal,
    *,
    end_date: date | None,
    until: date,
) -> list[PlannedInstallment]:
    """As entries de um recorrente da primeira competência até `until`, inclusive.

    `end_date` é a data da última cobrança: a última competência é
    `trunc_mes(end_date) + first_installment_offset`.
    """
    if until.day != 1:
        raise ValueError("until precisa ser o dia 1 do mês")
    last = until
    if end_date is not None:
        last = min(last, _add_months(end_date.replace(day=1), first_installment_offset))

    plan = []
    month = competencia(purchase_date, first_installment_offset, 1)
    while month <= last:
        seq = seq_from_competencia(purchase_date, first_installment_offset, month)
        plan.append(PlannedInstallment(seq, month, amount))
        month = _add_months(month, 1)
    return plan
