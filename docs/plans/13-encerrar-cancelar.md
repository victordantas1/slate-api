# Plano — #13 Motor: encerrar e cancelar commitment

Issue: https://github.com/victordantas1/slate-api/issues/13 · M2 · Motor · P1

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §6.3, que não
> existe neste repositório. Implementado a partir do corpo da issue.

## 1. Objetivo

Encerrar um recorrente e cancelar um commitment sem reescrever o histórico: o que foi
pago continua lá. E um commitment com todas as entries pagas passa a `settled`.

## 2. Arquivos

- `app/services/lifecycle.py` — criado: `cancel_commitment`, `end_recurring` e
  `settle_if_paid`.
- `app/services/cascade.py` — `reschedule_installments` recusa commitment cancelado e
  chama `settle_if_paid` no fim.
- `app/api/commitments.py` — `DELETE /commitments/{id}?keep_paid=true` deixa de
  responder 501 e cancela; 409 para commitment que não está `active`.
- `openapi.json` — regerado.
- `tests/test_lifecycle.py` — criado: testes contra Postgres (testcontainers).
- `tests/test_commitments_api.py` — o teste do 501 vira o do cancelamento.

## 3. Tarefas

1. `cancel_commitment(session, household_id=, commitment_id=)`: trava o commitment,
   exige `active`, apaga as entries `previsto`, `status → cancelled`. Devolve quantas
   entries saíram.
2. `end_recurring(session, household_id=, commitment_id=, end_date=, today=)`: exige
   recorrente `active`, grava `end_date`, apaga as entries com competência depois da
   última (`trunc_mes(end_date) + first_installment_offset`, a mesma regra de
   `plan_recurring`) que não estão `pago` nem `edited_manually`. Se o fim novo é
   posterior ao antigo, estende o horizonte até ele. Chama `settle_if_paid`.
3. `settle_if_paid(session, household_id=, commitment_id=)`: `active → settled` quando
   não há entry fora de `pago` e o plano acabou: parcelamento/avulso com entries, ou
   recorrente com `end_date` cuja última competência já está materializada.

## 4. Pressupostos

- Cancelar apaga só `previsto`, como diz a issue: `pago` e `confirmado` ficam. Entry
  `edited_manually` ainda `previsto` sai, porque o cancelamento não protege edição.
- Encerrar apaga tudo depois do fim que não é `pago` nem `edited_manually`, inclusive
  `confirmado`.
- Cancelar ou encerrar commitment que não está `active` é recusado
  (`CommitmentStateError`); encerrar exige `recurring`.
- `total_amount` do parcelamento cancelado fica como estava: é o valor contratado, e a
  constraint `total_amount > 0` não deixaria zerar um cancelamento sem pagas.
- Não há ainda operação de pagar entry na API; `settle_if_paid` é o gancho para ela e já
  roda no fim de `end_recurring` e `reschedule_installments`.
- Sem endpoint de encerrar: não existe PATCH de commitment ainda. O cancelamento usa a
  rota que a #19 já reservou (`keep_paid=true`).
- O job do pg_cron já ignora quem não está `active` e respeita `end_date`; não muda.
- Quando a #15 (PR #46) entrar, `cancel_commitment` e `end_recurring` vão para
  `ENGINE_OPERATIONS` de `tests/test_invariants.py`.

## 5. Testes

- Pagas sobrevivem ao cancelamento (snapshot de todas as colunas), `confirmado` também;
  `previsto` some e o status vira `cancelled`. O horizonte não volta a materializar.
- Encerramento: `edited_manually` e `pago` futuras sobrevivem; o resto depois do fim
  some; antes do fim nada muda; estender o fim materializa os meses novos.
- `settled`: parcelamento com todas pagas, recorrente encerrado com todas pagas, e os
  casos que não transicionam (sobra entry não paga, recorrente sem fim).
- Recusas: cancelar duas vezes, encerrar parcelamento, recalcular cancelado.
- API: `keep_paid=true` cancela e preserva as pagas; 409 no segundo cancelamento; 404
  para outra household.
