import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Column, Integer, Table, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _drop_probe_table(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text('DROP TABLE IF EXISTS "_test_autogenerate_probe"'))
    finally:
        await engine.dispose()


def test_alembic_upgrade_head_runs_against_real_postgres(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")


def test_alembic_autogenerate_detects_new_table(alembic_config: Config, postgres_url: str) -> None:
    probe = Table(
        "_test_autogenerate_probe",
        Base.metadata,
        Column("id", Integer, primary_key=True),
    )
    versions_dir = REPO_ROOT / "migrations" / "versions"
    before = set(versions_dir.glob("*.py"))
    try:
        asyncio.run(_drop_probe_table(postgres_url))
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
        asyncio.run(_drop_probe_table(postgres_url))
