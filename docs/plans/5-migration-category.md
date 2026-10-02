# Plano — #5 Migration: category com hierarquia de dois níveis

Issue: https://github.com/victordantas1/slate-api/issues/5

A spec citada (`docs/superpowers/specs/2026-08-22-slate-design.md` §5, §8) não existe
no repo; implementado a partir da issue.

## 1. Objetivo

Criar a tabela `category` com `parent_id` self-referencial, `direction`
(`'expense'|'income'`) e `archived`, onde o banco impede um terceiro nível, impede
delete físico e garante `UNIQUE (household_id, parent_id, name)`.

## 2. Arquivos

- `app/db/models.py`: modelo `Category`.
- `migrations/versions/<rev>_category.py`: tabela, índices, UNIQUE e os dois triggers.
- `tests/test_category.py`: um teste por critério de aceite, mais downgrade.
- `tests/test_account.py`: o teste de downgrade de account passa a mirar a revisão
  anterior por id, em vez de `-1`, que agora reverteria category.
- `docs/plans/5-migration-category.md`: este plano.

## 3. Tarefas

1. Escrever `tests/test_category.py` (vermelho: tabela não existe).
2. Modelo `Category`, migration autogerada a partir dele, triggers escritos à mão
   na mesma revisão, `down_revision = "7bedea4f2832"` (account). Gates verdes.

### Desenho da tabela `category`

| Coluna | Tipo | Nulo | Default / FK |
| --- | --- | --- | --- |
| `id` | UUID | não | `gen_random_uuid()` |
| `household_id` | UUID | não | FK `household.id` ON DELETE CASCADE, indexada |
| `parent_id` | UUID | sim | FK `category.id` ON DELETE RESTRICT, indexada |
| `name` | String(120) | não | |
| `direction` | String(10) | não | CHECK `direction IN ('expense','income')` |
| `archived` | Boolean | não | `server_default false` |
| `created_at` | timestamptz | não | `now()` |

- `ck_category_direction`: `direction IN ('expense', 'income')`.
- `ck_category_not_own_parent`: `parent_id <> id`.
- `uq_category_household_id_parent_id_name`: `UNIQUE NULLS NOT DISTINCT
  (household_id, parent_id, name)`.
- Trigger `category_enforce_hierarchy` (BEFORE INSERT OR UPDATE OF `parent_id`,
  `household_id`, `direction`): com `parent_id` preenchido, o pai precisa ser raiz
  (`parent_id IS NULL`), da mesma household e da mesma direction. Uma categoria que
  já tem filhas não pode ganhar pai. Uma raiz com filhas não muda de household nem
  de direction.
- Trigger `category_prevent_delete` (BEFORE DELETE): rejeita o `DELETE` direto.
  Deixa passar só o delete que vem do `ON DELETE CASCADE` de `household`,
  reconhecido por a household dona já não existir.

## 4. Pressupostos

- **Terceiro nível barrado por trigger, não CHECK.** Um CHECK não enxerga outra
  linha. A issue aceita "CHECK ou trigger".
- **Os dois lados do terceiro nível são barrados**: inserir neta e dar pai a uma
  categoria que já tem filhas. Só o primeiro deixaria o segundo caminho aberto.
- **`UNIQUE NULLS NOT DISTINCT`.** Com o UNIQUE padrão, `parent_id` NULL faz duas
  raízes com o mesmo nome passarem. O Supabase roda Postgres 15+, que suporta a
  cláusula.
- **Filha herda household e direction do pai, garantido no trigger.** A issue não
  pede, mas uma subcategoria de outra household vaza tenant (RLS, #7) e uma
  subcategoria de receita sob despesa não faz sentido para relatório. Custa duas
  comparações no trigger que já existe.
- **Delete bloqueado por trigger, com exceção do cascade de household.** Sem a
  exceção, apagar uma household com categorias falharia. `REVOKE DELETE` dependeria
  dos papéis do Supabase, fora do escopo.
- **`TRUNCATE` e `session_replication_role = replica` não são barrados.** Trigger de
  linha não dispara neles. São operações administrativas, fora do caminho da API.
- **`parent_id` com ON DELETE RESTRICT.** Como não há delete físico, o RESTRICT só
  documenta a intenção.
- **`direction` como `String` + CHECK, não ENUM.** Mesma decisão de `account.kind`
  na #4.
- **Sem `updated_at`, sem `relationship()` ORM, sem seed de categorias.** Nenhum
  critério pede.

## 5. Testes

Em `tests/test_category.py`, contra o Postgres do testcontainers, cada caso numa
transação revertida:

- `test_third_level_is_rejected`: critério 1. Raiz e filha inserem; neta dá erro, por INSERT e por
  UPDATE de `parent_id`;
  dar pai a uma categoria que tem filhas dá erro; ser pai de si mesma dá erro.
- `test_child_must_match_parent_household_and_direction`: pressuposto acima.
- `test_physical_delete_is_rejected_but_archive_works`: critério 2. `DELETE` dá
  erro; `UPDATE archived = true` passa.
- `test_household_delete_cascades_to_categories`: a exceção do trigger de delete.
- `test_name_is_unique_per_household_and_parent`: critério 3. Duas raízes com o
  mesmo nome na mesma household dão erro; o mesmo nome sob pais diferentes e em
  outra household passa.
- `test_direction_rejects_unknown_values`.
- `test_category_migration_downgrade_runs`: `downgrade -1` remove `category` e as
  funções dos triggers; `upgrade head` restaura.

## Contagem

2 tarefas, 4 arquivos de código (modelo, migration, dois testes) mais este plano.
≤ 2 tarefas e ≤ 4 arquivos de código, então a execução é
**inline, em TDD**.
