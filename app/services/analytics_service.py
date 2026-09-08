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

from app.domain.classification import TransactionType, has_wildcard
from app.models import Account, Owner, Transaction, effective_category, effective_merchant, effective_type
from app.services.label_filter import (
    ResolvedLabelFilters,
    apply_resolved_label_filters,
    resolve_label_filters,
)
from app.services.transaction_view import page_meta, transaction_card, transaction_detail

UNASSIGNED = "(unassigned)"
GroupBy = Literal["category", "owner", "month", "account", "merchant", "subcategory"]
ValueDimension = Literal["category", "subcategory", "merchant"]

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


def _resolve_optional_label_filters(
    db: Session,
    category: str | None,
    subcategory: str | None,
) -> ResolvedLabelFilters | None:
    if category is None and subcategory is None:
        return None
    return resolve_label_filters(db, category, subcategory)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("_", "\\_")


def merchant_clause(merchant: str) -> ColumnElement:
    """Exact equality unless ``merchant`` contains ``%`` (SQL ILIKE, ``_`` literal)."""
    if not has_wildcard(merchant):
        return effective_merchant == merchant
    return effective_merchant.ilike(_escape_like(merchant), escape="\\")


def contains_clause(column: ColumnElement, value: str) -> ColumnElement:
    """Case-insensitive contains. A ``%`` in ``value`` is a user wildcard."""
    escaped = _escape_like(value.strip())
    if has_wildcard(value):
        return column.ilike(escaped, escape="\\")
    return column.ilike(f"%{escaped}%", escape="\\")


def text_query_clause(query: str) -> ColumnElement:
    """Substring match across payee, labels, and raw type/owner fields."""
    pattern = f"%{query}%"
    return or_(
        Transaction.description.ilike(pattern),
        effective_merchant.ilike(pattern),
        Transaction.merchant_raw.ilike(pattern),
        Transaction.merchant_normalized.ilike(pattern),
        Transaction.merchant_override.ilike(pattern),
        Transaction.category_raw.ilike(pattern),
        Transaction.category_normalized.ilike(pattern),
        Transaction.category_override.ilike(pattern),
        Transaction.subcategory.ilike(pattern),
        Transaction.raw_type.ilike(pattern),
        Transaction.owner_raw.ilike(pattern),
    )


def _count_rows(db: Session, stmt: Select[Any]) -> int:
    subq = stmt.order_by(None).with_only_columns(Transaction.id).subquery()
    return int(db.scalar(select(func.count()).select_from(subq)) or 0)


def _apply_filters(
    stmt: Select[Any],
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    label_filters: ResolvedLabelFilters | None = None,
    default_date_to: bool = True,
    merchant_contains: bool = False,
    loose_category: str | None = None,
    loose_subcategory: str | None = None,
) -> Select[Any]:
    if default_date_to:
        stmt = stmt.where(Transaction.transaction_date <= _resolved_date_to(date_to))
    elif date_to is not None:
        stmt = stmt.where(Transaction.transaction_date <= date_to)
    if date_from is not None:
        stmt = stmt.where(Transaction.transaction_date >= date_from)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if owner_id is not None:
        stmt = stmt.where(Transaction.owner_id == owner_id)
    if merchant is not None:
        if merchant_contains:
            stmt = stmt.where(contains_clause(effective_merchant, merchant))
        else:
            stmt = stmt.where(merchant_clause(merchant))
    if transaction_type is not None:
        stmt = stmt.where(effective_type == transaction_type)
    if label_filters is not None:
        stmt = apply_resolved_label_filters(stmt, label_filters)
    if loose_category is not None and str(loose_category).strip():
        stmt = stmt.where(contains_clause(effective_category, loose_category))
    if loose_subcategory is not None and str(loose_subcategory).strip():
        stmt = stmt.where(contains_clause(Transaction.subcategory, loose_subcategory))
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
    if group_by == "subcategory":
        return func.coalesce(Transaction.subcategory, UNASSIGNED)
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
    text_query: str | None = None,
    label_filters: ResolvedLabelFilters | None = None,
    merchant_contains: bool = False,
    loose_category: str | None = None,
    loose_subcategory: str | None = None,
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
            db,
            date_from,
            date_to,
            account_id,
            owner_id,
            merchant=merchant,
            label_filters=label_filters,
            merchant_contains=merchant_contains,
            loose_category=loose_category,
            loose_subcategory=loose_subcategory,
        )
        if text_query is not None:
            stmt = stmt.where(text_query_clause(text_query))
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
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant=merchant,
        label_filters=label_filters,
        merchant_contains=merchant_contains,
        loose_category=loose_category,
        loose_subcategory=loose_subcategory,
    )
    if text_query is not None:
        stmt = stmt.where(text_query_clause(text_query))

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
    category: str | None = None,
    subcategory: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    label_filters = _resolve_optional_label_filters(db, category, subcategory)
    rows = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        group_by=group_by,
        label_filters=label_filters,
    )
    groups = _summarize_grouped_rows(rows, transaction_type, group_by, db, account_id)
    match_count = len(groups)
    if limit is not None:
        groups = groups[:limit]
    return {"groups": groups, **page_meta(match_count, len(groups))}


