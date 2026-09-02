"""Read-only agent tools. Each call opens and closes its own DB session."""
from __future__ import annotations

import json
from datetime import date
from typing import Literal

from langchain.tools import tool
from sqlalchemy import select

from app.agent.config import tool_session
from app.models import Account, NormalizationMapping, Owner, Transaction, effective_category, effective_merchant
from app.schemas import (
    AccountOut,
    GroupSummary,
    MerchantSummary,
    NormalizationMappingOut,
    OwnerOut,
    TotalOut,
    TransactionOut,
)
from app.services import analytics_service
from app.services.analytics_service import unmapped_summary

GroupBy = Literal["category", "owner", "month", "account", "merchant"]


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
    """Distinct raw type/category/owner/merchant values that still need mapping rules."""
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
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 25,
) -> str:
    """List stored transactions.

    Does not apply spend_only and does not default date_to.
    category/merchant filters match the effective value
    (override → normalized → raw). limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
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
        if category is not None:
            stmt = stmt.where(effective_category == category)
        if merchant is not None:
            stmt = stmt.where(effective_merchant == merchant)
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
    limit: int = 25,
) -> str:
    """Search transaction descriptions (case-insensitive substring).

    Does not apply spend_only. The analytics service still upper-bounds
    transaction_date at today when date_to is omitted.
    merchant matches the effective value (override → normalized → raw).
    limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        rows = analytics_service.search_transactions(
            db,
            _parse_date(date_from),
            _parse_date(date_to),
            account_id,
            owner_id,
            query,
            limit,
            merchant,
        )
        return _dump([TransactionOut.model_validate(row) for row in rows])


@tool
def summarize(
    group_by: GroupBy,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    spend_only: bool = True,
    merchant: str | None = None,
) -> str:
    """Group transaction totals.

    group_by is category, owner, month, account, or merchant.
    Month buckets are YYYY-MM, ascending; other groupings sort by total desc.
    "(unassigned)" is the bucket for missing groups.
    Defaults: spend_only=true (only SPEND rows; totals use abs(amount)) and
    date_to=today. spend_only=false sums signed amounts as stored.
    merchant matches the effective value (override → normalized → raw).
    Amounts are decimal strings. There is no category filter; group_by=category
    to break down by category.
    """
    with tool_session() as db:
        rows = analytics_service.summarize(
            db,
            _parse_date(date_from),
            _parse_date(date_to),
            account_id,
            owner_id,
            group_by,
            spend_only,
            merchant,
        )
        return _dump([GroupSummary.model_validate(row) for row in rows])


@tool
def get_total(
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    spend_only: bool = True,
    merchant: str | None = None,
) -> str:
    """Return total, count, and average for matching transactions.

    Defaults: spend_only=true (only SPEND rows; totals use abs(amount)) and
    date_to=today. spend_only=false sums signed amounts as stored.
    merchant matches the effective value (override → normalized → raw).
    Amounts are decimal strings.
    """
    with tool_session() as db:
        row = analytics_service.get_total(
            db,
            _parse_date(date_from),
            _parse_date(date_to),
            account_id,
            owner_id,
            spend_only,
            merchant,
        )
        return TotalOut.model_validate(row).model_dump_json()


@tool
def top_merchants(
    limit: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    spend_only: bool = True,
    merchant: str | None = None,
) -> str:
    """Top merchants by total.

    Defaults: spend_only=true (only SPEND rows; totals use abs(amount)),
    date_to=today, limit=10. spend_only=false sums signed amounts as stored.
    merchant matches the effective value. Amounts are decimal strings.
    limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        rows = analytics_service.top_merchants(
            db,
            _parse_date(date_from),
            _parse_date(date_to),
            account_id,
            owner_id,
            spend_only,
            limit,
            merchant,
        )
        return _dump([MerchantSummary.model_validate(row) for row in rows])


@tool
def largest_transactions(
    limit: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    spend_only: bool = True,
    merchant: str | None = None,
) -> str:
    """Largest transactions by absolute amount.

    Defaults: spend_only=true (only SPEND rows), date_to=today, limit=10.
    Does not default to a category filter. limit must be >= 1.
    """
    if err := _limit_error(limit):
        return err
    with tool_session() as db:
        rows = analytics_service.largest_transactions(
            db,
            _parse_date(date_from),
            _parse_date(date_to),
            account_id,
            owner_id,
            spend_only,
            limit,
            merchant,
        )
        return _dump([TransactionOut.model_validate(row) for row in rows])


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
