"""Job do horizonte (pg_cron): a função SQL que ele chama e o log de rodadas.

O Postgres dos testes não tem pg_cron, então o agendamento em si não roda aqui; o que
roda é exatamente o comando que o pg_cron executa, `slate_jobs.run_recurring_horizon()`.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Row, insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Commitment
from app.db.session import APP_ROLE
from app.services.materialization import extend_recurring_horizon
from tests.factories import HouseholdCtx, make_household

TODAY = date(2026, 10, 2)


@pytest.fixture
async def ctx(db_session: AsyncSession) -> HouseholdCtx:
    return await make_household(db_session)


async def _commitment(session: AsyncSession, ctx: HouseholdCtx, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "household_id": ctx.household_id,
        "account_id": ctx.account_id,
        "category_id": ctx.category_id,
        "kind": "recurring",
        "description": "Streaming",
        "purchase_date": date(2026, 10, 10),
        "recurring_amount": Decimal("39.90"),
    }
    values.update(overrides)
    return (
        await session.execute(insert(Commitment).values(**values).returning(Commitment.id))
    ).scalar_one()


async def _account(session: AsyncSession, ctx: HouseholdCtx, offset: int) -> uuid.UUID:
    return (
        await session.execute(
            insert(Account)
            .values(
                household_id=ctx.household_id,
                name=f"Conta offset {offset}",
                kind="checking",
                holder_kind="member",
                owner_member_id=await session.scalar(
                    text("SELECT owner_member_id FROM account WHERE id = :id"),
                    {"id": ctx.account_id},
                ),
                first_installment_offset=offset,
            )
            .returning(Account.id)
        )
    ).scalar_one()


async def _entries(session: AsyncSession) -> list[tuple[Any, ...]]:
    rows = await session.execute(
        text(
            "SELECT household_id, commitment_id, category_id, seq, competencia, amount, "
            "status, source, edited_manually, paid_at, idempotency_key "
            "FROM entry ORDER BY commitment_id, seq"
        )
    )
    return [tuple(r) for r in rows]


async def _run_job(session: AsyncSession, today: date | None = TODAY) -> Row[Any]:
    run_id = await session.scalar(
        text("SELECT slate_jobs.run_recurring_horizon(:today)"), {"today": today}
    )
    return (
        await session.execute(
            text(
                "SELECT today, horizon_end, status, inserted, error "
                "FROM slate_jobs.horizon_run WHERE id = :id"
            ),
            {"id": run_id},
        )
    ).one()


async def _mixed_portfolio(session: AsyncSession, ctx: HouseholdCtx) -> None:
    """Recorrentes que exercitam cada regra, e o que o job precisa ignorar."""
    offset_0 = await _account(session, ctx, 0)
    offset_2 = await _account(session, ctx, 2)
    await _commitment(session, ctx)  # offset 1, sem fim
    await _commitment(session, ctx, account_id=offset_0, purchase_date=date(2025, 12, 31))
    await _commitment(session, ctx, account_id=offset_2, purchase_date=date(2024, 11, 5))
    await _commitment(session, ctx, end_date=date(2027, 3, 9))
    await _commitment(session, ctx, account_id=offset_2, end_date=date(2027, 11, 30))
    # Termina antes de começar a ser cobrado no horizonte: zero entries.
    await _commitment(session, ctx, purchase_date=date(2026, 10, 1), end_date=date(2026, 10, 1))
    await _commitment(session, ctx, status="cancelled")
    await _commitment(session, ctx, status="settled")
    await _commitment(
        session,
        ctx,
        kind="installment",
        recurring_amount=None,
        total_amount=Decimal("300.00"),
        installment_count=3,
    )


async def test_sql_job_writes_the_same_entries_as_the_python_service(
    db_session: AsyncSession, ctx: HouseholdCtx
) -> None:
    await _mixed_portfolio(db_session, ctx)

    async with db_session.begin_nested() as python_run:
        python_inserted = await extend_recurring_horizon(db_session, today=TODAY)
        python_entries = await _entries(db_session)
        await python_run.rollback()

    run = await _run_job(db_session)

    assert python_inserted > 0
    assert run.status == "ok"
    assert run.inserted == python_inserted
    assert await _entries(db_session) == python_entries


async def test_sql_job_finds_nothing_left_after_python_service(
    db_session: AsyncSession, ctx: HouseholdCtx
) -> None:
    await _mixed_portfolio(db_session, ctx)
    await extend_recurring_horizon(db_session, today=TODAY)

    run = await _run_job(db_session)

    assert (run.status, run.inserted) == ("ok", 0)


async def test_invariant_3_job_is_idempotent(db_session: AsyncSession, ctx: HouseholdCtx) -> None:
    await _mixed_portfolio(db_session, ctx)

    first = await _run_job(db_session)
    once = await _entries(db_session)
    again = [await _run_job(db_session) for _ in range(3)]

    assert first.inserted > 0
    assert [r.inserted for r in again] == [0, 0, 0]
    assert await _entries(db_session) == once


async def test_job_rolls_horizon_forward_one_month(
    db_session: AsyncSession, ctx: HouseholdCtx
) -> None:
    commitment_id = await _commitment(db_session, ctx)

    await _run_job(db_session, date(2026, 10, 2))
    run = await _run_job(db_session, date(2026, 11, 1))

    assert (run.today, run.horizon_end, run.inserted) == (
        date(2026, 11, 1),
        date(2028, 11, 1),
        1,
    )
    last = (
        await db_session.execute(
            text(
                "SELECT seq, competencia FROM entry WHERE commitment_id = :id "
                "ORDER BY seq DESC LIMIT 1"
            ),
            {"id": commitment_id},
        )
    ).one()
    assert tuple(last) == (25, date(2028, 11, 1))


async def test_job_never_overwrites_existing_entries(
    db_session: AsyncSession, ctx: HouseholdCtx
) -> None:
    commitment_id = await _commitment(db_session, ctx)
    await _run_job(db_session)
    await db_session.execute(
        text(
            "UPDATE entry SET amount = 1, status = 'pago', paid_at = now(), "
            "edited_manually = true WHERE commitment_id = :id AND seq = 1"
        ),
        {"id": commitment_id},
    )
    edited = await _entries(db_session)

    await _run_job(db_session)

    assert await _entries(db_session) == edited


async def test_job_without_date_uses_today_in_sao_paulo(db_session: AsyncSession) -> None:
    expected = await db_session.scalar(
        text("SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date")
    )

    run = await _run_job(db_session, None)

    assert run.today == expected


async def test_failure_is_logged_and_leaves_no_partial_entries(
    db_session: AsyncSession, ctx: HouseholdCtx
) -> None:
    await _mixed_portfolio(db_session, ctx)
    # Trigger só desta transação: a terceira entry inserida derruba a rodada.
    await db_session.execute(
        text(
            """
            CREATE FUNCTION pg_temp.boom() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF (SELECT count(*) FROM public.entry) >= 3 THEN
                    RAISE EXCEPTION 'falha simulada';
                END IF;
                RETURN NEW;
            END $$
            """
        )
    )
    await db_session.execute(
        text(
            "CREATE TRIGGER boom BEFORE INSERT ON entry "
            "FOR EACH ROW EXECUTE FUNCTION pg_temp.boom()"
        )
    )

    run = await _run_job(db_session)

    assert run.status == "failed"
    assert run.inserted is None
    assert "falha simulada" in run.error
    assert await _entries(db_session) == []


async def test_app_role_cannot_run_or_read_the_job(db_session: AsyncSession) -> None:
    await db_session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))

    for statement in (
        "SELECT slate_jobs.run_recurring_horizon()",
        "SELECT slate_jobs.extend_recurring_horizon(current_date)",
        "SELECT count(*) FROM slate_jobs.horizon_run",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with db_session.begin_nested():
                await db_session.execute(text(statement))


async def test_without_pg_cron_migration_skips_scheduling(db_session: AsyncSession) -> None:
    has_cron = await db_session.scalar(
        text("SELECT EXISTS (SELECT FROM pg_extension WHERE extname = 'pg_cron')")
    )

    assert has_cron is False
