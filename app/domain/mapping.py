"""
Per-account import configuration.

Design note (revised): a single `type_col`, when present, is classified
through the normalization lookup table into a TransactionType (SPEND/REFUND/
PAYMENT/ADJUSTMENT/UNKNOWN) -- this replaces the earlier two-strategy design
(sign vs. type-column) once we saw Chase's real Type column ("Sale",
"Return", "Payment") is genuinely categorical, same as Apple Card's. Sign
convention is kept only as a fallback for sources with no type column at all.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SignConvention(str, Enum):
    NEGATIVE_IS_SPEND = "negative_is_spend"
    POSITIVE_IS_SPEND = "positive_is_spend"


@dataclass(frozen=True)
class ImportMapping:
    date_col: str
    description_col: str
    amount_col: str
    category_col: Optional[str] = None
    owner_col: Optional[str] = None   # per-row owner override (e.g. Apple Card "Purchased by")
    type_col: Optional[str] = None    # raw transaction type, classified via lookup table
    merchant_col: Optional[str] = None  # Apple "Merchant"; else extract from description

    # Fallback only, used when type_col is None (no categorical type available).
    sign_convention: Optional[SignConvention] = None

    def __post_init__(self) -> None:
        """Validate: either type_col is set, or sign_convention is set --
        need at least one way to determine spend vs. non-spend."""
        if not self.type_col and self.sign_convention is None:
            raise ValueError(
                "ImportMapping requires type_col or sign_convention"
            )


def resolve_mapping(
    account_default_mapping: ImportMapping,
    override: Optional[ImportMapping] = None,
) -> ImportMapping:
    """
    Single seam deciding which mapping wins for a given import.
    Today: always returns account_default_mapping.
    Future (per-import override): return override if override is not None.
    """
    return account_default_mapping
