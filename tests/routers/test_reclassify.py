from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Account, Owner, Transaction


def test_reclassify_applies_new_type_and_category_mappings(
    client: TestClient, db_session: Session
) -> None:
    owner = client.post("/owners", json={"name": "Camilo Romero"}).json()
    account = client.post(
        "/accounts",
        json={
            "name": "Chase",
            "last4": "9470",
            "default_owner_id": owner["id"],
            "default_mapping": {
                "date_col": "Transaction Date",
                "description_col": "Description",
                "amount_col": "Amount",
                "type_col": "Type",
                "category_col": "Category",
            },
        },
    ).json()

    db_session.add(
        Transaction(
            account_id=account["id"],
            owner_id=owner["id"],
            transaction_date=date(2024, 6, 1),
            description="STORE REFUND",
            amount=Decimal("-12.00"),
            transaction_type="UNKNOWN",
            is_spend=False,
            raw_type="Return",
            category_raw="Food & Drink",
            category_normalized=None,
            category_override="Keep Me",
            dedupe_hash="reclassify-return",
            raw={},
        )
    )
    db_session.commit()

    client.post(
        "/mappings",
        json={"kind": "transaction_type", "raw_value": "Return", "canonical_value": "REFUND"},
    )
    client.post(
        "/mappings",
        json={"kind": "category", "raw_value": "Food & Drink", "canonical_value": "Dining"},
    )

    result = client.post("/transactions/reclassify", params={"account_id": account["id"]})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["scanned"] == 1
    assert body["updated"] == 1
    assert body["unmapped"]["transaction_types"] == []
    assert body["unmapped"]["categories"] == []

    listed = client.get("/transactions", params={"account_id": account["id"]})
    row = listed.json()[0]
    assert row["transaction_type"] == "REFUND"
    assert row["is_spend"] is False
    assert row["category_normalized"] == "Dining"
    assert row["category_override"] == "Keep Me"
    assert row["category_raw"] == "Food & Drink"


def test_reclassify_backfills_merchant_from_description(
    client: TestClient, db_session: Session
) -> None:
    owner = client.post("/owners", json={"name": "Camilo Romero"}).json()
    account = client.post(
        "/accounts",
        json={
            "name": "Chase",
            "last4": "9470",
            "default_owner_id": owner["id"],
            "default_mapping": {
                "date_col": "Transaction Date",
                "description_col": "Description",
                "amount_col": "Amount",
                "type_col": "Type",
            },
        },
    ).json()

    db_session.add(
        Transaction(
            account_id=account["id"],
            owner_id=owner["id"],
            transaction_date=date(2024, 6, 1),
            description="STARBUCKS STORE 123",
            amount=Decimal("12.45"),
            transaction_type="SPEND",
            is_spend=True,
            merchant_raw=None,
            merchant_normalized=None,
            merchant_override="Keep Cafe",
            dedupe_hash="reclassify-merchant",
            raw={},
        )
    )
    db_session.commit()

    client.post(
        "/mappings",
        json={"kind": "merchant", "raw_value": "starbucks", "canonical_value": "Starbucks"},
    )

    result = client.post("/transactions/reclassify", params={"account_id": account["id"]})
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 1
    assert result.json()["unmapped"]["merchants"] == []

    row = client.get("/transactions", params={"account_id": account["id"]}).json()[0]
    assert row["merchant_raw"] == "STARBUCKS"
    assert row["merchant_normalized"] == "Starbucks"
    assert row["merchant_override"] == "Keep Cafe"


def test_reclassify_category_uses_resolved_merchant(
    client: TestClient, db_session: Session
) -> None:
    owner = client.post("/owners", json={"name": "Camilo Romero"}).json()
    account = client.post(
        "/accounts",
        json={
            "name": "Chase",
            "last4": "9470",
            "default_owner_id": owner["id"],
            "default_mapping": {
                "date_col": "Transaction Date",
                "description_col": "Description",
                "amount_col": "Amount",
                "type_col": "Type",
                "category_col": "Category",
            },
        },
    ).json()

    db_session.add(
        Transaction(
            account_id=account["id"],
            owner_id=owner["id"],
            transaction_date=date(2024, 6, 1),
            description="COSTCO STORE 5",
            amount=Decimal("80.00"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Shopping",
            category_normalized=None,
            merchant_raw=None,
            dedupe_hash="reclassify-costco",
            raw={},
        )
    )
    db_session.add(
        Transaction(
            account_id=account["id"],
            owner_id=owner["id"],
            transaction_date=date(2024, 6, 2),
            description="AMAZON MARKETPLACE",
            amount=Decimal("45.00"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Shopping",
            category_normalized=None,
            merchant_raw=None,
            dedupe_hash="reclassify-amazon",
            raw={},
        )
    )
    db_session.commit()

    client.post(
        "/mappings",
        json={"kind": "merchant", "raw_value": "costco", "canonical_value": "Costco"},
    )
    client.post(
        "/mappings",
        json={"kind": "category", "raw_value": "Shopping", "canonical_value": "Shopping"},
    )
    client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Household",
            "merchant": "Costco",
        },
    )

    result = client.post("/transactions/reclassify", params={"account_id": account["id"]})
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 2

    rows = {
        row["description"]: row
        for row in client.get("/transactions", params={"account_id": account["id"]}).json()
    }
    assert rows["COSTCO STORE 5"]["merchant_raw"] == "COSTCO"
    assert rows["COSTCO STORE 5"]["merchant_normalized"] == "Costco"
    assert rows["COSTCO STORE 5"]["category_normalized"] == "Household"
    assert rows["AMAZON MARKETPLACE"]["merchant_raw"] == "AMAZON MARKETPLACE"
    assert rows["AMAZON MARKETPLACE"]["category_normalized"] == "Shopping"


def test_reclassify_unknown_account(client: TestClient) -> None:
    response = client.post("/transactions/reclassify", params={"account_id": 999})
    assert response.status_code == 404
