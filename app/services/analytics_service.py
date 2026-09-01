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
from app.models import Account, Owner, Transaction, effective_category, effective_merchant

UNASSIGNED = "(unassigned)"
GroupBy = Literal["category", "owner", "month", "account", "merchant"]


def _resolved_date_to(date_to: date | None) -> date:
    return date_to if date_to is not None else date.today()


def _amount_expr(spend_only: bool) -> ColumnElement:
    if spend_only:
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
    if spend_only:
        stmt = stmt.where(Transaction.transaction_type == TransactionType.SPEND.value)
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
) -> list[dict[str, Any]]:
    amount = _amount_expr(spend_only)
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
        stmt, date_from, date_to, account_id, owner_id, spend_only, merchant
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


def get_total(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    spend_only: bool = True,
    merchant: str | None = None,
) -> dict[str, Any]:
    amount = _amount_expr(spend_only)
    stmt = select(
        func.coalesce(func.sum(amount), 0),
        func.count(Transaction.id),
    ).select_from(Transaction)
    stmt = _apply_filters(
        stmt, date_from, date_to, account_id, owner_id, spend_only, merchant
    )
    total, count = db.execute(stmt).one()
    total_dec = _as_decimal(total)
    count_int = int(count)
    if count_int == 0:
        average = Decimal("0.00")
    else:
        average = (total_dec / count_int).quantize(Decimal("0.01"))
    return {"total": total_dec, "count": count_int, "average": average}


def top_merchants(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    spend_only: bool = True,
    limit: int = 10,
    merchant: str | None = None,
) -> list[dict[str, Any]]:
    amount = _amount_expr(spend_only)
    total_col = func.coalesce(func.sum(amount), 0)
    count_col = func.count(Transaction.id)
    key = func.coalesce(effective_merchant, UNASSIGNED)
    stmt = select(key, total_col, count_col).select_from(Transaction)
    stmt = _apply_filters(
        stmt, date_from, date_to, account_id, owner_id, spend_only, merchant
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
) -> list[Transaction]:
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt, date_from, date_to, account_id, owner_id, spend_only, merchant
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


def unmapped_summary(db: Session) -> dict[str, list[str]]:
    types = db.scalars(
        select(Transaction.raw_type)
        .where(
            Transaction.transaction_type == TransactionType.UNKNOWN.value,
            Transaction.raw_type.isnot(None),
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
