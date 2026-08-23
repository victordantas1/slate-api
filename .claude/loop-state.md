# Slate Issue Loop — estado

- **repo:** slate-api
- **issue:** #3 — Migration: household, member e external_holder
- **url:** https://github.com/victordantas1/slate-api/issues/3
- **plano:** docs/plans/3-household-member-external-holder.md
- **milestone:** M1 · Fundação
- **rota:** writing-plans
- **execucao:** 2 tarefas / 4 arquivos → inline (TDD)
- **fase:** implementacao (gates verdes, indo para revisão)
- **branch:** chore/3-household-member-external-holder (base main)
- **pr:** ainda não aberto
- **ci:** repo ainda sem `.github/workflows/` — fase pulada quando o PR abrir
- **atualizado:** 2026-08-23T05:45Z

## Onde parei

Implementação completa: `app/db/models.py` (Household, Member, ExternalHolder),
migration `be5dfcccec1b_household_member_external_holder.py`, testes de integração
em `tests/test_household_member_external_holder.py` provando os três critérios de
aceite contra Postgres real (local desta sessão). Corrigido de quebra também
`migrations/script.py.mako` (tipagem `Union`/`Sequence` legada gerava migrations que
falhavam `ruff check` — a issue #2 não pegou isso porque não gerou nenhuma migration
de schema real). Os quatro gates (`ruff check`, `ruff format --check`, `mypy`,
`pytest`) passam limpos. Próximo passo: revisão contra o plano e os critérios de
aceite (subagente `general-purpose`), uma onda de correção se necessário, depois PR.

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
