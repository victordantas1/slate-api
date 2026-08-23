# Slate Issue Loop — estado

- **repo:** slate-api
- **issue:** #3 — Migration: household, member e external_holder
- **url:** https://github.com/victordantas1/slate-api/issues/3
- **plano:** docs/plans/3-household-member-external-holder.md
- **milestone:** M1 · Fundação
- **rota:** writing-plans
- **execucao:** 2 tarefas / 4 arquivos → inline (TDD)
- **fase:** entregue
- **branch:** chore/3-household-member-external-holder (base main)
- **pr:** https://github.com/victordantas1/slate-api/pull/29 (aberto, draft, status:review)
- **ci:** repo ainda sem `.github/workflows/` — fase pulada
- **atualizado:** 2026-08-23T17:10Z

## Onde parei

Duas execuções concorrentes tocaram este estado hoje. A primeira implementou
tudo, revisou contra o plano (achado de `created_at` timezone-naive corrigido
antes do PR) e abriu o PR #29 com `Closes #3` e label `status:review`, mas
encerrou sem marcar `entregue`. A segunda conferiu o GitHub, confirmou PR #29
limpo (zero threads de revisão pendentes, `mergeable_state: clean`), rodou os
quatro gates de novo na branch — todos verdes — e deixou o estado em `fase: pr
(aguardando merge)`, por cautela quanto a exigir merge humano quando não há CI.

Esta execução resolve a ambiguidade pela letra da skill: a seção "Fase ci"
diz que, sem `.github/workflows/`, a fase é pulada em uma linha, e o
procedimento vai direto do passo 17 (fase ci) para o 18 (marcar `entregue`,
comentar na issue, encerrar) — não há passo intermediário de "aguardar
merge". `status:review` + gates verdes + zero achados pendentes é o critério
de entrega quando não há CI para gatear. O PR seguir como *draft* é uma
convenção do harness que abriu o PR, não um sinal de trabalho incompleto por
parte da issue. Marcando `entregue` e encerrando. PR #29 fica aberto,
aguardando merge humano — mergear não é responsabilidade do loop.

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
