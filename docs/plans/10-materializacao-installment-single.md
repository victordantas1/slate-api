# Plano — #10 Motor: materialização de installment e single

Issue: https://github.com/victordantas1/slate-api/issues/10 · M2 · Motor · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §6, D5, D6,
> que não existe neste repositório. Implementado a partir do corpo da issue.

## 1. Objetivo

Dado um commitment `installment` ou `single`, gravar o commitment e as N entries numa
única transação, todas `previsto`/`manual`/`edited_manually=false`, com rollback completo
se qualquer linha violar uma constraint.

## 2. Arquivos

- `app/domain/installments.py` — tocado: `seq_from_competencia` (inversa de
  `competencia`) e `plan_installments`, que devolve `(seq, competencia, amount)` por parcela.
- `app/services/materialization.py` — criado: `create_installment_commitment`.
- `tests/test_domain_installments.py` — tocado: casos e property-based das funções novas.
- `tests/test_materialization.py` — criado: testes contra Postgres (testcontainers).

## 3. Tarefas

1. Domínio: `seq_from_competencia` e `plan_installments`, em TDD, com hypothesis.
2. Serviço: lê o `first_installment_offset` da conta (escopada pela household), insere
   commitment e entries dentro de `session.begin_nested()`, e testes de atomicidade.

## 4. Pressupostos

- O `seq` de cada entry é derivado da competência (`seq_from_competencia`), não de um
  contador, como pede o achado do PR #40: assim o recorrente (#11) reusa a mesma função
  e `ON CONFLICT (commitment_id, competencia)` nunca esbarra em `(commitment_id, seq)`.
- `single` passa pelo mesmo código de `installment`, com `installment_count = 1` (D6).
  `single` com contagem diferente de 1 e `recurring` são recusados com `ValueError`
  antes de tocar no banco (recorrente é #11).
- A atomicidade é um savepoint (`begin_nested`): o serviço roda dentro da transação da
  requisição (`get_member_session`) e uma falha desfaz commitment e entries sem abortar
  a transação externa. O commit fica com quem chamou.
- Conta inexistente ou de outra household levanta `AccountNotFoundError` (o offset vem
  dela, então não dá para deixar só a FK composta recusar).
- Sem endpoint HTTP: `POST /commitments` é issue própria.

## 5. Testes

- Atômica: commitment e entries aparecem juntos; contagem de entries == `installment_count`.
- Rollback completo: uma entry inválida (amount negativo forçado) e uma categoria de
  outra household desfazem tudo, e a sessão continua utilizável depois.
- `single` produz exatamente 1 entry, com o total inteiro.
- Toda entry nasce `previsto`, `manual`, `edited_manually=false`, categoria do commitment.
- Property-based: `plan_installments` soma o total, seqs 1..N, competências consecutivas
  no dia 1, e `seq_from_competencia(competencia(seq)) == seq`.
