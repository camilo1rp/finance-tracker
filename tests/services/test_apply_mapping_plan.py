from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, NormalizationMapping, Owner, Transaction
from app.schemas import ApplyMappingPlanIn, ProposedMappingIn
from app.services.mapping_preview_service import (
    MappingPlanValidationError,
    apply_mapping_plan,
)


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


def test_apply_transactional(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "COFFEE",
        category_raw="Food & Drink",
        category_normalized=None,
    )
    before = db_session.scalar(select(func.count()).select_from(NormalizationMapping))

    def boom(*_args, **_kwargs):
        raise RuntimeError("reclassify failed")

    monkeypatch.setattr(
        "app.services.mapping_preview_service.run_reclassification",
        boom,
    )
    with pytest.raises(RuntimeError, match="reclassify failed"):
        apply_mapping_plan(
            db_session,
            ApplyMappingPlanIn(
                rules=[
                    ProposedMappingIn(
                        kind="category",
                        raw_value="Food & Drink",
                        canonical_value="Dining",
                    )
                ]
            ),
        )

    db_session.expire_all()
    after = db_session.scalar(select(func.count()).select_from(NormalizationMapping))
    assert after == before
    assert db_session.scalars(select(NormalizationMapping)).all() == []


def test_apply_idempotent(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    txn = _add_txn(
        db_session,
        account.id,
        "COFFEE",
        category_raw="Food & Drink",
        category_normalized=None,
    )
    plan = ApplyMappingPlanIn(
        rules=[
            ProposedMappingIn(
                kind="category",
                raw_value=" Food & Drink ",
                canonical_value="Dining",
            )
        ],
        account_id=account.id,
    )

    first = apply_mapping_plan(db_session, plan)
    assert len(first.created_mapping_ids) == 1
    assert first.skipped_duplicates == []
    assert first.reclass_scanned == 1
    assert first.reclass_updated == 1
    assert first.unmapped_after.categories == []

    db_session.expire_all()
    txn = db_session.get(Transaction, txn.id)
    assert txn.category_normalized == "Dining"
    stored = db_session.get(NormalizationMapping, first.created_mapping_ids[0])
    assert stored.raw_value == "food & drink"

    second = apply_mapping_plan(db_session, plan)
    assert second.created_mapping_ids == []
    assert len(second.skipped_duplicates) == 1
    assert second.skipped_duplicates[0].canonical_value == "Dining"
    assert second.reclass_scanned == 1
    assert db_session.scalar(select(func.count()).select_from(NormalizationMapping)) == 1


def test_apply_rejects_invalid_plan(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    with pytest.raises(MappingPlanValidationError) as exc:
        apply_mapping_plan(
            db_session,
            ApplyMappingPlanIn(
                rules=[
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
                ]
            ),
        )
    assert "merchant only valid for category" in str(exc.value)
    assert db_session.scalars(select(NormalizationMapping)).all() == []
