"""
Read-side aggregation over persisted transactions.
Kept separate from ingest_service since it has no write concerns and a
different set of likely future changes (new groupings, date bucketing, etc).
"""
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.domain.classification import TransactionType
from app.models import Account, Owner, Transaction, effective_category, effective_merchant, effective_type

UNASSIGNED = "(unassigned)"
GroupBy = Literal["category", "owner", "month", "account", "merchant"]

_NET_TYPES = frozenset(
    {
        TransactionType.SPEND.value,
        TransactionType.INCOME.value,
        TransactionType.REFUND.value,
        TransactionType.FEE.value,
    }
)
_OTHER_TYPES = frozenset(
    {
        TransactionType.ADJUSTMENT.value,
        TransactionType.UNKNOWN.value,
    }
)


def _resolved_date_to(date_to: date | None) -> date:
    return date_to if date_to is not None else date.today()


def _amount_expr(spend_only: bool, transaction_type: str | None) -> ColumnElement:
    if transaction_type is not None or spend_only:
        return func.abs(Transaction.amount)
    return Transaction.amount


def _apply_filters(
    stmt: Select[Any],
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    spend_only: bool | None,
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
    elif spend_only:
        stmt = stmt.where(effective_type == TransactionType.SPEND.value)
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


def summarize(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    group_by: GroupBy,
    spend_only: bool = True,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> list[dict[str, Any]]:
    amount = _amount_expr(spend_only, transaction_type)
    total_col = func.coalesce(func.sum(amount), 0)
    count_col = func.count(Transaction.id)

    if group_by == "category":
        key = func.coalesce(effective_category, UNASSIGNED)
        stmt = select(key, total_col, count_col).select_from(Transaction)
    elif group_by == "owner":
        key = func.coalesce(Owner.name, UNASSIGNED)
        stmt = (
            select(key, total_col, count_col)
            .select_from(Transaction)
            .outerjoin(Owner, Transaction.owner_id == Owner.id)
        )
    elif group_by == "account":
        key = Account.name
        stmt = (
            select(key, total_col, count_col)
            .select_from(Transaction)
            .join(Account, Transaction.account_id == Account.id)
        )
    elif group_by == "month":
        key = _month_bucket(db)
        stmt = select(key, total_col, count_col).select_from(Transaction)
    elif group_by == "merchant":
        key = func.coalesce(effective_merchant, UNASSIGNED)
        stmt = select(key, total_col, count_col).select_from(Transaction)
    else:
        raise ValueError(f"unsupported group_by: {group_by}")

    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        spend_only,
        merchant,
        transaction_type,
    )
    stmt = stmt.group_by(key)
    if group_by == "month":
        stmt = stmt.order_by(key)
    else:
        stmt = stmt.order_by(total_col.desc(), key)

    rows = db.execute(stmt).all()
    return [
        {
            "group_value": _group_value(group_value),
            "total": _as_decimal(total),
            "count": int(count),
        }
        for group_value, total, count in rows
    ]


def _type_bucket(by_type: list[dict[str, Any]], transaction_type: str) -> dict[str, Any]:
    for row in by_type:
        if row["transaction_type"] == transaction_type:
            return row
    return {"transaction_type": transaction_type, "total": Decimal("0.00"), "count": 0}


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


def _totals_by_type(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None,
) -> list[dict[str, Any]]:
    total_col = func.coalesce(func.sum(func.abs(Transaction.amount)), 0)
    stmt = select(
        effective_type,
        total_col,
        func.count(Transaction.id),
    ).select_from(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        spend_only=False,
        merchant=merchant,
    )
    stmt = stmt.group_by(effective_type).order_by(effective_type)
    return [
        {
            "transaction_type": str(txn_type),
            "total": _as_decimal(total),
            "count": int(count),
        }
        for txn_type, total, count in db.execute(stmt).all()
    ]


def get_total(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    spend_only: bool = True,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> dict[str, Any]:
    by_type = _totals_by_type(
        db, date_from, date_to, account_id, owner_id, merchant
    )
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
        "sign_convention": _account_sign_convention(db, account_id),
    }


def top_merchants(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    spend_only: bool = True,
    limit: int = 10,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> list[dict[str, Any]]:
    amount = _amount_expr(spend_only, transaction_type)
    total_col = func.coalesce(func.sum(amount), 0)
    count_col = func.count(Transaction.id)
    key = func.coalesce(effective_merchant, UNASSIGNED)
    stmt = select(key, total_col, count_col).select_from(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        spend_only,
        merchant,
        transaction_type,
    )
    stmt = stmt.group_by(key).order_by(total_col.desc(), key).limit(limit)
    return [
        {
            "merchant": _group_value(name),
            "total": _as_decimal(total),
            "count": int(count),
        }
        for name, total, count in db.execute(stmt).all()
    ]


def largest_transactions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    spend_only: bool = True,
    limit: int = 10,
    merchant: str | None = None,
    transaction_type: str | None = None,
) -> list[Transaction]:
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        spend_only,
        merchant,
        transaction_type,
    )
    stmt = stmt.order_by(func.abs(Transaction.amount).desc(), Transaction.id).limit(limit)
    return list(db.scalars(stmt).all())


