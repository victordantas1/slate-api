# Plano — #7 RLS por household_id em todas as tabelas

> Spec citada (`docs/superpowers/specs/2026-08-22-slate-design.md` §4) não existe no repo;
> implementado a partir da issue.

## Objetivo

Toda tabela do schema `public` com RLS habilitado e policies de SELECT, INSERT, UPDATE e
DELETE que derivam a household do JWT, para que o banco recuse ler ou gravar dado de
outra household mesmo que a API erre o filtro.

## Arquivos

- `migrations/versions/<rev>_rls_household.py`: role `slate_app`, função
  `current_household_id()`, grants, `ENABLE ROW LEVEL SECURITY` e as policies.
- `app/db/session.py`: `apply_rls_claims` passa a fazer `SET LOCAL ROLE slate_app`.
- `tests/test_rls.py`: isolamento entre households em todas as tabelas.
- `tests/test_member_session.py`: passa a usar o banco migrado (a role vem da migration).
- `CLAUDE.md`: convenção para tabelas novas.

## Tarefas

1. Testes de RLS vermelhos (leitura, INSERT forjado, UPDATE e DELETE cruzados, catálogo).
2. Migration com role, função, grants e policies.
3. `SET LOCAL ROLE slate_app` na sessão do membro.
4. Gates.

## Pressupostos

- A API conecta como dono das tabelas (`postgres` no Supabase, superusuário nos testes),
  e dono ignora RLS. Por isso a sessão do membro troca para a role `slate_app`
  (`NOLOGIN`, sem `BYPASSRLS`) com `SET LOCAL ROLE`, que vale só para a transação, igual
  às claims. Não usei `FORCE ROW LEVEL SECURITY`, que travaria migrations e scripts de
  manutenção que rodam como dono.
- Role própria em vez da `authenticated` do Supabase: os grants não ampliam o que a Data
  API (PostgREST) expõe, e a role existe igual em Postgres puro e no Supabase.
- A household vem de `request.jwt.claims ->> 'household_id'`, o GUC que a #8 já seta.
  Sem claims, `current_household_id()` é NULL e nenhuma linha passa.
- `household` compara `id`; as demais tabelas comparam `household_id`.
- `alembic_version` também recebe `ENABLE ROW LEVEL SECURITY`, sem policy e sem grant:
  no Supabase as tabelas de `public` são expostas a `anon`/`authenticated` pela Data API,
  e RLS sem policy fecha esse acesso. O dono (Alembic) continua lendo.
- `current_household_id()` tem `EXECUTE` revogado de `PUBLIC` e concedido só a
  `slate_app`, para não virar RPC pública no Supabase.
- As policies chamam a função como `(SELECT current_household_id())`, que o planner
  avalia uma vez por query em vez de uma vez por linha.

## Testes

| Critério | Teste |
| --- | --- |
| RLS habilitado em todas as tabelas | `test_every_table_has_rls_enabled` e `test_every_tenant_table_has_policies_for_each_command` (catálogo, pega tabela nova sem RLS) |
| Membro de A não lê dado de B | `test_member_reads_only_own_household` (parametrizado nas 7 tabelas) |
| INSERT com `household_id` forjado | `test_insert_with_forged_household_is_rejected` (parametrizado) |
| Extra: UPDATE e DELETE | `test_update_cannot_move_row_to_other_household`, `test_update_and_delete_do_not_touch_other_household` |
| Extra: sem claims | `test_without_claims_nothing_is_visible` |
| Sessão da API usa a role | `test_member_session.py` confere `current_user = slate_app` |
