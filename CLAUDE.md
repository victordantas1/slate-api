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
uv run mypy app
uv run pytest -q
```

Os quatro precisam passar antes de qualquer commit. `--no-verify` nunca é aceitável.

## Rodar localmente

```
uv run uvicorn app.main:app --reload
```

`/health` é o endpoint de liveness.

## Estrutura

| Pacote | Papel |
| --- | --- |
| `app/api` | Routers HTTP |
| `app/core` | Settings e infraestrutura transversal |
| `app/db` | Sessão, modelos e migrations |
| `app/domain` | Regras puras, sem HTTP e sem banco |
| `app/services` | Orquestração entre domínio e banco |
| `tests/` | Testes |

`app/db`, `app/domain` e `app/services` estão vazios de propósito nesta fase —
serão preenchidos pelas issues seguintes.

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
