# Plano — #11 Motor: horizonte rolante de recorrentes

Issue: https://github.com/victordantas1/slate-api/issues/11 · M2 · Motor · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §6.3, D7, que
> não existe neste repositório. Implementado a partir do corpo da issue.

## 1. Objetivo

Materializar as entries de todo commitment `recurring` ativo até `hoje + 24 meses`, com
`INSERT ... ON CONFLICT (commitment_id, competencia) DO NOTHING`, de modo que rodar N
vezes tenha o mesmo efeito que rodar uma (invariante 3).

## 2. Arquivos

- `app/domain/recurring.py` — criado: `horizon_end` e `plan_recurring`, puros.
- `app/services/materialization.py` — tocado: `extend_recurring_horizon` e
  `create_recurring_commitment`.
- `tests/test_domain_recurring.py` — criado: casos e property-based do domínio.
- `tests/test_recurring_horizon.py` — criado: testes contra Postgres (testcontainers).

## 3. Tarefas

1. Domínio: `horizon_end(today)` e `plan_recurring(...)`, reusando `competencia` e
   `seq_from_competencia` de `app/domain/installments.py`, em TDD com hypothesis.
2. Serviço: `extend_recurring_horizon(session, today=..., commitment_ids=None)` lê os
   recorrentes `active` com o offset da conta, planeja e insere com `ON CONFLICT DO
   NOTHING`; `create_recurring_commitment` grava o commitment e já estende o horizonte
   dele no mesmo savepoint.

## 4. Pressupostos

- O horizonte é inclusivo e medido na competência: entra toda competência
  `<= trunc_mes(hoje) + 24 meses`.
- A primeira competência do recorrente segue a regra do parcelamento:
  `trunc_mes(purchase_date) + first_installment_offset` da conta. O seq sai da
  competência (`seq_from_competencia`), nunca de um contador, então a mesma competência
  sempre recebe o mesmo seq e `(commitment_id, seq)` nunca colide antes de
  `(commitment_id, competencia)`.
- `end_date` é a data da última cobrança: a última competência é
  `trunc_mes(end_date) + offset`.
- `today` é parâmetro obrigatório: quem chama (endpoint, job) decide o fuso e o relógio.
- Só `kind = 'recurring'` e `status = 'active'` entram; `cancelled` e `settled` ficam de
  fora, e o serviço não apaga nada que já exista deles.
- Entry existente nunca é tocada, nem para corrigir valor ou categoria: o conflito é
  descartado pelo banco.
- Sem endpoint HTTP e sem agendamento: a rota de commitments é a #19, e o disparo
  periódico fica com quem tiver o job. O `openapi.json` não muda.

## 5. Testes

- Invariante 3: rodar `extend_recurring_horizon` 3 vezes deixa as entries idênticas à
  primeira rodada, e a segunda insere 0 linhas.
- Horizonte rolante: avançar `today` um mês acrescenta exatamente a competência nova,
  com o seq seguinte.
- `end_date`: nenhuma competência depois de `trunc_mes(end_date) + offset`.
- `cancelled` e `settled` não ganham entries; `installment` também não.
- Nunca sobrescreve: entry editada (valor, status `pago`, `edited_manually`) sai igual.
- `create_recurring_commitment` grava commitment e as 24 entries juntos, nascidas
  `previsto`/`manual`/`edited_manually=false`.
- Property-based: seqs `1..N` consecutivos, competências no dia 1 e consecutivas, nada
  passa do horizonte nem do `end_date`, `seq_from_competencia(c) == seq`.
