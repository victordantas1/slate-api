import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
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
        # Alvo das FKs compostas de commitment: conta só de commitment da mesma household.
        UniqueConstraint("id", "household_id", name="uq_account_id_household_id"),
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
        # Alvo das FKs compostas de commitment e entry.
        UniqueConstraint("id", "household_id", name="uq_category_id_household_id"),
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


class Commitment(Base):
    """Parcelamento, recorrente ou avulso numa tabela só (D6): `single` é parcelamento de
    1x. Saldo devedor e mês de quitação são derivados das entries, nunca colunas (D10).

    As FKs para account e category são compostas com `household_id`, então um commitment
    não aponta para conta ou categoria de outra household. Ficam em NO ACTION, não
    RESTRICT, para o `DELETE household` em cascata não depender da ordem do cascade."""

    __tablename__ = "commitment"
    __table_args__ = (
        CheckConstraint("kind IN ('installment', 'recurring', 'single')", name="kind"),
        CheckConstraint("status IN ('active', 'cancelled', 'settled')", name="status"),
        CheckConstraint(
            "(kind = 'recurring') = (total_amount IS NULL) "
            "AND (kind = 'recurring') = (installment_count IS NULL) "
            "AND (kind = 'recurring') = (recurring_amount IS NOT NULL)",
            name="recurring_amounts",
        ),
        CheckConstraint(
            "kind <> 'single' OR installment_count = 1", name="single_is_one_installment"
        ),
        CheckConstraint("total_amount > 0", name="total_amount_positive"),
        CheckConstraint("installment_count >= 1", name="installment_count_positive"),
        CheckConstraint("recurring_amount >= 0", name="recurring_amount_not_negative"),
        CheckConstraint("end_date IS NULL OR kind = 'recurring'", name="end_date_only_recurring"),
        CheckConstraint("end_date >= purchase_date", name="end_date_after_start"),
        ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name="fk_commitment_account_id_account",
        ),
        ForeignKeyConstraint(
            ["category_id", "household_id"],
            ["category.id", "category.household_id"],
            name="fk_commitment_category_id_category",
        ),
        # Alvo da FK composta de entry.
        UniqueConstraint("id", "household_id", name="uq_commitment_id_household_id"),
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
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    installment_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    recurring_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), server_default=text("'active'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Entry(Base):
    """Uma linha mensal de um commitment: a única tabela que a tela de mês lê.

    `category_id` é copiada do commitment na materialização e pode ser sobrescrita só na
    entry. Todas as FKs, menos a de household, são compostas com `household_id`."""

    __tablename__ = "entry"
    __table_args__ = (
        CheckConstraint("status IN ('previsto', 'confirmado', 'pago')", name="status"),
        CheckConstraint("source IN ('manual', 'fincoach')", name="source"),
        CheckConstraint("EXTRACT(DAY FROM competencia) = 1", name="competencia_first_day"),
        CheckConstraint("(status = 'pago') = (paid_at IS NOT NULL)", name="paid_at_iff_pago"),
        CheckConstraint("seq >= 1", name="seq_positive"),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
        ForeignKeyConstraint(
            ["commitment_id", "household_id"],
            ["commitment.id", "commitment.household_id"],
            name="fk_entry_commitment_id_commitment",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["category_id", "household_id"],
            ["category.id", "category.household_id"],
            name="fk_entry_category_id_category",
        ),
        UniqueConstraint("commitment_id", "competencia", name="uq_entry_commitment_id_competencia"),
        UniqueConstraint("commitment_id", "seq", name="uq_entry_commitment_id_seq"),
        UniqueConstraint(
            "household_id", "idempotency_key", name="uq_entry_household_id_idempotency_key"
        ),
        Index("ix_entry_household_id_competencia", "household_id", "competencia"),
        Index("ix_entry_commitment_id", "commitment_id"),
        Index(
            "ix_entry_household_id_category_id_competencia",
            "household_id",
            "category_id",
            "competencia",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id", ondelete="CASCADE"), nullable=False
    )
    commitment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    competencia: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), server_default=text("'previsto'"), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(10), server_default=text("'manual'"), nullable=False)
    edited_manually: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
