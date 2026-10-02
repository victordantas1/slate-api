# Plano — #26 Publicar OpenAPI para consumo do slate-web

Issue: https://github.com/victordantas1/slate-api/issues/26 · M4 · Deploy · P1

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §7, que não
> existe neste repositório. Implementado a partir do escopo e dos critérios de aceite
> da própria issue.

## 1. Objetivo

`openapi.json` versionado na raiz, gerado das rotas do app, conferido em CI e anexado
como artefato, com garantias de que as respostas são tipadas e os domínios fechados
saem como `enum`.

## 2. Arquivos

Criados:

- `app/openapi.py`: `render_openapi()` determinístico e CLI (`--check`, `--output`)
- `openapi.json`: o contrato gerado
- `tests/test_openapi.py`: arquivo em dia, respostas 2xx tipadas, enums do banco
- `docs/openapi.md`: como gerar, o que é garantido, como o web consome

Modificados:

- `app/api/health.py`: `HealthResponse.status` vira `Literal["ok", "degraded"]`
- `.github/workflows/ci.yml`: `--check` e upload do artefato `openapi`
- `CLAUDE.md`: seção do contrato

## 3. Decisões

- O arquivo é commitado, não só gerado em CI: mudança de contrato aparece no diff do
  PR e o web tem uma URL estável em `main`. O custo é regerar em PRs paralelos que
  mexem em rotas; conflito no arquivo se resolve regerando.
- A checagem de enum lê os `CHECK <coluna> IN (...)` dos modelos em vez de uma lista
  manual, para acompanhar colunas novas. Como `kind` e `status` existem em mais de
  uma tabela com domínios diferentes, o valor precisa bater com um dos domínios
  daquele nome. `HealthResponse` fica de fora porque seu `status` não é coluna.
- A quebra do build do web depende do slate-web gerar tipos e rodar typecheck; está
  documentada aqui e é trabalho daquele repositório.
