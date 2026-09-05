from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Account, Owner, Transaction
from app.services.analytics_service import get_total, summarize, unmapped_summary


def _hash(suffix: str) -> str:
    return f"hash-{suffix}"


def _seed_account(db: Session, name: str = "Card") -> tuple[Owner, Account]:
    owner = Owner(name=f"Owner-{name}")
    db.add(owner)
    db.flush()
    account = Account(
        name=name,
        last4="0000",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
        },
    )
    db.add(account)
    db.commit()
    db.refresh(owner)
    db.refresh(account)
    return owner, account


def _add_txn(db: Session, account_id: int, suffix: str, **kwargs) -> Transaction:
    values = {
        "account_id": account_id,
        "transaction_date": date(2024, 6, 1),
        "description": suffix,
        "amount": Decimal("10.00"),
        "transaction_type": "SPEND",
        "is_spend": True,
        "dedupe_hash": _hash(suffix),
        "raw": {},
    }
    values.update(kwargs)
    txn = Transaction(**values)
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def test_summarize_category_coalesce_override_wins(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "override",
        category_raw="Raw",
        category_normalized="Normalized",
        category_override="Override",
        amount=Decimal("5.00"),
    )
    _add_txn(
        db_session,
        account.id,
        "normalized",
        category_raw="Raw",
        category_normalized="Normalized",
        amount=Decimal("7.00"),
    )
    _add_txn(
        db_session,
        account.id,
        "raw-only",
        category_raw="RawOnly",
        amount=Decimal("3.00"),
    )
    rows = summarize(db_session, None, None, None, None, "category")
    by_name = {row["group_value"]: row for row in rows}
    assert by_name["Override"]["total"] == Decimal("5.00")
    assert by_name["Normalized"]["total"] == Decimal("7.00")
    assert by_name["RawOnly"]["total"] == Decimal("3.00")


