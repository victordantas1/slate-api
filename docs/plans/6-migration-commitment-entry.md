# Plano — #6 Migration: commitment e entry com constraints e índices

Issue: https://github.com/victordantas1/slate-api/issues/6 · M1 · Fundação · P0

A spec citada (`docs/superpowers/specs/2026-08-22-slate-design.md` §5, D6, D9) não
existe no repo; implementado a partir da issue e dos campos que as issues do motor
(#10–#14), dos endpoints (#19–#24) e do pg_cron (#14) leem dessas tabelas.

## 1. Objetivo

Criar `commitment` (installment, recurring e single numa tabela só) e `entry` (a linha
mensal que a tela de mês lê), com as UNIQUE, CHECK e índices dos critérios de aceite.

## 2. Arquivos

- `app/db/models.py`: modelos `Commitment` e `Entry`; UNIQUE `(id, household_id)` em
  `Account` e `Category` para servirem de alvo das FKs compostas.
- `migrations/versions/<rev>_commitment_entry.py`: as duas tabelas, as duas UNIQUE
  novas em account/category, índices.
- `tests/test_commitment_entry.py`: um teste por critério de aceite, mais coerência
  de household, cascade e downgrade.
- `docs/plans/6-migration-commitment-entry.md`: este plano.

## 3. Tarefas

1. Escrever `tests/test_commitment_entry.py` (vermelho: tabelas não existem).
2. Modelos, migration autogerada a partir deles, `down_revision = "9fd2e8412e8f"`
   (category). Gates verdes.

### `commitment`

| Coluna | Tipo | Nulo | Default / FK / CHECK |
| --- | --- | --- | --- |
| `id` | UUID | não | `gen_random_uuid()` |
| `household_id` | UUID | não | FK `household.id` ON DELETE CASCADE |
| `account_id` | UUID | **não** (D9) | FK composta `(account_id, household_id)` → `account(id, household_id)` |
| `category_id` | UUID | não | FK composta `(category_id, household_id)` → `category(id, household_id)` |
| `kind` | String(12) | não | `installment` \| `recurring` \| `single` |
| `description` | String(200) | não | |
| `purchase_date` | date | não | data da compra; início da recorrência |
| `total_amount` | Numeric(12,2) | sim | NULL ⟺ `recurring`; `> 0` |
| `installment_count` | SmallInteger | sim | NULL ⟺ `recurring`; `>= 1`; `single` ⟹ `= 1` |
| `recurring_amount` | Numeric(12,2) | sim | NOT NULL ⟺ `recurring`; `>= 0` |
| `end_date` | date | sim | só `recurring`; `>= purchase_date` |
| `status` | String(10) | não | `active` \| `cancelled` \| `settled`, default `active` |
| `created_at` | timestamptz | não | `now()` |

UNIQUE `(id, household_id)`, alvo da FK composta de `entry`.

### `entry`

| Coluna | Tipo | Nulo | Default / FK / CHECK |
| --- | --- | --- | --- |
| `id` | UUID | não | `gen_random_uuid()` |
| `household_id` | UUID | não | FK `household.id` ON DELETE CASCADE |
| `commitment_id` | UUID | não | FK composta `(commitment_id, household_id)` → `commitment(id, household_id)` ON DELETE CASCADE |
| `category_id` | UUID | **não** (D9) | FK composta `(category_id, household_id)` → `category(id, household_id)` |
| `seq` | SmallInteger | não | `>= 1` |
| `competencia` | date | não | `EXTRACT(DAY FROM competencia) = 1` |
| `amount` | Numeric(12,2) | não | `>= 0` |
| `status` | String(10) | não | `previsto` \| `confirmado` \| `pago`, default `previsto` |
| `paid_at` | timestamptz | sim | NOT NULL ⟺ `status = 'pago'` |
| `source` | String(10) | não | `manual` \| `fincoach`, default `manual` |
| `edited_manually` | Boolean | não | default `false` |
| `idempotency_key` | String(200) | sim | |
| `created_at` | timestamptz | não | `now()` |

- UNIQUE `(commitment_id, competencia)`, `(commitment_id, seq)`,
  `(household_id, idempotency_key)`.
- Índices `(household_id, competencia)`, `(commitment_id)`,
  `(household_id, category_id, competencia)`.

## 4. Pressupostos

- **Coerência de household por FK composta.** `entry → commitment`,
  `commitment → account`, `commitment → category` e `entry → category` apontam para
  `(id, household_id)`. Uma entry não consegue apontar para commitment, conta ou
  categoria de outra household, mesmo com bug na API. Exige UNIQUE `(id, household_id)`
  em `account` e `category`, adicionadas nesta migration.
- **FKs para account e category em NO ACTION**, não RESTRICT. Os dois bloqueiam o
  delete de conta/categoria em uso, mas NO ACTION checa no fim do statement, então o
  `DELETE household` em cascata passa. Com RESTRICT a ordem do cascade decidiria.
- **`entry.commitment_id` NOT NULL, cascade do commitment.** Toda entry nasce de um
  commitment (D6: `single` é parcelamento de 1x, e o ingest #24 usa o mesmo serviço).
  Preservar entries pagas é cancelamento (#13), não delete físico.
- **`recurring` com `total_amount`, `installment_count` NULL e `recurring_amount` NOT
  NULL.** O CHECK pedido (recurring ⟺ total_amount NULL) vem acompanhado dos dois
  vizinhos: sem valor mensal o horizonte (#11) não tem o que materializar.
- **`single` ⟹ `installment_count = 1`** (D6).
- **`end_date` só em recurring.** Parcelamento termina pelo número de parcelas.
- **Sem coluna de direção.** Entrada/saída vem de `category.direction`.
- **Sem `account_id` na entry.** A conta é do commitment; a tela de mês faz um join.
- **`paid_at` NOT NULL ⟺ `status = 'pago'`.** `/pay` (#20) grava os dois juntos.
- **`amount >= 0`, não `> 0`.** `split_installments` produz 0,00 quando há menos
  centavos que parcelas (plano #9).
- **Enums como `String` + CHECK**, mesma decisão de `account.kind` (#4).
- **Índice `(commitment_id)` mantido** embora a UNIQUE `(commitment_id, competencia)`
  já sirva de prefixo: a issue pede literalmente.
- **Sem `updated_at`, sem `relationship()` ORM.** Nenhum critério pede.

## 5. Testes

Em `tests/test_commitment_entry.py`, contra o Postgres do testcontainers, cada caso
numa transação revertida:

- `test_account_and_category_are_required`: critério 1.
- `test_competencia_and_seq_are_unique_per_commitment`: critérios 2 e 3.
- `test_idempotency_key_is_unique_per_household`: critério 4; NULL repetido passa,
  a mesma chave em outra household passa.
- `test_recurring_iff_total_amount_is_null`: critério 5, mais `single` com 1 parcela.
- `test_competencia_must_be_first_day_of_month`: critério 6.
- `test_status_and_paid_at_are_consistent`.
- `test_household_must_match_across_references`: FKs compostas.
- `test_household_delete_cascades`: o cascade passa com as FKs NO ACTION.
- `test_indexes_exist`: critério 7, pelo inspector.
- `test_commitment_entry_migration_downgrade_runs`.

## Contagem

2 tarefas, 4 arquivos (modelos, migration, teste, plano). ≤ 2 tarefas e ≤ 4 arquivos,
então a execução é **inline, em TDD**.