def get_total(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> dict[str, Any]:
    label_filters = _resolve_optional_label_filters(db, category, subcategory)
    by_type = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        label_filters=label_filters,
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
    category: str | None = None,
    subcategory: str | None = None,
) -> list[dict[str, Any]]:
    label_filters = _resolve_optional_label_filters(db, category, subcategory)
    rows = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        group_by="merchant",
        label_filters=label_filters,
    )
    grouped = _summarize_grouped_rows(
        rows, transaction_type, "merchant", db, account_id
    )
    limited = grouped[:limit]
    return [
        {**row, "merchant": row.pop("group_value")}
        for row in limited
    ]


def _parse_sort_order(sort: str | None, default_order: list[Any]) -> list[Any]:
    if not sort or not sort.strip():
        return default_order

    descending = False
    col_str = sort.strip()
    if col_str.startswith("-"):
        descending = True
        col_str = col_str[1:]
    elif ":" in col_str:
        parts = col_str.split(":", 1)
        col_str = parts[0].strip()
        descending = parts[1].strip().lower() == "desc"

    col_map = {
        "date": Transaction.transaction_date,
        "transaction_date": Transaction.transaction_date,
        "amount": Transaction.amount,
        "description": Transaction.description,
        "id": Transaction.id,
    }
    if col_str in col_map:
        c = col_map[col_str]
        order = c.desc() if descending else c.asc()
        return [order, Transaction.id.asc()]

    return default_order


def largest_transactions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    limit: int = 10,
    offset: int = 0,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> dict[str, Any]:
    label_filters = _resolve_optional_label_filters(db, category, subcategory)
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt,
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant=merchant,
        transaction_type=transaction_type,
        label_filters=label_filters,
    )
    match_count = _count_rows(db, stmt)
    stmt = stmt.order_by(func.abs(Transaction.amount).desc(), Transaction.id)
    if offset:
        stmt = stmt.offset(offset)
    stmt = stmt.limit(limit)
    transactions = list(db.scalars(stmt).all())

    by_type = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        label_filters=label_filters,
    )
    totals = _derive_totals(by_type, transaction_type)
    _attach_sign_convention(totals, db, account_id)
    cards = [transaction_card(txn) for txn in transactions]
    return {
        "totals": totals,
        "transactions": cards,
        **page_meta(match_count, len(cards)),
    }


def search_transactions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    query: str,
    limit: int = 50,
    offset: int = 0,
    sort: str | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> dict[str, Any]:
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt,
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant=merchant,
        merchant_contains=True,
        loose_category=category,
        loose_subcategory=subcategory,
    )
    stmt = stmt.where(text_query_clause(query))
    match_count = _count_rows(db, stmt)
    order_clause = _parse_sort_order(sort, [Transaction.transaction_date.asc(), Transaction.id.asc()])
    stmt = stmt.order_by(*order_clause)
    if offset:
        stmt = stmt.offset(offset)
    stmt = stmt.limit(limit)
    transactions = list(db.scalars(stmt).all())

    by_type = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        text_query=query,
        merchant_contains=True,
        loose_category=category,
        loose_subcategory=subcategory,
    )
    totals = _derive_totals(by_type)
    _attach_sign_convention(totals, db, account_id)
    cards = [transaction_card(txn) for txn in transactions]
    return {
        "totals": totals,
        "transactions": cards,
        **page_meta(match_count, len(cards)),
    }


def get_transaction(db: Session, transaction_id: int) -> dict[str, Any] | None:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        return None
    return transaction_detail(txn)


