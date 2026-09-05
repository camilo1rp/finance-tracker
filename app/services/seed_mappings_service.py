"""
Idempotent normalization_mapping seeds for transaction types.

Global rules cover kind-agnostic tokens only. Kind-scoped rules are inserted
per account when created or backfilled as credit_card / depository.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.classification import AccountKind, NormalizationKind, clean_raw_value
from app.models import Account, NormalizationMapping

GLOBAL_TYPE_SEEDS: tuple[tuple[str, str], ...] = (
    ("sale", "SPEND"),
    ("purchase", "SPEND"),
    ("refund", "REFUND"),
    ("return", "REFUND"),
    ("fee", "FEE"),
    ("adjustment", "ADJUSTMENT"),
)

CREDIT_CARD_TYPE_SEEDS: tuple[tuple[str, str], ...] = (
    ("payment", "TRANSFER"),
    ("credit", "REFUND"),
    ("reversal", "REFUND"),
    ("debit", "SPEND"),
    ("interest", "FEE"),
    ("interest charge", "FEE"),
)

DEPOSITORY_TYPE_SEEDS: tuple[tuple[str, str], ...] = (
    ("ach_credit", "INCOME"),
    ("deposit", "INCOME"),
    ("misc_credit", "INCOME"),
    ("quickpay_credit", "INCOME"),
    ("acct_xfer", "TRANSFER"),
    ("chase_to_partnerfi", "TRANSFER"),
    ("fee_transaction", "FEE"),
    ("ach_debit", "SPEND"),
    ("misc_debit", "SPEND"),
)


def _identity_exists(
    db: Session,
    raw_value: str,
    account_id: int | None,
) -> bool:
    stmt = select(NormalizationMapping.id).where(
        NormalizationMapping.kind == NormalizationKind.TRANSACTION_TYPE.value,
        NormalizationMapping.raw_value == raw_value,
        NormalizationMapping.merchant.is_(None),
    )
    if account_id is None:
        stmt = stmt.where(NormalizationMapping.account_id.is_(None))
    else:
        stmt = stmt.where(NormalizationMapping.account_id == account_id)
    return db.execute(stmt).scalar_one_or_none() is not None


def _insert_if_missing(
    db: Session,
    raw_value: str,
    canonical_value: str,
    account_id: int | None,
) -> bool:
    cleaned = clean_raw_value(raw_value)
    if _identity_exists(db, cleaned, account_id):
        return False
    db.add(
        NormalizationMapping(
            kind=NormalizationKind.TRANSACTION_TYPE.value,
            raw_value=cleaned,
            canonical_value=canonical_value,
            account_id=account_id,
            merchant=None,
        )
    )
    return True


def seed_global_transaction_type_mappings(db: Session) -> int:
    inserted = 0
    for raw_value, canonical in GLOBAL_TYPE_SEEDS:
        if _insert_if_missing(db, raw_value, canonical, None):
            inserted += 1
    return inserted


def seed_account_kind_type_mappings(db: Session, account: Account) -> int:
    kind = AccountKind(account.account_kind)
    seeds = (
        CREDIT_CARD_TYPE_SEEDS
        if kind is AccountKind.CREDIT_CARD
        else DEPOSITORY_TYPE_SEEDS
    )
    inserted = 0
    for raw_value, canonical in seeds:
        if _insert_if_missing(db, raw_value, canonical, account.id):
            inserted += 1
    return inserted


def seed_transaction_type_mappings(db: Session) -> int:
    inserted = seed_global_transaction_type_mappings(db)
    for account in db.scalars(select(Account)).all():
        inserted += seed_account_kind_type_mappings(db, account)
    return inserted
