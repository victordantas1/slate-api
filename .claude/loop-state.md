# Slate Issue Loop — estado

- **repo:** slate-api
- **issue:** #2 — Conexão com Supabase e setup do Alembic
- **url:** https://github.com/victordantas1/slate-api/issues/2
- **plano:** docs/plans/2-conexao-supabase-alembic.md
- **milestone:** M1 · Fundação
- **rota:** writing-plans
- **execucao:** 6 tarefas / 12 arquivos → subagent-driven-development
- **fase:** pr (CI n/a — repo ainda sem `.github/workflows/`)
- **branch:** chore/2-conexao-supabase-alembic (base main sha 8f0f267, head b08269f)
- **pr:** https://github.com/victordantas1/slate-api/pull/28 — aberto como draft,
  label `status:review`
- **ci:** fase pulada — repo sem `.github/workflows/`; configurar CI é escopo da
  issue #25. Sem CI para aguardar, esta execução termina com o PR aberto, igual à
  trajetória da issue #1 (PR #27 só virou "entregue" depois do merge humano).
- **atualizado:** 2026-08-23T02:55Z

## Onde parei

Rodada concluída. As 6 tarefas do plano foram implementadas via
subagent-driven-development, cada uma com implementador + revisor dedicados:

1. Dependências (SQLAlchemy async, asyncpg, Alembic) — `b2256ae`
2. Settings de pool de conexão — `1dc7543`
3. `app/db/base.py` + `app/db/session.py` (engine async, `Base` declarativo) — `5cc9b81`
4. Alembic inicializado (template assíncrono) — `3b08ee2`
5. Testes de integração reais contra Postgres local provando os dois critérios de
   aceite centrais (`alembic upgrade head`, `alembic revision --autogenerate`) — `2793581`
6. `CLAUDE.md` atualizado — `6d74795`

Revisão final de branch (whole-branch, contra o plano + critérios de aceite da issue
colados literalmente): 1 achado Important (schema `postgresql+asyncpg://` da
`DATABASE_URL` não documentado — o dashboard do Supabase entrega `postgresql://`
sem driver, e isso não estava avisado em lugar nenhum) e 2 Minor (guard de
`DATABASE_URL` ausente em `migrations/env.py` inconsistente com o `RuntimeError`
claro já existente em `app/db/session.py`; plano não listava `tests/test_config.py`).
Uma única onda de correção (commit `b08269f`) + re-revisão escopada: os três
endereçados, sem nova quebra. Nenhum ruling de conflito foi necessário em nenhuma
etapa — todas as tarefas e a revisão final aprovaram de primeira ou após a única
onda prevista pelo processo.

PR #28 aberto como draft contra `main`, `Closes #2`, label `status:review` na issue.
Repositório ainda sem `.github/workflows/` — a fase `ci` desta skill é pulada em uma
linha, conforme o próprio texto da skill prevê para as issues de M1 anteriores à
issue #25 (deploy/CI). Sem gate de CI para aguardar, o PR fica aberto para merge
humano — igual ao que aconteceu com a issue #1 (PR #27 só foi marcado `entregue` numa
execução posterior, depois do merge por `victordantas1`). Esta execução termina aqui:
uma issue por execução, e o próximo passo automatizável (fila do `slate-api` ou
`slate-web`) é trabalho de uma execução futura, não desta.

## Pressupostos

- Sem credenciais reais de Supabase neste runner. Validação de `alembic upgrade head` /
  `--autogenerate` contra um Postgres 16 local (instalado no runner, iniciado nesta
  sessão — não Docker/testcontainers, não SQLite). Código de conexão não é específico
  de Supabase (usa `DATABASE_URL` genérica), roda igual contra o Supabase real em
  produção. Registrado no comentário da issue #2 e no plano.
- Pool dimensionado com base nos limites documentados do free tier do Supabase: 60
  conexões diretas, 200 clientes via Supavisor
  (`supabase.com/docs/guides/platform/compute-and-disk`). Defaults: `db_pool_size=5`,
  `db_max_overflow=5`, `db_pool_recycle_seconds=1800`, `db_statement_cache_size=0`
  (transaction mode do Supavisor não suporta prepared statements).
- `naming_convention` no `Base.metadata` — não pedido explicitamente pela issue, mas
  pré-requisito padrão para autogenerate gerar nomes de constraint estáveis.
- Template assíncrono do Alembic (`alembic init -t async`), sem segundo driver síncrono.
- Nenhuma migration de schema nesta issue — `migrations/versions/` vazio, tabelas são
  as issues #3-#6.
- Teste de integração (`tests/test_db_migrations.py`) pulado via `skipif` sem
  `DATABASE_URL` configurada — nesta sessão roda de verdade (Postgres local
  disponível), prova real, não fabricada; protege ambientes futuros sem banco antes da
  suíte de testcontainers (issue #15).

## Próxima execução

Issue #2 fica em `status:review` até merge humano (ou até uma execução futura
confirmar CI verde, se a issue #25 configurar CI antes disso). A próxima execução do
loop deve: conferir o GitHub primeiro (PR #28 pode já estar mergeado); se sim, marcar
`entregue` e seguir para a próxima issue elegível (`slate-api` #3 — Migration:
household, member e external_holder — é a próxima em milestone M1/P0 depois da #2,
mas confira dependências e `status:*` atuais no GitHub antes de assumir).

## Bloqueios registrados

- nenhum
