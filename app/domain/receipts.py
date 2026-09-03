from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field

from app.domain.classification import clean_raw_value
from app.domain.email_source import EmailMessage

EXACT_TOTAL_TOLERANCE = Decimal("0.01")
SIBLING_WINDOW_DAYS = 5
MAX_SIBLINGS_FOR_SPLIT = 4
DATE_ONLY_LOOKBACK_DAYS = 2
DATE_ONLY_LOOKAHEAD_DAYS = 7

SEED_SENDERS: dict[str, list[str]] = {
    "amazon": ["amazon.com"],
    "apple": ["apple.com"],
    "uber": ["uber.com"],
    "lyft": ["lyft.com"],
    "netflix": ["netflix.com"],
    "spotify": ["spotify.com"],
    "google": ["google.com"],
    "microsoft": ["microsoft.com"],
}


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quantity: Decimal | None = None
    amount: Decimal | None = None
    category_hint: str | None = None


class ReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_name: str | None = None
    order_id: str | None = None
    order_date: date | None = None
    currency: str | None = None
    total: Decimal | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    shipping: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    payment_hint: str | None = None
    extractor_version: str = "unknown"
    raw_confidence: float = 0.0


class MatchKind(str, Enum):
    EXACT_TOTAL = "exact_total"
    SPLIT_PARTIAL = "split_partial"
    DATE_ONLY = "date_only"
    UNMATCHED = "unmatched"


class EvidenceKind(str, Enum):
    EMAIL_RECEIPT = "email_receipt"


class SenderOrigin(str, Enum):
    SEED = "seed"
    LEARNED = "learned"
    USER = "user"


@dataclass(frozen=True)
class EvidenceMatch:
    transaction_id: int
    message_id: str
    match_kind: MatchKind
    amount_delta: Decimal | None
    days_delta: int | None
    confidence: float
    dominant_category: str | None
    dominant_category_raw: str | None


class ReceiptExtractor(ABC):
    @abstractmethod
    def extract(
        self, message: EmailMessage, known_categories: list[str]
    ) -> ReceiptExtraction:
        raise NotImplementedError


def dominant_line_item(items: list[LineItem]) -> LineItem | None:
    best: LineItem | None = None
    for item in items:
        if item.amount is None:
            continue
        if best is None or best.amount is None or item.amount > best.amount:
            best = item
    return best


def snap_category(hint: str | None, known: list[str]) -> str:
    if hint is None or not hint.strip():
        return "unknown"
    cleaned_hint = clean_raw_value(hint)
    for category in known:
        if clean_raw_value(category) == cleaned_hint:
            return category
    return "unknown"


def _subset_sum_match(total: Decimal, values: list[Decimal]) -> bool:
    if not values:
        return False

    def _walk(index: int, remaining: Decimal, used: bool) -> bool:
        if abs(remaining) <= EXACT_TOTAL_TOLERANCE and used:
            return True
        if index >= len(values) or remaining < -EXACT_TOTAL_TOLERANCE:
            return False
        if _walk(index + 1, remaining - values[index], True):
            return True
        return _walk(index + 1, remaining, used)

    return _walk(0, total, False)


