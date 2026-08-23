# Slate Issue Loop — estado

- **repo:** slate-api
- **issue:** #2 — Conexão com Supabase e setup do Alembic
- **url:** https://github.com/victordantas1/slate-api/issues/2
- **plano:** docs/plans/2-conexao-supabase-alembic.md
- **milestone:** M1 · Fundação
- **rota:** writing-plans
- **execucao:** 6 tarefas / 12 arquivos → subagent-driven-development
- **fase:** entregue (mergeado)
- **branch:** chore/2-conexao-supabase-alembic (base main sha 8f0f267) — mergeada em
  `main` pelo merge commit trazido pelo PR #28.
- **pr:** https://github.com/victordantas1/slate-api/pull/28 — MERGEADO em
  2026-08-23T02:57:18Z por victordantas1
- **ci:** fase pulada — repo sem `.github/workflows/`; configurar CI é escopo da
  issue #25.
- **atualizado:** 2026-08-23T02:59Z

## Onde parei

Rodada concluída. PR #28 foi mergeado por `victordantas1` logo após ser marcado
"ready for review" (converteu de draft e mergeou na mesma janela — notificação de
`pull_request.ready_for_review` seguida, minutos depois, da confirmação de merge via
`pull_request_read`). Issue #2 fecha automaticamente pelo `Closes #2` no corpo do PR.
Sessão desinscrita da atividade do PR #28 (subscription encerrada — regra: só
permanece até o PR ser mergeado ou fechado).

Resumo do que foi entregue: engine SQLAlchemy 2.x assíncrono (`app/db/session.py`),
`Base` declarativo com `naming_convention` (`app/db/base.py`), quatro settings de pool
dimensionadas para o free tier do Supabase (`app/core/config.py`), Alembic
inicializado com template assíncrono (`alembic.ini`, `migrations/env.py`), e dois
testes de integração reais provando `alembic upgrade head` e
`alembic revision --autogenerate` contra Postgres real
(`tests/test_db_migrations.py`). Implementado via subagent-driven-development (6
tarefas, cada uma com implementador + revisor dedicados) mais uma revisão final de
branch (1 achado Important corrigido, 2 Minor corrigidos, sem rulings de conflito em
nenhuma etapa).

## Próxima execução

Fila do `slate-api` destravada: com a #2 fechada, a #3 (Migration: household, member e
external_holder) tem sua dependência implícita de M1 satisfeita e é a próxima
candidata — M1, P0, menor número entre as sem `status:*`. Confira o GitHub primeiro
(labels, PRs abertos, branches) antes de assumir — pode ter mudado desde este registro.

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

## Bloqueios registrados

- nenhum
