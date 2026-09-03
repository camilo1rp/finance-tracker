from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, MerchantSender, Owner, Transaction, TransactionOverride, effective_category
from app.schemas import (
    CreateMappingOp,
    MappingPlanIn,
    RemoveTransactionOverrideOp,
    SetTransactionCategoryOp,
)
from app.services.analytics_service import summarize
from app.services.mapping_preview_service import MappingPlanValidationError, apply_mapping_plan


def _seed_account(db: Session) -> Account:
    owner = Owner(name="Pat")
    db.add(owner)
    db.flush()
    account = Account(
        name="Card",
        last4="1111",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={"date_col": "Date", "description_col": "Description", "amount_col": "Amount"},
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _txn(db: Session, account_id: int, suffix: str, **kwargs) -> Transaction:
    row = Transaction(
        account_id=account_id,
        transaction_date=date(2024, 6, 1),
        description=suffix,
        amount=Decimal("10.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        category_normalized="Shopping",
        merchant_raw="Amazon",
        merchant_normalized="Amazon",
        dedupe_hash=f"hash-{suffix}",
        raw={},
        **kwargs,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_set_override_create_duplicate_remove_and_replace(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "base", category_override=None)
    plan = MappingPlanIn(
        ops=[SetTransactionCategoryOp(transaction_id=txn.id, category="Dining", evidence_ids=[1])],
        account_id=account.id,
    )
    first = apply_mapping_plan(db_session, plan)
    assert first.overrides_set == 1
    db_session.expire_all()
    assert db_session.get(Transaction, txn.id).category_override == "Dining"
    provenance = db_session.scalars(select(TransactionOverride)).one()
    assert provenance.evidence_ids == [1]
    assert db_session.scalar(select(effective_category).where(Transaction.id == txn.id)) == "Dining"

    dup = apply_mapping_plan(db_session, plan)
    assert dup.overrides_set == 0
    assert dup.skipped[0].reason == "duplicate"
    assert len(db_session.scalars(select(TransactionOverride)).all()) == 1

    with pytest.raises(MappingPlanValidationError):
        apply_mapping_plan(
            db_session,
            MappingPlanIn(
                ops=[
                    CreateMappingOp(op="create", kind="category", raw_value="Food", canonical_value="Dining"),
                    SetTransactionCategoryOp(transaction_id=txn.id, category="Travel"),
                ]
            ),
        )
    assert db_session.get(Transaction, txn.id).category_override == "Dining"

    removed = apply_mapping_plan(
        db_session,
        MappingPlanIn(ops=[RemoveTransactionOverrideOp(transaction_id=txn.id)]),
    )
    assert removed.overrides_removed == 1
    assert db_session.get(Transaction, txn.id).category_override is None
    assert db_session.scalars(select(TransactionOverride)).all() == []

    missing = apply_mapping_plan(
        db_session,
        MappingPlanIn(ops=[RemoveTransactionOverrideOp(transaction_id=txn.id)]),
    )
    assert missing.overrides_removed == 0
    assert missing.skipped[0].reason == "missing"

    apply_mapping_plan(
        db_session,
        MappingPlanIn(ops=[SetTransactionCategoryOp(transaction_id=txn.id, category="Dining")]),
    )
    replace = apply_mapping_plan(
        db_session,
        MappingPlanIn(
            ops=[
                RemoveTransactionOverrideOp(transaction_id=txn.id),
                SetTransactionCategoryOp(transaction_id=txn.id, category="Travel", evidence_ids=[3]),
            ]
        ),
    )
    assert replace.overrides_set == 1
    assert replace.overrides_removed == 1
    db_session.expire_all()
    assert db_session.get(Transaction, txn.id).category_override == "Travel"
    provenance = db_session.scalars(select(TransactionOverride)).one()
    assert provenance.category == "Travel"
    assert provenance.evidence_ids == [3]


def test_override_survives_reclassify_and_analytics_use_effective_category(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "summary")
    apply_mapping_plan(
        db_session,
        MappingPlanIn(ops=[SetTransactionCategoryOp(transaction_id=txn.id, category="Dining")]),
    )
    apply_mapping_plan(
        db_session,
        MappingPlanIn(
            ops=[
                CreateMappingOp(op="create", kind="category", raw_value="Shopping", canonical_value="Shopping-2")
            ]
        ),
    )
    db_session.expire_all()
    assert db_session.get(Transaction, txn.id).category_override == "Dining"
    rows = summarize(db_session, None, None, None, None, "category")
    assert rows == [{"group_value": "Dining", "total": Decimal("10.00"), "count": 1}]


def test_missing_transaction_is_validation_error(db_session: Session) -> None:
    with pytest.raises(MappingPlanValidationError):
        apply_mapping_plan(
            db_session,
            MappingPlanIn(ops=[SetTransactionCategoryOp(transaction_id=999, category="Dining")]),
        )
