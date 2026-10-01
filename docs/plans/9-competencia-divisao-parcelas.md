# Plano — #9 Motor: cálculo de competência e divisão de parcelas

Issue: https://github.com/victordantas1/slate-api/issues/9 · M2 · Motor · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §6.1, D2, D3,
> D4, que não existe neste repositório. Implementado a partir do corpo da issue.

## 1. Objetivo

Duas funções puras em `app/domain`: `competencia(purchase_date, offset, seq)` devolvendo
sempre o dia 1 do mês da parcela, e `split_installments(total, count)` dividindo em
parcelas iguais para baixo com o resíduo na primeira, cuja soma é sempre o total.

## 2. Arquivos

- `app/domain/installments.py` — criado: `competencia`, `split_installments`
- `tests/test_domain_installments.py` — criado: casos da issue + property-based
- `pyproject.toml`, `uv.lock` — `hypothesis` no grupo `dev`

## 3. Tarefas

1. Testes de `competencia` e `split_installments` (vermelho), depois implementação.
2. Testes property-based com `hypothesis` sobre soma, dia 1 e forma das parcelas.

## 4. Pressupostos

- Dinheiro como `Decimal` com 2 casas; `total` com mais de 2 casas é rejeitado
  (`ValueError`) em vez de arredondado silenciosamente.
- `total` precisa ser > 0, `count` ≥ 1, `seq` ≥ 1, `first_installment_offset` ≥ 0;
  fora disso, `ValueError`.
- Quando `total` tem menos centavos que parcelas (0,01 em 7x), as parcelas excedentes
  ficam em 0,00 — é a consequência literal de "iguais para baixo, resíduo na primeira".
- A função recebe o offset como inteiro, não a entidade `account` (ainda não existe e o
  domínio não depende de banco).

## 5. Testes

- Invariante 1: soma == total para 1000/3, 0,01/7, 999,99/6, 100/1, e property-based
  sobre totais e contagens arbitrários.
- 1000/3 → 333,34 / 333,33 / 333,33 (resíduo na primeira).
- `competencia` sempre dia 1 (casos fixos + property-based).
- Virada de ano: compra em dezembro → parcelas em janeiro em diante.
