# Plano — #15 Suite de testes de integração com testcontainers

Issue: https://github.com/victordantas1/slate-api/issues/15 · M2 · Motor · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §11, §6.4, que
> não existe neste repositório. Implementado a partir do corpo da issue e das issues
> #10, #11 e #12, que descrevem as operações a que cada invariante se refere.

## 1. Objetivo

Fixtures de household/conta/categoria, factory de commitments e as invariantes do motor
como testes de primeira classe contra Postgres real.

## 2. Arquivos

- `tests/factories.py` — criado: `HouseholdCtx`, `make_household`, `make_commitment`,
  `entries_of`.
- `tests/conftest.py` — tocado: fixture `household`.
- `tests/test_materialization.py` — tocado: passa a usar a factory no lugar do `_ctx`
  local.
- `tests/test_invariants.py` — criado: invariantes 1 e 2.

## 3. Tarefas

1. Extrair a factory que `test_materialization.py` já tinha para `tests/factories.py`.
2. Invariante 1 verificada em SQL sobre o que foi gravado, com valores adversariais,
   bordas de calendário, o teto de `SmallInteger` e uma bateria aleatória de semente fixa.
3. Invariante 2 como snapshot de todas as colunas das entries pagas antes e depois de
   cada operação do motor, numa lista que #11 e #12 estendem.

## 4. Pressupostos

- O que a #15 já tinha em main fica como está: CI (`.github/workflows/ci.yml`), fixtures
  de testcontainers (`tests/conftest.py`) e RLS com duas households (`tests/test_rls.py`,
  da #7).
- Invariante 2 hoje cobre as operações que existem (materialização de parcelamento e
  single, inclusive a que falha). A cascata de edição (#12) é a operação que ela mais
  protege e entra na lista `ENGINE_OPERATIONS` com a #12.
- Invariante 3 (idempotência do horizonte) não tem operação para testar até o horizonte
  de recorrentes (#11) existir; a #11 a traz como critério de aceite próprio. Por isso o
  PR usa `Refs #15`, não `Closes`.
- Bateria aleatória com `random.Random(15)` em vez de hypothesis: hypothesis não combina
  com fixtures async por teste (`db_session`), e a semente fixa mantém falhas
  reproduzíveis.

## 5. Testes

- Invariante 1: `installment_sum_violations` vazio para 0,01 em 1/2/12x, dízimas, contagens
  primas, 360x, o teto de `Numeric(12,2)`, 32767x, viradas de ano, bissexto e 150 casos
  aleatórios; controle que mostra a consulta pegando um centavo a mais e uma parcela
  faltando; total com 3 casas e acima do teto recusados sem gravar nada.
- Invariante 2: snapshot das pagas idêntico depois de cada operação do motor; controle que
  mostra o snapshot mudando quando uma paga é alterada.
- Conferido por mutação: com o resíduo removido de `split_installments`, 21 testes da
  invariante 1 falham.
