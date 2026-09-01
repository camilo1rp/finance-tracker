"""Read-only steward tools. Each call opens and closes its own DB session."""
from __future__ import annotations

import json
from datetime import date

from langchain.tools import tool
from sqlalchemy import select

from app.agent.config import tool_session
from app.models import Account, NormalizationMapping, Owner, Transaction, effective_category, effective_merchant
from app.schemas import AccountOut, NormalizationMappingOut, OwnerOut, TransactionOut
from app.services.analytics_service import search_transactions, unmapped_summary


def _parse_date(value: str | None) -> date | None:
    if value is None or not str(value).strip():
        return None
    return date.fromisoformat(str(value))


def _dump(models: list) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


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
    """List stored transactions. category/merchant filter on effective values."""
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
    limit: int = 25,
) -> str:
    """Search transaction descriptions (case-insensitive substring)."""
    with tool_session() as db:
        rows = search_transactions(
            db,
            _parse_date(date_from),
            _parse_date(date_to),
            account_id,
            owner_id,
            query,
            limit,
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
