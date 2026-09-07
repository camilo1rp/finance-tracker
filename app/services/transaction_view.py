"""Serialize stored transactions for list (card) and detail responses."""
from __future__ import annotations

from typing import Any

from app.domain.effective import resolved_value
from app.domain.merchant import resolved_merchant
from app.models import Transaction


def page_meta(match_count: int, returned: int) -> dict[str, Any]:
    return {
        "match_count": match_count,
        "returned": returned,
        "truncated": match_count > returned,
    }


def transaction_card(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "account_id": txn.account_id,
        "transaction_date": txn.transaction_date,
        "description": txn.description,
        "amount": txn.amount,
        "effective_type": resolved_value(txn.type_override, txn.transaction_type),
        "effective_category": resolved_value(
            txn.category_override, txn.category_normalized, txn.category_raw
        ),
        "effective_merchant": resolved_merchant(
            txn.merchant_raw, txn.merchant_normalized, txn.merchant_override
        ),
        "subcategory": txn.subcategory,
        "owner_id": txn.owner_id,
    }


def transaction_detail(txn: Transaction) -> dict[str, Any]:
    return {
        **transaction_card(txn),
        "transaction_type": txn.transaction_type,
        "type_override": txn.type_override,
        "is_spend": txn.is_spend,
        "raw_type": txn.raw_type,
        "category_raw": txn.category_raw,
        "category_normalized": txn.category_normalized,
        "category_override": txn.category_override,
        "owner_raw": txn.owner_raw,
        "merchant_raw": txn.merchant_raw,
        "merchant_normalized": txn.merchant_normalized,
        "merchant_override": txn.merchant_override,
    }
