"""
Resolve transaction_type from raw type, amount sign, account kind, and mapping.

Lookup wins when raw_type maps. Sign fallback runs only when sign_convention
is explicit on the mapping. account_kind chooses INCOME vs TRANSFER for
non-spend-signed rows only — never infers sign convention.
"""
from decimal import Decimal
from typing import Optional

from app.domain.classification import (
    AccountKind,
    NormalizationLookup,
    TransactionType,
    classify_transaction_type,
)
from app.domain.mapping import ImportMapping, SignConvention


def is_spend_signed(amount: Decimal, convention: SignConvention) -> bool:
    if convention is SignConvention.NEGATIVE_IS_SPEND:
        return amount < 0
    return amount > 0


def effective_transaction_type(
    transaction_type: str,
    type_override: str | None,
) -> str:
    return type_override if type_override is not None else transaction_type


def is_effective_spend(transaction_type: str, type_override: str | None) -> bool:
    return effective_transaction_type(transaction_type, type_override) == TransactionType.SPEND.value


def resolve_transaction_type(
    raw_type: Optional[str],
    amount: Decimal,
    mapping: ImportMapping,
    account_kind: AccountKind | str,
    lookup: NormalizationLookup,
    account_id: int,
    merchant: Optional[str] = None,
) -> TransactionType:
    if raw_type is not None and str(raw_type).strip():
        mapped = classify_transaction_type(
            raw_type, lookup, account_id, merchant=merchant
        )
        if mapped is not TransactionType.UNKNOWN:
            return mapped

    if mapping.sign_convention is not None:
        if amount == 0:
            return TransactionType.ADJUSTMENT
        if is_spend_signed(amount, mapping.sign_convention):
            return TransactionType.SPEND
        kind = (
            account_kind
            if isinstance(account_kind, AccountKind)
            else AccountKind(str(account_kind))
        )
        if kind is AccountKind.CREDIT_CARD:
            return TransactionType.TRANSFER
        return TransactionType.INCOME

    return TransactionType.UNKNOWN
