"""
Domain value objects for a normalized transaction.

Design note: kept separate from the SQLAlchemy model so normalize()/dedupe()
stay unit-testable with plain dataclasses -- no DB, no ORM import here.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.domain.classification import TransactionType


@dataclass(frozen=True)
class CanonicalTransaction:
    """
    One transaction, mapped and classified into a source-agnostic shape.

    transaction_type: canonical classification (SPEND/REFUND/PAYMENT/
    ADJUSTMENT/UNKNOWN), resolved via the normalization lookup table.
    is_spend is derived from transaction_type == SPEND (kept as a plain bool
    too since it's the single most common analytics filter).

    category_normalized: resolved via lookup, None if not yet mapped --
    callers fall back to category_raw for display per the 3-tier precedence
    (category_override > category_normalized > category_raw).
    """
    account_id: int
    transaction_date: date
    description: str
    amount: Decimal
    transaction_type: TransactionType
    is_spend: bool
    raw_type: Optional[str] = None
    category_raw: Optional[str] = None
    category_normalized: Optional[str] = None
    owner: Optional[str] = None
    owner_raw: Optional[str] = None
    merchant_raw: Optional[str] = None
    merchant_normalized: Optional[str] = None
    dedupe_hash: str = ""  # computed in dedupe.py, not here
    raw: dict[str, Any] = field(default_factory=dict)  # original row, untouched


@dataclass(frozen=True)
class UnmappedValues:
    """Raw values encountered during an import that had no normalization
    mapping and fell back to UNKNOWN/passthrough -- surfaced in ImportResult
    so the user knows what to go register after each monthly import."""
    transaction_types: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    merchants: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReclassifyResult:
    """Summary of applying current normalization mappings to stored rows."""
    scanned: int
    updated: int
    unmapped: UnmappedValues


@dataclass(frozen=True)
class IngestResult:
    """Summary returned by the ingest service after a full pipeline run."""
    account_id: int
    import_batch_id: Optional[int]
    total_rows_read: int
    inserted: int
    duplicates_skipped: int
    unmapped: UnmappedValues
    errors: list[str] = field(default_factory=list)
