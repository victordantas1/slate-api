"""Recálculo da divisão de um parcelamento com parcelas travadas.

Regras puras do motor: sem HTTP e sem banco. Uma parcela travada (`pago` ou
`edited_manually`) mantém o valor; o resto do total é dividido entre as outras.
"""

from collections.abc import Mapping
from decimal import Decimal

from app.domain.installments import CENT, split_installments


class InvalidRescheduleError(ValueError):
    """O novo plano não cabe nas parcelas travadas."""


def resplit_installments(
    total: Decimal, count: int, locked: Mapping[int, Decimal]
) -> dict[int, Decimal]:
    """Valores dos seqs livres de `1..count`, dividindo `total - soma(locked)`.

    Mesma regra de `split_installments` (iguais para baixo, resíduo no primeiro livre),
    então sem travadas o resultado é o plano original. A soma de livres e travadas é
    sempre exatamente `total`.
    """
    if count < 1:
        raise ValueError("count precisa ser >= 1")
    if total <= 0:
        raise ValueError("total precisa ser > 0")
    if total != total.quantize(CENT):
        raise ValueError("total precisa ter no máximo 2 casas decimais")
    beyond = sorted(s for s in locked if not 1 <= s <= count)
    if beyond:
        raise InvalidRescheduleError(f"parcelas travadas fora de 1..{count}: {beyond}")

    free_seqs = [s for s in range(1, count + 1) if s not in locked]
    rest = total - sum(locked.values(), Decimal("0"))
    if not free_seqs:
        if rest != 0:
            raise InvalidRescheduleError("todas as parcelas travadas: total precisa ser a soma")
        return {}
    if rest <= 0:
        raise InvalidRescheduleError("total não cobre as parcelas travadas")
    return dict(zip(free_seqs, split_installments(rest, len(free_seqs)), strict=True))
