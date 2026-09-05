from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.classification import NormalizationKind
from app.domain.db_lookup import DbNormalizationLookup
from app.models import Transaction


def _category_mappings(client: TestClient) -> list[dict]:
    return [m for m in client.get("/mappings").json() if m["kind"] == "category"]


def _account(client: TestClient) -> int:
    owner = client.post("/owners", json={"name": "Pat"}).json()
    account = client.post(
        "/accounts",
        json={
            "name": "Card",
            "last4": "1111",
            "default_owner_id": owner["id"],
            "account_kind": "credit_card",
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


def test_create_mapping_rejects_wildcard_only_pattern(client: TestClient) -> None:
    rejected = client.post(
        "/mappings",
        json={
            "kind": "merchant",
            "raw_value": "%%",
            "canonical_value": "Anything",
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "pattern cannot be only wildcards"


def test_create_mapping_stores_wildcard_pattern_cleaned(client: TestClient) -> None:
    created = client.post(
        "/mappings",
        json={
            "kind": "merchant",
            "raw_value": "%STARBUCKS%",
            "canonical_value": "Starbucks",
        },
    )
    assert created.status_code == 201
    assert created.json()["raw_value"] == "%starbucks%"


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
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted_id"] == created["id"]
    assert "reclass_scanned" in body
    assert "reclass_updated" in body
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
            "canonical_value": "TRANSFER",
            "account_id": account_id,
        },
    )
    lookup = DbNormalizationLookup(db_session)
    assert (
        lookup.resolve(NormalizationKind.TRANSACTION_TYPE, "sale", account_id)
        == "TRANSFER"
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
    assert "merchant scope is only allowed" in rejected.json()["detail"]

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


def test_transaction_type_mapping_merchant_scope(
    client: TestClient, db_session: Session
) -> None:
    account_id = _account(client)
    scoped = client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "MISC_DEBIT",
            "canonical_value": "TRANSFER",
            "account_id": account_id,
            "merchant": " Western Union% ",
        },
    )
    assert scoped.status_code == 201, scoped.text
    assert scoped.json()["merchant"] == "western union%"

    unscoped = client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "MISC_DEBIT",
            "canonical_value": "SPEND",
            "account_id": account_id,
        },
    )
    assert unscoped.status_code == 201, unscoped.text

    lookup = DbNormalizationLookup(db_session)
    assert (
        lookup.resolve(
            NormalizationKind.TRANSACTION_TYPE,
            "misc_debit",
            account_id,
            "western union",
        )
        == "TRANSFER"
    )
    assert (
        lookup.resolve(
            NormalizationKind.TRANSACTION_TYPE,
            "misc_debit",
            account_id,
            "western union capture 623287974331123 web id: 9222993574",
        )
        == "TRANSFER"
    )
    assert (
        lookup.resolve(
            NormalizationKind.TRANSACTION_TYPE,
            "misc_debit",
            account_id,
            "woodlake op",
        )
        == "SPEND"
    )


def test_merchant_mapping_raw_value_wildcard(
    client: TestClient, db_session: Session
) -> None:
    account_id = _account(client)
    created = client.post(
        "/mappings",
        json={
            "kind": "merchant",
            "raw_value": "Western Union%",
            "canonical_value": "Western Union",
            "account_id": account_id,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["raw_value"] == "western union%"

    lookup = DbNormalizationLookup(db_session)
    assert (
        lookup.resolve(
            NormalizationKind.MERCHANT,
            "western union capture 623287974331123 web id: 9222993574",
            account_id,
        )
        == "Western Union"
    )
    assert (
        lookup.resolve(
            NormalizationKind.MERCHANT,
            "woodlake op rent 270209230 web id: 1861072180",
            account_id,
        )
        is None
    )


def test_preview_and_apply_endpoints(client: TestClient, db_session: Session) -> None:
    account_id = _account(client)
    db_session.add(
        Transaction(
            account_id=account_id,
            transaction_date=date(2024, 6, 1),
            description="COFFEE",
            amount=Decimal("4.50"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Food & Drink",
            category_normalized=None,
            dedupe_hash="preview-apply-coffee",
            raw={},
        )
    )
    db_session.commit()

    body = {
        "ops": [
            {
                "op": "create",
                "kind": "category",
                "raw_value": "Food & Drink",
                "canonical_value": "Dining",
            }
        ],
        "account_id": account_id,
    }
    preview = client.post("/mappings/preview", json=body)
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["scanned"] == 1
    assert payload["total_would_change"] == 1
    assert payload["ops"][0]["would_change"] == 1
    assert _category_mappings(client) == []

    listed_before = client.get("/transactions", params={"account_id": account_id}).json()
    assert listed_before[0]["category_normalized"] is None

    applied = client.post("/mappings/apply", json=body)
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert len(result["created_ids"]) == 1
    assert result["updated_ids"] == []
    assert result["deleted_ids"] == []
    assert result["skipped"] == []
    assert result["reclass_updated"] == 1
    assert result["unmapped_after"]["categories"] == []

    mappings = _category_mappings(client)
    assert len(mappings) == 1
    assert mappings[0]["raw_value"] == "food & drink"
    listed = client.get("/transactions", params={"account_id": account_id}).json()
    assert listed[0]["category_normalized"] == "Dining"

    again = client.post("/mappings/apply", json=body)
    assert again.status_code == 200, again.text
    assert again.json()["created_ids"] == []
    assert len(again.json()["skipped"]) == 1
    assert again.json()["skipped"][0]["reason"] == "duplicate"


def test_apply_endpoint_rejects_invalid_plan(client: TestClient) -> None:
    response = client.post(
        "/mappings/apply",
        json={
            "ops": [
                {
                    "op": "create",
                    "kind": "merchant",
                    "raw_value": "x",
                    "canonical_value": "X",
                    "merchant": "Nope",
                }
            ]
        },
    )
    assert response.status_code == 422
    assert "merchant only valid for category or transaction_type" in str(
        response.json()["detail"]
    )


def test_patch_and_delete_reclassify(client: TestClient, db_session: Session) -> None:
    account_id = _account(client)
    created = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Food & Drink",
            "canonical_value": "Dining",
        },
    ).json()
    db_session.add(
        Transaction(
            account_id=account_id,
            transaction_date=date(2024, 6, 1),
            description="COFFEE",
            amount=Decimal("4.50"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Food & Drink",
            category_normalized="Dining",
            dedupe_hash="patch-delete-coffee",
            raw={},
        )
    )
    db_session.commit()

    patched = client.patch(
        f"/mappings/{created['id']}",
        json={"canonical_value": "Cafes"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["id"] == created["id"]
    assert body["canonical_value"] == "Cafes"
    assert body["reclass_updated"] == 1
    listed = client.get("/transactions", params={"account_id": account_id}).json()
    assert listed[0]["category_normalized"] == "Cafes"

    missing = client.patch("/mappings/999999", json={"canonical_value": "X"})
    assert missing.status_code == 404

    deleted = client.delete(f"/mappings/{created['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_id"] == created["id"]
    assert deleted.json()["reclass_updated"] == 1
    listed = client.get("/transactions", params={"account_id": account_id}).json()
    assert listed[0]["category_normalized"] is None
    assert _category_mappings(client) == []
