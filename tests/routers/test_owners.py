from fastapi.testclient import TestClient


def test_create_and_list_owners(client: TestClient) -> None:
    created = client.post("/owners", json={"name": "John Smith"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "John Smith"
    assert body["id"] >= 1

    listed = client.get("/owners")
    assert listed.status_code == 200
    assert listed.json() == [body]


def test_create_owner_conflict(client: TestClient) -> None:
    assert client.post("/owners", json={"name": "Pat"}).status_code == 201
    conflict = client.post("/owners", json={"name": "Pat"})
    assert conflict.status_code == 409
