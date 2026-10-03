# Plano — #23 GET /external-holders/balance

Issue: https://github.com/victordantas1/slate-api/issues/23 · M3 · API · P1

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §5, D10, que
> não existe neste repositório. Implementado a partir do escopo e dos critérios de
> aceite da própria issue.

## 1. Objetivo

Quanto a household deve a cada titular externo, derivado das entries e nunca
armazenado. Marcar a entry como `pago` é o registro do reembolso.

## 2. Arquivos

Criados:

- `tests/test_external_holders_balance.py`

Modificados:

- `app/api/external_holders.py`: `GET /external-holders/balance`, declarado antes de
  `/{holder_id}`
- `openapi.json`: regerado

## 3. Contrato

`GET /external-holders/balance` → lista `{id, name, balance}` de todos os titulares da
household, ordenada por nome. `balance` soma `entry.amount` com status diferente de
`pago` das contas `holder_kind='external'` do titular.

## 4. Pressupostos

- Uma consulta: `external_holder LEFT JOIN (account JOIN commitment JOIN entry não
  paga)`, agrupada por titular. O LEFT JOIN faz titular sem saldo aberto voltar com
  zero em vez de sumir.
- Conta arquivada continua contando: arquivar não quita dívida.
- Nenhuma tabela nova, nenhuma migration.

## 5. Testes

- Nenhuma tabela de transferência → `test_no_transfer_table_exists`
- Titular sem saldo volta zero → `test_holder_without_accounts_returns_zero`,
  `test_balance_sums_unpaid_entries_per_holder` (titular com tudo pago)
