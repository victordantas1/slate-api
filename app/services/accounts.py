import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, ExternalHolder, Member


class AccountNotFoundError(Exception):
    pass


class InvalidAccountError(Exception):
    pass


async def list_accounts(
    session: AsyncSession, household_id: uuid.UUID, *, include_archived: bool
) -> Sequence[Account]:
    query = select(Account).where(Account.household_id == household_id)
    if not include_archived:
        query = query.where(Account.archived.is_(False))
    result = await session.scalars(query.order_by(Account.created_at, Account.id))
    return result.all()


async def get_account(
    session: AsyncSession, household_id: uuid.UUID, account_id: uuid.UUID
) -> Account:
    # Conta de outro household é indistinguível de conta inexistente.
    account = await session.scalar(
        select(Account).where(Account.id == account_id, Account.household_id == household_id)
    )
    if account is None:
        raise AccountNotFoundError(account_id)
    return account


async def _resolve_holder(
    session: AsyncSession,
    household_id: uuid.UUID,
    holder_kind: str,
    owner_member_id: uuid.UUID | None,
    external_holder_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Valida a coerência holder_kind × titular antes do banco, que só a garante por
    CHECK (erro 500 em vez de 422), e que o titular é do mesmo household."""
    if holder_kind == "member":
        if owner_member_id is None:
            raise InvalidAccountError("holder_kind='member' exige owner_member_id")
        if external_holder_id is not None:
            raise InvalidAccountError("holder_kind='member' não aceita external_holder_id")
        found = await session.scalar(
            select(Member.id).where(
                Member.id == owner_member_id, Member.household_id == household_id
            )
        )
        if found is None:
            raise InvalidAccountError("owner_member_id não pertence ao household")
    else:
        if external_holder_id is None:
            raise InvalidAccountError("holder_kind='external' exige external_holder_id")
        if owner_member_id is not None:
            raise InvalidAccountError("holder_kind='external' não aceita owner_member_id")
        found = await session.scalar(
            select(ExternalHolder.id).where(
                ExternalHolder.id == external_holder_id,
                ExternalHolder.household_id == household_id,
            )
        )
        if found is None:
            raise InvalidAccountError("external_holder_id não pertence ao household")
    return owner_member_id, external_holder_id


async def create_account(
    session: AsyncSession, household_id: uuid.UUID, data: Mapping[str, Any]
) -> Account:
    owner, external = await _resolve_holder(
        session,
        household_id,
        data["holder_kind"],
        data.get("owner_member_id"),
        data.get("external_holder_id"),
    )
    account = Account(
        household_id=household_id,
        name=data["name"],
        kind=data["kind"],
        holder_kind=data["holder_kind"],
        owner_member_id=owner,
        external_holder_id=external,
        closing_day=data.get("closing_day"),
        due_day=data.get("due_day"),
        first_installment_offset=data["first_installment_offset"],
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


_HOLDER_FIELDS = {"holder_kind", "owner_member_id", "external_holder_id"}


async def update_account(
    session: AsyncSession,
    household_id: uuid.UUID,
    account_id: uuid.UUID,
    changes: Mapping[str, Any],
) -> Account:
    """Aplica só os campos presentes em `changes`. Trocar `holder_kind` descarta o id
    do tipo anterior; o id do novo tipo precisa vir junto."""
    account = await get_account(session, household_id, account_id)

    if _HOLDER_FIELDS & changes.keys():
        holder_kind = changes.get("holder_kind", account.holder_kind)
        owner = changes.get(
            "owner_member_id", account.owner_member_id if holder_kind == "member" else None
        )
        external = changes.get(
            "external_holder_id",
            account.external_holder_id if holder_kind == "external" else None,
        )
        owner, external = await _resolve_holder(session, household_id, holder_kind, owner, external)
        account.holder_kind = holder_kind
        account.owner_member_id = owner
        account.external_holder_id = external

    for field, value in changes.items():
        if field not in _HOLDER_FIELDS:
            setattr(account, field, value)

    await session.flush()
    await session.refresh(account)
    return account
