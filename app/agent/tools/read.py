"""Read-only agent tools. Each call opens and closes its own DB session."""
from __future__ import annotations

import json
from datetime import date
from typing import Literal

from langchain.tools import tool
from sqlalchemy import select

from app.agent.config import tool_session
from app.domain.label_filter import UnknownLabelFilterError
from app.models import Account, Owner
from app.schemas import (
    AccountOut,
    CashFlowOut,
    GroupSummaryPage,
    MerchantSummary,
    NormalizationMappingOut,
    OwnerOut,
    TotalOut,
    TransactionListOut,
    TransactionOut,
    TransactionPage,
    ValueListOut,
)
from app.services import analytics_service
from app.services.analytics_service import unmapped_summary
from app.services.mapping_query import list_normalization_mappings

GroupBy = Literal["category", "owner", "month", "account", "merchant", "subcategory"]
ValueDimension = Literal["category", "subcategory", "merchant"]


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


def _optional_limit_error(limit: int | None) -> str | None:
    if limit is None:
        return None
    return _limit_error(limit)


def _label_filter_error(exc: UnknownLabelFilterError) -> str:
    return json.dumps({"error": exc.detail})


@tool
def list_owners() -> str:
    """List registered owners (id and name)."""
    with tool_session() as db:
        rows = db.scalars(select(Owner).order_by(Owner.id)).all()
        return _dump([OwnerOut.model_validate(row) for row in rows])


@tool
def list_accounts() -> str:
    """List accounts (id, name, last4, account_kind, default_owner_id, source_format)."""
    with tool_session() as db:
        rows = db.scalars(select(Account).order_by(Account.id)).all()
        return _dump([AccountOut.model_validate(row) for row in rows])


@tool
def list_values(
    dimension: ValueDimension,
    query: str | None = None,
    limit: int = 25,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
) -> str:
    """Catalog of distinct effective labels with counts.

    dimension is category, subcategory, or merchant. query is a case-insensitive
    substring. Does not default date_to. Use this before get_total/summarize when
    the stored spelling is unknown. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        payload = analytics_service.list_values(
            db,
            dimension,
            query=query,
            limit=limit,
            date_from=_parse_date(date_from),
            date_to=_parse_date(date_to),
            account_id=account_id,
            owner_id=owner_id,
        )
        return ValueListOut.model_validate(payload).model_dump_json()


@tool
def get_unmapped_values() -> str:
    """Distinct raw type/category/owner/merchant values that still need mapping rules, plus merchants_without_category for rows with no bank category."""
    with tool_session() as db:
        return json.dumps(unmapped_summary(db))


@tool
def list_mappings(
    kind: str | None = None,
    account_id: int | None = None,
    include_global: bool = True,
) -> str:
    """List normalization mapping rules.

    When account_id is set, include_global=true (default) returns that account's
    rules plus global rules (account_id IS NULL). Set include_global=false for
    account-scoped rules only.
    """
    with tool_session() as db:
        rows = list_normalization_mappings(
            db, kind=kind, account_id=account_id, include_global=include_global
        )
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
    """List compact transaction cards (effective labels, no raw/override triples).

    Does not default date_to. merchant is exact unless it contains `%`.
    category/subcategory must exist on stored transactions. Returns
    {transactions, match_count, returned, truncated}. Use get_transaction for
    triples / raw_type / owner_raw. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        try:
            payload = analytics_service.list_transactions(
                db,
                date_from=_parse_date(date_from),
                date_to=_parse_date(date_to),
                account_id=account_id,
                owner_id=owner_id,
                merchant=merchant,
                category=category,
                subcategory=subcategory,
                limit=limit,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return TransactionPage.model_validate(payload).model_dump_json()


@tool
def get_transaction(transaction_id: int) -> str:
    """Full transaction detail: triples, raw_type, owner_raw, and effective labels."""
    with tool_session() as db:
        row = analytics_service.get_transaction(db, transaction_id)
        if row is None:
            return json.dumps({"error": f"transaction {transaction_id} not found"})
        return TransactionOut.model_validate(row).model_dump_json()


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
    limit: int = 200,
) -> str:
    """Search payee and label fields (case-insensitive substring).

    query matches description, merchant (raw/normalized/override/effective),
    category triple, subcategory, raw_type, and owner_raw. Optional merchant /
    category / subcategory filters are also contains-matches (not exact labels).
    Returns {totals, transactions, match_count, returned, truncated}. totals
    cover all matches, not just limit. date_to defaults to today. Default
    limit is 200; limit >= 1.
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
        return TransactionListOut.model_validate(payload).model_dump_json()


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
    limit: int | None = None,
) -> str:
    """Group transaction totals with the same breakdown as get_total per bucket.

    Returns {groups, match_count, returned, truncated}. group_by is category,
    owner, month, account, merchant, or subcategory. Month buckets are YYYY-MM,
    ascending; other groupings sort by spend desc. limit caps groups after sort
    (top N by spend, or first N months). date_to defaults to today. merchant
    is exact unless it contains `%`.
    """
    if err := _optional_limit_error(limit):
        return err
    with tool_session() as db:
        try:
            payload = analytics_service.summarize(
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
                limit=limit,
            )
        except UnknownLabelFilterError as exc:
            return _label_filter_error(exc)
        return GroupSummaryPage.model_validate(payload).model_dump_json()


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
    date_to defaults to today. merchant is exact unless it contains `%`.
    Amounts are positive magnitudes except net_cash_flow, which can be negative.
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
    date_to defaults to today, limit=10. merchant filter is exact unless it
    contains `%`. limit must be >= 1.
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

    Returns {totals, transactions, match_count, returned, truncated}.
    totals use get_total field meanings over the same window. The list
    includes all types unless transaction_type is set. Cards are compact;
    use get_transaction for triples. limit must be >= 1.
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
        return TransactionListOut.model_validate(payload).model_dump_json()


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
    merchant is exact unless it contains `%`.
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


CATALOG_TOOLS = [
    list_owners,
    list_accounts,
    list_values,
]

READ_TOOLS = [
    *CATALOG_TOOLS,
    get_unmapped_values,
    list_mappings,
    list_transactions,
    get_transaction,
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
    *CATALOG_TOOLS,
    list_transactions,
    get_transaction,
    search_transactions_tool,
    *ANALYTICS_TOOLS,
]
