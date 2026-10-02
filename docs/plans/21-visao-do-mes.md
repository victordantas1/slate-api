# Plano — #21 GET /months/{yyyy-mm}

Issue: https://github.com/victordantas1/slate-api/issues/21 · M3 · API · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §7, §8, que
> não existe neste repositório. Implementado a partir da issue.

## 1. Objetivo

A tela inicial: entries da competência agrupadas por conta, com entradas, saídas,
saldo e total comprometido em parcelas, numa única query.

## 2. Arquivos

- `app/services/months.py` (novo): a query e o agrupamento
- `app/api/months.py` (novo): router `/months`
- `app/main.py`: registra o router
- `tests/test_months_api.py` (novo)
- `openapi.json`: regerado

## 3. Tarefas

1. Testes HTTP dos critérios de aceite (TDD).
2. Service e router; `openapi.json`.

## 4. Pressupostos

- Um SELECT só: `entry` filtrada por `household_id = ? AND competencia = ?` (o índice
  `ix_entry_household_id_competencia`), com commitment, conta e categoria por join na PK.
  Agrupamento e totais saem em Python das linhas devolvidas, então conferem por construção.
- Direção vem da categoria **da entry** (que pode ter override), não do commitment.
- Entradas = soma das entries de categoria `income`; saídas = `expense`; saldo = entradas − saídas.
- Total comprometido em parcelas = saídas de commitments `installment` no mês. Avulso
  (`single`) e recorrente não entram.
- Todas as entries da competência entram, pagas ou não, de qualquer status de commitment.
- Totais por conta e do mês. Contas sem entry no mês não aparecem.
- Mês fora de `yyyy-mm` (ou mês 00/13, ano 0000) → 422. Mês sem lançamento → 200 com
  `accounts: []` e totais zerados.

## 5. Testes

- Agrupamento por conta e totais do mês e por conta, com receita, despesa, parcela e avulso.
- Totais = soma das entries devolvidas (mês e conta).
- Override de categoria para `income` move a entry de saída para entrada.
- Mês vazio → estrutura vazia; outra household não aparece; formato inválido → 422; sem token → 401.
- Uma única query por requisição (contando statements no engine, fora `SET ROLE`/claims).
- `EXPLAIN` do SELECT usa `ix_entry_household_id_competencia` para ler `entry`.