def list_transactions(
    db: Session,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int = 25,
    offset: int = 0,
    sort: str | None = None,
) -> dict[str, Any]:
    """List rows. Does not default date_to. Cards only — use get for triples."""
    label_filters = _resolve_optional_label_filters(db, category, subcategory)
    stmt = select(Transaction)
    stmt = _apply_filters(
        stmt,
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant=merchant,
        label_filters=label_filters,
        default_date_to=False,
    )
    match_count = _count_rows(db, stmt)
    order_clause = _parse_sort_order(sort, [Transaction.transaction_date.asc(), Transaction.id.asc()])
    stmt = stmt.order_by(*order_clause)
    if offset:
        stmt = stmt.offset(offset)
    stmt = stmt.limit(limit)
    rows = list(db.scalars(stmt).all())
    cards = [transaction_card(txn) for txn in rows]
    return {"transactions": cards, **page_meta(match_count, len(cards))}


def list_values(
    db: Session,
    dimension: ValueDimension,
    query: str | None = None,
    limit: int = 25,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
) -> dict[str, Any]:
    columns: dict[str, ColumnElement] = {
        "category": effective_category,
        "subcategory": Transaction.subcategory,
        "merchant": effective_merchant,
    }
    if dimension not in columns:
        raise ValueError(f"unsupported dimension: {dimension}")
    col = columns[dimension]
    stmt = (
        select(col, func.count(Transaction.id))
        .select_from(Transaction)
        .where(col.isnot(None), func.trim(col) != "")
    )
    stmt = _apply_filters(
        stmt,
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        default_date_to=False,
    )
    if query is not None and str(query).strip():
        stmt = stmt.where(col.ilike(f"%{str(query).strip()}%"))
    stmt = stmt.group_by(col).order_by(func.count(Transaction.id).desc(), col)
    rows = db.execute(stmt).all()
    match_count = len(rows)
    limited = rows[:limit]
    return {
        "values": [{"value": str(value), "count": int(count)} for value, count in limited],
        **page_meta(match_count, len(limited)),
    }


def cash_flow(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> dict[str, Any]:
    """
    Household cash-flow buckets by effective transaction type.

    net_cash_flow = income + refunds - purchases - fees. Excludes transfers
    and other (ADJUSTMENT, UNKNOWN, legacy PAYMENT). Depository outflows that
    fund card payments may appear as SPEND until overridden — prefer
    account_id for a single-account view.
    """
    label_filters = _resolve_optional_label_filters(db, category, subcategory)
    by_type = _totals_by_type(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        label_filters=label_filters,
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


def _or_unassigned(value: str | None) -> str:
    if value is None or not str(value).strip():
        return UNASSIGNED
    return str(value).strip()


def ledger_snapshot(db: Session) -> dict[str, Any]:
    """Whole-ledger catalog for analyst turn context. Not a spend total."""
    transaction_count = int(
        db.scalar(select(func.count()).select_from(Transaction)) or 0
    )
    merchant_subq = (
        select(effective_merchant)
        .where(
            effective_merchant.isnot(None),
            func.trim(effective_merchant) != "",
        )
        .distinct()
        .subquery()
    )
    merchant_count = int(
        db.scalar(select(func.count()).select_from(merchant_subq)) or 0
    )
    owners = [
        {"id": row.id, "name": row.name}
        for row in db.scalars(select(Owner).order_by(Owner.id)).all()
    ]
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for category, subcategory, count in db.execute(
        select(
            effective_category,
            Transaction.subcategory,
            func.count(Transaction.id),
        )
        .select_from(Transaction)
        .group_by(effective_category, Transaction.subcategory)
    ).all():
        pair_counts[
            (_or_unassigned(category), _or_unassigned(subcategory))
        ] += int(count)

    cat_totals: dict[str, int] = defaultdict(int)
    cat_subs: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (category, subcategory), count in pair_counts.items():
        cat_totals[category] += count
        cat_subs[category].append((subcategory, count))

    categories: list[dict[str, Any]] = []
    for name, total in sorted(cat_totals.items(), key=lambda item: (-item[1], item[0])):
        real = sorted(
            [(sub, count) for sub, count in cat_subs[name] if sub != UNASSIGNED],
            key=lambda item: (-item[1], item[0]),
        )
        unassigned = [
            (sub, count) for sub, count in cat_subs[name] if sub == UNASSIGNED
        ]
        subcategories = [{"name": sub, "count": count} for sub, count in real]
        if real:
            subcategories.extend(
                {"name": sub, "count": count} for sub, count in unassigned
            )
        categories.append(
            {"name": name, "count": total, "subcategories": subcategories}
        )
    return {
        "transaction_count": transaction_count,
        "merchant_count": merchant_count,
        "owners": owners,
        "categories": categories,
    }
