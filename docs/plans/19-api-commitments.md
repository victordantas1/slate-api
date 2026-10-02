# Plano — #19 POST /commitments, GET /commitments/active e DELETE

Issue: https://github.com/victordantas1/slate-api/issues/19 · M3 · API · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §7, D10, que
> não existe neste repositório. Implementado a partir do escopo e dos critérios de
> aceite da própria issue, sobre o motor da #10 e a RLS da #7.

## 1. Objetivo

Endpoints autenticados para criar um commitment já materializado em entries, listar os
commitments ativos com saldo devedor e mês de quitação derivados das entries, e apagar
um commitment.

## 2. Arquivos

Criados:

- `app/services/commitments.py`: listagem dos ativos com os agregados, busca e remoção
- `app/api/commitments.py`: router `/commitments` e schemas Pydantic
- `tests/test_commitments_api.py`: testes HTTP contra o Postgres do testcontainers

Modificados:

- `app/main.py`: registra o router
- `openapi.json`: regerado

## 3. Tarefas

1. **Testes (TDD).** POST de installment e single devolvendo as entries, validações
   (422), conta e categoria de outra household (422), GET active com saldo e quitação
   derivados, DELETE, isolamento entre households e 401 sem token.
2. **Service e router**, depois `openapi.json`.

## 4. Pressupostos

- `POST /commitments` chama `create_installment_commitment` (#10) dentro da transação
  da requisição e responde 201 com o commitment e as entries materializadas.
- `recurring` chama `create_recurring_commitment` (#11) e responde com as entries do
  horizonte rolante (`hoje + 24 meses`), limitadas pelo `end_date`. Exige
  `recurring_amount`, recusa `total_amount` e `installment_count`, e `end_date` precisa
  ser `>= purchase_date` (a mesma regra de `end_recurring`, #13). "Hoje" é a data em
  `America/Sao_Paulo` pelo relógio do banco, o mesmo cálculo do job de horizonte.
- Conta ou categoria inexistente, de outra household ou arquivada responde 422: é erro
  no corpo do pedido, não recurso da URL.
- Saldo devedor = `SUM(amount)` das entries com `status <> 'pago'`; mês de quitação =
  `MAX(competencia)` dessas mesmas entries. Os dois saem de um agregado na consulta,
  nunca de coluna (D10). Sem entry em aberto, saldo `0` e quitação `null`.
- Recorrente sem `end_date` não quita: `payoff_month` é `null` e o saldo cobre só as
  entries já materializadas no horizonte.
- `GET /commitments/active` lista `status = 'active'`. A transição para `settled`
  é da #13, então um commitment todo pago aparece com saldo `0` até lá.
- `DELETE /commitments/{id}` (padrão `keep_paid=false`) apaga o commitment e, pela FK
  em cascata, todas as entries. Responde 204.
- `DELETE /commitments/{id}?keep_paid=true` é o cancelamento (preserva as pagas e
  confirmadas, remove as previstas, status `cancelled`), entregue pela #13. Commitment
  que não está `active` responde 409.
- Valores monetários saem como string decimal no JSON (padrão do Pydantic para
  `Decimal`), para não perder centavos em `float`.

## 5. Testes

- POST installment: 201, `entries` com N linhas somando o total, competências
  consecutivas, todas `previsto`; single com 1 entry.
- POST recurring: com `end_date`, entries até `trunc_mes(end_date) + offset`; sem
  `end_date`, até o fim do horizonte, e o GET active traz `payoff_month` nulo.
- POST inválido: installment sem `installment_count`, single com contagem diferente de
  1, sem `total_amount`, recurring sem `recurring_amount`, com `total_amount` ou
  `installment_count`, ou com `end_date` antes da compra (422); conta/categoria de
  outra household (422).
- GET active: saldo e quitação mudam quando uma entry é marcada `pago`; todas pagas
  dão saldo 0 e quitação `null`; commitments de outra household não aparecem;
  cancelados não aparecem.
- DELETE: 204 e commitment e entries somem; outra household 404; `keep_paid=true` cancela e
  mantém as pagas.
- 401 sem token.
