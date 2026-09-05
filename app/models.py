"""
SQLAlchemy ORM models (Postgres).

Design note: separate from app/domain/transaction.py's CanonicalTransaction
dataclass -- the service layer is the only place that converts between them.
ImportMapping is stored as JSONB on Account (default_mapping). Owner is a
real table (not a free string) so it can be referenced consistently across
accounts and validated at creation time. NormalizationMapping is the single
generic lookup table backing type/category/owner classification.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    JSON,
    Numeric,
    String,
    Text,
    Date,
    DateTime,
    ForeignKey,
    Boolean,
    Float,
    Index,
    UniqueConstraint,
    case,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    account_kind: Mapped[str] = mapped_column(String, default="depository")
    default_mapping: Mapped[dict] = mapped_column(JSON)  # serialized ImportMapping

    default_owner: Mapped["Owner | None"] = relationship()
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class NormalizationMapping(Base):
    """
    Generic raw-value -> canonical-value lookup, shared by transaction_type,
    category, owner, and merchant classification. account_id NULL means a
    global rule; set means it overrides the global rule for that one account.
    merchant is used for kind=category and kind=transaction_type: NULL
    applies to every merchant; set applies only when the row's resolved
    merchant matches (cleaned). Type rules also accept a space-bounded
    prefix so ACH labels like "western union capture 623…" hit
    merchant=western union.
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
    raw_value: Mapped[str | None] = mapped_column(String, nullable=True)
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
    type_override: Mapped[str | None] = mapped_column(String, nullable=True)
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


class TransactionEvidence(Base):
    __tablename__ = "transaction_evidence"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            "kind",
            "external_ref",
            name="uq_transaction_evidence_identity",
        ),
        Index("ix_transaction_evidence_kind_match_kind", "kind", "match_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    external_ref: Mapped[str] = mapped_column(String)
    extraction: Mapped[dict] = mapped_column(JSON)
    match_kind: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    dominant_category: Mapped[str | None] = mapped_column(String, nullable=True)
    dominant_category_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    extractor_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class MerchantSender(Base):
    __tablename__ = "merchant_senders"
    __table_args__ = (
        UniqueConstraint(
            "merchant_key",
            "sender_pattern",
            name="uq_merchant_sender",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_key: Mapped[str] = mapped_column(String)
    sender_pattern: Mapped[str] = mapped_column(String)
    origin: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class TransactionOverride(Base):
    __tablename__ = "transaction_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), unique=True
    )
    category: Mapped[str] = mapped_column(String)
    evidence_ids: Mapped[list[int]] = mapped_column(JSON)
    plan_source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class ProposalStatus(str, Enum):
    OPEN = "open"
    CONSUMED = "consumed"
    DISCARDED = "discarded"


class EnrichmentProposal(Base):
    __tablename__ = "enrichment_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String)
    consumed_plan_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


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

effective_type = case(
    (Transaction.type_override.isnot(None), Transaction.type_override),
    else_=Transaction.transaction_type,
)
