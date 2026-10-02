# Deploy no Render

A API roda como web service no plano free do Render, definido em `render.yaml`. O banco
é o Postgres do Supabase (free tier). Dois limites do free tier moldam este setup:

- O Render hiberna o serviço após 15 minutos sem requisição; o cold start leva 30–50 s.
- O Supabase pausa o projeto após 7 dias sem atividade no banco.

O keepalive (`.github/workflows/keepalive.yml`) cobre os dois: chama `/health` a cada
10 minutos, e `/health` executa `SELECT 1` no Postgres.

## O que acontece em cada deploy

1. **Build:** `pip install uv && uv sync --frozen --no-dev` instala as dependências do
   `uv.lock`, sem o grupo de dev.
2. **Start:** `alembic upgrade head` aplica as migrations pendentes e, só se ele
   passar, o `uvicorn` sobe na porta `$PORT`. O `preDeployCommand` do Render não
   existe no plano free, por isso as migrations ficam no start.
3. **Healthcheck:** o Render considera o deploy saudável quando `/health` responde 200.

## `/health`

| Situação | HTTP | Corpo |
| --- | --- | --- |
| Banco respondeu ao `SELECT 1` | 200 | `{"status": "ok", "database": "ok", ...}` |
| `DATABASE_URL` vazia (dev, testes) | 200 | `{"status": "ok", "database": "not_configured", ...}` |
| Banco configurado e inacessível | 503 | `{"status": "degraded", "database": "unavailable", ...}` |

## Job do horizonte

A extensão do horizonte de recorrentes roda no `pg_cron` do Supabase, não no Render.
A migration habilita e agenda; o que conferir no painel está em `docs/horizon-job.md`.

## Variáveis de ambiente

Todas são campos de `Settings` (`app/core/config.py`) e estão declaradas em
`render.yaml`. As marcadas como "pedida no Blueprint" têm `sync: false`: o Render pede o
valor na criação do Blueprint e ele nunca entra no repositório.

| Variável | Valor em produção | Origem |
| --- | --- | --- |
| `PYTHON_VERSION` | `3.12.7` | `render.yaml` |
| `APP_NAME` | `slate-api` | `render.yaml` |
| `ENVIRONMENT` | `production` | `render.yaml` |
| `DATABASE_URL` | URL do Supavisor em session mode, com schema `postgresql+asyncpg://` | pedida no Blueprint |
| `SUPABASE_JWT_SECRET` | Settings → API → JWT Secret do Supabase | pedida no Blueprint |
| `CORS_ALLOWED_ORIGINS` | domínio do slate-web, ex. `https://slate-web.onrender.com` (vírgula separa vários) | pedida no Blueprint |
| `DB_POOL_SIZE` | `5` | `render.yaml` |
| `DB_MAX_OVERFLOW` | `5` | `render.yaml` |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | `render.yaml` |
| `DB_STATEMENT_CACHE_SIZE` | `0` | `render.yaml` |

### `DATABASE_URL`

A conexão direta do Supabase (`db.<ref>.supabase.co`) é só IPv6, e o Render não tem
saída IPv6. Use o pooler (Supavisor) em **session mode**, porta 5432:

```
postgresql+asyncpg://postgres.<ref>:<senha>@aws-0-<regiao>.pooler.supabase.com:5432/postgres
```

No dashboard: Connect → Session pooler. O Supabase entrega `postgresql://`; troque o
schema para `postgresql+asyncpg://` antes de colar.

## Passos manuais

Nada disso é automatizado: depende de contas e segredos.

1. **Render:** New → Blueprint, aponte para `victordantas1/slate-api` (branch `main`).
   O Render lê `render.yaml` e pede `DATABASE_URL`, `SUPABASE_JWT_SECRET` e
   `CORS_ALLOWED_ORIGINS`. Confirme e aguarde o primeiro deploy.
2. **Confira o deploy:** nos logs, `alembic upgrade head` deve aparecer antes do
   uvicorn. Depois, `curl https://<servico>.onrender.com/health` deve responder
   `"database": "ok"`.
3. **Keepalive:** no GitHub, Settings → Secrets and variables → Actions → Variables,
   crie `SLATE_API_URL` com a URL pública do serviço (ex.
   `https://slate-api.onrender.com`). Em Actions → keepalive → Run workflow, rode uma
   vez à mão e confira que passou.
4. **CORS:** quando o slate-web tiver domínio definitivo, atualize
   `CORS_ALLOWED_ORIGINS` no dashboard do Render (Environment) com ele.

## Limites do keepalive

- O cron do GitHub Actions é best effort: em horários de pico pode atrasar além de
  15 minutos, e o Render hiberna nesse intervalo. Para o Supabase (7 dias) isso não
  importa. Se o cold start incomodar, crie também um monitor HTTP gratuito
  (cron-job.org ou UptimeRobot) em `GET <url>/health` a cada 10 minutos; os dois podem
  coexistir.
- O GitHub desativa workflows agendados após 60 dias sem commits no repositório. Se
  isso acontecer, reative em Actions → keepalive → Enable workflow.
- Um serviço free acordado o mês inteiro usa cerca de 744 das 750 horas mensais do
  Render. Um segundo serviço free na mesma conta não caberia acordado em tempo
  integral.
