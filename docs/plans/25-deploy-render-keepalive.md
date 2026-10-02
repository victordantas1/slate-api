# Plano — #25 Deploy no Render com healthcheck e keepalive

Issue: https://github.com/victordantas1/slate-api/issues/25 · M4 · Deploy · P1

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §4, que não
> existe neste repositório. Implementado a partir do corpo e dos critérios de aceite
> da própria issue.

## 1. Objetivo

A API sobe no Render (free tier) a partir de um `render.yaml` versionado, roda as
migrations a cada deploy, aceita CORS só do domínio do slate-web, e um keepalive
externo bate em `/health` com uma consulta real ao Postgres, mantendo o serviço do
Render acordado e o projeto do Supabase ativo.

## 2. Arquivos

Criados:

- `render.yaml`: blueprint do web service (build, start com migrations, healthcheck,
  variáveis de ambiente)
- `.github/workflows/keepalive.yml`: cron do GitHub Actions que chama `/health`
- `docs/deploy.md`: variáveis de ambiente e passos manuais (Render, Supabase,
  keepalive)
- `tests/test_cors.py`: CORS restrito à origem configurada

Modificados:

- `app/core/config.py`: campo `cors_allowed_origins`
- `.env.example`: chave `CORS_ALLOWED_ORIGINS`
- `app/main.py`: `CORSMiddleware` com as origens configuradas
- `app/api/health.py`: `/health` executa `SELECT 1` no banco e informa o resultado
- `tests/test_health.py`: casos do banco em `/health`

## 3. Tarefas

1. **`/health` toca o banco (TDD).** Testes para `database: not_configured` (sem
   `DATABASE_URL`, 200), `ok` (probe sobrescrita, 200) e `unavailable` (URL apontando
   para porta fechada, 503). Depois a implementação: `SELECT 1` pela engine de
   `app/db/session.py`.
2. **CORS restrito (TDD).** Testes de preflight: origem configurada recebe
   `access-control-allow-origin`, outra origem não recebe, e sem configuração nenhuma
   origem é liberada. Depois `Settings.cors_allowed_origins` + `CORSMiddleware`.
3. **`render.yaml`.** Web service Python com `uv sync --frozen --no-dev` no build,
   `alembic upgrade head && uvicorn` no start, `healthCheckPath: /health` e todas as
   variáveis de `Settings` declaradas (segredos com `sync: false`).
4. **Keepalive.** Workflow agendado a cada 10 minutos, com `workflow_dispatch`, que
   lê a URL da variável de repositório `SLATE_API_URL` e falha se `/health` não
   responder 200.
5. **Documentação.** `docs/deploy.md` com a tabela de variáveis e os passos manuais
   que dependem de conta (criar o Blueprint, colar segredos, criar a variável do
   GitHub, alternativa de keepalive externo).

## 4. Pressupostos

- Migrations rodam no `startCommand`, antes do uvicorn: `preDeployCommand` do Render
  não existe no plano free. Com uma única instância não há corrida entre migrations.
- `/health` responde 503 quando `DATABASE_URL` está configurada e o banco não
  responde. É o sinal que o keepalive e o healthcheck do Render precisam enxergar;
  sem `DATABASE_URL` (desenvolvimento, testes) continua 200 com
  `database: not_configured`.
- `SELECT 1` é a atividade no Postgres: passa pelo Supavisor até o banco, o que conta
  como uso do projeto. Nenhuma tabela de heartbeat é criada (menor diff).
- `CORS_ALLOWED_ORIGINS` é uma lista separada por vírgula; vazia (default) não libera
  nenhuma origem. Métodos e headers liberados são todos (`*`), com credenciais
  desligadas: a autenticação vem em `Authorization: Bearer` (issue #8), não em cookie.
- Keepalive no GitHub Actions, por ser versionado junto do código. O cron do Actions
  pode atrasar além de 15 minutos; o Supabase (7 dias) fica sempre coberto, e
  `docs/deploy.md` descreve um monitor externo (cron-job.org/UptimeRobot) como reforço
  para o Render não hibernar.
- Python fixado em 3.12 via `PYTHON_VERSION` no `render.yaml`, alinhado ao
  `requires-python`.

## 5. Testes

| Critério de aceite | Prova |
| --- | --- |
| `render.yaml` versionado, variáveis documentadas | `render.yaml` + tabela em `docs/deploy.md`; toda chave de `.env.example` aparece no `render.yaml` |
| Migrations rodam no deploy | `startCommand` começa com `alembic upgrade head` |
| Keepalive atinge `/health` e toca o banco | `tests/test_health.py` (ok / unavailable / not_configured) + `.github/workflows/keepalive.yml` |
| CORS restrito ao slate-web | `tests/test_cors.py` |
