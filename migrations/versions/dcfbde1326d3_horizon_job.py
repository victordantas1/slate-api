"""job do horizonte de recorrentes (pg_cron)

Revision ID: dcfbde1326d3
Revises: b3c1f0a7d2e4
Create Date: 2026-10-02 16:45:00.000000

O job roda no Postgres (pg_cron do Supabase), não na API: o Render free hiberna e não
serve para agendamento. Por isso o horizonte existe também em SQL, espelhando
`app.services.materialization.extend_recurring_horizon`; `tests/test_horizon_job.py`
prova que as duas versões gravam as mesmas entries.

Sem pg_cron no servidor (Postgres puro, testcontainers), a migration cria o schema, a
tabela de log e as funções, e só não agenda. Detalhes em `docs/horizon-job.md`.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dcfbde1326d3"
down_revision: str | Sequence[str] | None = "b3c1f0a7d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fora de `public`: a Data API do Supabase não expõe o schema, e nada aqui é dado de
# household, então a regra de RLS por household não se aplica.
SCHEMA = "slate_jobs"
JOB_NAME = "slate-recurring-horizon"
# Dia 1, 03:00 UTC (00:00 em São Paulo). O horizonte anda de mês em mês, e uma rodada
# perdida não deixa buraco: ainda restam 23 meses materializados à frente.
JOB_SCHEDULE = "0 3 1 * *"
JOB_COMMAND = f"SELECT {SCHEMA}.run_recurring_horizon()"

CREATE_RUN_TABLE = f"""
CREATE TABLE {SCHEMA}.horizon_run (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    today date NOT NULL,
    horizon_end date NOT NULL,
    status text NOT NULL CHECK (status IN ('ok', 'failed')),
    inserted integer CHECK ((status = 'ok') = (inserted IS NOT NULL)),
    error text CHECK ((status = 'failed') = (error IS NOT NULL)),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT clock_timestamp()
)
"""

# Espelho SQL de `extend_recurring_horizon`: mesmas regras de `app/domain/recurring.py`.
# Primeira competência = trunc_mes(purchase_date) + offset da conta; última =
# min(trunc_mes(today) + 24 meses, trunc_mes(end_date) + offset); o seq sai da
# competência. `search_path` vazio: tudo qualificado, nada resolve por acaso.
CREATE_EXTEND_FUNCTION = f"""
CREATE FUNCTION {SCHEMA}.extend_recurring_horizon(p_today date) RETURNS integer
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_until date := (date_trunc('month', p_today) + interval '24 months')::date;
    v_inserted integer;
BEGIN
    INSERT INTO public.entry (
        household_id, commitment_id, category_id, seq, competencia, amount,
        status, source, edited_manually
    )
    SELECT
        p.household_id,
        p.id,
        p.category_id,
        ((extract(year FROM m.competencia) - extract(year FROM p.first_month)) * 12
            + extract(month FROM m.competencia) - extract(month FROM p.first_month)
            + 1)::smallint,
        m.competencia,
        p.recurring_amount,
        'previsto',
        'manual',
        false
    FROM (
        SELECT
            c.id,
            c.household_id,
            c.category_id,
            c.recurring_amount,
            (date_trunc('month', c.purchase_date)
                + make_interval(months => a.first_installment_offset))::date AS first_month,
            LEAST(
                v_until,
                (date_trunc('month', c.end_date)
                    + make_interval(months => a.first_installment_offset))::date
            ) AS last_month
        FROM public.commitment c
        JOIN public.account a
            ON a.id = c.account_id AND a.household_id = c.household_id
        WHERE c.kind = 'recurring'
            AND c.status = 'active'
            AND c.recurring_amount IS NOT NULL
    ) p
    CROSS JOIN LATERAL (
        SELECT gs::date AS competencia
        FROM generate_series(p.first_month, p.last_month, interval '1 month') gs
    ) m
    ON CONFLICT (commitment_id, competencia) DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RETURN v_inserted;
END
$$
"""

# O que o pg_cron chama. Falha não propaga: o bloco EXCEPTION desfaz só as entries da
# rodada, e a linha `failed` em `horizon_run` fica gravada (com RAISE WARNING no log do
# Postgres). `today` padrão é a data de São Paulo, o fuso do app.
CREATE_RUN_FUNCTION = f"""
CREATE FUNCTION {SCHEMA}.run_recurring_horizon(p_today date DEFAULT NULL) RETURNS bigint
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_today date := coalesce(p_today, (now() AT TIME ZONE 'America/Sao_Paulo')::date);
    v_started timestamptz := clock_timestamp();
    v_inserted integer;
    v_error text;
    v_run bigint;
BEGIN
    BEGIN
        v_inserted := {SCHEMA}.extend_recurring_horizon(v_today);
    EXCEPTION WHEN OTHERS THEN
        v_error := SQLSTATE || ': ' || SQLERRM;
        RAISE WARNING '{SCHEMA}.run_recurring_horizon(%) falhou: %', v_today, v_error;
    END;

    INSERT INTO {SCHEMA}.horizon_run (today, horizon_end, status, inserted, error, started_at)
    VALUES (
        v_today,
        (date_trunc('month', v_today) + interval '24 months')::date,
        CASE WHEN v_error IS NULL THEN 'ok' ELSE 'failed' END,
        v_inserted,
        v_error,
        v_started
    )
    RETURNING id INTO v_run;
    RETURN v_run;
END
$$
"""

# pg_cron só entra se o servidor o carrega (shared_preload_libraries); senão
# CREATE EXTENSION falharia. No Supabase ele já vem pré-carregado. `cron.schedule` com
# nome é upsert: rodar de novo só reescreve o mesmo job.
SCHEDULE_JOB = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_extension WHERE extname = 'pg_cron')
        OR (
            EXISTS (SELECT FROM pg_available_extensions WHERE name = 'pg_cron')
            AND 'pg_cron' = ANY (
                string_to_array(
                    replace(current_setting('shared_preload_libraries'), ' ', ''), ','
                )
            )
        )
    THEN
        CREATE EXTENSION IF NOT EXISTS pg_cron;
        PERFORM cron.schedule('{JOB_NAME}', '{JOB_SCHEDULE}', '{JOB_COMMAND}');
    ELSE
        RAISE WARNING 'pg_cron indisponível: job {JOB_NAME} não agendado';
    END IF;
END
$$
"""

# IFs aninhados: sem pg_cron, `cron.job` não existe, e um AND na mesma expressão já
# falharia ao planejar.
UNSCHEDULE_JOB = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_extension WHERE extname = 'pg_cron') THEN
        IF EXISTS (SELECT FROM cron.job WHERE jobname = '{JOB_NAME}') THEN
            PERFORM cron.unschedule('{JOB_NAME}');
        END IF;
    END IF;
END
$$
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(CREATE_RUN_TABLE)
    # Sem policy: só o dono (quem roda migrations e o pg_cron) lê e grava.
    op.execute(f"ALTER TABLE {SCHEMA}.horizon_run ENABLE ROW LEVEL SECURITY")
    op.execute(CREATE_EXTEND_FUNCTION)
    op.execute(CREATE_RUN_FUNCTION)
    for signature in ("extend_recurring_horizon(date)", "run_recurring_horizon(date)"):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {SCHEMA}.{signature} FROM PUBLIC")
    op.execute(SCHEDULE_JOB)


def downgrade() -> None:
    """Downgrade schema."""
    # A extensão fica: outros jobs podem depender dela.
    op.execute(UNSCHEDULE_JOB)
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
