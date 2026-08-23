# Plano — #2 Conexão com Supabase e setup do Alembic

Issue: https://github.com/victordantas1/slate-api/issues/2 · M1 · Fundação · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §4, que não
> existe neste repositório. Documento ausente não bloqueia: implementado a partir do
> escopo e dos critérios de aceite da própria issue.

## 1. Objetivo

Engine SQLAlchemy 2.x assíncrono com pool dimensionado para o limite do free tier do
Supabase, e Alembic inicializado com `env.py` lendo a URL das settings — provado por
`alembic upgrade head` e `alembic revision --autogenerate` rodando de fato contra um
Postgres real.

## 2. Arquivos

Criados:

- `app/db/base.py` — `Base` declarativo com `naming_convention` (constraints com nome
  estável, pré-requisito para autogenerate confiável)
- `app/db/session.py` — `build_async_engine`, `get_engine` (cache), `get_sessionmaker`,
  `get_session` (dependency)
- `alembic.ini` — aponta para `migrations/`, sem URL real (vem das settings em runtime)
- `migrations/env.py` — template assíncrono, `target_metadata = Base.metadata`, URL via
  `get_settings().database_url`
- `migrations/script.py.mako` — template padrão do `alembic init`
- `migrations/versions/.gitkeep` — nenhuma migration de schema ainda; issues #3–#6
  preenchem
- `tests/test_db_session.py` — kwargs do pool a partir das settings, sem tocar rede
- `tests/test_db_migrations.py` — `upgrade head` e `autogenerate` contra Postgres real,
  pulado se `DATABASE_URL` não estiver configurada

Modificados:

- `pyproject.toml` — `sqlalchemy[asyncio]`, `asyncpg`, `alembic` como dependências
- `app/core/config.py` — `db_pool_size`, `db_max_overflow`, `db_pool_recycle_seconds`,
  `db_statement_cache_size`
- `.env.example` — as quatro chaves novas documentadas com os defaults
- `CLAUDE.md` — comando `alembic upgrade head` / `alembic revision --autogenerate` na
  seção de comandos, e `app/db` deixa de estar "vazio de propósito"

## 3. Tarefas

1. `pyproject.toml`: adicionar `sqlalchemy[asyncio]`, `asyncpg`, `alembic`; `uv sync`.
2. `app/core/config.py` + `.env.example`: quatro campos de pool, com defaults
   conservadores (ver Pressupostos).
3. `tests/test_db_session.py` (TDD, vermelho primeiro) + `app/db/base.py` +
   `app/db/session.py`: `_engine_kwargs` como função pura testável sem rede;
   `build_async_engine` levanta `RuntimeError` sem `DATABASE_URL`.
4. `alembic init -t async migrations`, depois editar `env.py` para usar
   `get_settings().database_url` e `Base.metadata`; `alembic.ini` sem URL fixa.
5. `tests/test_db_migrations.py` (TDD, vermelho primeiro contra o Postgres local desta
   sessão) provando os dois critérios de aceite ligados a comando; ajustar `env.py` até
   verde.
6. `CLAUDE.md`: documentar os dois comandos de Alembic e atualizar a tabela de
   estrutura.

## 4. Pressupostos

- **Sem credencial real de Supabase neste runner.** Validação de
  `alembic upgrade head` / `--autogenerate` contra um Postgres 16 local (instalado no
  runner, iniciado nesta sessão — não é Docker/testcontainers, não é SQLite). O código
  de conexão não assume Supabase especificamente (URL vem de `DATABASE_URL`), então o
  mesmo caminho roda contra o Supabase real em produção. Registrado no comentário da
  issue.
- **Dimensionamento do pool**: Supabase free tier (Nano/Micro) — "Database Max
  Connections" 60 (conexão direta), "Connection Pooler Max Clients" 200 (Supavisor).
  Fonte: `supabase.com/docs/guides/platform/compute-and-disk`. Defaults conservadores:
  `db_pool_size=5`, `db_max_overflow=5` (máx. 10 conexões por instância da API — larga
  margem sob os dois limites, mesmo com múltiplas instâncias ou migrations rodando em
  paralelo), `db_pool_recycle_seconds=1800` (evita conexão presa em pooler serverless),
  `pool_pre_ping=True` fixo no código.
- **`statement_cache_size=0` (asyncpg) por padrão**, não apenas quando pooler.
  Documentação do Supabase: "Transaction mode does not support prepared statements. To
  avoid errors, turn off prepared statements for your connection library." Custo é
  pequeno (sem cache de statement preparado) e a alternativa (deixar default e quebrar
  em produção atrás do Supavisor) é pior. Configurável via `db_statement_cache_size`
  caso uma conexão direta futura queira reabilitar.
- **Alembic com template assíncrono** (`alembic init -t async`), não uma segunda
  dependência de driver síncrono só para migrations — a spec pede "SQLAlchemy 2.x
  async" e o template assíncrono é o caminho oficialmente suportado pelo Alembic para
  isso, sem adicionar `psycopg2`/`psycopg` só para esse propósito.
- **`naming_convention` no `Base.metadata`** — não pedido explicitamente pela issue,
  mas é o pré-requisito padrão (cookbook do próprio SQLAlchemy) para o autogenerate
  gerar nomes de constraint estáveis entre revisões; sem isso, o critério de aceite 2
  fica frágil a partir da segunda migration. Menor decisão que sustenta o critério, não
  escopo novo.
- **Nenhuma migration de schema nesta issue.** `migrations/versions/` fica vazio
  (`.gitkeep`) — as tabelas (`household`, `account`, `category`, `commitment`/`entry`)
  são as issues #3–#6. O teste de autogenerate prova o mecanismo com uma tabela de
  sonda temporária, criada e removida dentro do próprio teste.
- **Teste de integração pulado sem `DATABASE_URL`.** Sem a suíte de testcontainers
  (issue #15) ainda no repo, o teste teria de assumir Docker ou Postgres local sempre
  presentes — nenhum dos dois é garantido em toda execução futura. Guardado com
  `skipif`, e nesta sessão roda de fato (Postgres local disponível) — não é um "pulo"
  fabricado, é a prova real acontecendo agora, com o guard para não quebrar ambientes
  sem banco antes da issue #15.

## Contagem

6 tarefas, 12 arquivos distintos (5 criados + 4 modificados na lista acima, mais
`script.py.mako` e `versions/.gitkeep` gerados pelo `alembic init`) → **≥ 3 tarefas
ou ≥ 5 arquivos → subagent-driven-development.**

## 5. Testes

- `_engine_kwargs` mapeia cada campo de `Settings` para o kwarg correto do
  `create_async_engine` (critério "pool dimensionado").
- `build_async_engine` levanta `RuntimeError` sem `DATABASE_URL` configurada.
- `test_alembic_upgrade_head_runs_against_real_postgres` — critério 1.
- `test_alembic_autogenerate_detects_new_table` — critério 2, tabela de sonda.
