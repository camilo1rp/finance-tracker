"""
Read-side aggregation over persisted transactions.
Kept separate from ingest_service since it has no write concerns and a
different set of likely future changes (new groupings, date bucketing, etc).
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.domain.classification import TransactionType
from app.models import Account, Owner, Transaction, effective_category, effective_merchant, effective_type

UNASSIGNED = "(unassigned)"
GroupBy = Literal["category", "owner", "month", "account", "merchant"]

_KNOWN_CASH_FLOW_TYPES = frozenset(
    {
        TransactionType.SPEND.value,
        TransactionType.INCOME.value,
        TransactionType.REFUND.value,
        TransactionType.FEE.value,
        TransactionType.TRANSFER.value,
    }
)


def _resolved_date_to(date_to: date | None) -> date:
    return date_to if date_to is not None else date.today()


def _apply_filters(
    stmt: Select[Any],
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> Select[Any]:
    stmt = stmt.where(Transaction.transaction_date <= _resolved_date_to(date_to))
    if date_from is not None:
        stmt = stmt.where(Transaction.transaction_date >= date_from)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if owner_id is not None:
        stmt = stmt.where(Transaction.owner_id == owner_id)
    if merchant is not None:
        stmt = stmt.where(effective_merchant == merchant)
    if transaction_type is not None:
        stmt = stmt.where(effective_type == transaction_type)
    return stmt


def _month_bucket(db: Session) -> ColumnElement:
    dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
    if dialect == "sqlite":
        return func.strftime("%Y-%m", Transaction.transaction_date)
    return func.to_char(Transaction.transaction_date, "YYYY-MM")


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("0.01"))


def _group_value(value: Any) -> str:
    if value is None or value == "":
        return UNASSIGNED
    return str(value)


def _type_bucket(by_type: list[dict[str, Any]], transaction_type: str) -> dict[str, Any]:
    for row in by_type:
        if row["transaction_type"] == transaction_type:
            return row
    return {"transaction_type": transaction_type, "total": Decimal("0.00"), "count": 0}


def _derive_totals(
    by_type: list[dict[str, Any]],
    transaction_type: str | None = None,
) -> dict[str, Any]:
    purchases = _type_bucket(by_type, TransactionType.SPEND.value)
    refunds = _type_bucket(by_type, TransactionType.REFUND.value)
    income = _type_bucket(by_type, TransactionType.INCOME.value)
    fees = _type_bucket(by_type, TransactionType.FEE.value)
    spend = (purchases["total"] - refunds["total"]).quantize(Decimal("0.01"))
    net_cash_flow = (
        income["total"] + refunds["total"] - purchases["total"] - fees["total"]
    ).quantize(Decimal("0.01"))

    if transaction_type is not None:
        matched = _type_bucket(by_type, transaction_type)
        total_dec = matched["total"]
        count_int = matched["count"]
    else:
        total_dec = spend
        count_int = purchases["count"]

    if count_int == 0:
        average = Decimal("0.00")
    else:
        average = (total_dec / count_int).quantize(Decimal("0.01"))

    return {
        "by_type": by_type,
        "purchases": purchases["total"],
        "refunds": refunds["total"],
        "spend": spend,
        "net_cash_flow": net_cash_flow,
        "total": total_dec,
        "count": count_int,
        "average": average,
    }


def _cash_flow_extras(by_type: list[dict[str, Any]]) -> dict[str, Any]:
    income = _type_bucket(by_type, TransactionType.INCOME.value)["total"]
    fees = _type_bucket(by_type, TransactionType.FEE.value)["total"]
    transfers = _type_bucket(by_type, TransactionType.TRANSFER.value)["total"]
    other_total = Decimal("0.00")
    other_count = 0
    for row in by_type:
        if row["transaction_type"] not in _KNOWN_CASH_FLOW_TYPES:
            other_total += row["total"]
            other_count += row["count"]
    return {
        "income": income,
        "fees": fees,
        "transfers": transfers,
        "other": other_total.quantize(Decimal("0.01")),
        "other_count": other_count,
    }


def _account_sign_convention(db: Session, account_id: int | None) -> str | None:
    if account_id is None:
        return None
    account = db.get(Account, account_id)
    if account is None:
        return None
    mapping = account.default_mapping or {}
    sign = mapping.get("sign_convention")
    if not sign and not mapping.get("type_col"):
        return "negative_is_spend"
    return sign


def _attach_sign_convention(
    payload: dict[str, Any],
    db: Session,
    account_id: int | None,
) -> dict[str, Any]:
    payload["sign_convention"] = _account_sign_convention(db, account_id)
    return payload


def _group_key(db: Session, group_by: GroupBy) -> ColumnElement:
    if group_by == "category":
        return func.coalesce(effective_category, UNASSIGNED)
    if group_by == "owner":
        return func.coalesce(Owner.name, UNASSIGNED)
    if group_by == "account":
        return Account.name
    if group_by == "month":
        return _month_bucket(db)
    if group_by == "merchant":
        return func.coalesce(effective_merchant, UNASSIGNED)
    raise ValueError(f"unsupported group_by: {group_by}")


def _totals_by_type(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None,
    *,
    group_by: GroupBy | None = None,
    description_query: str | None = None,
) -> list[dict[str, Any]]:
    total_col = func.coalesce(func.sum(func.abs(Transaction.amount)), 0)
    count_col = func.count(Transaction.id)

    if group_by is None:
        stmt = select(
            effective_type,
            total_col,
            count_col,
        ).select_from(Transaction)
        stmt = _apply_filters(
            stmt,
            date_from,
            date_to,
            account_id,
            owner_id,
            merchant=merchant,
        )
        if description_query is not None:
            stmt = stmt.where(Transaction.description.ilike(f"%{description_query}%"))
        stmt = stmt.group_by(effective_type).order_by(effective_type)
        return [
            {
                "transaction_type": str(txn_type),
                "total": _as_decimal(total),
                "count": int(count),
            }
            for txn_type, total, count in db.execute(stmt).all()
        ]

    key = _group_key(db, group_by)
    if group_by == "owner":
        stmt = (
            select(key, effective_type, total_col, count_col)
            .select_from(Transaction)
            .outerjoin(Owner, Transaction.owner_id == Owner.id)
        )
    elif group_by == "account":
        stmt = (
            select(key, effective_type, total_col, count_col)
            .select_from(Transaction)
            .join(Account, Transaction.account_id == Account.id)
        )
    else:
        stmt = select(key, effective_type, total_col, count_col).select_from(Transaction)

    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant=merchant,
    )
    if description_query is not None:
        stmt = stmt.where(Transaction.description.ilike(f"%{description_query}%"))

    stmt = stmt.group_by(key, effective_type).order_by(key, effective_type)
    return [
        {
            "group_value": _group_value(group_value),
            "transaction_type": str(txn_type),
            "total": _as_decimal(total),
            "count": int(count),
        }
        for group_value, txn_type, total, count in db.execute(stmt).all()
    ]


def _summarize_grouped_rows(
    rows: list[dict[str, Any]],
    transaction_type: str | None,
    group_by: GroupBy,
    db: Session,
    account_id: int | None,
) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[row["group_value"]].append(
            {
                "transaction_type": row["transaction_type"],
                "total": row["total"],
                "count": row["count"],
            }
        )

    results: list[dict[str, Any]] = []
    for group_value, type_rows in by_group.items():
        derived = _derive_totals(type_rows, transaction_type)
        derived["group_value"] = group_value
        _attach_sign_convention(derived, db, account_id)
        results.append(derived)

    if group_by == "month":
        results.sort(key=lambda row: row["group_value"])
    else:
        results.sort(key=lambda row: (-row["spend"], row["group_value"]))
    return results


def summarize(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    group_by: GroupBy,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> list[dict[str, Any]]:
    rows = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        group_by=group_by,
    )
    return _summarize_grouped_rows(rows, transaction_type, group_by, db, account_id)


def get_total(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> dict[str, Any]:
    by_type = _totals_by_type(
        db, date_from, date_to, account_id, owner_id, merchant
    )
    payload = _derive_totals(by_type, transaction_type)
    return _attach_sign_convention(payload, db, account_id)


def top_merchants(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    limit: int = 10,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> list[dict[str, Any]]:
    rows = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        group_by="merchant",
    )
    grouped = _summarize_grouped_rows(
        rows, transaction_type, "merchant", db, account_id
    )
    limited = grouped[:limit]
    return [
        {**row, "merchant": row.pop("group_value")}
        for row in limited
    ]


def largest_transactions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    limit: int = 10,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> dict[str, Any]:
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        transaction_type,
    )
    stmt = stmt.order_by(func.abs(Transaction.amount).desc(), Transaction.id).limit(limit)
    transactions = list(db.scalars(stmt).all())

    by_type = _totals_by_type(
        db, date_from, date_to, account_id, owner_id, merchant
    )
    totals = _derive_totals(by_type, transaction_type)
    _attach_sign_convention(totals, db, account_id)
    return {"totals": totals, "transactions": transactions}


def search_transactions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    query: str,
    limit: int = 50,
    merchant: str | None = None,
) -> dict[str, Any]:
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant=merchant,
    )
    stmt = stmt.where(Transaction.description.ilike(f"%{query}%"))
    stmt = stmt.order_by(Transaction.transaction_date, Transaction.id).limit(limit)
    transactions = list(db.scalars(stmt).all())

    by_type = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        description_query=query,
    )
    totals = _derive_totals(by_type)
    _attach_sign_convention(totals, db, account_id)
    return {"totals": totals, "transactions": transactions}


def cash_flow(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None = None,
) -> dict[str, Any]:
    """
    Household cash-flow buckets by effective transaction type.

    net_cash_flow = income + refunds - purchases - fees. Excludes transfers
    and other (ADJUSTMENT, UNKNOWN, legacy PAYMENT). Depository outflows that
    fund card payments may appear as SPEND until overridden — prefer
    account_id for a single-account view.
    """
    by_type = _totals_by_type(
        db, date_from, date_to, account_id, owner_id, merchant
    )
    payload = _derive_totals(by_type)
    payload.update(_cash_flow_extras(by_type))
    return _attach_sign_convention(payload, db, account_id)


def unmapped_summary(db: Session) -> dict[str, list[str]]:
    types = db.scalars(
        select(Transaction.raw_type)
        .where(
            effective_type == TransactionType.UNKNOWN.value,
            Transaction.raw_type.isnot(None),
            Transaction.type_override.is_(None),
        )
        .distinct()
        .order_by(Transaction.raw_type)
    ).all()
    categories = db.scalars(
        select(Transaction.category_raw)
        .where(
            Transaction.category_override.is_(None),
            Transaction.category_normalized.is_(None),
            Transaction.category_raw.isnot(None),
        )
        .distinct()
        .order_by(Transaction.category_raw)
    ).all()
    owners = db.scalars(
        select(Transaction.owner_raw)
        .where(
            Transaction.owner_id.is_(None),
            Transaction.owner_raw.isnot(None),
        )
        .distinct()
        .order_by(Transaction.owner_raw)
    ).all()
    merchants = db.scalars(
        select(Transaction.merchant_raw)
        .where(
            Transaction.merchant_override.is_(None),
            Transaction.merchant_normalized.is_(None),
            Transaction.merchant_raw.isnot(None),
        )
        .distinct()
        .order_by(Transaction.merchant_raw)
    ).all()
    merchants_without_category = db.scalars(
        select(effective_merchant)
        .where(
            Transaction.category_override.is_(None),
            Transaction.category_normalized.is_(None),
            or_(
                Transaction.category_raw.is_(None),
                func.trim(Transaction.category_raw) == "",
            ),
            effective_merchant.isnot(None),
        )
        .distinct()
        .order_by(effective_merchant)
    ).all()
    return {
        "transaction_types": list(types),
        "categories": list(categories),
        "owners": list(owners),
        "merchants": list(merchants),
        "merchants_without_category": list(merchants_without_category),
    }
