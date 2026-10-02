# Plano — #16 CRUD de contas

Issue: https://github.com/victordantas1/slate-api/issues/16 · M3 · API · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §5, §7 e D8,
> que não existe neste repositório. Implementado a partir do escopo e dos critérios de
> aceite da própria issue e da tabela `account` (#4).

## 1. Objetivo

`GET/POST/PATCH /accounts` autenticados por JWT, restritos ao household do token,
com arquivamento no lugar de delete, coerência `holder_kind` × titular validada antes
do banco e `first_installment_offset` editável.

## 2. Arquivos

Criados:

- `app/services/accounts.py`: consultas e regras (escopo por household, coerência do
  titular, titular pertencente ao household)
- `app/api/accounts.py`: router `/accounts` e schemas Pydantic de entrada e saída
- `tests/test_accounts_api.py`: testes HTTP contra Postgres real (testcontainers)

Modificados:

- `app/main.py`: registra o router

## 3. Tarefas

1. **Service + router (TDD).** Testes HTTP primeiro: criação com titular member e
   external, rejeição de `external` sem `external_holder_id` (e simétricos), titular de
   outro household, listagem sem e com arquivadas, leitura e edição isoladas por
   household, `first_installment_offset` editável, arquivamento via PATCH, DELETE
   inexistente (405), 401 sem token. Depois `app/services/accounts.py` e
   `app/api/accounts.py`.
2. **Registro.** `include_router` em `app/main.py`; gates do `CLAUDE.md`.

## 4. Pressupostos

- Não há endpoint DELETE: a conta sai de uso com `PATCH {"archived": true}` e volta com
  `false`. Isso cobre "não permite delete de conta com entries" sem depender da tabela
  `entry`, que ainda não existe; `DELETE /accounts/{id}` responde 405.
- `GET /accounts` esconde as arquivadas por padrão; `?include_archived=true` as inclui.
- O household vem só do token (`CurrentMember.household_id`). Conta de outro household
  responde 404, para não revelar que existe. Como ainda não há RLS (#7), o filtro por
  `household_id` é feito explicitamente em toda consulta.
- Titular (`owner_member_id` / `external_holder_id`) precisa ser do mesmo household;
  caso contrário 422.
- No PATCH, trocar `holder_kind` descarta o id do tipo anterior; o id do novo tipo
  precisa vir no corpo. Mandar o id do tipo oposto é 422.
- `kind` não é editável: mudar o tipo de uma conta com lançamentos mudaria o sentido
  deles. Os demais campos são editáveis.
- `first_installment_offset` aceita inteiro `>= 0` (o mesmo limite de
  `app/domain/installments.py`), padrão 1. `closing_day` e `due_day` são opcionais,
  1 a 31, e aceitam `null` no PATCH para limpar.
- Nenhuma regra extra por `kind` (por exemplo, exigir `closing_day` em cartão): a issue
  não pede.

## 5. Testes

| Critério | Teste |
| --- | --- |
| Não permite delete, só `archived` | `test_delete_is_not_allowed_and_account_survives`, `test_archive_hides_from_default_list` |
| Rejeita `external` sem `external_holder_id` | `test_external_without_external_holder_is_rejected`, `test_patch_to_external_without_holder_is_rejected` e os simétricos de `member` |
| `first_installment_offset` editável | `test_first_installment_offset_is_editable`, `test_negative_offset_is_rejected` |
| Isolamento por household | `test_other_household_account_is_not_visible`, `test_holder_from_other_household_is_rejected` |
| Autenticação | `test_requires_token` |
