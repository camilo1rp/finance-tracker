from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, NormalizationMapping, Owner, Transaction
from app.schemas import ProposedMappingIn
from app.services.mapping_preview_service import preview_mappings


def _seed_account(db: Session, name: str = "Card") -> tuple[Owner, Account]:
    owner = Owner(name=f"Owner-{name}")
    db.add(owner)
    db.flush()
    account = Account(
        name=name,
        last4="0000",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
            "category_col": "Category",
        },
    )
    db.add(account)
    db.commit()
    db.refresh(owner)
    db.refresh(account)
    return owner, account


def _add_txn(db: Session, account_id: int, suffix: str, **kwargs) -> Transaction:
    values = {
        "account_id": account_id,
        "transaction_date": date(2024, 6, 1),
        "description": suffix,
        "amount": Decimal("10.00"),
        "transaction_type": "SPEND",
        "is_spend": True,
        "dedupe_hash": f"hash-{suffix}",
        "raw": {},
    }
    values.update(kwargs)
    txn = Transaction(**values)
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _add_mapping(db: Session, **kwargs) -> NormalizationMapping:
    row = NormalizationMapping(**kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_preview_is_pure(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    txn = _add_txn(
        db_session,
        account.id,
        "COFFEE",
        category_raw="Food & Drink",
        category_normalized=None,
        merchant_raw="Starbucks",
    )
    existing = _add_mapping(
        db_session,
        kind="merchant",
        raw_value="starbucks",
        canonical_value="Starbucks",
        account_id=None,
        merchant=None,
    )
    mapping_count = db_session.scalar(select(func.count()).select_from(NormalizationMapping))
    txn_count = db_session.scalar(select(func.count()).select_from(Transaction))
    snapshot = (
        txn.category_raw,
        txn.category_normalized,
        txn.merchant_raw,
        txn.merchant_normalized,
        txn.transaction_type,
        existing.canonical_value,
    )

    preview = preview_mappings(
        db_session,
        [
            ProposedMappingIn(
                kind="category",
                raw_value="Food & Drink",
                canonical_value="Dining",
            )
        ],
    )

    assert preview.scanned == 1
    assert preview.total_would_change == 1
    assert preview.rules[0].would_change == 1
    assert preview.rules[0].samples[0].current_effective == "Food & Drink"
    assert preview.rules[0].samples[0].new_effective == "Dining"

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted

    db_session.expire_all()
    txn = db_session.get(Transaction, txn.id)
    existing = db_session.get(NormalizationMapping, existing.id)
    assert db_session.scalar(select(func.count()).select_from(NormalizationMapping)) == mapping_count
    assert db_session.scalar(select(func.count()).select_from(Transaction)) == txn_count
    assert (
        txn.category_raw,
        txn.category_normalized,
        txn.merchant_raw,
        txn.merchant_normalized,
        txn.transaction_type,
        existing.canonical_value,
    ) == snapshot


def test_preview_gates(db_session: Session) -> None:
    owner, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "sign-derived",
        transaction_type="SPEND",
        raw_type=None,
        category_raw="Misc",
        owner_id=owner.id,
        owner_raw=None,
    )
    _add_txn(
        db_session,
        account.id,
        "has-raw-type",
        transaction_type="UNKNOWN",
        raw_type="Sale",
        category_raw="Misc",
        owner_id=owner.id,
        owner_raw=None,
    )
    _add_txn(
        db_session,
        account.id,
        "has-owner-raw",
        transaction_type="SPEND",
        raw_type=None,
        category_raw="Misc",
        owner_id=None,
        owner_raw="Pat",
    )
    _add_txn(
        db_session,
        account.id,
        "overridden",
        transaction_type="SPEND",
        raw_type=None,
        category_raw="Food & Drink",
        category_normalized=None,
        category_override="Keep Me",
        owner_id=owner.id,
        owner_raw=None,
    )

    preview = preview_mappings(
        db_session,
        [
            ProposedMappingIn(
                kind="transaction_type",
                raw_value="Sale",
                canonical_value="SPEND",
            ),
            ProposedMappingIn(
                kind="owner",
                raw_value="Pat",
                canonical_value=owner.name,
            ),
            ProposedMappingIn(
                kind="category",
                raw_value="Food & Drink",
                canonical_value="Dining",
            ),
        ],
    )

    by_kind = {impact.rule.kind: impact for impact in preview.rules}
    # Only the row with raw_type=Sale is eligible; it is already SPEND so no stored change.
    # Add a UNKNOWN+Sale case that would change — has-raw-type is UNKNOWN → SPEND.
    assert by_kind["transaction_type"].would_change == 1
    assert by_kind["transaction_type"].samples[0].description == "has-raw-type"
    assert by_kind["owner"].would_change == 1
    assert by_kind["owner"].samples[0].description == "has-owner-raw"
    assert by_kind["category"].would_change == 0
    assert by_kind["category"].suppressed_by_override == 1
    assert by_kind["category"].samples == []


def test_preview_shadowing_and_duplicates(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    other_owner = Owner(name="Other")
    db_session.add(other_owner)
    db_session.flush()
    other = Account(
        name="OtherCard",
        last4="2222",
        default_owner_id=other_owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
        },
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    _add_txn(
        db_session,
        account.id,
        "acct-row",
        category_raw="Shopping",
        category_normalized=None,
        merchant_raw="Amazon",
    )
    _add_txn(
        db_session,
        other.id,
        "other-row",
        category_raw="Shopping",
        category_normalized=None,
        merchant_raw="Amazon",
    )

    existing_account = _add_mapping(
        db_session,
        kind="category",
        raw_value="shopping",
        canonical_value="Account Shop",
        account_id=account.id,
        merchant=None,
    )
    existing_global = _add_mapping(
        db_session,
        kind="category",
        raw_value="groceries",
        canonical_value="Groceries",
        account_id=None,
        merchant=None,
    )

    shadowed = preview_mappings(
        db_session,
        [
            ProposedMappingIn(
                kind="category",
                raw_value="Shopping",
                canonical_value="Global Shop",
            )
        ],
    )
    impact = shadowed.rules[0]
    assert impact.duplicate_of_existing_id is None
    assert impact.shadowed_by_existing == 1
    assert impact.would_change == 1
    assert impact.samples[0].description == "other-row"
    assert impact.samples[0].new_effective == "Global Shop"

    duplicate = preview_mappings(
        db_session,
        [
            ProposedMappingIn(
                kind="category",
                raw_value="Groceries",
                canonical_value="Dup",
            )
        ],
    )
    dup = duplicate.rules[0]
    assert dup.duplicate_of_existing_id == existing_global.id
    assert dup.would_change == 0
    assert dup.suppressed_by_override == 0
    assert dup.shadowed_by_existing == 0
    assert dup.samples == []
    assert existing_account.id != existing_global.id


def test_preview_validation_excludes_invalid_and_continues(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "row",
        category_raw="Food",
        category_normalized=None,
    )
    preview = preview_mappings(
        db_session,
        [
            ProposedMappingIn(
                kind="merchant",
                raw_value="x",
                canonical_value="X",
                merchant="Nope",
            ),
            ProposedMappingIn(
                kind="category",
                raw_value="Food",
                canonical_value="Dining",
            ),
            ProposedMappingIn(
                kind="transaction_type",
                raw_value="Sale",
                canonical_value="NOT_A_TYPE",
            ),
        ],
    )
    assert preview.validation_errors == [
        "rule 1: merchant only valid for category",
        "rule 3: canonical_value must be a TransactionType",
    ]
    assert len(preview.rules) == 1
    assert preview.rules[0].would_change == 1
    assert preview.rules[0].rule.canonical_value == "Dining"
