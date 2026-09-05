from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent.tools.enricher import list_unmatched
from app.models import Account, Owner, Transaction
from tests.agent.helpers import agent_sessions


def _seed_spend(db: Session) -> Transaction:
    owner = Owner(name="Pat")
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
        },
    )
    db.add(account)
    db.flush()
    txn = Transaction(
        account_id=account.id,
        transaction_date=date(2024, 6, 1),
        description="COFFEE",
        amount=Decimal("-4.50"),
        transaction_type="SPEND",
        is_spend=True,
        dedupe_hash="type-override-spend",
        raw={},
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def test_type_override_flips_is_spend_and_drops_unmatched(
    client: TestClient, db_session: Session, agent_sessions
) -> None:
    txn = _seed_spend(db_session)
    before = list_unmatched.invoke(
        {"date_from": date(2024, 6, 1), "date_to": date(2024, 6, 30), "limit": 10}
    )
    assert f"id={txn.id}" in before

    patched = client.patch(
        f"/transactions/{txn.id}", json={"type_override": "INCOME"}
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["type_override"] == "INCOME"
    assert body["is_spend"] is False

    after = list_unmatched.invoke(
        {"date_from": date(2024, 6, 1), "date_to": date(2024, 6, 30), "limit": 10}
    )
    assert f"id={txn.id}" not in after


def test_spend_analytics_respects_type_override(client: TestClient, db_session: Session) -> None:
    txn = _seed_spend(db_session)
    spend_before = client.get("/analytics/total")
    assert spend_before.json()["count"] == 1

    client.patch(f"/transactions/{txn.id}", json={"type_override": "INCOME"})
    spend_after = client.get("/analytics/total")
    assert spend_after.json()["count"] == 0
