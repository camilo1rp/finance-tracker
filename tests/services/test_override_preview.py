from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, Owner, Transaction, TransactionOverride
from app.schemas import CreateMappingOp, MappingPlanIn, RemoveTransactionOverrideOp, SetTransactionCategoryOp
from app.services.mapping_preview_service import preview_mappings


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
    values = {
        "account_id": account_id,
        "transaction_date": date(2024, 6, 1),
        "description": suffix,
        "amount": Decimal("10.00"),
        "transaction_type": "SPEND",
        "is_spend": True,
        "category_raw": "Shopping",
        "category_normalized": "Shopping",
        "merchant_raw": "Amazon",
        "merchant_normalized": "Amazon",
        "dedupe_hash": f"hash-{suffix}",
        "raw": {},
    }
    values.update(kwargs)
    row = Transaction(
        **values,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_override_preview_actions_and_purity(db_session: Session) -> None:
    account = _seed_account(db_session)
    clean = _txn(db_session, account.id, "clean", category_override=None)
    overridden = _txn(db_session, account.id, "over", category_override="Dining")
    db_session.add(TransactionOverride(transaction_id=overridden.id, category="Dining", evidence_ids=[1], plan_source=None))
    db_session.commit()
    before = db_session.scalar(select(func.count()).select_from(TransactionOverride))

    preview = preview_mappings(
        db_session,
        MappingPlanIn(
            ops=[
                SetTransactionCategoryOp(transaction_id=clean.id, category="Dining", evidence_ids=[2]),
                SetTransactionCategoryOp(transaction_id=overridden.id, category="Dining"),
                SetTransactionCategoryOp(transaction_id=overridden.id, category="Travel"),
                RemoveTransactionOverrideOp(transaction_id=overridden.id),
                RemoveTransactionOverrideOp(transaction_id=clean.id),
                SetTransactionCategoryOp(transaction_id=999, category="Missing"),
            ]
        ),
    )
    actions = {item.transaction_id: item.action for item in preview.overrides if item.transaction_id in {clean.id, 999}}
    assert actions[clean.id] in {"set", "remove_noop"}
    assert actions[999] == "missing"
    over_actions = [item.action for item in preview.overrides if item.transaction_id == overridden.id]
    assert over_actions == ["noop", "replace_conflict", "remove"]
    assert preview.ops == []
    assert db_session.scalar(select(func.count()).select_from(TransactionOverride)) == before


def test_rule_only_preview_keeps_existing_shape_plus_empty_overrides(db_session: Session) -> None:
    account = _seed_account(db_session)
    _txn(db_session, account.id, "rule-only", category_normalized=None)
    preview = preview_mappings(
        db_session,
        MappingPlanIn(
            ops=[CreateMappingOp(op="create", kind="category", raw_value="Shopping", canonical_value="Dining")]
        ),
    )
    dumped = preview.model_dump(mode="json")
    assert dumped["overrides"] == []
    assert dumped["scanned"] == 1
    assert dumped["total_would_change"] == 1
    assert dumped["ops"][0]["would_change"] == 1
