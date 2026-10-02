# Job do horizonte de recorrentes (pg_cron)

Todo dia 1, às 03:00 UTC (00:00 em São Paulo), o `pg_cron` do Supabase roda:

```sql
SELECT slate_jobs.run_recurring_horizon();
```

Ele materializa as entries de todo commitment `recurring` `active` até
`trunc_mes(hoje) + 24 meses`, com `ON CONFLICT (commitment_id, competencia) DO NOTHING`:
rodar de novo nunca duplica nem altera entry existente (invariante 3). "Hoje" é a data
em `America/Sao_Paulo`.

O job roda no banco, não na API, porque o Render free hiberna (ver `docs/deploy.md`).
Por isso a regra do horizonte existe em SQL (`slate_jobs.extend_recurring_horizon`),
espelhando `app.services.materialization.extend_recurring_horizon`.
`tests/test_horizon_job.py` prova que as duas gravam as mesmas entries; quem mudar uma
muda a outra.

## O que a migration faz

`migrations/versions/dcfbde1326d3_horizon_job.py`:

- Cria o schema `slate_jobs`, fora de `public` e portanto fora da Data API do Supabase.
  Nada nele é acessível a `anon`, `authenticated` ou `slate_app`.
- Cria `slate_jobs.horizon_run`, uma linha por rodada, e as duas funções.
- Se o servidor carrega o `pg_cron`, roda `CREATE EXTENSION IF NOT EXISTS pg_cron` e
  registra o job `slate-recurring-horizon`. Se não carrega (Postgres puro, os testes),
  emite um `WARNING` e segue sem agendar.

## Supabase: o que conferir no painel

No Supabase o `pg_cron` já vem pré-carregado, e a própria migration habilita a extensão
quando o deploy roda `alembic upgrade head`. Nada precisa ser ligado antes. Depois do
primeiro deploy com esta migration:

1. **Database → Extensions:** `pg_cron` aparece habilitada.
2. **Integrations → Cron** (ou SQL Editor): o job existe e está ativo.

   ```sql
   SELECT jobname, schedule, command, active FROM cron.job;
   ```

   Esperado: `slate-recurring-horizon | 0 3 1 * * | SELECT slate_jobs.run_recurring_horizon() | t`.

Se o `pg_cron` for habilitado só depois da migration (por exemplo, a extensão foi
desligada no painel), registre o job à mão no SQL Editor:

```sql
SELECT cron.schedule(
  'slate-recurring-horizon', '0 3 1 * *', 'SELECT slate_jobs.run_recurring_horizon()'
);
```

## Observabilidade

Cada rodada grava uma linha em `slate_jobs.horizon_run`:

| Coluna | Conteúdo |
| --- | --- |
| `today`, `horizon_end` | Data usada e última competência materializada |
| `status` | `ok` ou `failed` |
| `inserted` | Entries novas (só em `ok`) |
| `error` | `SQLSTATE: mensagem` (só em `failed`) |
| `started_at`, `finished_at` | Duração da rodada |

Uma falha desfaz as entries daquela rodada, grava a linha `failed` e emite um `WARNING`
no log do Postgres (Logs → Postgres no painel). Ela não propaga: em `cron.job_run_details`
a execução aparece como `succeeded`, então a fonte de verdade é `horizon_run`.

Últimas rodadas:

```sql
SELECT * FROM slate_jobs.horizon_run ORDER BY id DESC LIMIT 12;
```

Alerta mínimo: o mês corrente precisa ter uma rodada `ok`. Se esta query voltar
`false`, o job falhou ou não rodou (pg_cron desligado, projeto pausado):

```sql
SELECT EXISTS (
  SELECT FROM slate_jobs.horizon_run
  WHERE status = 'ok'
    AND today >= date_trunc('month', now() AT TIME ZONE 'America/Sao_Paulo')
);
```

## Rodar à mão

É idempotente, então reexecutar depois de uma falha é seguro:

```sql
SELECT slate_jobs.run_recurring_horizon();              -- hoje em São Paulo
SELECT slate_jobs.run_recurring_horizon('2026-11-01');  -- data explícita
```

Uma rodada perdida não abre buraco visível: ainda restam 23 meses materializados à
frente, e a próxima rodada completa o que faltar.
