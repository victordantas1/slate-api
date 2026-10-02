# Plano — #22 Relatórios: debt-curve, committed-income e category-delta

Issue: https://github.com/victordantas1/slate-api/issues/22 · M3 · API · P1

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §8, que não
> existe neste repositório. Implementado a partir do escopo e dos critérios de aceite
> da própria issue.

## 1. Objetivo

Três relatórios de leitura para o membro autenticado, todos calculados das entries da
household em uma única consulta agregada cada.

## 2. Arquivos

Criados:

- `app/services/reports.py`: as três consultas
- `app/api/reports.py`: schemas e router `/reports`
- `tests/test_reports_api.py`: testes de API contra Postgres real

Modificados:

- `app/main.py`: registro do router
- `openapi.json`: regerado

## 3. Contrato

- `GET /reports/debt-curve?months=24&start=yyyy-mm` → lista `{competencia, amount}`,
  uma linha por mês a partir de `start` (padrão: mês atual). `amount` soma as entries
  de commitment `installment` com status diferente de `pago`.
- `GET /reports/committed-income?months=12&start=yyyy-mm` → lista
  `{competencia, installments, income, ratio}`. `ratio = installments / income` com 4
  casas, nulo quando não há entrada no mês.
- `GET /reports/category-delta?month=yyyy-mm&baseline=3` →
  `{month, baseline, categories: [{category_id, name, direction, parent_id, current,
  baseline_average, delta, delta_pct}]}`, ordenado por `|delta|` decrescente.

## 4. Pressupostos

- Definições iguais às da tela de mês (#50): entrada é entry de categoria `income`;
  parcela é saída (categoria `expense`) de commitment `installment`. No debt-curve,
  como pede o critério, o filtro é só `kind='installment'` e não pago.
- committed-income conta parcelas pagas ou não: é o que o mês comprometeu.
- Mês corrente no fuso `America/Sao_Paulo`, via a dependency `current_month`, que os
  testes trocam por uma data fixa.
- Os meses da série vêm de `generate_series`, então mês vazio aparece com zero. A
  aritmética de meses roda no banco, sem estouro de `date` em Python.
- category-delta: a média divide pelo número de meses da janela (mês sem lançamento
  conta como zero). Categoria sem histórico tem média zero e `delta_pct` nulo; categoria
  que só aparece na janela entra com `current` zero. Agrupa pela categoria da entry,
  que pode ter sido sobrescrita. Entram entries de qualquer status.
- Toda consulta filtra por `household_id` do token, além da RLS.

## 5. Testes

- Só parcelamento não pago → `test_debt_curve_counts_only_unpaid_installments`
- Divisão por zero → `test_committed_income_without_income_has_null_ratio`
- Categoria sem histórico → `test_category_delta_without_history_has_zero_baseline`
- Uma query por relatório → `test_each_report_is_one_query` (conta os statements
  executados na conexão durante a requisição)
