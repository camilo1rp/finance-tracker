from datetime import date
from decimal import Decimal

import pytest

from app.domain.receipts import (
    DATE_ONLY_LOOKAHEAD_DAYS,
    MAX_SIBLINGS_FOR_SPLIT,
    EvidenceMatch,
    LineItem,
    MatchKind,
    ReceiptExtraction,
    dominant_line_item,
    match_receipt,
    snap_category,
)


def test_dominant_line_item_cases() -> None:
    assert dominant_line_item([]) is None
    assert dominant_line_item([LineItem("a"), LineItem("b")]) is None
    tied = dominant_line_item(
        [LineItem("first", amount=Decimal("10.00")), LineItem("second", amount=Decimal("10.00"))]
    )
    assert tied is not None
    assert tied.description == "first"


def test_snap_category_returns_stored_canonical_or_unknown() -> None:
    assert snap_category(" groceries ", ["Groceries", "Dining"]) == "Groceries"
    assert snap_category("unknown thing", ["Groceries"]) == "unknown"
    assert snap_category(None, ["Groceries"]) == "unknown"


def test_match_receipt_exact_total() -> None:
    match = match_receipt(
        Decimal("-25.00"),
        date(2024, 6, 1),
        ReceiptExtraction(total=Decimal("25.00"), raw_confidence=0.7, extractor_version="x"),
        [],
    )
    assert match.match_kind is MatchKind.EXACT_TOTAL
    assert match.confidence == pytest.approx(0.9)


def test_match_receipt_split_partial_two_and_three_siblings() -> None:
    extraction = ReceiptExtraction(total=Decimal("60.00"), raw_confidence=0.5, extractor_version="x")
    two = match_receipt(
        Decimal("-20.00"),
        date(2024, 6, 1),
        extraction,
        [(2, Decimal("-15.00"), date(2024, 6, 2)), (3, Decimal("-25.00"), date(2024, 6, 3))],
    )
    assert two.match_kind is MatchKind.SPLIT_PARTIAL
    assert two.confidence == 0.4

    three = match_receipt(
        Decimal("-10.00"),
        date(2024, 6, 1),
        extraction,
        [
            (2, Decimal("-15.00"), date(2024, 6, 2)),
            (3, Decimal("-20.00"), date(2024, 6, 3)),
            (4, Decimal("-15.00"), date(2024, 6, 3)),
        ],
    )
    assert three.match_kind is MatchKind.SPLIT_PARTIAL


def test_match_receipt_sibling_cap_respected() -> None:
    siblings = [
        (idx, Decimal("-10.00"), date(2024, 6, 1)) for idx in range(MAX_SIBLINGS_FOR_SPLIT + 1)
    ]
    match = match_receipt(
        Decimal("-10.00"),
        date(2024, 6, 1),
        ReceiptExtraction(total=Decimal("60.00"), raw_confidence=0.5, extractor_version="x"),
        siblings,
    )
    assert match.match_kind is MatchKind.UNMATCHED


def test_match_receipt_date_only_and_unmatched() -> None:
    date_only = match_receipt(
        Decimal("-10.00"),
        date(2024, 6, 1),
        ReceiptExtraction(
            order_date=date(2024, 6, 1).fromordinal(
                date(2024, 6, 1).toordinal() + DATE_ONLY_LOOKAHEAD_DAYS
            ),
            raw_confidence=0.5,
            extractor_version="x",
        ),
        [],
    )
    assert date_only.match_kind is MatchKind.DATE_ONLY
    assert date_only.confidence == 0.2

    unmatched = match_receipt(
        Decimal("-10.00"),
        date(2024, 6, 1),
        ReceiptExtraction(order_date=date(2024, 7, 1), raw_confidence=0.5, extractor_version="x"),
        [],
    )
    assert isinstance(unmatched, EvidenceMatch)
    assert unmatched.match_kind is MatchKind.UNMATCHED
    assert unmatched.confidence == 0.0
