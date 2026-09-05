from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, NormalizationMapping, Owner, Transaction
from app.schemas import (
    CreateMappingOp,
    DeleteMappingOp,
    MappingPlanIn,
    UpdateMappingOp,
)
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


def _add_mapping(db: Session, **kwargs) -> NormalizationMapping:
    row = NormalizationMapping(**kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _create(**kwargs) -> CreateMappingOp:
    return CreateMappingOp(op="create", **kwargs)


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
            MappingPlanIn(
                ops=[
                    _create(
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
    plan = MappingPlanIn(
        ops=[
            _create(
                kind="category",
                raw_value=" Food & Drink ",
                canonical_value="Dining",
            )
        ],
        account_id=account.id,
    )

    first = apply_mapping_plan(db_session, plan)
    assert len(first.created_ids) == 1
    assert first.updated_ids == []
    assert first.deleted_ids == []
    assert first.skipped == []
    assert first.reclass_scanned == 1
    assert first.reclass_updated == 1
    assert first.unmapped_after.categories == []

    db_session.expire_all()
    txn = db_session.get(Transaction, txn.id)
    assert txn.category_normalized == "Dining"
    stored = db_session.get(NormalizationMapping, first.created_ids[0])
    assert stored.raw_value == "food & drink"

    second = apply_mapping_plan(db_session, plan)
    assert second.created_ids == []
    assert len(second.skipped) == 1
    assert second.skipped[0].reason == "duplicate"
    assert second.skipped[0].op.canonical_value == "Dining"  # type: ignore[union-attr]
    assert second.reclass_scanned == 1
    assert db_session.scalar(select(func.count()).select_from(NormalizationMapping)) == 1


def test_apply_rejects_invalid_plan(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    with pytest.raises(MappingPlanValidationError) as exc:
        apply_mapping_plan(
            db_session,
            MappingPlanIn(
                ops=[
                    _create(
                        kind="merchant",
                        raw_value="x",
                        canonical_value="X",
                        merchant="Nope",
                    ),
                    _create(
                        kind="category",
                        raw_value="Food",
                        canonical_value="Dining",
                    ),
                ]
            ),
        )
    assert "merchant only valid for category or transaction_type" in str(exc.value)
    assert db_session.scalars(select(NormalizationMapping)).all() == []


def test_apply_mixed_plan_atomic_and_reapply(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    to_delete = _add_mapping(
        db_session,
        kind="category",
        raw_value="misc",
        canonical_value="Misc",
        account_id=None,
        merchant=None,
    )
    to_update = _add_mapping(
        db_session,
        kind="category",
        raw_value="groceries",
        canonical_value="Groceries",
        account_id=None,
        merchant=None,
    )
    txn_delete = _add_txn(
        db_session,
        account.id,
        "misc-row",
        category_raw="Misc",
        category_normalized="Misc",
    )
    txn_update = _add_txn(
        db_session,
        account.id,
        "groc-row",
        category_raw="Groceries",
        category_normalized="Groceries",
    )
    txn_create = _add_txn(
        db_session,
        account.id,
        "food-row",
        category_raw="Food & Drink",
        category_normalized=None,
    )
    plan = MappingPlanIn(
        ops=[
            DeleteMappingOp(op="delete", mapping_id=to_delete.id),
            UpdateMappingOp(
                op="update", mapping_id=to_update.id, canonical_value="Grocery"
            ),
            _create(
                kind="category",
                raw_value="Food & Drink",
                canonical_value="Dining",
            ),
        ]
    )

    first = apply_mapping_plan(db_session, plan)
    assert first.deleted_ids == [to_delete.id]
    assert first.updated_ids == [to_update.id]
    assert len(first.created_ids) == 1
    assert first.skipped == []
    assert first.reclass_updated == 3

    db_session.expire_all()
    assert db_session.get(NormalizationMapping, to_delete.id) is None
    assert db_session.get(NormalizationMapping, to_update.id).canonical_value == "Grocery"
    assert db_session.get(Transaction, txn_delete.id).category_normalized is None
    assert db_session.get(Transaction, txn_update.id).category_normalized == "Grocery"
    assert db_session.get(Transaction, txn_create.id).category_normalized == "Dining"

    second = apply_mapping_plan(db_session, plan)
    assert second.created_ids == []
    assert second.updated_ids == [to_update.id]
    assert second.deleted_ids == []
    reasons = {item.reason for item in second.skipped}
    assert reasons == {"duplicate", "missing"}
    assert second.reclass_updated == 0


def test_apply_mixed_plan_rolls_back_on_reclass_failure(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, account = _seed_account(db_session)
    to_delete = _add_mapping(
        db_session,
        kind="category",
        raw_value="misc",
        canonical_value="Misc",
        account_id=None,
        merchant=None,
    )
    to_update = _add_mapping(
        db_session,
        kind="category",
        raw_value="groceries",
        canonical_value="Groceries",
        account_id=None,
        merchant=None,
    )
    _add_txn(
        db_session,
        account.id,
        "food-row",
        category_raw="Food",
        category_normalized=None,
    )
    mapping_count = db_session.scalar(select(func.count()).select_from(NormalizationMapping))

    def boom(*_args, **_kwargs):
        raise RuntimeError("reclassify failed")

    monkeypatch.setattr(
        "app.services.mapping_preview_service.run_reclassification",
        boom,
    )
    with pytest.raises(RuntimeError, match="reclassify failed"):
        apply_mapping_plan(
            db_session,
            MappingPlanIn(
                ops=[
                    DeleteMappingOp(op="delete", mapping_id=to_delete.id),
                    UpdateMappingOp(
                        op="update",
                        mapping_id=to_update.id,
                        canonical_value="Grocery",
                    ),
                    _create(kind="category", raw_value="Food", canonical_value="Dining"),
                ]
            ),
        )

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(NormalizationMapping)) == mapping_count
    assert db_session.get(NormalizationMapping, to_delete.id) is not None
    assert (
        db_session.get(NormalizationMapping, to_update.id).canonical_value == "Groceries"
    )


def test_apply_rejects_conflict_and_missing_id(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    existing = _add_mapping(
        db_session,
        kind="category",
        raw_value="groceries",
        canonical_value="Groceries",
        account_id=None,
        merchant=None,
    )
    _add_txn(
        db_session,
        account.id,
        "groc",
        category_raw="Groceries",
        category_normalized="Groceries",
    )

    with pytest.raises(MappingPlanValidationError) as conflict:
        apply_mapping_plan(
            db_session,
            MappingPlanIn(
                ops=[
                    _create(
                        kind="category",
                        raw_value="groceries",
                        canonical_value="Grocery",
                    )
                ]
            ),
        )
    assert f"conflicts with mapping {existing.id}" in str(conflict.value)

    db_session.expire_all()
    assert db_session.get(NormalizationMapping, existing.id).canonical_value == "Groceries"
    assert db_session.scalar(select(func.count()).select_from(NormalizationMapping)) == 1

    with pytest.raises(MappingPlanValidationError) as missing:
        apply_mapping_plan(
            db_session,
            MappingPlanIn(
                ops=[
                    UpdateMappingOp(op="update", mapping_id=999, canonical_value="X")
                ]
            ),
        )
    assert "mapping 999 not found" in str(missing.value)
    assert db_session.scalar(select(func.count()).select_from(NormalizationMapping)) == 1
