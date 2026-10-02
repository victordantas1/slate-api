import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Household(Base):
    __tablename__ = "household"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Member(Base):
    __tablename__ = "member"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("household.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    supabase_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class ExternalHolder(Base):
    __tablename__ = "external_holder"
    __table_args__ = (UniqueConstraint("household_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("household.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (
        CheckConstraint("kind IN ('checking', 'credit_card', 'store_credit')", name="kind"),
        CheckConstraint("holder_kind IN ('member', 'external')", name="holder_kind"),
        CheckConstraint(
            "(holder_kind = 'member') = (owner_member_id IS NOT NULL)", name="member_holder"
        ),
        CheckConstraint(
            "(holder_kind = 'external') = (external_holder_id IS NOT NULL)",
            name="external_holder",
        ),
        CheckConstraint("closing_day BETWEEN 1 AND 31", name="closing_day_range"),
        CheckConstraint("due_day BETWEEN 1 AND 31", name="due_day_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("household.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    holder_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    external_holder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_holder.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    closing_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    due_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    first_installment_offset: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("1"), nullable=False
    )
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Category(Base):
    """Categoria de dois níveis. O terceiro nível e o delete físico são barrados por
    triggers na migration (`category_enforce_hierarchy`, `category_prevent_delete`),
    que o autogenerate não enxerga."""

    __tablename__ = "category"
    __table_args__ = (
        CheckConstraint("direction IN ('expense', 'income')", name="direction"),
        CheckConstraint("parent_id <> id", name="not_own_parent"),
        UniqueConstraint(
            "household_id",
            "parent_id",
            "name",
            name="uq_category_household_id_parent_id_name",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("household.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("category.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
