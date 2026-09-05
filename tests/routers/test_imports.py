from pathlib import Path

from fastapi.testclient import TestClient

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.csv"

ACCOUNT_MAPPING = {
    "date_col": "Date",
    "description_col": "Description",
    "amount_col": "Amount",
    "category_col": "Category",
    "owner_col": "Purchased By",
    "type_col": "Type",
}


def _seed(client: TestClient) -> tuple[int, int]:
    owner = client.post("/owners", json={"name": "John Smith"}).json()
    account = client.post(
        "/accounts",
        json={
            "name": "Shared Card",
            "last4": "4242",
            "account_kind": "credit_card",
            "default_owner_id": owner["id"],
            "default_mapping": ACCOUNT_MAPPING,
        },
    ).json()
    for payload in (
        {"kind": "transaction_type", "raw_value": "Sale", "canonical_value": "SPEND"},
        {"kind": "transaction_type", "raw_value": "Return", "canonical_value": "REFUND"},
        {"kind": "transaction_type", "raw_value": "Payment", "canonical_value": "TRANSFER"},
        {"kind": "category", "raw_value": "Food & Drink", "canonical_value": "Dining"},
        {
            "kind": "owner",
            "raw_value": "John",
            "canonical_value": "John Smith",
        },
    ):
        created = client.post("/mappings", json=payload)
        assert created.status_code == 201, created.text
    return owner["id"], account["id"]


def _import(client: TestClient, account_id: int):
    return client.post(
        "/imports",
        params={"account_id": account_id},
        files={"file": ("sample.csv", FIXTURE.read_bytes(), "text/csv")},
    )


def test_import_pipeline_dedupe_filter_and_patch(client: TestClient) -> None:
    owner_id, account_id = _seed(client)

    first = _import(client, account_id)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total_rows_read"] == 4
    assert body["inserted"] == 4
    assert body["duplicates_skipped"] == 0
    assert body["unmapped"]["categories"] == ["Mystery", "Shopping"]
    assert body["unmapped"]["owners"] == ["Jane"]
    assert body["errors"] == []

    second = _import(client, account_id)
    assert second.status_code == 200
    assert second.json()["inserted"] == 0
    assert second.json()["duplicates_skipped"] == 4

    listed = client.get("/transactions", params={"owner_id": owner_id})
    assert listed.status_code == 200
    owned = listed.json()
    assert len(owned) == 3
    coffee = next(row for row in owned if row["description"] == "Coffee Shop")
    assert coffee["category_normalized"] == "Dining"
    assert coffee["category_raw"] == "Food & Drink"
    assert coffee["owner_id"] == owner_id
    assert coffee["is_spend"] is True

    jane_rows = client.get("/transactions")
    unknown = next(
        row for row in jane_rows.json() if row["description"] == "Unknown Merchant"
    )
    assert unknown["owner_id"] is None
    assert unknown["category_normalized"] is None
    assert unknown["category_raw"] == "Mystery"

    patched = client.patch(
        f"/transactions/{coffee['id']}",
        json={"category_override": "Cafes"},
    )
    assert patched.status_code == 200
    assert patched.json()["category_override"] == "Cafes"
    assert patched.json()["category_raw"] == "Food & Drink"

    filtered = client.get("/transactions", params={"category": "Cafes"})
    assert [row["id"] for row in filtered.json()] == [coffee["id"]]


def _import_csv(client: TestClient, account_id: int, csv: str, *, allow_duplicates: bool = False):
    params: dict = {"account_id": account_id}
    if allow_duplicates:
        params["allow_duplicates"] = True
    return client.post(
        "/imports",
        params=params,
        files={"file": ("dupes.csv", csv.encode(), "text/csv")},
    )


def test_import_skips_within_file_duplicates_by_default(client: TestClient) -> None:
    _, account_id = _seed(client)
    csv = (
        "Date,Description,Amount,Type,Category,Purchased By\n"
        "2024-03-01,Coffee Shop,4.50,Sale,Food & Drink,John\n"
        "2024-03-01,Coffee Shop,4.50,Sale,Food & Drink,John\n"
        "2024-03-02,Refund,12.00,Return,Shopping,John\n"
    )
    response = _import_csv(client, account_id, csv)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inserted"] == 2
    assert body["duplicates_skipped"] == 1


def test_import_allow_duplicates_inserts_identical_rows(client: TestClient) -> None:
    _, account_id = _seed(client)
    csv = (
        "Date,Description,Amount,Type,Category,Purchased By\n"
        "2024-03-01,Coffee Shop,4.50,Sale,Food & Drink,John\n"
        "2024-03-01,Coffee Shop,4.50,Sale,Food & Drink,John\n"
        "2024-03-02,Refund,12.00,Return,Shopping,John\n"
    )
    first = _import_csv(client, account_id, csv, allow_duplicates=True)
    assert first.status_code == 200, first.text
    assert first.json()["inserted"] == 3
    assert first.json()["duplicates_skipped"] == 0

    coffees = [
        row
        for row in client.get("/transactions", params={"account_id": account_id}).json()
        if row["description"] == "Coffee Shop"
    ]
    assert len(coffees) == 2

    replay = _import_csv(client, account_id, csv, allow_duplicates=True)
    assert replay.status_code == 200
    assert replay.json()["inserted"] == 0
    assert replay.json()["duplicates_skipped"] == 3


def test_import_unknown_account(client: TestClient) -> None:
    response = _import(client, account_id=99)
    assert response.status_code == 404
