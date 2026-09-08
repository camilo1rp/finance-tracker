from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, AnalysisArtifact, Owner, Transaction
from app.services import artifact_service
from tests.agent.helpers import seed_coffee


def test_artifacts_crud_and_derive(client: TestClient, db_session: Session) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()

    # Pre-seed an artifact
    spec = {
        "tool": "summarize",
        "kwargs": {
            "group_by": "category",
            "date_from": "2024-06-01",
            "date_to": "2024-06-30",
        },
    }
    res = artifact_service.execute_spec(db_session, "summarize", spec["kwargs"])
    art_id, _ = artifact_service.persist_artifact(
        db_session,
        thread_id="thread-web-1",
        kind="group_summary",
        title="July spend by category",
        spec=spec,
        result=res,
    )

    # 1. GET /artifacts
    resp = client.get("/artifacts?thread_id=thread-web-1")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == art_id
    assert items[0]["title"] == "July spend by category"

    # 2. GET /artifacts/{id}
    resp = client.get(f"/artifacts/{art_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == art_id
    assert data["spec"]["tool"] == "summarize"
    assert "digest" in data

    # 3. GET /artifacts/{id}/rows
    resp = client.get(f"/artifacts/{art_id}/rows")
    assert resp.status_code == 200
    rows_data = resp.json()
    assert "groups" in rows_data
    assert len(rows_data["groups"]) >= 1

    # 4. POST /artifacts/{id}/derive
    resp = client.post(
        f"/artifacts/{art_id}/derive",
        json={
            "mutations": {"group_by": "subcategory"},
            "title": "July spend by subcategory",
        },
    )
    assert resp.status_code == 201
    derived = resp.json()
    assert derived["id"] != art_id
    assert derived["derived_from"] == art_id
    assert derived["spec"]["kwargs"]["group_by"] == "subcategory"

    # 5. DELETE /artifacts/{id}
    resp = client.delete(f"/artifacts/{art_id}")
    assert resp.status_code == 204

    # Verify status is expired
    art = db_session.get(AnalysisArtifact, art_id)
    assert art.status == "expired"


def test_artifacts_error_handling(client: TestClient) -> None:
    # 404 on missing
    resp = client.get("/artifacts/999999")
    assert resp.status_code == 404

    resp = client.get("/artifacts/999999/rows")
    assert resp.status_code == 404

    resp = client.post("/artifacts/999999/derive", json={"mutations": {}})
    assert resp.status_code == 404

    resp = client.delete("/artifacts/999999")
    assert resp.status_code == 404


def test_artifacts_rows_paging_and_sorting(client: TestClient, db_session: Session) -> None:
    seed_coffee(db_session)
    account = db_session.scalars(select(Account)).first()
    db_session.add_all([
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 2),
            description="TEA",
            amount=Decimal("3.00"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Food & Drink",
            dedupe_hash="test-tea",
            raw={},
        ),
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 3),
            description="BAGEL",
            amount=Decimal("8.00"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Food & Drink",
            dedupe_hash="test-bagel",
            raw={},
        ),
    ])
    db_session.commit()

    spec = {
        "tool": "list_transactions",
        "kwargs": {
            "date_from": "2024-06-01",
            "date_to": "2024-06-30",
        },
    }
    res = artifact_service.execute_spec(db_session, "list_transactions", spec["kwargs"])
    art_id, _ = artifact_service.persist_artifact(
        db_session,
        thread_id="test-paging-thread",
        kind="transaction_list",
        title="June transactions",
        spec=spec,
        result=res,
    )

    # 1. Page with limit=1, offset=0
    resp1 = client.get(f"/artifacts/{art_id}/rows?limit=1&offset=0")
    assert resp1.status_code == 200
    txns1 = resp1.json()["transactions"]
    assert len(txns1) == 1

    # 2. Page with limit=1, offset=1
    resp2 = client.get(f"/artifacts/{art_id}/rows?limit=1&offset=1")
    assert resp2.status_code == 200
    txns2 = resp2.json()["transactions"]
    assert len(txns2) == 1
    assert txns1[0]["id"] != txns2[0]["id"]

    # 3. Sort by amount:desc
    resp3 = client.get(f"/artifacts/{art_id}/rows?limit=5&sort=amount:desc")
    assert resp3.status_code == 200
    txns3 = resp3.json()["transactions"]
    amounts = [float(t["amount"]) for t in txns3]
    assert amounts == sorted(amounts, reverse=True)

