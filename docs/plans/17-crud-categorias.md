# Plano — #17 CRUD de categorias e seed inicial

Issue: https://github.com/victordantas1/slate-api/issues/17 · M3 · API · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §8, que não
> existe neste repositório. Implementado a partir do escopo e dos critérios de aceite
> da própria issue, sobre a tabela `category` da #5 e a autenticação da #8.

## 1. Objetivo

Endpoints autenticados para listar, criar, ler, editar e arquivar categorias da
household do token, com no máximo dois níveis, e um seed idempotente com as doze
categorias padrão.

## 2. Arquivos

Criados:

- `app/services/categories.py`: seed, consultas e regras de criação, edição e
  arquivamento, com erros de domínio próprios
- `app/api/categories.py`: router `/categories` e schemas Pydantic
- `tests/test_categories_api.py`: testes HTTP contra o Postgres do testcontainers

Modificados:

- `app/main.py`: registra o router

## 3. Tarefas

1. **Seed e CRUD (TDD).** Testes HTTP de seed idempotente, terceiro nível rejeitado,
   arquivamento que preserva a linha, isolamento entre households e 401 sem token.
   Depois o service e o router.

## 4. Pressupostos

- O seed é exposto como `POST /categories/seed` e como a função
  `seed_default_categories`, para o fluxo de criação de household chamar quando
  existir. Ele só insere se a household ainda não tem nenhuma categoria (roda uma
  vez por household), e o `ON CONFLICT DO NOTHING` cobre duas chamadas concorrentes.
- As doze categorias do seed são de nível raiz e `direction = 'expense'`.
- `DELETE /categories/{id}` arquiva (não há delete físico, a #5 barra no banco).
  `PATCH` com `archived: false` desarquiva.
- Arquivar uma categoria raiz arquiva também as subcategorias dela. Desarquivar não
  propaga.
- `direction` não muda depois de criada: entries futuras dependem dela. Subcategoria
  sem `direction` herda a do pai.
- Não se cria subcategoria sob categoria arquivada.
- A listagem é plana, com `parent_id`, e esconde arquivadas salvo
  `?include_archived=true`. O front monta a árvore.
- Todas as consultas filtram pelo `household_id` do token, sem depender da RLS
  (#7). Categoria de outra household responde 404.
- Nome duplicado no mesmo nível responde 409. Terceiro nível, pai inexistente e pai
  arquivado respondem 422.

## 5. Testes

- Seed idempotente: duas chamadas criam 12 e depois 0; a segunda household recebe
  as suas próprias 12; household que já tem categorias não recebe seed.
- Terceiro nível: criar filha de subcategoria e dar pai a uma categoria com filhas
  respondem 422.
- Arquivar: `DELETE` responde 204, a categoria some da listagem padrão, continua
  legível por id e na listagem com `include_archived`, e as filhas são arquivadas.
  Como `entry` ainda não existe, a garantia para entries é que a linha nunca é
  apagada e o id continua válido para a FK.
- CRUD: criar, ler, renomear, mover, nome duplicado (409), outra household (404),
  sem token (401).
