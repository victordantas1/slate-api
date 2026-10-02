# CLAUDE.md

## Visão do projeto

`slate-api` é a API do Slate, um app de finanças domésticas: FastAPI + Postgres
(Supabase), com um motor de materialização de compromissos (parcelamentos,
recorrentes e avulsos) em entries mensais. O front é o repositório `slate-web`,
separado.

## Setup

```
uv sync
```

Nada mais é necessário num checkout novo.

## Comandos de gate

```
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv run pytest -q
```

Os quatro precisam passar antes de qualquer commit. `--no-verify` nunca é aceitável.
O CI (`.github/workflows/ci.yml`) roda os mesmos quatro em todo PR e push em `main`.

## Testes contra Postgres

Testes que precisam de banco usam as fixtures de `tests/conftest.py` (`db_session`,
`db_engine`, `migrated_postgres_url`, `alembic_config`), que sobem um Postgres 17
descartável via testcontainers, um por sessão de `pytest`. Requer Docker. Sem Docker
esses testes são pulados localmente; com `CI` definida, falham. `TEST_POSTGRES_IMAGE`
troca a imagem (por exemplo, um mirror do Docker Hub).

## Rodar localmente

```
uv run uvicorn app.main:app --reload
```

`/health` é o endpoint de liveness.

## Contrato OpenAPI

`openapi.json` (raiz do repo) é o contrato que o slate-web usa para gerar tipos. É
gerado das rotas do app e versionado junto com o código:

```
uv run python -m app.openapi
```

Toda mudança em rota ou schema regrava o arquivo no mesmo commit; o teste
`tests/test_openapi.py` e o CI falham se ele estiver desatualizado. Em conflito de
merge no arquivo, não resolva à mão: regere. Detalhes em `docs/openapi.md`.

## Migrations

```
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "<mensagem>"
```

A URL de conexão vem de `DATABASE_URL` no `.env`, nunca de `alembic.ini`.

Toda tabela nova em `public` entra com RLS na mesma migration: `GRANT` para a role
`slate_app`, `ENABLE ROW LEVEL SECURITY` e uma policy por comando (SELECT, INSERT,
UPDATE, DELETE) comparando `household_id` com `(SELECT current_household_id())`. O modelo
é `migrations/versions/b3c1f0a7d2e4_rls_household.py`. A sessão autenticada da API
(`get_member_session`) roda como `slate_app`; `tests/test_rls.py` falha se uma tabela
ficar sem RLS ou sem as quatro policies.

## Estrutura

| Pacote | Papel |
| --- | --- |
| `app/api` | Routers HTTP |
| `app/core` | Settings e infraestrutura transversal |
| `app/db` | Sessão, modelos e migrations |
| `app/domain` | Regras puras, sem HTTP e sem banco |
| `app/services` | Orquestração entre domínio e banco |
| `tests/` | Testes |
| `migrations/` | Histórico de migrações Alembic |

`app/db` contém a infraestrutura de conexão (engine, `Base`), e o histórico de
migrações fica em `migrations/` (raiz do repo). `app/domain` e `app/services` estão
vazios de propósito nesta fase — serão preenchidos pelas issues seguintes.

## Convenções

- Base de todo PR: `main`.
- Commits em português, no formato Conventional Commits (`feat:`, `fix:`, `chore:`,
  `docs:`, `test:`).
- `git add` caminho a caminho, nunca `-A`.
- Segredos ficam em `.env` (git-ignored). `.env.example` documenta as chaves e só
  contém placeholders.
- Configuração nova entra em `app/core/config.py` como campo de `Settings`, e a chave
  correspondente entra em `.env.example` no mesmo commit.

## Onde as decisões moram

Os requisitos são as issues do GitHub. Planos de implementação ficam em
`docs/plans/<numero>-<slug>.md`, um por issue.
