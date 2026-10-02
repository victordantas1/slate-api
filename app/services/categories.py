import uuid
from collections.abc import Sequence

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category

DEFAULT_CATEGORIES: tuple[str, ...] = (
    "Mercado",
    "Necessidades",
    "Saúde",
    "Beleza",
    "Assinaturas",
    "Contas",
    "Transporte",
    "Lazer",
    "Presentes",
    "Desenvolvimento",
    "Eletrônicos",
    "Roupa",
)

_UNIQUE_NAME = "uq_category_household_id_parent_id_name"
_UNIQUE_VIOLATION = "23505"


class CategoryNotFoundError(Exception):
    pass


class CategoryConflictError(Exception):
    pass


class CategoryRuleError(Exception):
    """Regra de hierarquia ou de arquivamento violada pelo pedido."""


async def seed_default_categories(session: AsyncSession, household_id: uuid.UUID) -> int:
    """Insere as categorias padrão se a household ainda não tem nenhuma.

    Roda uma vez por household: depois do primeiro seed (ou da primeira categoria
    criada à mão) é no-op, mesmo que uma padrão tenha sido renomeada ou arquivada.
    O `ON CONFLICT` cobre dois seeds concorrentes na mesma household.
    """
    has_any = await session.scalar(select(exists().where(Category.household_id == household_id)))
    if has_any:
        return 0
    result = await session.execute(
        insert(Category)
        .values(
            [
                {"household_id": household_id, "name": name, "direction": "expense"}
                for name in DEFAULT_CATEGORIES
            ]
        )
        .on_conflict_do_nothing(constraint=_UNIQUE_NAME)
        .returning(Category.id)
    )
    return len(result.all())


async def list_categories(
    session: AsyncSession, household_id: uuid.UUID, *, include_archived: bool
) -> Sequence[Category]:
    stmt = select(Category).where(Category.household_id == household_id)
    if not include_archived:
        stmt = stmt.where(Category.archived.is_(False))
    stmt = stmt.order_by(Category.parent_id.nulls_first(), Category.name)
    return (await session.scalars(stmt)).all()


async def get_category(
    session: AsyncSession, household_id: uuid.UUID, category_id: uuid.UUID
) -> Category:
    category = await session.scalar(
        select(Category).where(Category.id == category_id, Category.household_id == household_id)
    )
    if category is None:
        raise CategoryNotFoundError
    return category


async def _root_parent(
    session: AsyncSession, household_id: uuid.UUID, parent_id: uuid.UUID
) -> Category:
    try:
        parent = await get_category(session, household_id, parent_id)
    except CategoryNotFoundError as exc:
        raise CategoryRuleError("categoria pai não encontrada") from exc
    if parent.parent_id is not None:
        raise CategoryRuleError("terceiro nível não é permitido: o pai já é subcategoria")
    if parent.archived:
        raise CategoryRuleError("categoria pai está arquivada")
    return parent


async def _has_children(session: AsyncSession, category_id: uuid.UUID) -> bool:
    return bool(await session.scalar(select(exists().where(Category.parent_id == category_id))))


async def _flush(session: AsyncSession) -> None:
    # Savepoint: um nome duplicado não pode abortar a transação da requisição.
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
            raise CategoryConflictError("já existe categoria com esse nome neste nível") from exc
        # Triggers da migration (#5) são a última barreira da hierarquia.
        raise CategoryRuleError(str(exc.orig)) from exc


async def create_category(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    name: str,
    direction: str | None,
    parent_id: uuid.UUID | None,
) -> Category:
    if parent_id is not None:
        parent = await _root_parent(session, household_id, parent_id)
        if direction is not None and direction != parent.direction:
            raise CategoryRuleError("subcategoria precisa da mesma direction do pai")
        direction = parent.direction
    category = Category(
        household_id=household_id,
        parent_id=parent_id,
        name=name,
        direction=direction or "expense",
    )
    session.add(category)
    await _flush(session)
    await session.refresh(category)
    return category


async def update_category(
    session: AsyncSession,
    household_id: uuid.UUID,
    category_id: uuid.UUID,
    changes: dict[str, object],
) -> Category:
    """Aplica `changes`, que só traz as chaves enviadas: `name`, `parent_id`, `archived`."""
    category = await get_category(session, household_id, category_id)

    if "parent_id" in changes and changes["parent_id"] != category.parent_id:
        parent_id = changes["parent_id"]
        if parent_id is not None:
            assert isinstance(parent_id, uuid.UUID)
            if parent_id == category.id:
                raise CategoryRuleError("categoria não pode ser pai de si mesma")
            parent = await _root_parent(session, household_id, parent_id)
            if parent.direction != category.direction:
                raise CategoryRuleError("subcategoria precisa da mesma direction do pai")
            if await _has_children(session, category.id):
                raise CategoryRuleError("terceiro nível não é permitido: a categoria tem filhas")
        category.parent_id = parent_id

    if "name" in changes:
        assert isinstance(changes["name"], str)
        category.name = changes["name"]

    if "archived" in changes:
        if changes["archived"]:
            await _archive(session, category)
        else:
            category.archived = False

    await _flush(session)
    return category


async def _archive(session: AsyncSession, category: Category) -> None:
    category.archived = True
    # Subcategoria ativa sob raiz arquivada ficaria órfã na listagem.
    await session.execute(
        update(Category)
        .where(Category.parent_id == category.id, Category.archived.is_(False))
        .values(archived=True)
        .execution_options(synchronize_session=False)
    )


async def archive_category(
    session: AsyncSession, household_id: uuid.UUID, category_id: uuid.UUID
) -> None:
    category = await get_category(session, household_id, category_id)
    await _archive(session, category)
    await _flush(session)
