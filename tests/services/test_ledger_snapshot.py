from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Account, Owner, Transaction
from app.services.analytics_service import UNASSIGNED, ledger_snapshot


def _seed_account(db: Session, owner_name: str = "Pat") -> tuple[Owner, Account]:
    owner = Owner(name=owner_name)
    db.add(owner)
    db.flush()
    account = Account(
        name="Card",
        last4="1111",
        default_owner_id=owner.id,
        source_format="csv",
        account_kind="credit_card",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
        },
    )
    db.add(account)
    db.flush()
    return owner, account


def _add_txn(db: Session, account: Account, suffix: str, **kwargs) -> None:
    values = {
        "account_id": account.id,
        "transaction_date": date(2024, 6, 1),
        "description": suffix,
        "amount": Decimal("10.00"),
        "transaction_type": "SPEND",
        "is_spend": True,
        "dedupe_hash": f"snap-{suffix}",
        "raw": {},
    }
    values.update(kwargs)
    db.add(Transaction(**values))


def test_ledger_snapshot_empty(db_session: Session) -> None:
    assert ledger_snapshot(db_session) == {
        "transaction_count": 0,
        "merchant_count": 0,
        "owners": [],
        "categories": [],
    }


def test_ledger_snapshot_groups_subcategories_and_counts(db_session: Session) -> None:
    pat, account = _seed_account(db_session)
    other = Owner(name="Sam")
    db_session.add(other)
    db_session.flush()
    _add_txn(
        db_session,
        account,
        "coffee-1",
        owner_id=pat.id,
        category_normalized="Dining",
        subcategory="Coffee",
        merchant_normalized="Starbucks",
    )
    _add_txn(
        db_session,
        account,
        "coffee-2",
        owner_id=pat.id,
        category_normalized="Dining",
        subcategory="Coffee",
        merchant_normalized="Starbucks",
    )
    _add_txn(
        db_session,
        account,
        "dinner",
        owner_id=pat.id,
        category_override="Dining",
        category_normalized="Food",
        subcategory="Restaurants",
        merchant_normalized="Local Bistro",
    )
    _add_txn(
        db_session,
        account,
        "gas",
        owner_id=pat.id,
        category_raw="Transport",
        subcategory=None,
        merchant_normalized="Shell",
    )
    _add_txn(
        db_session,
        account,
        "blank-merchant",
        owner_id=pat.id,
        category_raw="Transport",
        merchant_raw=None,
    )
    db_session.commit()

    snapshot = ledger_snapshot(db_session)
    assert snapshot["transaction_count"] == 5
    assert snapshot["merchant_count"] == 3
    assert snapshot["owners"] == [
        {"id": pat.id, "name": "Pat"},
        {"id": other.id, "name": "Sam"},
    ]
    by_name = {row["name"]: row for row in snapshot["categories"]}
    assert by_name["Dining"]["count"] == 3
    assert by_name["Dining"]["subcategories"] == [
        {"name": "Coffee", "count": 2},
        {"name": "Restaurants", "count": 1},
    ]
    assert by_name["Transport"]["count"] == 2
    assert by_name["Transport"]["subcategories"] == []
    assert UNASSIGNED not in by_name


def test_ledger_snapshot_keeps_unassigned_subcategory_when_mixed(
    db_session: Session,
) -> None:
    _, account = _seed_account(db_session, owner_name="Pat")
    _add_txn(
        db_session,
        account,
        "labeled",
        category_normalized="Shopping",
        subcategory="Amazon",
        merchant_normalized="Amazon",
    )
    _add_txn(
        db_session,
        account,
        "bare",
        category_normalized="Shopping",
        merchant_normalized="Target",
    )
    db_session.commit()

    snapshot = ledger_snapshot(db_session)
    shopping = snapshot["categories"][0]
    assert shopping["name"] == "Shopping"
    assert shopping["count"] == 2
    assert shopping["subcategories"] == [
        {"name": "Amazon", "count": 1},
        {"name": UNASSIGNED, "count": 1},
    ]
