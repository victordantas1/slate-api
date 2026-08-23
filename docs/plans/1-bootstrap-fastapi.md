# Plano — #1 Bootstrap do projeto FastAPI

Issue: https://github.com/victordantas1/slate-api/issues/1 · M1 · Fundação · P0

> Nota: a issue cita `docs/superpowers/specs/2026-08-22-slate-design.md` §4, que não
> existe neste repositório. Pela regra da skill, documento ausente não bloqueia:
> implementado a partir do escopo e dos critérios de aceite da própria issue.

## 1. Objetivo

Deixar o repositório executável: `uvicorn app.main:app` sobe, `/health` responde 200,
e os gates (`ruff`, `mypy`, `pytest`) passam limpos num checkout novo.

## 2. Arquivos

Criados:

- `pyproject.toml` — dependências, config de ruff/mypy/pytest
- `.gitignore`
- `.env.example` — variáveis documentadas, nenhum segredo real
- `CLAUDE.md` — comandos de gate, base e convenções (tarefa implícita da issue de bootstrap)
- `app/__init__.py`
- `app/main.py` — instância da app em nível de módulo (`app = FastAPI(...)`, exigido
  por `uvicorn app.main:app`) e registro de routers
- `app/core/__init__.py`
- `app/core/config.py` — `Settings` via pydantic-settings
- `app/api/__init__.py`
- `app/api/health.py` — router de `/health`
- `app/db/__init__.py` — pacote vazio, preenchido pela issue #2
- `app/domain/__init__.py` — pacote vazio
- `app/services/__init__.py` — pacote vazio
- `tests/__init__.py`
- `tests/conftest.py` — client de teste
- `tests/test_health.py`

Nenhum arquivo existente é modificado.

## 3. Tarefas

1. `pyproject.toml`, `.gitignore` e `.env.example`: dependências (fastapi, uvicorn,
   pydantic-settings) e dev (ruff, mypy, pytest, httpx), com a configuração das três
   ferramentas no mesmo arquivo.
2. `app/core/config.py`: `Settings(BaseSettings)` lendo `.env`, com `app_name`,
   `environment`, `database_url` e `supabase_jwt_secret` opcionais nesta fase;
   `get_settings()` com cache.
3. `app/api/health.py` + `app/main.py`: router `/health` retornando
   `{"status": "ok", "environment": ...}` e a app montando o router. Pacotes
   `app/db`, `app/domain`, `app/services` criados vazios para fixar a estrutura.
4. `tests/`: teste de `/health` (200 e corpo), escrito antes da implementação (TDD).
5. `CLAUDE.md`: comandos reais de gate, base `main`, convenções de commit e estrutura.

## 4. Pressupostos

Cada um é uma decisão do degrau 5 da escada (nada respondeu): menor diff, nenhuma
dependência extra, contrato público inalterado.

- **Python 3.12.** O runner tem 3.10–3.13; 3.12 é a mais nova com suporte pleno das
  libs do stack. `requires-python = ">=3.12"`.
- **Gerenciador `uv`.** Já presente no ambiente e citado pela skill como comando do
  repo. `uv.lock` é versionado.
- **`/health` sem checar banco.** A issue #2 é que traz a conexão; um healthcheck que
  toca o Postgres é escopo da issue #25 (keepalive). Aqui ele é liveness puro.
- **Settings com `database_url` e `supabase_jwt_secret` opcionais.** A app precisa subir
  antes da issue #2 existir. Quando a conexão entrar, o campo vira obrigatório lá.
- **Branch `claude/wonderful-fermi-cv4zih`** em vez de `feat/1-bootstrap-fastapi`: é a
  branch designada pelo ambiente desta execução, que tem precedência sobre a convenção
  de nome da skill.
- **Sem CI nesta issue.** `.github/workflows/` é escopo da issue #25 (deploy). A fase
  `ci` desta rodada é pulada por ausência de workflow, conforme a skill.

## 5. Testes

| Critério de aceite da issue | O que prova |
| --- | --- |
| `uvicorn app.main:app` sobe e `/health` responde 200 | `tests/test_health.py::test_health_returns_ok` — `TestClient(app)` faz GET `/health`, espera 200 e `status == "ok"`. O client importa `app.main:app`, então uma app que não sobe quebra o teste. Além disso, subida real via `uvicorn` verificada manualmente antes do PR. |
| `ruff check` e `mypy` passam limpos | `ruff check .`, `ruff format --check .` e `mypy app tests` rodados como gate antes do commit. |
| `pytest` roda | `pytest -q` verde com os testes acima. |
| Nenhum segredo no repositório; `.env.example` documentado | `.env.example` só com placeholders; `.gitignore` cobre `.env*` (com `.env.example` explicitamente rastreado); nenhum valor real no diff. |
