from fastapi.testclient import TestClient

MAPPING = {
    "date_col": "Date",
    "description_col": "Description",
    "amount_col": "Amount",
    "type_col": "Type",
}


def test_create_and_list_accounts(client: TestClient) -> None:
    owner = client.post("/owners", json={"name": "Pat"}).json()
    created = client.post(
        "/accounts",
        json={
            "name": "Chase Sapphire",
            "last4": "1234",
            "account_kind": "credit_card",
            "default_owner_id": owner["id"],
            "default_mapping": MAPPING,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Chase Sapphire"
    assert body["last4"] == "1234"
    assert body["account_kind"] == "credit_card"
    assert body["default_owner_id"] == owner["id"]
    assert body["source_format"] == "csv"

    listed = client.get("/accounts")
    assert listed.status_code == 200
    assert listed.json() == [body]


def test_create_account_unknown_owner(client: TestClient) -> None:
    response = client.post(
        "/accounts",
        json={
            "name": "Card",
            "last4": "0000",
            "account_kind": "credit_card",
            "default_owner_id": 99,
            "default_mapping": MAPPING,
        },
    )
    assert response.status_code == 404


def test_create_account_requires_account_kind(client: TestClient) -> None:
    response = client.post(
        "/accounts",
        json={
            "name": "Card",
            "last4": "0000",
            "default_mapping": MAPPING,
        },
    )
    assert response.status_code == 422


def test_create_account_rejects_mapping_without_type_or_sign(client: TestClient) -> None:
    response = client.post(
        "/accounts",
        json={
            "name": "Card",
            "last4": "0000",
            "account_kind": "depository",
            "default_mapping": {
                "date_col": "Date",
                "description_col": "Description",
                "amount_col": "Amount",
            },
        },
    )
    assert response.status_code == 422
