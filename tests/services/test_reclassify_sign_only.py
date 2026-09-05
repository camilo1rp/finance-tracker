from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Owner, Transaction
from app.services.ingest_service import reclassify_transactions


def test_reclassify_sign_only_paycheck_becomes_income(db_session: Session) -> None:
    owner = Owner(name="Pat")
    db_session.add(owner)
    db_session.flush()
    account = Account(
        name="Checking",
        last4="3333",
        default_owner_id=owner.id,
        source_format="csv",
        account_kind="depository",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "sign_convention": "negative_is_spend",
        },
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 15),
            description="PAYCHECK",
            amount=Decimal("1234.56"),
            transaction_type="PAYMENT",
            is_spend=False,
            raw_type=None,
            dedupe_hash="sign-only-paycheck",
            raw={},
        )
    )
    db_session.commit()

    result = reclassify_transactions(db_session, account_id=account.id)
    db_session.expire_all()
    txn = db_session.scalars(select(Transaction)).one()
    assert result.updated == 1
    assert txn.transaction_type == "INCOME"
    assert txn.is_spend is False


def test_kind_flip_changes_transfer_to_income(db_session: Session) -> None:
    owner = Owner(name="Pat")
    db_session.add(owner)
    db_session.flush()
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
            "sign_convention": "negative_is_spend",
        },
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 1),
            description="Payment",
            amount=Decimal("100.00"),
            transaction_type="TRANSFER",
            is_spend=False,
            raw_type=None,
            dedupe_hash="kind-flip",
            raw={},
        )
    )
    db_session.commit()

    account.account_kind = "depository"
    db_session.commit()
    result = reclassify_transactions(db_session, account_id=account.id)
    db_session.expire_all()
    txn = db_session.scalars(select(Transaction)).one()
    assert result.updated == 1
    assert txn.transaction_type == "INCOME"
