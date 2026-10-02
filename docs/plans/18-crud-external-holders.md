# Plano — #18 CRUD de titulares externos

Issue: https://github.com/victordantas1/slate-api/issues/18 · M3 · API · P1

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §5, D8, que
> não existe neste repositório. Implementado a partir do escopo e dos critérios de
> aceite da própria issue.

## 1. Objetivo

Um membro autenticado lista, cria, renomeia e remove os titulares externos da sua
household; nome repetido na household e remoção de titular com conta vinculada são
recusados com 409.

## 2. Arquivos

Criados:

- `app/api/external_holders.py`: schemas e router `/external-holders`
- `tests/test_api_external_holders.py`: testes de API contra Postgres real

Modificados:

- `app/main.py`: registro do router

## 3. Tarefas

1. **Testes (TDD).** Listagem filtrada pela household do token, criação, 409 em nome
   repetido (criação e renomeação), 404 para titular de outra household, remoção,
   409 ao remover titular com conta vinculada, 401 sem token, 422 em nome vazio.
2. **Router.** `GET /external-holders`, `GET/PATCH/DELETE /external-holders/{id}`,
   `POST /external-holders`, usando `get_current_member` e `get_member_session`.

## 4. Pressupostos

- A issue lista `GET/POST/PATCH`, mas o segundo critério de aceite só faz sentido com
  remoção, então entra `DELETE /external-holders/{id}` (204). Também entra
  `GET /external-holders/{id}`, por simetria e para o front.
- Toda consulta filtra por `household_id` do token, sem depender da RLS (#7). Titular
  de outra household responde 404, não 403, para não revelar que existe.
- Unicidade de nome é a da constraint do banco (`household_id, name`), sensível a
  maiúsculas. O nome é aparado (`strip`) e precisa ter de 1 a 120 caracteres.
- Nome repetido é detectado pela `IntegrityError` dentro de um savepoint, o que cobre
  corrida entre duas requisições.
- Conta vinculada é qualquer `account` com `external_holder_id` apontando para o
  titular, arquivada ou não: a FK é `RESTRICT` e não há delete lógico de titular.

## 5. Testes

`tests/test_api_external_holders.py`, com o app real, JWT assinado no teste e a
dependency `get_member_session` trocada pela sessão `db_session` (transação revertida):

- Nome único por household → `test_create_duplicate_name_returns_409`,
  `test_rename_to_existing_name_returns_409`, `test_same_name_in_other_household_is_allowed`
- Não remove com conta vinculada → `test_delete_with_linked_account_returns_409`
- Isolamento → `test_list_only_returns_own_household`, `test_other_household_holder_is_404`
