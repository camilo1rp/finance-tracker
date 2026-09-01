"""
Canonical classification for transaction type, category, and owner --
three instances of the same problem: a raw source string needs to resolve
to a canonical value, and new raw values will appear over time as new
merchants/categories/people show up in imports.

Design note: ONE generic lookup mechanism (NormalizationLookup) backs all
three, keyed by `kind`. This avoids three parallel mapping systems that
would each need their own resolution/precedence logic.
"""
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional


class TransactionType(str, Enum):
    SPEND = "SPEND"
    REFUND = "REFUND"
    PAYMENT = "PAYMENT"
    ADJUSTMENT = "ADJUSTMENT"
    UNKNOWN = "UNKNOWN"


class NormalizationKind(str, Enum):
    TRANSACTION_TYPE = "transaction_type"
    CATEGORY = "category"
    OWNER = "owner"
    MERCHANT = "merchant"


class NormalizationLookup(ABC):
    """
    Resolves a raw string to a canonical value for a given kind.
    Implementations query NormalizationMapping rows (account-specific first,
    then global, else None). Kept as an interface so normalize() can be unit
    tested with a fake in-memory lookup, no DB required.

    Matching is always done against the CLEANED raw value (see
    clean_raw_value) -- both at write time (when a mapping rule is created)
    and at read time (when resolving a row's raw value) -- so "Sale",
    "sale", and " Sale " all hit the same rule.
    """

    @abstractmethod
    def resolve(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int,
        merchant: Optional[str] = None,
    ) -> Optional[str]:
        """
        Returns the canonical value, or None if no mapping exists yet
        (caller decides the UNKNOWN/passthrough fallback).
        `raw_value` (and `merchant`, when used) are expected already cleaned
        via clean_raw_value() -- implementations should not re-clean.

        For kind=category, `merchant` is the cleaned resolved merchant.
        Precedence: account+merchant, account, global+merchant, global.
        Other kinds ignore `merchant`.
        """
        raise NotImplementedError


def clean_raw_value(raw_value: str) -> str:
    """
    The single normalization rule applied to every raw value before it is
    stored as a NormalizationMapping.raw_value or used as a lookup key:
    trim surrounding whitespace, lowercase. Called both when a mapping rule
    is created (router/service) and when classifying an incoming row
    (normalize.py), so the two sides always compare on equal terms.
    """
    return raw_value.strip().lower()


def classify_transaction_type(
    raw_type: Optional[str],
    lookup: NormalizationLookup,
    account_id: int,
) -> TransactionType:
    """Cleans raw_type via clean_raw_value(), resolves via lookup; defaults
    to UNKNOWN if unmapped or raw_type is absent."""
    if raw_type is None or not str(raw_type).strip():
        return TransactionType.UNKNOWN
    resolved = lookup.resolve(
        NormalizationKind.TRANSACTION_TYPE,
        clean_raw_value(str(raw_type)),
        account_id,
    )
    if resolved is None:
        return TransactionType.UNKNOWN
    try:
        return TransactionType(resolved)
    except ValueError:
        return TransactionType.UNKNOWN


def classify_category(
    raw_category: Optional[str],
    lookup: NormalizationLookup,
    account_id: int,
    merchant: Optional[str] = None,
) -> Optional[str]:
    """Cleans raw_category via clean_raw_value(), resolves via lookup;
    returns None if unmapped (caller falls back to raw_category for display,
    per the 3-tier precedence). `merchant` is the resolved merchant label
    (override > normalized > raw); it is cleaned here before lookup."""
    if raw_category is None or not str(raw_category).strip():
        return None
    cleaned_merchant = None
    if merchant is not None and str(merchant).strip():
        cleaned_merchant = clean_raw_value(str(merchant))
    return lookup.resolve(
        NormalizationKind.CATEGORY,
        clean_raw_value(str(raw_category)),
        account_id,
        cleaned_merchant,
    )


def classify_owner(
    raw_owner: Optional[str],
    lookup: NormalizationLookup,
    account_id: int,
) -> Optional[str]:
    """Cleans raw_owner via clean_raw_value(), resolves via lookup to a
    registered Owner's canonical name. Returns None if unmapped."""
    if raw_owner is None or not str(raw_owner).strip():
        return None
    return lookup.resolve(
        NormalizationKind.OWNER,
        clean_raw_value(str(raw_owner)),
        account_id,
    )


def classify_merchant(
    raw_merchant: Optional[str],
    lookup: NormalizationLookup,
    account_id: int,
) -> Optional[str]:
    """Cleans raw_merchant via clean_raw_value(), resolves via lookup;
    returns None if unmapped (caller falls back to merchant_raw)."""
    if raw_merchant is None or not str(raw_merchant).strip():
        return None
    return lookup.resolve(
        NormalizationKind.MERCHANT,
        clean_raw_value(str(raw_merchant)),
        account_id,
    )
