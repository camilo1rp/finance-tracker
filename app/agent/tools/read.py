"""Read-only agent tools. Each call opens and closes its own DB session."""
from __future__ import annotations

import json
from datetime import date
from typing import Literal

from langchain.tools import tool
from sqlalchemy import select

from app.agent.config import tool_session
from app.domain.label_filter import UnknownLabelFilterError
from app.models import Account, NormalizationMapping, Owner, Transaction, effective_merchant
from app.schemas import (
    AccountOut,
    CashFlowOut,
    GroupSummary,
    MerchantSummary,
    NormalizationMappingOut,
    OwnerOut,
    TotalOut,
    TransactionListOut,
    TransactionOut,
)
from app.services import analytics_service
from app.services.analytics_service import unmapped_summary
from app.services.label_filter import (
    ResolvedLabelFilters,
    apply_resolved_label_filters,
    resolve_label_filters,
)

GroupBy = Literal["category", "owner", "month", "account", "merchant", "subcategory"]


def _parse_date(value: str | None) -> date | None:
    if value is None or not str(value).strip():
        return None
    return date.fromisoformat(str(value))


def _dump(models: list) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


def _limit_error(limit: int) -> str | None:
    if limit < 1:
        return json.dumps({"error": "limit must be >= 1"})
    return None


def _label_filter_error(exc: UnknownLabelFilterError) -> str:
    return json.dumps({"error": exc.detail})


def _resolve_label_filters_or_error(
    db,
    category: str | None,
    subcategory: str | None,
) -> ResolvedLabelFilters | str | None:
    if category is None and subcategory is None:
        return None
    try:
        return resolve_label_filters(db, category, subcategory)
    except UnknownLabelFilterError as exc:
        return _label_filter_error(exc)


@tool
def list_owners() -> str:
    """List registered owners (id and name)."""
    with tool_session() as db:
        rows = db.scalars(select(Owner).order_by(Owner.id)).all()
        return _dump([OwnerOut.model_validate(row) for row in rows])


@tool
def list_accounts() -> str:
    """List accounts (id, name, last4)."""
    with tool_session() as db:
        rows = db.scalars(select(Account).order_by(Account.id)).all()
        return _dump([AccountOut.model_validate(row) for row in rows])


@tool
def get_unmapped_values() -> str:
    """Distinct raw type/category/owner/merchant values that still need mapping rules, plus merchants_without_category for rows with no bank category."""
    with tool_session() as db:
        return json.dumps(unmapped_summary(db))


@tool
def list_mappings(kind: str | None = None, account_id: int | None = None) -> str:
    """List normalization mapping rules.

    Filtering by account_id excludes global rules (account_id IS NULL).
    To see the full effective ruleset, call this twice: once with the account_id
    and once without (global rules).
    """
    with tool_session() as db:
        stmt = select(NormalizationMapping)
        if kind is not None:
            stmt = stmt.where(NormalizationMapping.kind == kind)
        if account_id is not None:
            stmt = stmt.where(NormalizationMapping.account_id == account_id)
        rows = db.scalars(stmt.order_by(NormalizationMapping.id)).all()
        return _dump([NormalizationMappingOut.model_validate(row) for row in rows])


