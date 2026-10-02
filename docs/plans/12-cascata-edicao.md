# Plano — #12 Motor: cascata de edição com escopo this/forward/all

Issue: https://github.com/victordantas1/slate-api/issues/12 · M2 · Motor · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §6.2, que não
> existe neste repositório. Implementado a partir do corpo da issue.

## 1. Objetivo

Editar um commitment e suas entries com escopo `this`, `forward` ou `all` sem nunca
alterar entry `pago` e sem sobrescrever entry `edited_manually`, e recalcular a divisão
de um parcelamento (`installment_count`/`total_amount`) preservando as entries pagas.

## 2. Arquivos

- `app/domain/cascade.py` — criado: `resplit_installments`, puro.
- `app/services/cascade.py` — criado: `edit_entries` e `reschedule_installments`.
- `tests/test_domain_cascade.py` — criado: casos e property-based (hypothesis).
- `tests/test_cascade.py` — criado: testes contra Postgres (testcontainers).

## 3. Tarefas

1. Domínio: `resplit_installments(total, count, locked)` divide `total - soma(locked)`
   entre os seqs `1..count` que não estão travados, resíduo no primeiro livre, em TDD
   com hypothesis.
2. Serviço `edit_entries(session, household_id=, entry_id=, scope=, amount=,
   category_id=)`, com o filtro da issue:
   - `this`: UPDATE só na entry âncora e liga `edited_manually`.
   - `forward`: UPDATE em `competencia >= X AND NOT edited_manually AND status <> 'pago'`.
   - `all`: idem, sem filtro de competência.
3. Serviço `reschedule_installments(session, household_id=, commitment_id=,
   installment_count=, total_amount=)`: trava `pago` e `edited_manually`, redivide o
   resto entre os seqs livres, apaga os livres além da nova contagem e insere os seqs
   novos.

## 4. Pressupostos

- Campos em cascata: `amount` e `category_id`, os únicos que vivem na entry.
  `description`, conta e data de compra são do commitment e não cascateiam.
- `this` numa entry `pago` é recusado (`PaidEntryError`): entry paga não muda em
  nenhuma operação do motor.
- `forward`/`all` também atualizam o commitment para o horizonte futuro e a
  materialização: `recurring_amount` (recorrente) e `category_id`. `this` não muda
  esses campos do commitment.
- Em parcelamento e avulso, toda edição de `amount`, em qualquer escopo, faz
  `total_amount` passar a ser a soma das entries, mantendo a invariante 1 (soma ==
  total). A renegociação de 393 para 134 baixa o total do parcelamento.
- Recalcular a divisão trata `edited_manually` como travada, igual a `pago`: é o
  `all` aplicado ao plano. O resíduo da divisão vai para o primeiro seq livre, como em
  `split_installments`.
- Diminuir `installment_count` abaixo do seq de uma entry travada, ou um total menor
  que a soma das travadas, é recusado (`InvalidRescheduleError`). Sem seqs livres, o
  total precisa ser exatamente a soma das travadas.
- A competência das parcelas novas sai das entries existentes (âncora no seq), não do
  `first_installment_offset` atual da conta, que pode ter mudado depois da compra.
- `single` continua 1x: só aceita mudar `total_amount`. Recorrente não tem divisão.
- O commitment é travado com `SELECT ... FOR UPDATE` durante a cascata.
- Sem endpoint HTTP: a rota de commitments é a #19. O `openapi.json` não muda.
- A invariante 2 desta cascata é testada aqui; quando a #15 (PR #46) entrar, a cascata
  vai para `ENGINE_OPERATIONS` de `tests/test_invariants.py`.

## 5. Testes

- Invariante 2: snapshot de todas as colunas das entries `pago` antes/depois de cada
  escopo e do reschedule, inclusive numa bateria aleatória de operações com semente fixa.
- `edited_manually` é pulada por `forward` e `all`, e `this` a liga.
- Caso real: parcela renegociada de 393 para 134 (`this`) sobrevive a `forward`/`all`
  em valor e categoria e a um recálculo do commitment pai.
- `installment_count` 10 → 12 e 10 → 6 com entries pagas: pagas idênticas, soma ==
  `total_amount`, seqs `1..n` e competências consecutivas.
- Recorrente: `forward` muda `recurring_amount` e o horizonte estendido depois herda o
  novo valor.
- Property-based do domínio: soma exata, travadas intactas, seqs livres = `1..count`
  menos travadas, nenhum valor negativo.