def test_spend_only_excludes_payments_and_refunds(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(db_session, account.id, "spend", amount=Decimal("10.00"))
    _add_txn(
        db_session,
        account.id,
        "pay",
        amount=Decimal("100.00"),
        transaction_type="INCOME",
        is_spend=False,
    )
    _add_txn(
        db_session,
        account.id,
        "refund",
        amount=Decimal("4.00"),
        transaction_type="REFUND",
        is_spend=False,
    )
    spend = get_total(db_session, None, None, None, None, spend_only=True)
    assert spend["purchases"] == Decimal("10.00")
    assert spend["refunds"] == Decimal("4.00")
    assert spend["spend"] == Decimal("6.00")
    assert spend["total"] == Decimal("6.00")
    assert spend["count"] == 1
    assert spend["net_cash_flow"] == Decimal("94.00")
    all_types = get_total(db_session, None, None, None, None, spend_only=False)
    assert all_types["spend"] == Decimal("6.00")
    assert all_types["net_cash_flow"] == Decimal("94.00")
    assert {row["transaction_type"] for row in all_types["by_type"]} == {
        "SPEND",
        "INCOME",
        "REFUND",
    }


def test_mixed_sign_spends_use_abs(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "chase",
        amount=Decimal("12.45"),
        category_normalized="Dining",
    )
    _add_txn(
        db_session,
        account.id,
        "debit",
        amount=Decimal("-54.32"),
        category_normalized="Dining",
    )
    rows = summarize(db_session, None, None, None, None, "category")
    assert rows == [
        {"group_value": "Dining", "total": Decimal("66.77"), "count": 2}
    ]


def test_group_by_month_yyyy_mm(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(db_session, account.id, "june", transaction_date=date(2024, 6, 15))
    _add_txn(db_session, account.id, "july", transaction_date=date(2024, 7, 1))
    rows = summarize(db_session, None, None, None, None, "month")
    assert [row["group_value"] for row in rows] == ["2024-06", "2024-07"]


def test_unmapped_lists_raw_values(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "unknown",
        transaction_type="UNKNOWN",
        is_spend=False,
        raw_type="Fee",
        category_raw="Mystery Category",
        owner_raw="Jane",
        merchant_raw="UNKNOWN MART",
    )
    summary = unmapped_summary(db_session)
    assert summary["transaction_types"] == ["Fee"]
    assert summary["categories"] == ["Mystery Category"]
    assert summary["owners"] == ["Jane"]
    assert summary["merchants"] == ["UNKNOWN MART"]


def test_get_total_average_and_zero_rows(db_session: Session) -> None:
    empty = get_total(db_session, None, None, None, None)
    assert empty["total"] == Decimal("0.00")
    assert empty["count"] == 0
    assert empty["average"] == Decimal("0.00")
    assert empty["by_type"] == []
    assert empty["purchases"] == Decimal("0.00")
    assert empty["refunds"] == Decimal("0.00")
    assert empty["spend"] == Decimal("0.00")
    assert empty["net_cash_flow"] == Decimal("0.00")

    _, account = _seed_account(db_session)
    _add_txn(db_session, account.id, "a", amount=Decimal("10.00"))
    _add_txn(db_session, account.id, "b", amount=Decimal("20.00"))
    totals = get_total(db_session, None, None, None, None)
    assert totals["purchases"] == Decimal("30.00")
    assert totals["refunds"] == Decimal("0.00")
    assert totals["spend"] == Decimal("30.00")
    assert totals["net_cash_flow"] == Decimal("-30.00")
    assert totals["total"] == Decimal("30.00")
    assert totals["count"] == 2
    assert totals["average"] == Decimal("15.00")
    assert totals["by_type"] == [
        {"transaction_type": "SPEND", "total": Decimal("30.00"), "count": 2}
    ]


def test_get_total_by_type_and_sign_convention(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    account.default_mapping = {
        **account.default_mapping,
        "sign_convention": "negative_is_spend",
    }
    db_session.commit()

    _add_txn(db_session, account.id, "spend-neg", amount=Decimal("-10.00"))
    _add_txn(
        db_session,
        account.id,
        "income",
        amount=Decimal("100.00"),
        transaction_type="INCOME",
        is_spend=False,
    )
    _add_txn(
        db_session,
        account.id,
        "refund",
        amount=Decimal("5.00"),
        transaction_type="REFUND",
        is_spend=False,
    )

    body = get_total(db_session, None, None, account.id, None)
    assert body["purchases"] == Decimal("10.00")
    assert body["refunds"] == Decimal("5.00")
    assert body["spend"] == Decimal("5.00")
    assert body["net_cash_flow"] == Decimal("95.00")
    assert body["total"] == Decimal("5.00")
    assert body["count"] == 1
    assert body["sign_convention"] == "negative_is_spend"
    assert body["by_type"] == [
        {"transaction_type": "INCOME", "total": Decimal("100.00"), "count": 1},
        {"transaction_type": "REFUND", "total": Decimal("5.00"), "count": 1},
        {"transaction_type": "SPEND", "total": Decimal("10.00"), "count": 1},
    ]


def test_invalid_group_by_is_422(client: TestClient) -> None:
    response = client.get("/analytics/summary", params={"group_by": "nope"})
    assert response.status_code == 422


def test_search_is_case_insensitive_and_includes_all_types(
    client: TestClient, db_session: Session
) -> None:
    _, account = _seed_account(db_session)
    _add_txn(db_session, account.id, "AMAZON MARKETPLACE", description="AMAZON MARKETPLACE")
    _add_txn(
        db_session,
        account.id,
        "amazon refund",
        description="amazon refund",
        transaction_type="REFUND",
        is_spend=False,
    )
    response = client.get("/analytics/search", params={"query": "AmAzOn"})
    assert response.status_code == 200
    descriptions = {row["description"] for row in response.json()}
    assert descriptions == {"AMAZON MARKETPLACE", "amazon refund"}


def test_largest_orders_by_absolute_amount(
    client: TestClient, db_session: Session
) -> None:
    _, account = _seed_account(db_session)
    _add_txn(db_session, account.id, "small", amount=Decimal("12.45"))
    _add_txn(db_session, account.id, "neg-big", amount=Decimal("-54.32"))
    response = client.get("/analytics/largest", params={"limit": 2})
    assert response.status_code == 200
    amounts = [Decimal(row["amount"]) for row in response.json()]
    assert amounts == [Decimal("-54.32"), Decimal("12.45")]


def test_by_category_alias_matches_summary(
    client: TestClient, db_session: Session
) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "coffee",
        category_normalized="Dining",
        amount=Decimal("4.50"),
    )
    alias = client.get("/analytics/by-category")
    summary = client.get("/analytics/summary", params={"group_by": "category"})
    assert alias.status_code == 200
    assert alias.json() == summary.json()
    assert alias.json()[0]["group_value"] == "Dining"


def test_merchant_filter_and_group_by_use_effective_value(
    client: TestClient, db_session: Session
) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "mapped-a",
        merchant_raw="starbucks store 123",
        merchant_normalized="Starbucks",
        category_normalized="Dining",
        amount=Decimal("12.45"),
    )
    _add_txn(
        db_session,
        account.id,
        "mapped-b",
        merchant_raw="STARBUCKS",
        merchant_normalized="Starbucks",
        category_normalized="Dining",
        amount=Decimal("4.50"),
    )
    _add_txn(
        db_session,
        account.id,
        "raw-only",
        merchant_raw="AMAZON MARKETPLACE",
        category_normalized="Shopping",
        amount=Decimal("45.00"),
    )
    _add_txn(
        db_session,
        account.id,
        "override",
        merchant_raw="LOCAL CAFE",
        merchant_normalized="Local Cafe",
        merchant_override="Starbucks",
        category_normalized="Dining",
        amount=Decimal("3.00"),
    )

    total = client.get("/analytics/total", params={"merchant": "Starbucks"})
    assert total.status_code == 200
    assert Decimal(str(total.json()["total"])) == Decimal("19.95")
    assert total.json()["count"] == 3

    by_cat = client.get(
        "/analytics/summary",
        params={"group_by": "category", "merchant": "Starbucks"},
    )
    assert by_cat.status_code == 200
    assert len(by_cat.json()) == 1
    assert by_cat.json()[0]["group_value"] == "Dining"
    assert Decimal(str(by_cat.json()[0]["total"])) == Decimal("19.95")
    assert by_cat.json()[0]["count"] == 3

    by_merchant = client.get("/analytics/summary", params={"group_by": "merchant"})
    assert by_merchant.status_code == 200
    by_name = {row["group_value"]: row for row in by_merchant.json()}
    assert by_name["Starbucks"]["count"] == 3
    assert Decimal(str(by_name["Starbucks"]["total"])) == Decimal("19.95")
    assert by_name["AMAZON MARKETPLACE"]["count"] == 1

    top = client.get("/analytics/top-merchants")
    assert top.status_code == 200
    assert [row["merchant"] for row in top.json()] == [
        "AMAZON MARKETPLACE",
        "Starbucks",
    ]

    listed = client.get("/transactions", params={"merchant": "AMAZON MARKETPLACE"})
    assert listed.status_code == 200
    assert [row["description"] for row in listed.json()] == ["raw-only"]


def test_identity_merchant_map_not_required(
    client: TestClient, db_session: Session
) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "whole-foods",
        merchant_raw="WHOLE FOODS",
        amount=Decimal("67.20"),
    )
    filtered = client.get("/analytics/total", params={"merchant": "WHOLE FOODS"})
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 1
    grouped = client.get("/analytics/summary", params={"group_by": "merchant"})
    assert grouped.json()[0]["group_value"] == "WHOLE FOODS"
