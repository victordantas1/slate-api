# Plano — #4 Migration: account

Issue: https://github.com/victordantas1/slate-api/issues/4 · M1 · Fundação · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §5, D4, D8,
> que não existe neste repositório. Documento ausente não bloqueia: implementado a
> partir do escopo e dos critérios de aceite da própria issue.

> Dependência implícita: `account` referencia `household`, `member` e
> `external_holder`, criadas pela issue #3 (PR #29, ainda aberto). A issue não tem
> seção `## Depende de`, então ela está na fila. A implementação parte do código do
> PR #29: se ele já estiver mergeado, a branch sai de `main`; se não, a branch
> incorpora `chore/3-household-member-external-holder` com merge, e a migration
> nova usa `down_revision = "019fca530b88"`.

## 1. Objetivo

Tabela `account` com `kind`, `holder_kind`, as FKs de titular (`owner_member_id` /
`external_holder_id`), `closing_day`, `due_day`, `first_installment_offset`
(NOT NULL DEFAULT 1) e `archived`. Os dois CHECKs de exclusividade do titular valem
no banco, e a migration Alembic tem downgrade funcional.

## 2. Arquivos

Criados:

- `migrations/versions/<hash>_account.py`: migration gerada por autogenerate e
  revisada à mão (os CHECKs precisam aparecer no `create_table`)
- `tests/test_account.py`: testes de integração contra Postgres real, um por
  critério de aceite, mais o downgrade

Modificados:

