"""
SQLAlchemy ORM models (Postgres).

Design note: separate from app/domain/transaction.py's CanonicalTransaction
dataclass -- the service layer is the only place that converts between them.
ImportMapping is stored as JSONB on Account (default_mapping). Owner is a
real table (not a free string) so it can be referenced consistently across
accounts and validated at creation time. NormalizationMapping is the single
generic lookup table backing type/category/owner classification.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Numeric,
    String,
    Date,
    DateTime,
    ForeignKey,
    Boolean,
    UniqueConstraint,
    case,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    last4: Mapped[str] = mapped_column(String)
    default_owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"), nullable=True)
    source_format: Mapped[str] = mapped_column(String)  # "csv" (extensible: "pdf", "api")
    default_mapping: Mapped[dict] = mapped_column(JSON)  # serialized ImportMapping

    default_owner: Mapped["Owner | None"] = relationship()
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class NormalizationMapping(Base):
    """
    Generic raw-value -> canonical-value lookup, shared by transaction_type,
    category, owner, and merchant classification. account_id NULL means a
    global rule; set means it overrides the global rule for that one account.
    merchant is only used for kind=category: NULL applies to every merchant,
    set applies only when the row's resolved merchant matches (cleaned).
    """
    __tablename__ = "normalization_mappings"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "raw_value",
            "account_id",
            "merchant",
            name="uq_normalization_rule",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    raw_value: Mapped[str] = mapped_column(String)
    canonical_value: Mapped[str] = mapped_column(String)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    merchant: Mapped[str | None] = mapped_column(String, nullable=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    filename: Mapped[str] = mapped_column(String)
    imported_at: Mapped[datetime] = mapped_column(DateTime)
    row_count: Mapped[int] = mapped_column()


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("dedupe_hash", name="uq_transaction_dedupe_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"), nullable=True)
    owner_raw: Mapped[str | None] = mapped_column(String, nullable=True)

    transaction_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    transaction_type: Mapped[str] = mapped_column(String)  # TransactionType enum value
    is_spend: Mapped[bool] = mapped_column(Boolean)
    raw_type: Mapped[str | None] = mapped_column(String, nullable=True)

    category_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    category_normalized: Mapped[str | None] = mapped_column(String, nullable=True)
    category_override: Mapped[str | None] = mapped_column(String, nullable=True)

    merchant_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    merchant_normalized: Mapped[str | None] = mapped_column(String, nullable=True)
    merchant_override: Mapped[str | None] = mapped_column(String, nullable=True)

    dedupe_hash: Mapped[str] = mapped_column(String, index=True)
    raw: Mapped[dict] = mapped_column(JSON)  # original row, untouched

    account: Mapped["Account"] = relationship(back_populates="transactions")
    owner: Mapped["Owner | None"] = relationship()


effective_category = case(
    (Transaction.category_override.isnot(None), Transaction.category_override),
    (Transaction.category_normalized.isnot(None), Transaction.category_normalized),
    else_=Transaction.category_raw,
)

effective_merchant = case(
    (Transaction.merchant_override.isnot(None), Transaction.merchant_override),
    (Transaction.merchant_normalized.isnot(None), Transaction.merchant_normalized),
    else_=Transaction.merchant_raw,
)
