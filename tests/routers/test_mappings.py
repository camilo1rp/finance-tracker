from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.classification import NormalizationKind
from app.domain.db_lookup import DbNormalizationLookup


def _account(client: TestClient) -> int:
    owner = client.post("/owners", json={"name": "Pat"}).json()
    account = client.post(
        "/accounts",
        json={
            "name": "Card",
            "last4": "1111",
            "default_owner_id": owner["id"],
            "default_mapping": {
                "date_col": "Date",
                "description_col": "Description",
                "amount_col": "Amount",
                "type_col": "Type",
            },
        },
    ).json()
    return account["id"]


def test_create_mapping_cleans_raw_value_and_collapses_duplicates(client: TestClient) -> None:
    created = client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": " Sale ",
            "canonical_value": "SPEND",
        },
    )
    assert created.status_code == 201
    assert created.json()["raw_value"] == "sale"

    duplicate = client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "sale",
            "canonical_value": "SPEND",
        },
    )
    assert duplicate.status_code == 409


def test_list_and_delete_mapping(client: TestClient) -> None:
    created = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Food & Drink",
            "canonical_value": "Dining",
        },
    ).json()
    listed = client.get("/mappings", params={"kind": "category"})
    assert listed.status_code == 200
    assert listed.json() == [created]

    deleted = client.delete(f"/mappings/{created['id']}")
    assert deleted.status_code == 204
    assert client.get("/mappings").json() == []
    assert client.delete(f"/mappings/{created['id']}").status_code == 404


def test_db_lookup_prefers_account_rule(client: TestClient, db_session: Session) -> None:
    account_id = _account(client)
    client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "sale",
            "canonical_value": "SPEND",
        },
    )
    client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "sale",
            "canonical_value": "PAYMENT",
            "account_id": account_id,
        },
    )
    lookup = DbNormalizationLookup(db_session)
    assert (
        lookup.resolve(NormalizationKind.TRANSACTION_TYPE, "sale", account_id)
        == "PAYMENT"
    )
    assert (
        lookup.resolve(NormalizationKind.TRANSACTION_TYPE, "sale", account_id=999)
        == "SPEND"
    )


def test_category_mapping_merchant_scope(client: TestClient, db_session: Session) -> None:
    account_id = _account(client)
    scoped = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Household",
            "merchant": " Costco ",
        },
    )
    assert scoped.status_code == 201, scoped.text
    assert scoped.json()["merchant"] == "costco"

    unscoped = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Shopping",
        },
    )
    assert unscoped.status_code == 201, unscoped.text
    assert unscoped.json()["merchant"] is None

    account_scoped = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Warehouse",
            "account_id": account_id,
            "merchant": "Costco",
        },
    )
    assert account_scoped.status_code == 201, account_scoped.text

    rejected = client.post(
        "/mappings",
        json={
            "kind": "merchant",
            "raw_value": "costco",
            "canonical_value": "Costco",
            "merchant": "Costco",
        },
    )
    assert rejected.status_code == 422

    duplicate = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "shopping",
            "canonical_value": "Dup",
            "merchant": "COSTCO",
        },
    )
    assert duplicate.status_code == 409

    lookup = DbNormalizationLookup(db_session)
    assert lookup.resolve(NormalizationKind.CATEGORY, "shopping", account_id, "costco") == (
        "Warehouse"
    )
    assert lookup.resolve(NormalizationKind.CATEGORY, "shopping", 999, "costco") == (
        "Household"
    )
    assert lookup.resolve(NormalizationKind.CATEGORY, "shopping", 999, "amazon") == (
        "Shopping"
    )
    assert lookup.resolve(NormalizationKind.CATEGORY, "shopping", 999) == "Shopping"
