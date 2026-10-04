# slate-api

API do Slate, um app de finanças domésticas: FastAPI + Postgres (Supabase), com um
motor que materializa compromissos (parcelamentos, recorrentes e avulsos) em entries
mensais. O front é o `slate-web`.

- Contrato: `openapi.json` (27 endpoints), regerado com `uv run python -m app.openapi`.
- Autenticação: JWT do Supabase, com os claims `member_id` e `household_id`.
- Deploy no Render e passos manuais: `docs/deploy.md`.
- Setup, gates e convenções: `CLAUDE.md`.
