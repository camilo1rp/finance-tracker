from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.domain.label_filter import UnknownLabelFilterError, unknown_label_detail
from app.models import Transaction
from app.services.label_filter import (
    available_categories,
    available_subcategories,
    resolve_label_filters,
)


def _seed_account(db: Session):
    from app.models import Account, Owner

    owner = Owner(name="Owner-label-filter")
    db.add(owner)
    db.flush()
    account = Account(
        name="Card",
        last4="0000",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
        },
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


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


def test_available_categories_uses_effective_category(db_session: Session) -> None:
    account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "override",
        category_raw="Raw",
        category_normalized="Normalized",
        category_override="OverrideCat",
    )
    _add_txn(
        db_session,
        account.id,
        "normalized",
        category_raw="Raw2",
        category_normalized="Dining",
    )
    assert available_categories(db_session) == ["Dining", "OverrideCat"]


def test_available_subcategories_distinct_non_empty(db_session: Session) -> None:
    account = _seed_account(db_session)
    _add_txn(db_session, account.id, "a", subcategory="card_payment")
    _add_txn(db_session, account.id, "b", subcategory="card_payment")
    _add_txn(db_session, account.id, "c", subcategory="savings")
    assert available_subcategories(db_session) == ["card_payment", "savings"]


def test_resolve_label_filters_case_insensitive_category(db_session: Session) -> None:
    account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "dining",
        category_normalized="Dining",
    )
    resolved = resolve_label_filters(db_session, category=" dining ")
    assert resolved.categories == ("Dining",)
    assert resolved.subcategories == ()


def test_resolve_label_filters_and_independent(db_session: Session) -> None:
    account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "both",
        category_normalized="Dining",
        subcategory="card_payment",
    )
    resolved = resolve_label_filters(
        db_session,
        category="Dining",
        subcategory="CARD_PAYMENT",
    )
    assert resolved.categories == ("Dining",)
    assert resolved.subcategories == ("card_payment",)


def test_resolve_label_filters_unknown_category(db_session: Session) -> None:
    account = _seed_account(db_session)
    _add_txn(db_session, account.id, "x", category_normalized="Dining")
    with pytest.raises(UnknownLabelFilterError) as exc_info:
        resolve_label_filters(db_session, category="Travel")
    assert exc_info.value.detail == unknown_label_detail(
        "category", "Travel", ["Dining"]
    )


def test_resolve_label_filters_both_unknown_joined_detail(db_session: Session) -> None:
    with pytest.raises(UnknownLabelFilterError) as exc_info:
        resolve_label_filters(db_session, category="Travel", subcategory="missing")
    assert exc_info.value.errors == [
        unknown_label_detail("category", "Travel", []),
        unknown_label_detail("subcategory", "missing", []),
    ]