- `app/db/models.py`: modelo ORM `Account`, ao lado de `Household`, `Member` e
  `ExternalHolder` (arquivo modelo: o próprio `app/db/models.py` da issue #3)

Total: 3 arquivos distintos.

## 3. Tarefas

1. **Testes primeiro (vermelho).** Criar `tests/test_account.py` seguindo o padrão
   de `tests/test_household_member_external_holder.py`: `skipif` sem
   `DATABASE_URL`, engine assíncrona, transação com rollback, `begin_nested()`
   envolvendo cada `pytest.raises(IntegrityError)`, fixture `alembic_config`. Rodar
   e confirmar que falham porque `Account` não existe.
2. **Modelo e migration (verde).** Adicionar `Account` em `app/db/models.py`,
   gerar `uv run alembic revision --autogenerate -m "account"`, conferir que o
   arquivo tem os CHECKs, as FKs com os `ondelete` certos e
   `down_revision = "019fca530b88"`, e que o downgrade dropa índices e tabela.
   Rodar `alembic upgrade head` e os quatro gates do `CLAUDE.md` até ficarem verdes.

### Desenho da tabela `account`

| Coluna | Tipo | Nulo | Default / FK |
| --- | --- | --- | --- |
| `id` | UUID | não | `gen_random_uuid()` |
| `household_id` | UUID | não | FK `household.id` ON DELETE CASCADE, indexada |
| `name` | String(120) | não | |
| `kind` | String(20) | não | CHECK `kind IN ('checking','credit_card','store_credit')` |
| `holder_kind` | String(10) | não | CHECK `holder_kind IN ('member','external')` |
| `owner_member_id` | UUID | sim | FK `member.id` ON DELETE RESTRICT, indexada |
| `external_holder_id` | UUID | sim | FK `external_holder.id` ON DELETE RESTRICT, indexada |
| `closing_day` | SmallInteger | sim | CHECK `closing_day BETWEEN 1 AND 31` |
| `due_day` | SmallInteger | sim | CHECK `due_day BETWEEN 1 AND 31` |
| `first_installment_offset` | SmallInteger | não | `server_default '1'` |
| `archived` | Boolean | não | `server_default false` |
| `created_at` | timestamptz | não | `now()` |

CHECKs nomeados (convenção `ck_%(table_name)s_%(constraint_name)s` de
`app/db/base.py`), declarados em `__table_args__`:

- `ck_account_kind`: `kind IN ('checking','credit_card','store_credit')`
- `ck_account_holder_kind`: `holder_kind IN ('member','external')`
- `ck_account_member_holder`: `(holder_kind = 'member') = (owner_member_id IS NOT NULL)`,
  critério 1, literal da issue
- `ck_account_external_holder`: `(holder_kind = 'external') = (external_holder_id IS NOT NULL)`,
  critério 2, literal da issue
- `ck_account_closing_day_range` e `ck_account_due_day_range`: `BETWEEN 1 AND 31`
  (NULL passa, porque CHECK com NULL não é violação)

## 4. Pressupostos

- **`kind` e `holder_kind` como `String` + CHECK, não `ENUM` do Postgres.** Um ENUM
  exige `ALTER TYPE` para ganhar valor e o autogenerate lida mal com isso. O CHECK
  dá a mesma garantia com o menor custo de migration futura.
- **`name` NOT NULL.** A issue não cita a coluna, mas uma conta sem nome não
  aparece em nenhuma UI. Mesma decisão de `household.name` e `member.name` na #3.
- **`household_id` NOT NULL com CASCADE e índice.** Toda tabela tem a coluna de
  tenant (issue #7, RLS). O CASCADE segue a #3.
- **`owner_member_id` e `external_holder_id` com ON DELETE RESTRICT.** Apagar um
  membro ou titular que tem contas precisa falhar: SET NULL quebraria os CHECKs e
  CASCADE apagaria contas com histórico de lançamentos.
- **Coerência de tenant entre a conta e o titular não é garantida no banco.** O
  banco não impede `owner_member_id` de outra household. Uma FK composta
  `(household_id, id)` exigiria UNIQUE extra em `member` e `external_holder`,
  fora do escopo. A RLS (#7) e o service do CRUD de contas (#16) cobrem isso.
- **`closing_day` e `due_day` nuláveis, sem exigência por `kind`.** Nenhum critério
  pede "cartão exige fechamento e vencimento". Só o range 1–31 é validado. A regra
  por `kind` fica para a validação da API (#16), se for necessária.
- **`first_installment_offset` como SmallInteger sem CHECK de range.** A issue só
  exige NOT NULL DEFAULT 1. Qualquer limite seria regra do motor (#9).
- **`archived` em vez de delete físico.** Segue o padrão da issue #5 para
  `category`. Nenhuma lógica de arquivamento entra aqui, só a coluna.
- **Sem `updated_at` e sem `relationship()` ORM.** Nenhum critério pede. Menor diff,
  como na #3.
- **Testes contra o Postgres real da sessão, não testcontainers.** A issue #15 ainda
  não foi feita. Mesmo `skipif` sem `DATABASE_URL` da #3.

## 5. Testes

Em `tests/test_account.py`, cada teste cria household, member e external_holder
dentro de uma transação que sofre rollback no final:

- `test_member_holder_requires_owner_member_id`: critério 1.
  - `holder_kind='member'` com `owner_member_id` preenchido: insere.
  - `holder_kind='member'` sem `owner_member_id`: `IntegrityError`.
  - `holder_kind='external'` com `owner_member_id` preenchido: `IntegrityError`.
- `test_external_holder_requires_external_holder_id`: critério 2, espelho do
  anterior com `external_holder_id`. Inclui o caso de conta `external` com
  `external_holder_id` e também `owner_member_id`, que deve dar `IntegrityError`.
- `test_first_installment_offset_defaults_to_one_and_is_not_null`: critério 3.
  - Insert sem a coluna: o valor lido é `1`.
  - Insert com `first_installment_offset=None` explícito: `IntegrityError`.
- `test_kind_and_holder_kind_reject_unknown_values`: `kind='savings'` e
  `holder_kind='other'` dão `IntegrityError`.
- `test_account_migration_downgrade_runs`: `alembic downgrade -1` a partir de
  `head` remove `account` e mantém `household`, `member` e `external_holder`.
  Depois, `upgrade head` restaura o estado.

## Contagem

2 tarefas, 3 arquivos distintos (1 migration gerada, 1 modelo modificado, 1 teste).
≤ 2 tarefas e ≤ 4 arquivos, então a execução é **inline, em TDD**.
