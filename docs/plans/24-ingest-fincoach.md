# Plano — #24 POST /ingest/entries (porta do FinCoach)

Issue: https://github.com/victordantas1/slate-api/issues/24 · M3 · API · P1

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §7, D1, que
> não existe neste repositório. Implementado a partir do escopo e dos critérios de
> aceite da própria issue.

## 1. Objetivo

Receber lançamentos do FinCoach em lote, com `idempotency_key` obrigatória e
`source='fincoach'`, sem duplicar no reenvio e sem caminho de escrita paralelo ao da
API manual.

## 2. Arquivos

Criados:

- `app/api/ingest.py`: router `POST /ingest/entries`
- `app/services/ingest.py`: orquestra o lote item a item
- `tests/test_ingest_api.py`

Modificados:

- `app/services/materialization.py`: `create_installment_commitment` aceita
  `entry_status`, `source` e `idempotency_key` (padrões iguais ao comportamento atual)
- `app/services/commitments.py`: `create_commitment`, o serviço de escrita único
  (validação de conta e categoria + materialização)
- `app/services/entries.py`: `confirm_entry`, que passa uma entry `previsto` a
  `confirmado` com a chave de idempotência
- `app/api/commitments.py`: `POST /commitments` passa a chamar `create_commitment`
- `app/main.py`: registra o router
- `openapi.json`: regerado

## 3. Contrato

`POST /ingest/entries` com `{"entries": [...]}` (1 a 500 itens). Cada item:

| Campo | Regra |
| --- | --- |
| `idempotency_key` | obrigatória, 1–200 caracteres; ausente → 422 |
| `match_entry_id` | opcional: casa com essa entry em vez de criar |
| `account_id`, `category_id`, `description`, `purchase_date`, `amount` | obrigatórios quando não há `match_entry_id` |

Para cada item, na ordem:

1. Já existe entry da household com essa chave → devolve ela (`existing`), sem escrever.
2. Com `match_entry_id` → a entry precisa ser da household (senão 404) e estar
   `previsto` (senão 409); passa a `confirmado` com a chave (`matched`).
3. Sem `match_entry_id` → cria um commitment `single` pelo mesmo
   `create_commitment` do `POST /commitments`, com a entry em `confirmado`,
   `source='fincoach'` e a chave (`created`).

Resposta 200 com `{"results": [{idempotency_key, outcome, entry}]}` na ordem do lote.
O lote é uma transação: um item inválido devolve o erro com o índice e nada é gravado.
Reenviar é seguro, porque o que já entrou volta como `existing`.

## 4. Pressupostos

- Casamento explícito por `match_entry_id`, não por heurística de valor e data: o
  critério pede "conseguir casar", e um casamento automático errado confirmaria a
  entry de outro lançamento sem ninguém ver.
- Entry criada pelo ingest nasce `confirmado`: é um lançamento que aconteceu, e é o
  mesmo estado do caminho de casamento.
- Ingest cria só `single` (uma entry): a chave é única por entry, então um
  parcelamento de N entries não teria onde guardá-la.
- `source` não é campo de entrada: a rota é a porta do FinCoach, então toda entry
  criada por ela grava `source='fincoach'`. Um campo de valor único também viraria
  `const` no OpenAPI, fora do enum do banco que `tests/test_openapi.py` exige.
- Entry casada mantém o `source` original (`manual`); a chave registra que o FinCoach
  a confirmou.
- Corrida entre duas requisições com a mesma chave: a segunda pega a violação da
  UNIQUE dentro do savepoint e devolve a entry da primeira como `existing`.
- Nenhuma tabela nova, nenhuma migration: a UNIQUE `(household_id, idempotency_key)`
  e as colunas `source`/`idempotency_key` já existem desde a #6.

## 5. Testes

- Sem `idempotency_key` → 422 → `test_missing_idempotency_key_is_422`
- Reenvio não duplica → `test_resending_batch_returns_existing_without_duplicating`,
  `test_duplicate_key_inside_batch_creates_once`, `test_keys_are_scoped_per_household`,
  `test_concurrent_requests_with_same_key_create_once`
- Casa e confirma → `test_match_confirms_existing_entry`,
  `test_match_paid_entry_is_409_and_rolls_back_batch`,
  `test_match_entry_of_other_household_is_404`
- Mesmo serviço de escrita → `test_ingest_and_manual_api_share_write_service`,
  `test_ingest_rejects_archived_category_like_manual_api`
