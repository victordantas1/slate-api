import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from app.core.config import get_settings
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]

# Mesma major do Supabase. Sobrescrevível para apontar para um mirror do Docker Hub.
POSTGRES_IMAGE = os.environ.get("TEST_POSTGRES_IMAGE", "postgres:17-alpine")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """URL asyncpg de um Postgres descartável, um container por sessão de testes.

    Sem Docker, os testes que dependem dele são pulados localmente. Em CI (variável
    `CI` definida) a ausência de Docker é falha, para a suíte nunca ficar verde à toa.
    """
    try:
        # Sem autovacuum: um ANALYZE automático de `account` no meio do
        # `test_rls_migration_downgrade_runs` trava contra o `ENABLE ROW LEVEL SECURITY`
        # da migration (ambos atualizam a linha da tabela em pg_class) e o Postgres
        # derruba a migration por deadlock. Os testes que precisam de estatística
        # rodam `ANALYZE` explícito.
        container = PostgresContainer(POSTGRES_IMAGE, driver="asyncpg").with_command(
            "postgres -c autovacuum=off"
        )
        container.start()
    except Exception as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres via testcontainers indisponível (Docker?): {exc}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture
def alembic_config(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    # migrations/env.py lê a URL de Settings, então ela entra pelo ambiente.
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="session")
def migrated_postgres_url(postgres_url: str) -> str:
    """O mesmo Postgres de `postgres_url`, com `alembic upgrade head` aplicado uma vez."""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", postgres_url)
        get_settings.cache_clear()
        command.upgrade(cfg, "head")
    get_settings.cache_clear()
    return postgres_url


@pytest.fixture
async def db_engine(migrated_postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_postgres_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Sessão dentro de uma transação revertida ao fim do teste: nada vaza entre testes.

    `join_transaction_mode="create_savepoint"` deixa o código sob teste chamar
    `commit()` sem efetivar a transação externa.
    """
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
