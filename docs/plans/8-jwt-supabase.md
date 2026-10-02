# Plano — #8 Validação de JWT do Supabase e dependency de sessão

Issue: https://github.com/victordantas1/slate-api/issues/8 · M1 · Fundação · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §4, que não
> existe neste repositório. Implementado a partir do escopo e dos critérios de aceite
> da própria issue.

> Dependência: a issue depende de "RLS por household_id" (#7), ainda aberta. A parte
> de autenticação não depende dela. A sessão de banco seta as claims no formato que
> o Supabase usa (`request.jwt.claims`, lido por `auth.jwt()`), então as policies da
> #7 podem ler `household_id` e `member_id` dali sem mudança neste código. Nenhuma
> migration é tocada.

## 1. Objetivo

Requisição sem token, com token adulterado, expirado ou malformado recebe 401;
`get_current_member` expõe `household_id` e `member_id` vindos só das claims; a
sessão de banco autenticada abre a transação com as claims setadas para a RLS.

## 2. Arquivos

Criados:

- `app/core/auth.py`: `CurrentMember`, `decode_access_token` e a dependency
  `get_current_member`
- `tests/test_auth.py`: testes unitários do decode e da dependency via app de teste
- `tests/test_member_session.py`: teste da sessão autenticada contra Postgres real

Modificados:

- `app/db/session.py`: `apply_rls_claims` e a dependency `get_member_session`
- `pyproject.toml`, `uv.lock`: dependência `pyjwt`
- `.env.example`: comentário de `SUPABASE_JWT_SECRET` atualizado (a chave já existe
  em `Settings` desde a #1)

## 3. Tarefas

1. **Auth (TDD).** Testes de 401 para token ausente, esquema errado, assinatura
   adulterada, payload adulterado, expirado, `alg: none`, audience errada e claims
   faltando; teste de que `household_id` do corpo é ignorado. Depois
   `app/core/auth.py` com PyJWT, HS256 fixo, `aud = authenticated`, `exp` obrigatório.
2. **Sessão com claims (TDD).** Teste contra Postgres real de que
   `current_setting('request.jwt.claims')` devolve as claims dentro da transação e
   volta vazio depois dela. Depois `get_member_session` em `app/db/session.py`.

## 4. Pressupostos

- `household_id` e `member_id` são claims de topo do access token, injetadas por um
  Custom Access Token Hook do Supabase. Não há fallback para `app_metadata`.
- Algoritmo HS256 com `SUPABASE_JWT_SECRET` (o segredo legado do projeto), único
  aceito. Chaves assimétricas/JWKS ficam fora deste escopo.
- `aud` precisa ser `authenticated`, o valor que o Supabase emite para usuários logados.
- Segredo não configurado é erro de servidor (500), não 401: falha fechada e visível.
- As claims entram com `set_config(..., is_local => true)`, só na transação. Com o
  pooler em transaction mode, um valor de sessão vazaria para outra requisição.
- `get_member_session` abre a transação e faz commit ao fim da requisição (rollback
  em exceção). É a unidade de trabalho de cada endpoint autenticado.

## 5. Testes

| Critério | Teste |
| --- | --- |
| Token inválido, expirado ou ausente → 401 | `tests/test_auth.py`: ausente, esquema `Basic`, lixo, expirado, `alg: none`, audience errada, claim faltando ou não-UUID |
| `household_id` só das claims | `tests/test_auth.py`: POST com `household_id` forjado no corpo devolve o da claim |
| Token adulterado | `tests/test_auth.py`: assinatura com outro segredo e payload reescrito mantendo a assinatura original |
| Claim setada para a RLS | `tests/test_member_session.py` contra Postgres |