@tool
def list_transactions(
    account_id: int | None = None,
    owner_id: int | None = None,
    category: str | None = None,
    merchant: str | None = None,
    subcategory: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 25,
) -> str:
    """List stored transactions.

    Does not default date_to.
    category/subcategory must exist on stored transactions (case and whitespace
    insensitive). When both are set, rows must match both. merchant filters
    against the effective value. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        label_filters = _resolve_label_filters_or_error(db, category, subcategory)
        if isinstance(label_filters, str):
            return label_filters
        stmt = select(Transaction)
        parsed_from = _parse_date(date_from)
        parsed_to = _parse_date(date_to)
        if parsed_from is not None:
            stmt = stmt.where(Transaction.transaction_date >= parsed_from)
        if parsed_to is not None:
            stmt = stmt.where(Transaction.transaction_date <= parsed_to)
        if owner_id is not None:
            stmt = stmt.where(Transaction.owner_id == owner_id)
        if account_id is not None:
            stmt = stmt.where(Transaction.account_id == account_id)
        if merchant is not None:
            stmt = stmt.where(effective_merchant == merchant)
        if label_filters is not None:
            stmt = apply_resolved_label_filters(stmt, label_filters)
        stmt = stmt.order_by(Transaction.transaction_date, Transaction.id).limit(limit)
        rows = db.scalars(stmt).all()
        return _dump([TransactionOut.model_validate(row) for row in rows])


@tool("search_transactions")
def search_transactions_tool(
    query: str,
    account_id: int | None = None,
    owner_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int = 25,
) -> str:
    """Search transaction descriptions (case-insensitive substring).

    Returns {totals, transactions}. totals uses the same field meanings as
    get_total over all rows matching the query (not just limit). The transaction
    list is capped at limit. date_to defaults to today when omitted.
    merchant matches the effective value. category/subcategory use the same
    validation as list_transactions. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        try:
            payload = analytics_service.search_transactions(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                query,
                limit,
                merchant,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return TransactionListOut.model_validate(
            {
                "totals": payload["totals"],
                "transactions": payload["transactions"],
            }
        ).model_dump_json()


@tool
def summarize(
    group_by: GroupBy,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> str:
    """Group transaction totals with the same breakdown as get_total per bucket.

    group_by is category, owner, month, account, merchant, or subcategory.
    Each row includes by_type, purchases, refunds, spend (purchases − refunds),
    net_cash_flow, total, count, average. Month buckets are YYYY-MM, ascending;
    other groupings sort by spend desc.
    "(unassigned)" is the bucket for missing groups.
    date_to defaults to today. When transaction_type is set, total/count/average
    in each row match that type instead of net spend.
    merchant matches the effective value. category/subcategory use the same
    validation as list_transactions.
    """
    with tool_session() as db:
        try:
            rows = analytics_service.summarize(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                group_by,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return _dump([GroupSummary.model_validate(row) for row in rows])


@tool
def get_total(
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> str:
    """Return per-type magnitudes, net spending, and net cash flow.

    Type SPEND means purchases/charges, not household spending.
    `purchases` = SPEND bucket; `refunds` = REFUND bucket;
    `spend`/`total` = purchases − refunds; `count`/`average` over purchase
    rows. `net_cash_flow` = income + refunds − purchases − fees (same as get_cash_flow).
    `by_type` lists every effective type (abs amounts). If transaction_type
    is set, `total`/`count`/`average` match that type instead.
    sign_convention (when account_id is set) is CSV import convention.
    date_to defaults to today. Amounts are positive magnitudes except
    net_cash_flow, which can be negative. category/subcategory use the same
    validation as list_transactions.
    """
    with tool_session() as db:
        try:
            row = analytics_service.get_total(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return TotalOut.model_validate(row).model_dump_json()


@tool
def top_merchants(
    limit: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> str:
    """Top merchants by net spend (purchases − refunds) with get_total breakdown.

    Each row includes the same fields as get_total plus merchant.
    date_to defaults to today, limit=10. When transaction_type is set,
    total/count/average match that type. merchant filter matches the effective value.
    category/subcategory use the same validation as list_transactions.
    limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        try:
            rows = analytics_service.top_merchants(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                limit,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return _dump([MerchantSummary.model_validate(row) for row in rows])


@tool
def largest_transactions(
    limit: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> str:
    """Largest transactions by absolute amount with filter-scoped totals.

    Returns {totals, transactions}. totals uses get_total field meanings over
    the same date/account/owner/merchant window (all types, not just the list).
    The transactions list includes all types unless transaction_type is set.
    category/subcategory use the same validation as list_transactions.
    limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        try:
            payload = analytics_service.largest_transactions(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                limit,
                merchant,
                transaction_type,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return TransactionListOut.model_validate(
            {
                "totals": payload["totals"],
                "transactions": payload["transactions"],
            }
        ).model_dump_json()


@tool
def get_cash_flow(
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> str:
    """Household cash-flow with the same core totals as get_total plus extras.

    Includes by_type, purchases, refunds, spend (purchases − refunds),
    net_cash_flow, total, count, average, plus income, fees, transfers,
    other, and other_count. net_cash_flow excludes transfers and other.
    Depository outflows that fund card payments may appear as SPEND until
    overridden — prefer account_id for a single-account view.
    category/subcategory use the same validation as list_transactions.
    """
    with tool_session() as db:
        try:
            row = analytics_service.cash_flow(
                db,
                _parse_date(date_from),
                _parse_date(date_to),
                account_id,
                owner_id,
                merchant,
                category=category,
                subcategory=subcategory,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return CashFlowOut.model_validate(row).model_dump_json()


READ_TOOLS = [
    list_owners,
    list_accounts,
    get_unmapped_values,
    list_mappings,
    list_transactions,
    search_transactions_tool,
]

ANALYTICS_TOOLS = [
    summarize,
    get_total,
    get_cash_flow,
    top_merchants,
    largest_transactions,
]

ANALYST_TOOLS = [
    list_owners,
    list_accounts,
    list_transactions,
    search_transactions_tool,
    *ANALYTICS_TOOLS,
]
