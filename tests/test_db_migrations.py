import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Column, Integer, Table, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = get_settings().database_url

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "requer DATABASE_URL configurada para um Postgres real; pulado até a suíte "
        "de testcontainers (issue #15) padronizar isso em todo ambiente"
    ),
)


async def _drop_probe_table() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text('DROP TABLE IF EXISTS "_test_autogenerate_probe"'))
    finally:
        await engine.dispose()


@pytest.fixture
def alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    assert DATABASE_URL is not None
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    return cfg


def test_alembic_upgrade_head_runs_against_real_postgres(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")


def test_alembic_autogenerate_detects_new_table(alembic_config: Config) -> None:
    probe = Table(
        "_test_autogenerate_probe",
        Base.metadata,
        Column("id", Integer, primary_key=True),
    )
    versions_dir = REPO_ROOT / "migrations" / "versions"
    before = set(versions_dir.glob("*.py"))
    try:
        asyncio.run(_drop_probe_table())
        command.upgrade(alembic_config, "head")
        command.revision(alembic_config, autogenerate=True, message="probe")

        new_files = set(versions_dir.glob("*.py")) - before
        assert len(new_files) == 1, f"esperava uma revisão nova, achei {new_files}"
        content = new_files.pop().read_text()
        assert "_test_autogenerate_probe" in content
        assert "create_table" in content
    finally:
        for f in set(versions_dir.glob("*.py")) - before:
            f.unlink(missing_ok=True)
        Base.metadata.remove(probe)
        asyncio.run(_drop_probe_table())
