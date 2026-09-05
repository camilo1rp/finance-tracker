from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Account, Owner, Transaction
from app.services.analytics_service import cash_flow, get_total


def _seed(db: Session) -> int:
    owner = Owner(name="Pat")
    db.add(owner)
    db.flush()
    account = Account(
        name="Mixed",
        last4="0000",
        default_owner_id=owner.id,
        source_format="csv",
        account_kind="depository",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
        },
    )
    db.add(account)
    db.flush()
    rows = [
        ("spend", "SPEND", Decimal("-10.00"), True),
        ("income", "INCOME", Decimal("100.00"), False),
        ("refund", "REFUND", Decimal("5.00"), False),
        ("fee", "FEE", Decimal("-2.00"), False),
        ("transfer", "TRANSFER", Decimal("50.00"), False),
        ("adjustment", "ADJUSTMENT", Decimal("-3.00"), False),
        ("unknown", "UNKNOWN", Decimal("1.00"), False),
    ]
    for suffix, txn_type, amount, is_spend in rows:
        db.add(
            Transaction(
                account_id=account.id,
                transaction_date=date(2024, 6, 1),
                description=suffix,
                amount=amount,
                transaction_type=txn_type,
                is_spend=is_spend,
                dedupe_hash=f"cash-flow-{suffix}",
                raw={},
            )
        )
    db.commit()
    return account.id


def test_cash_flow_buckets_and_other(client: TestClient, db_session: Session) -> None:
    account_id = _seed(db_session)
    response = client.get("/analytics/cash-flow", params={"account_id": account_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["purchases"] == "10.00"
    assert body["refunds"] == "5.00"
    assert body["spend"] == "5.00"
    assert body["income"] == "100.00"
    assert body["fees"] == "2.00"
    assert body["transfers"] == "50.00"
    assert body["other"] == "4.00"
    assert body["other_count"] == 2
    assert body["net_cash_flow"] == "93.00"


def test_cash_flow_matches_get_total(db_session: Session) -> None:
    account_id = _seed(db_session)
    total = get_total(db_session, None, None, account_id, None)
    flow = cash_flow(db_session, None, None, account_id, None)
    assert flow["purchases"] == total["purchases"]
    assert flow["refunds"] == total["refunds"]
    assert flow["spend"] == total["spend"]
    assert flow["net_cash_flow"] == total["net_cash_flow"]
    assert flow["by_type"] == total["by_type"]
