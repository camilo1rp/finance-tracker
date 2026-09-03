from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Owner, Transaction, TransactionOverride


def _seed(db_session: Session) -> tuple[Owner, Account]:
    owner = Owner(name="Pat")
    db_session.add(owner)
    db_session.flush()
    account = Account(
        name="Card",
        last4="1111",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
        },
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(owner)
    db_session.refresh(account)
    return owner, account


def test_patch_category_override_deletes_provenance_row(
    client: TestClient, db_session: Session
) -> None:
    owner, account = _seed(db_session)
    txn = Transaction(
        account_id=account.id,
        owner_id=owner.id,
        transaction_date=date(2024, 6, 1),
        description="txn",
        amount=Decimal("10.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        category_normalized="Shopping",
        category_override="Dining",
        dedupe_hash="patch-override",
        raw={},
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    db_session.add(
        TransactionOverride(
            transaction_id=txn.id,
            category="Dining",
            evidence_ids=[1],
            plan_source="plan",
        )
    )
    db_session.commit()

    response = client.patch(f"/transactions/{txn.id}", json={"category_override": None})
    assert response.status_code == 200
    assert db_session.scalars(select(TransactionOverride)).all() == []


def test_patch_other_field_leaves_provenance_row(client: TestClient, db_session: Session) -> None:
    owner, account = _seed(db_session)
    txn = Transaction(
        account_id=account.id,
        owner_id=owner.id,
        transaction_date=date(2024, 6, 1),
        description="txn",
        amount=Decimal("10.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        category_normalized="Shopping",
        category_override="Dining",
        dedupe_hash="patch-owner",
        raw={},
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    db_session.add(
        TransactionOverride(
            transaction_id=txn.id,
            category="Dining",
            evidence_ids=[1],
            plan_source="plan",
        )
    )
    db_session.commit()

    response = client.patch(f"/transactions/{txn.id}", json={"owner_id": owner.id})
    assert response.status_code == 200
    assert len(db_session.scalars(select(TransactionOverride)).all()) == 1