def match_receipt(
    txn_amount: Decimal,
    txn_date: date,
    extraction: ReceiptExtraction,
    siblings: list[tuple[int, Decimal, date]],
    *,
    transaction_id: int = 0,
    message_id: str = "",
    known_categories: list[str] | None = None,
) -> EvidenceMatch:
    abs_amount = abs(txn_amount)
    dominant = dominant_line_item(extraction.line_items)
    raw_category = dominant.category_hint if dominant is not None else None
    dominant_category = snap_category(raw_category, known_categories or [])
    total = extraction.total
    order_date = extraction.order_date

    if total is not None:
        delta = total - abs_amount
        if abs(delta) <= EXACT_TOTAL_TOLERANCE:
            return EvidenceMatch(
                transaction_id=transaction_id,
                message_id=message_id,
                match_kind=MatchKind.EXACT_TOTAL,
                amount_delta=delta,
                days_delta=(order_date - txn_date).days if order_date is not None else None,
                confidence=min(1.0, extraction.raw_confidence + 0.2),
                dominant_category=dominant_category,
                dominant_category_raw=raw_category,
            )

        eligible = [
            abs(amount)
            for _txn_id, amount, sibling_date in siblings[:MAX_SIBLINGS_FOR_SPLIT]
            if abs((sibling_date - txn_date).days) <= SIBLING_WINDOW_DAYS
        ]
        if _subset_sum_match(total - abs_amount, eligible):
            return EvidenceMatch(
                transaction_id=transaction_id,
                message_id=message_id,
                match_kind=MatchKind.SPLIT_PARTIAL,
                amount_delta=total - (abs_amount + sum(eligible)),
                days_delta=(order_date - txn_date).days if order_date is not None else None,
                confidence=extraction.raw_confidence * 0.8,
                dominant_category=dominant_category,
                dominant_category_raw=raw_category,
            )

    if (
        total is None
        and order_date is not None
        and txn_date.toordinal() - DATE_ONLY_LOOKBACK_DAYS
        <= order_date.toordinal()
        <= txn_date.toordinal() + DATE_ONLY_LOOKAHEAD_DAYS
    ):
        return EvidenceMatch(
            transaction_id=transaction_id,
            message_id=message_id,
            match_kind=MatchKind.DATE_ONLY,
            amount_delta=None,
            days_delta=(order_date - txn_date).days,
            confidence=extraction.raw_confidence * 0.4,
            dominant_category=dominant_category,
            dominant_category_raw=raw_category,
        )

    return EvidenceMatch(
        transaction_id=transaction_id,
        message_id=message_id,
        match_kind=MatchKind.UNMATCHED,
        amount_delta=(total - abs_amount) if total is not None else None,
        days_delta=(order_date - txn_date).days if order_date is not None else None,
        confidence=0.0,
        dominant_category=dominant_category,
        dominant_category_raw=raw_category,
    )


def receipt_extraction_dump(extraction: ReceiptExtraction) -> dict:
    return extraction.model_dump(mode="json")


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


_ORDER_ID_PATTERNS = [
    re.compile(r"order\s*#\s*([A-Z0-9-]+)", re.IGNORECASE),
    re.compile(r"order\s*id[:\s#]*([A-Z0-9-]+)", re.IGNORECASE),
]
_AMOUNT_PATTERN = re.compile(r"(?<!\d)(?:USD\s*)?\$?\s*(\d+\.\d{2})(?!\d)")
_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_TEXT_DATE_PATTERN = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b"
)


class RegexReceiptExtractor(ReceiptExtractor):
    def extract(
        self, message: EmailMessage, known_categories: list[str]
    ) -> ReceiptExtraction:
        del known_categories
        text = "\n".join([message.ref.subject, message.body_text])
        order_id = None
        for pattern in _ORDER_ID_PATTERNS:
            match = pattern.search(text)
            if match:
                order_id = match.group(1)
                break

        amounts = [Decimal(match.group(1)) for match in _AMOUNT_PATTERN.finditer(text)]
        total = max(amounts) if amounts else None

        order_date = None
        iso_match = _ISO_DATE_PATTERN.search(text)
        if iso_match:
            try:
                order_date = date.fromisoformat(iso_match.group(1))
            except ValueError:
                order_date = None
        if order_date is None:
            text_match = _TEXT_DATE_PATTERN.search(text)
            if text_match:
                try:
                    order_date = datetime.strptime(text_match.group(0), "%b %d, %Y").date()
                except ValueError:
                    order_date = None

        return ReceiptExtraction(
            order_id=order_id,
            order_date=order_date,
            total=total,
            line_items=[],
            extractor_version="regex-0",
            raw_confidence=0.5 if total is not None else 0.2,
        )
