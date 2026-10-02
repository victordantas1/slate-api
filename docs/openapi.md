# Contrato OpenAPI

O slate-web gera seus tipos a partir de `openapi.json`, na raiz deste repo. O arquivo é
gerado das rotas do FastAPI, nunca escrito à mão.

## Gerar

```
uv run python -m app.openapi           # regrava openapi.json
uv run python -m app.openapi --check   # só compara; sai com 1 se divergir
```

O título é fixo (`slate-api`) e a versão vem de `version` no `pyproject.toml`, para que
o arquivo não mude com o `.env` de quem gerou.

## O que garante o contrato

`tests/test_openapi.py`, que roda no gate do `pytest`:

- `openapi.json` commitado é idêntico ao gerado do app. PR que muda rota ou schema sem
  regerar o arquivo fica vermelho, e a mudança de contrato aparece no diff do PR.
- Toda resposta 2xx, exceto 204, tem schema tipado. Rota sem `response_model` sairia
  como `{}` e viraria `unknown` no web.
- Campo cujo nome é uma coluna com `CHECK <coluna> IN (...)` nos modelos (`kind`,
  `status`, `direction`...) sai como `enum` no schema, com os mesmos valores do banco.
  Use `Literal[...]` ou `enum.Enum` nesses campos, nunca `str`.

## Publicação

- Versionado: `openapi.json` em `main` é sempre o contrato da API em produção.
- CI: o job `lint` roda `--check` e anexa `openapi.json` como artefato `openapi` em
  cada execução, inclusive nos PRs, para conferir o contrato antes do merge.

## Consumo no slate-web

O build do web baixa `openapi.json` de `main`, gera os tipos (por exemplo com
`openapi-typescript`) e roda o typecheck. Campo removido, renomeado ou com tipo
alterado quebra a compilação do código que o usa, em vez de falhar em runtime. Esse
passo mora no repositório `slate-web`.
