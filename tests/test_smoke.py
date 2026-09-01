from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_session_creates_tables(db_session) -> None:
    from sqlalchemy import inspect

    from tests.conftest import engine

    tables = inspect(engine).get_table_names()
    assert "owners" in tables
    assert "accounts" in tables
    assert "transactions" in tables
    assert "import_batches" in tables
    assert "normalization_mappings" in tables
