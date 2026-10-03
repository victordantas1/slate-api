# Plano — #20 PATCH /entries/{id} com scope e POST /entries/{id}/pay

Issue: https://github.com/victordantas1/slate-api/issues/20 · M3 · API · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §6.2, §7, que
> não existe neste repositório. Implementado a partir da issue, sobre o motor de
> cascata da #12 (`app/services/cascade.py`), sem alterá-lo.

## 1. Objetivo

Editar `amount` e/ou `category_id` de uma entry com escopo explícito
(`this`/`forward`/`all`) e marcar uma entry como paga.

## 2. Arquivos

- `app/services/entries.py` (novo): regras do endpoint em volta de `edit_entries`, e o pagamento
- `app/api/entries.py` (novo): router `/entries`
- `app/main.py`: registra o router
- `tests/api_support.py` (novo), `tests/test_entries_api.py` (novo)
- `openapi.json`: regerado

## 3. Tarefas

1. Testes HTTP dos critérios de aceite (TDD).
2. Service e router; `openapi.json`.

## 4. Pressupostos

- `scope` é query param obrigatório e sem padrão: ausente ou fora do enum → 422.
- Entry `pago` → 409 nos três escopos. O motor só recusa em `this`; em `forward`/`all`
  ele pularia a âncora e editaria as seguintes, o que seria surpresa para quem clicou
  numa entry paga.
- A resposta do PATCH é `{affected, entry}`: quantas entries mudaram e a entry âncora
  já atualizada.
- Categoria de outra household, inexistente ou arquivada → 422 (mesma regra do POST de
  commitments).
- `POST /entries/{id}/pay` aceita corpo opcional `{paid_at}` (com fuso); sem ele usa
  `now()` do banco. Entry já paga → 409. Responde a entry.
- Pagar a última entry não muda o `status` do commitment para `settled`: isso é do
  motor de cancelamento/quitação (#13).

## 5. Testes

- PATCH sem `scope` → 422 e nada muda; `scope` inválido → 422.
- PATCH em entry paga → 409 em `this`, `forward` e `all`, sem mexer nas outras.
- `affected` = 1 em `this`, N em `forward` e `all` (pagas fora da conta).
- Override de `category_id` em `this` muda só a entry; commitment e irmãs mantêm a categoria.
- Categoria de outra household ou arquivada → 422; corpo vazio/negativo/3 casas → 422.
- `/pay` grava `status=pago` e `paid_at`; aceita `paid_at` explícito; duas vezes → 409.
- Outra household → 404; sem token → 401.
