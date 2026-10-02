"""RLS por household: a segunda barreira, caso a API erre o filtro de household.

Os dados são semeados como dono das tabelas (que ignora RLS) e as asserções rodam depois
de `apply_rls_claims`, o mesmo caminho da sessão autenticada da API: troca para a role
`slate_app` e setta as claims do JWT na transação.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.auth import CurrentMember
from app.core.config import get_settings
from app.db.base import Base
from app.db.models import (
    Account,
    Category,
    Commitment,
    Entry,
    ExternalHolder,
    Household,
    Member,
)
from app.db.session import APP_ROLE, apply_rls_claims

TENANT_TABLES = [
    "household",
    "member",
    "external_holder",
    "account",
    "category",
    "commitment",
    "entry",
]


@dataclass(frozen=True)
class Seed:
    """Uma linha em cada tabela de tenant, todas da mesma household."""

    household: uuid.UUID
    member: uuid.UUID
    external_holder: uuid.UUID
    account: uuid.UUID
    category: uuid.UUID
    commitment: uuid.UUID
    entry: uuid.UUID

    def id_of(self, table: str) -> uuid.UUID:
        value: uuid.UUID = getattr(self, table)
        return value


async def _returning_id(session: AsyncSession, model: type[Base], **values: Any) -> uuid.UUID:
    result = await session.execute(insert(model).values(**values).returning(model.id))  # type: ignore[attr-defined]
    value: uuid.UUID = result.scalar_one()
    return value


async def _seed(session: AsyncSession, name: str) -> Seed:
    household = await _returning_id(session, Household, name=name)
    member = await _returning_id(
        session, Member, household_id=household, supabase_user_id=uuid.uuid4(), name="Ana"
    )
    holder = await _returning_id(session, ExternalHolder, household_id=household, name="Vó")
    account = await _returning_id(
        session,
        Account,
        household_id=household,
        name="Nubank",
        kind="credit_card",
        holder_kind="member",
        owner_member_id=member,
    )
    category = await _returning_id(
        session, Category, household_id=household, name="Mercado", direction="expense"
    )
    commitment = await _returning_id(
        session, Commitment, **_commitment_row(household, account, category)
    )
    entry = await _returning_id(
        session, Entry, **_entry_row(household, commitment, category, seq=1)
    )
    return Seed(household, member, holder, account, category, commitment, entry)


def _commitment_row(
    household: uuid.UUID, account: uuid.UUID, category: uuid.UUID
) -> dict[str, Any]:
    return {
        "household_id": household,
        "account_id": account,
        "category_id": category,
        "kind": "installment",
        "description": "Geladeira",
        "purchase_date": date(2026, 8, 15),
        "total_amount": Decimal("1000.00"),
        "installment_count": 3,
    }


def _entry_row(
    household: uuid.UUID, commitment: uuid.UUID, category: uuid.UUID, seq: int
) -> dict[str, Any]:
    return {
        "household_id": household,
        "commitment_id": commitment,
        "category_id": category,
        "seq": seq,
        "competencia": date(2026, 8 + seq, 1),
        "amount": Decimal("333.33"),
    }


# Linha nova para cada tabela, toda ela coerente com a household de `seed` (FKs inclusive),
# para que a única coisa errada num INSERT forjado seja a household.
NEW_ROW: dict[str, tuple[type[Base], Callable[[Seed], dict[str, Any]]]] = {
    "household": (Household, lambda s: {"id": uuid.uuid4(), "name": "Forjada"}),
    "member": (
        Member,
        lambda s: {"household_id": s.household, "supabase_user_id": uuid.uuid4(), "name": "Bia"},
    ),
    "external_holder": (ExternalHolder, lambda s: {"household_id": s.household, "name": "Tio"}),
    "account": (
        Account,
        lambda s: {
            "household_id": s.household,
            "name": "Loja",
            "kind": "store_credit",
            "holder_kind": "external",
            "external_holder_id": s.external_holder,
        },
    ),
    "category": (
        Category,
        lambda s: {"household_id": s.household, "name": "Lazer", "direction": "expense"},
    ),
    "commitment": (Commitment, lambda s: _commitment_row(s.household, s.account, s.category)),
    "entry": (Entry, lambda s: _entry_row(s.household, s.commitment, s.category, seq=2)),
}


@dataclass(frozen=True)
class Households:
    a: Seed
    b: Seed


@pytest.fixture
async def households(db_session: AsyncSession) -> Households:
    return Households(a=await _seed(db_session, "Casa A"), b=await _seed(db_session, "Casa B"))


def _member_of(household: uuid.UUID) -> CurrentMember:
    member_id = uuid.uuid4()
    return CurrentMember(
        member_id=member_id,
        household_id=household,
        claims={
            "sub": str(uuid.uuid4()),
            "role": "authenticated",
            "household_id": str(household),
            "member_id": str(member_id),
        },
    )


async def _act_as(session: AsyncSession, household: uuid.UUID) -> None:
    await apply_rls_claims(session, _member_of(household))


async def _count_as_owner(session: AsyncSession, table: str, id: uuid.UUID) -> int:
    await session.execute(text("RESET ROLE"))
    count = await session.scalar(text(f"SELECT count(*) FROM {table} WHERE id = :id"), {"id": id})
    return int(count or 0)


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_member_reads_only_own_household(
    db_session: AsyncSession, households: Households, table: str
) -> None:
    await _act_as(db_session, households.a.household)

    rows = (await db_session.execute(text(f"SELECT id FROM {table}"))).scalars().all()
    other = await db_session.scalar(
        text(f"SELECT id FROM {table} WHERE id = :id"), {"id": households.b.id_of(table)}
    )

    assert rows == [households.a.id_of(table)]
    assert other is None


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_insert_with_forged_household_is_rejected(
    db_session: AsyncSession, households: Households, table: str
) -> None:
    model, new_row = NEW_ROW[table]
    await _act_as(db_session, households.a.household)

    with pytest.raises(DBAPIError, match="row-level security"):
        async with db_session.begin_nested():
            await db_session.execute(insert(model).values(**new_row(households.b)))

    if table != "household":
        # Controle: a mesma linha na household do token entra. A recusa acima é da policy.
        async with db_session.begin_nested():
            await db_session.execute(insert(model).values(**new_row(households.a)))


@pytest.mark.parametrize("table", [t for t in TENANT_TABLES if t != "household"])
async def test_update_cannot_move_row_to_other_household(
    db_session: AsyncSession, households: Households, table: str
) -> None:
    await _act_as(db_session, households.a.household)

    with pytest.raises(DBAPIError, match="row-level security"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(f"UPDATE {table} SET household_id = :other WHERE id = :id"),
                {"other": households.b.household, "id": households.a.id_of(table)},
            )


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_update_and_delete_do_not_touch_other_household(
    db_session: AsyncSession, households: Households, table: str
) -> None:
    target = households.b.id_of(table)
    await _act_as(db_session, households.a.household)

    updated = await db_session.execute(
        text(f"UPDATE {table} SET created_at = now() WHERE id = :id"), {"id": target}
    )
    deleted = await db_session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": target})

    assert updated.rowcount == 0  # type: ignore[attr-defined]
    assert deleted.rowcount == 0  # type: ignore[attr-defined]
    assert await _count_as_owner(db_session, table, target) == 1


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_without_claims_nothing_is_visible(
    db_session: AsyncSession, households: Households, table: str
) -> None:
    await db_session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))

    count = await db_session.scalar(text(f"SELECT count(*) FROM {table}"))

    assert count == 0


async def test_every_table_has_rls_enabled(db_session: AsyncSession) -> None:
    without_rls = (
        await db_session.scalars(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                "AND NOT c.relrowsecurity"
            )
        )
    ).all()

    assert without_rls == []


async def test_every_tenant_table_has_policies_for_each_command(
    db_session: AsyncSession,
) -> None:
    tables = (
        await db_session.scalars(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
    ).all()
    rows = (
        await db_session.execute(
            text(
                "SELECT tablename, cmd FROM pg_policies "
                "WHERE schemaname = 'public' AND :role = ANY(roles)"
            ),
            {"role": APP_ROLE},
        )
    ).all()
    commands: dict[str, set[str]] = {}
    for tablename, cmd in rows:
        commands.setdefault(tablename, set()).add(cmd)

    assert sorted(tables) == sorted(TENANT_TABLES)
    for table in tables:
        assert commands.get(table) == {"SELECT", "INSERT", "UPDATE", "DELETE"}, table


async def test_app_role_cannot_bypass_rls(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text("SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = :r"),
            {"r": APP_ROLE},
        )
    ).one()

    assert tuple(row) == (False, False, False)


async def _rls_state() -> tuple[set[str], bool]:
    url = get_settings().database_url
    assert url is not None
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.scalars(
                        text(
                            "SELECT c.relname FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' AND c.relrowsecurity"
                        )
                    )
                ).all()
            )
            role = await conn.scalar(
                text("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = :r)"), {"r": APP_ROLE}
            )
        return tables, bool(role)
    finally:
        await engine.dispose()


def test_rls_migration_downgrade_runs(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_rls_state()) == ({*TENANT_TABLES, "alembic_version"}, True)

    # Revisão anterior (commitment e entry), fixa para não depender de quem é head.
    command.downgrade(alembic_config, "9808f27666c4")
    assert asyncio.run(_rls_state()) == (set(), False)

    command.upgrade(alembic_config, "head")
    assert asyncio.run(_rls_state()) == ({*TENANT_TABLES, "alembic_version"}, True)
