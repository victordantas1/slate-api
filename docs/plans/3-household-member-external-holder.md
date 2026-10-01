# Plano — #3 Migration: household, member e external_holder

Issue: https://github.com/victordantas1/slate-api/issues/3 · M1 · Fundação · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §5, que não
> existe neste repositório. Pela regra da skill, documento ausente não bloqueia:
> implementado a partir do escopo e dos critérios de aceite da própria issue, com
> apoio no padrão já estabelecido pelas issues #4, #7, #8 e #18 (que referenciam
> `household_id` como coluna de tenant em todas as tabelas, e `owner_member_id` /
> `external_holder_id` como FKs a partir de `account`).

## 1. Objetivo

Três tabelas base — `household`, `member`, `external_holder` — com as constraints de
unicidade exigidas e migration Alembic com downgrade funcional.

## 2. Arquivos

Criados:

- `app/db/models.py` — modelos ORM `Household`, `Member`, `ExternalHolder`
- `migrations/versions/<hash>_household_member_external_holder.py` — migration gerada
  por autogenerate
- `tests/test_household_member_external_holder.py` — testes de integração contra
  Postgres real provando os três critérios de aceite

Modificados:

- `migrations/env.py` — importa `app.db.models` para registrar as tabelas em
  `Base.metadata` antes do autogenerate (sem isso `target_metadata` fica vazio para
  essas classes)

## 3. Tarefas

1. `app/db/models.py`: `Household` (id, name, created_at), `Member` (id,
   household_id FK, supabase_user_id UNIQUE, name, created_at), `ExternalHolder` (id,
   household_id FK, name, created_at, UNIQUE(household_id, name)). `migrations/env.py`
   importando o módulo. Gerar a migration com
   `uv run alembic revision --autogenerate -m "household member external_holder"` e
   revisar o arquivo produzido (upgrade cria as três tabelas na ordem certa por causa
   das FKs; downgrade dropa na ordem inversa).
2. `tests/test_household_member_external_holder.py` (TDD, vermelho primeiro): três
   testes de integração contra o Postgres real desta sessão, seguindo o padrão de
   `tests/test_db_migrations.py` (`skipif` sem `DATABASE_URL`) — um por critério de
   aceite. Rodar até verde.

## 4. Pressupostos

Cada um é decisão do degrau 2 ou 5 da escada (código vizinho ou nada respondeu):

- **Chaves primárias UUID** (`gen_random_uuid()`, nativo do Postgres 13+, sem
  extensão). A issue #8 usa `member_id` extraído do JWT do Supabase, e
  `supabase_user_id` já é UUID (é o `auth.users.id` do Supabase) — manter os IDs
  internos no mesmo tipo evita conversão em toda FK e em toda policy de RLS (issue
  #7) que vai comparar `household_id`/`member_id` contra claims do JWT.
- **`household.name`, `member.name`**: não citados nos critérios de aceite, mas
  necessários para o objeto ser utilizável (um household ou membro sem nome não tem
  como aparecer em UI nenhuma) — menor decisão que sustenta o propósito da tabela, não
  escopo novo. Ambos `String(120) NOT NULL`.
- **`ON DELETE CASCADE`** de `member.household_id` e `external_holder.household_id`
  para `household.id`. Não há cenário no design de um membro ou titular externo sem
  household — apagar a household é o único jeito de "apagar" os dois, então a
  alternativa (`RESTRICT`) só empurraria o cascade para código de aplicação sem
  ganhar nada.
- **Índice em `member.household_id` e `external_holder.household_id`**: toda query
  do produto filtra por household (é a unidade de tenant — issue #7, RLS). Sem
  índice, esse filtro faz table scan em todas as tabelas filhas desde a primeira
  query. Menor decisão que sustenta o padrão de acesso já definido, não feature nova.
- **`created_at` com `server_default=now()`** nas três tabelas — mesmo padrão que
  seria repetido em `account`, `category` etc. (issues #4-#6); fixado aqui como
  convenção porque é a primeira migration de dados do repo.
- **Sem `updated_at`, sem soft-delete, sem relationships ORM (`relationship()`)**:
  nenhum critério de aceite pede, e nenhuma issue até agora (#4, #7, #8, #18) depende
  de navegação objeto-a-objeto — só das colunas de FK. Menor diff.
- **Teste de integração contra Postgres real desta sessão** (não testcontainers,
  ainda não migrado — issue #15), mesmo padrão de `skipif` sem `DATABASE_URL` de
  `tests/test_db_migrations.py`.

## 5. Testes

- `test_member_supabase_user_id_is_unique` — dois `member` com o mesmo
  `supabase_user_id` (em households diferentes) violam a constraint UNIQUE → critério
  1.
- `test_external_holder_name_is_unique_per_household` — dois `external_holder` com o
  mesmo nome na mesma household violam UNIQUE; o mesmo nome em households diferentes
  não viola → critério 2.
- `test_migration_downgrade_runs` — `alembic downgrade -1` depois do `upgrade head`
  roda sem erro e as três tabelas somem → critério 3.

## Contagem

2 tarefas, 4 arquivos distintos (1 criado direto + 1 gerado + 1 modificado + 1 teste)
→ **≤ 2 tarefas e ≤ 4 arquivos → inline, em TDD.**
