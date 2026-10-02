# Plano — #14 pg_cron: job mensal de extensão de horizonte

Issue: https://github.com/victordantas1/slate-api/issues/14 · M2 · Motor · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §4, §6.3, que
> não existe neste repositório. Implementado a partir do corpo da issue.

## 1. Objetivo

Uma migration habilita `pg_cron` e registra um job mensal que estende o horizonte de
todo commitment `recurring` `active`, de forma idempotente (invariante 3), com cada
rodada (e cada falha) registrada numa tabela.

## 2. Arquivos

- `migrations/versions/dcfbde1326d3_horizon_job.py` — criado: schema `slate_jobs`,
  tabela `horizon_run`, funções `extend_recurring_horizon(date)` e
  `run_recurring_horizon(date)`, agendamento condicional no pg_cron.
- `tests/test_horizon_job.py` — criado: paridade com o serviço Python, idempotência,
  rolagem do horizonte, falha registrada, permissões.
- `app/services/materialization.py` — tocado: docstring aponta para o espelho SQL.
- `docs/horizon-job.md` — criado: o que conferir no Supabase, monitoramento, execução
  manual.
- `docs/deploy.md` — tocado: link para o doc do job.

## 3. Tarefas

1. Migration com o horizonte em SQL, o log de rodadas e o agendamento condicional.
2. Testes contra Postgres puro (testcontainers) e verificação manual num Postgres 17
   com pg_cron pré-carregado.

## 4. Pressupostos

- O job roda SQL, não a API: pg_cron chama uma função plpgsql que espelha
  `extend_recurring_horizon`. Chamar a API via `pg_net` dependeria do Render acordado e
  de um segredo no banco. A paridade é garantida por teste, não por código único.
- Agendamento `0 3 1 * *` (dia 1, 00:00 em São Paulo), como a issue pede ("mensal").
  "Hoje" é a data em `America/Sao_Paulo`.
- Schema próprio `slate_jobs`, fora de `public`: o log não é dado de household, então a
  regra de RLS por household não se aplica, e fora de `public` ele não aparece na Data
  API do Supabase. A tabela tem RLS ligada sem policy e nenhum grant.
- Falha não propaga: a rodada é desfeita num subbloco, a linha `failed` fica gravada e
  sai um `WARNING` no log do Postgres. Propagar apagaria o próprio registro da falha.
- Sem pg_cron carregado no servidor, a migration não agenda e emite `WARNING`, para
  rodar em Postgres puro. No Supabase ele já vem pré-carregado.
- O downgrade remove o job e o schema, mas mantém a extensão.

## 5. Testes

- Paridade: num portfólio com offsets 0/1/2, virada de ano, `end_date`, `cancelled`,
  `settled` e `installment`, o job grava exatamente as entries do serviço Python, e
  depois do serviço não encontra nada a inserir.
- Invariante 3: rodar o job 4 vezes deixa as entries iguais à primeira; as seguintes
  inserem 0.
- Rolagem: avançar um mês insere só a competência nova, com o seq seguinte.
- Nunca sobrescreve entry editada.
- Sem data, usa hoje em São Paulo.
- Falha simulada: linha `failed` com a mensagem, nenhuma entry parcial, sem exceção.
- `slate_app` não executa as funções nem lê o log.
- Manual, Postgres 17 + pg_cron: a migration registra o job; downgrade remove;
  um job `* * * * *` temporário rodou o comando real e gravou 24 entries e a linha `ok`.