def search_transactions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    query: str,
    limit: int = 50,
    merchant: str | None = None,
) -> list[Transaction]:
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        spend_only=False,
        merchant=merchant,
    )
    stmt = stmt.where(Transaction.description.ilike(f"%{query}%"))
    stmt = stmt.order_by(Transaction.transaction_date, Transaction.id).limit(limit)
    return list(db.scalars(stmt).all())


def _sum_by_effective_type(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None,
    type_value: str,
) -> tuple[Decimal, int]:
    stmt = select(
        func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
        func.count(Transaction.id),
    ).select_from(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        spend_only=False,
        merchant=merchant,
    )
    stmt = stmt.where(effective_type == type_value)
    total, count = db.execute(stmt).one()
    return _as_decimal(total), int(count)


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

    net = income + refunds - spend - fees. Excludes transfers and other
    (ADJUSTMENT, UNKNOWN, legacy PAYMENT). Depository outflows that fund
    card payments may appear as SPEND until overridden — prefer account_id
    for a single-account view.
    """
    spend, _ = _sum_by_effective_type(
        db, date_from, date_to, account_id, owner_id, merchant, TransactionType.SPEND.value
    )
    income, _ = _sum_by_effective_type(
        db, date_from, date_to, account_id, owner_id, merchant, TransactionType.INCOME.value
    )
    refunds, _ = _sum_by_effective_type(
        db, date_from, date_to, account_id, owner_id, merchant, TransactionType.REFUND.value
    )
    fees, _ = _sum_by_effective_type(
        db, date_from, date_to, account_id, owner_id, merchant, TransactionType.FEE.value
    )
    transfers, _ = _sum_by_effective_type(
        db, date_from, date_to, account_id, owner_id, merchant, TransactionType.TRANSFER.value
    )

    stmt = select(
        func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
        func.count(Transaction.id),
    ).select_from(Transaction)
    stmt = _apply_filters(
        stmt,
        date_from,
        date_to,
        account_id,
        owner_id,
        spend_only=False,
        merchant=merchant,
    )
    known = {
        TransactionType.SPEND.value,
        TransactionType.INCOME.value,
        TransactionType.REFUND.value,
        TransactionType.FEE.value,
        TransactionType.TRANSFER.value,
    }
    stmt = stmt.where(effective_type.notin_(known))
    other_total, other_count = db.execute(stmt).one()

    net = (income + refunds - spend - fees).quantize(Decimal("0.01"))
    return {
        "spend": spend,
        "income": income,
        "refunds": refunds,
        "fees": fees,
        "transfers": transfers,
        "other": _as_decimal(other_total),
        "net": net,
        "other_count": int(other_count),
    }


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
    return {
        "transaction_types": list(types),
        "categories": list(categories),
        "owners": list(owners),
        "merchants": list(merchants),
    }
