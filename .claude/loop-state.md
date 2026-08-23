# Slate Issue Loop — estado

- **repo:** slate-api
- **issue:** #3 — Migration: household, member e external_holder
- **url:** https://github.com/victordantas1/slate-api/issues/3
- **plano:** docs/plans/3-household-member-external-holder.md
- **milestone:** M1 · Fundação
- **rota:** writing-plans
- **execucao:** 2 tarefas / 4 arquivos → inline (TDD)
- **fase:** pr (aberto, status:review, aguardando merge — sem CI configurado)
- **branch:** chore/3-household-member-external-holder (base main)
- **pr:** https://github.com/victordantas1/slate-api/pull/29 (draft)
- **ci:** repo ainda sem `.github/workflows/` — fase pulada
- **atualizado:** 2026-08-23T16:45Z

## Onde parei

Estado anterior estava desatualizado: registrava fase `implementacao` mas o
GitHub já mostrava PR #29 aberto (`Closes #3`), rotulado `status:review`, sem
comentários de revisão pendentes e `mergeable_state: clean`. A execução
anterior evidentemente concluiu a revisão e abriu o PR mas morreu antes de
commitar o estado final. Esta execução conferiu o GitHub (fonte de verdade),
confirmou PR #29 limpo (zero review threads, zero comments, zero reviews
pendentes), rodou os quatro gates na branch `chore/3-household-member-external-holder`
(`ruff check`, `ruff format --check`, `mypy app tests`, `pytest -q`) — todos
verdes — e sincronizou este arquivo. Repo ainda sem `.github/workflows/`,
então a fase `ci` é pulada. Não há mais trabalho acionável nesta issue até
que alguém faça o merge do PR #29 (draft) — a próxima execução deve conferir
o GitHub primeiro: se já tiver sido mergeado, marcar `entregue` e seguir para
a próxima issue elegível.

## Pressupostos

- IDs UUID (`gen_random_uuid()`) nas três tabelas — consistente com
  `supabase_user_id` (que já é UUID) e com o que a RLS (issue #7) e o JWT (issue #8)
  vão comparar.
- `household.name` e `member.name` adicionados (não pedidos explicitamente pelos
  critérios de aceite, mas mínimos para o registro ser utilizável).
- `ON DELETE CASCADE` de member/external_holder para household; índice em
  `household_id` nas duas tabelas filhas (padrão de acesso por tenant, issue #7).
- Sem `updated_at`, sem soft-delete, sem `relationship()` ORM — nenhum critério pede.
- Teste de integração contra Postgres real desta sessão (não testcontainers — issue
  #15), mesmo padrão `skipif` sem `DATABASE_URL` de `tests/test_db_migrations.py`.

## Bloqueios registrados

- nenhum
