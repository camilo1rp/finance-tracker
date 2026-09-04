from datetime import date
from decimal import Decimal

import pytest

from app.agent.config import DEFAULT_ENRICHMENT_CONFIDENCE_THRESHOLD
from app.domain.receipts import (
    DATE_ONLY_LOOKAHEAD_DAYS,
    EXACT_TOTAL_BASE_CONFIDENCE,
    MAX_SIBLINGS_FOR_SPLIT,
    MATCH_CONFIDENCE_CAP,
    ORDER_ID_CONFIDENCE_BONUS,
    SEED_SENDERS,
    SPLIT_PARTIAL_BASE_CONFIDENCE,
    EvidenceMatch,
    LineItem,
    MatchKind,
    ReceiptExtraction,
    count_currency_like_tokens,
    count_date_like_tokens,
    dominant_line_item,
    match_receipt,
    merchant_hint_tokens,
    receipt_extraction_dump,
    snap_category,
)


def test_dominant_line_item_cases() -> None:
    assert dominant_line_item([]) is None
    assert dominant_line_item([LineItem(description="a"), LineItem(description="b")]) is None
    tied = dominant_line_item(
        [
            LineItem(description="first", amount=Decimal("10.00")),
            LineItem(description="second", amount=Decimal("10.00")),
        ]
    )
    assert tied is not None
    assert tied.description == "first"


def test_snap_category_returns_stored_canonical_or_unknown() -> None:
    assert snap_category(" groceries ", ["Groceries", "Dining"]) == "Groceries"
    assert snap_category("unknown thing", ["Groceries"]) == "unknown"
    assert snap_category(None, ["Groceries"]) == "unknown"


def test_match_receipt_stores_hint_verbatim_and_snaps_or_unknown() -> None:
    extraction = ReceiptExtraction(
        total=Decimal("25.00"),
        raw_confidence=0.7,
        extractor_version="x",
        line_items=[
            LineItem(
                description="55-inch OLED",
                amount=Decimal("25.00"),
                product_type="television",
                category_hint="electronics",
            )
        ],
    )
    unmatched = match_receipt(
        Decimal("-25.00"),
        date(2024, 6, 1),
        extraction,
        [],
        known_categories=["Shopping", "Dining"],
    )
    assert unmatched.dominant_category_raw == "electronics"
    assert unmatched.dominant_category == "unknown"

    snapped = match_receipt(
        Decimal("-25.00"),
        date(2024, 6, 1),
        extraction.model_copy(
            update={
                "line_items": [
                    LineItem(
                        description="55-inch OLED",
                        amount=Decimal("25.00"),
                        product_type="television",
                        category_hint=" shopping ",
                    )
                ]
            }
        ),
        [],
        known_categories=["Shopping", "Dining"],
    )
    assert snapped.dominant_category_raw == " shopping "
    assert snapped.dominant_category == "Shopping"


def test_line_item_dump_includes_product_type() -> None:
    dumped = receipt_extraction_dump(
        ReceiptExtraction(
            line_items=[
                LineItem(
                    description="55-inch OLED",
                    amount=Decimal("25.00"),
                    product_type="television",
                    category_hint="electronics",
                )
            ]
        )
    )
    assert dumped["line_items"][0]["product_type"] == "television"
    assert dumped["line_items"][0]["category_hint"] == "electronics"


def test_match_receipt_exact_total() -> None:
    match = match_receipt(
        Decimal("-25.00"),
        date(2024, 6, 1),
        ReceiptExtraction(total=Decimal("25.00"), raw_confidence=0.1, extractor_version="x"),
        [],
    )
    assert match.match_kind is MatchKind.EXACT_TOTAL
    assert match.confidence == pytest.approx(EXACT_TOTAL_BASE_CONFIDENCE)

    with_order = match_receipt(
        Decimal("-25.00"),
        date(2024, 6, 1),
        ReceiptExtraction(
            total=Decimal("25.00"),
            order_id="A-99",
            raw_confidence=0.1,
            extractor_version="x",
        ),
        [],
    )
    assert with_order.confidence == pytest.approx(
        min(MATCH_CONFIDENCE_CAP, EXACT_TOTAL_BASE_CONFIDENCE + ORDER_ID_CONFIDENCE_BONUS)
    )


def test_exact_total_zero_raw_confidence_clears_threshold() -> None:
    match = match_receipt(
        Decimal("-25.00"),
        date(2024, 6, 1),
        ReceiptExtraction(total=Decimal("25.00"), raw_confidence=0.0, extractor_version="x"),
        [],
    )
    assert match.match_kind is MatchKind.EXACT_TOTAL
    assert match.confidence == pytest.approx(EXACT_TOTAL_BASE_CONFIDENCE)
    assert match.confidence >= DEFAULT_ENRICHMENT_CONFIDENCE_THRESHOLD


def test_match_receipt_split_partial_two_and_three_siblings() -> None:
    extraction = ReceiptExtraction(total=Decimal("60.00"), raw_confidence=0.0, extractor_version="x")
    two = match_receipt(
        Decimal("-20.00"),
        date(2024, 6, 1),
        extraction,
        [(2, Decimal("-15.00"), date(2024, 6, 2)), (3, Decimal("-25.00"), date(2024, 6, 3))],
    )
    assert two.match_kind is MatchKind.SPLIT_PARTIAL
    assert two.confidence == pytest.approx(SPLIT_PARTIAL_BASE_CONFIDENCE)

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
    assert three.confidence == pytest.approx(SPLIT_PARTIAL_BASE_CONFIDENCE)

    with_order = match_receipt(
        Decimal("-20.00"),
        date(2024, 6, 1),
        extraction.model_copy(update={"order_id": "S-1"}),
        [(2, Decimal("-15.00"), date(2024, 6, 2)), (3, Decimal("-25.00"), date(2024, 6, 3))],
    )
    assert with_order.confidence == pytest.approx(
        SPLIT_PARTIAL_BASE_CONFIDENCE + ORDER_ID_CONFIDENCE_BONUS
    )


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


def test_merchant_hint_tokens_dollar_tree_payee() -> None:
    assert merchant_hint_tokens(
        "dollar tree 9523 westheimer rd houston tx"
    ) == ["dollar", "tree"]


@pytest.mark.parametrize(
    ("merchant_key", "expected"),
    [
        ("amazon", ["amazon"]),
        ("amazon suite 100", ["amazon"]),
        ("tx 12 st rd", []),
        ("usa inc llc", []),
        ("home depot 123 main st", ["home", "depot"]),
        ("ab cd shop", ["shop"]),
        ("microsoft*xbox", ["microsoft", "xbox"]),
    ],
)
def test_merchant_hint_tokens_filters(merchant_key: str, expected: list[str]) -> None:
    assert merchant_hint_tokens(merchant_key) == expected


def test_seed_senders_include_required_domains() -> None:
    patterns = {item for values in SEED_SENDERS.values() for item in values}
    for domain in (
        "amazon.com",
        "apple.com",
        "cursor.com",
        "cursor.sh",
        "microsoft.com",
        "xbox.com",
    ):
        assert domain in patterns


def test_count_currency_and_date_like_tokens() -> None:
    text = "Order #A-99 Total $25.00 on Jun 2, 2024 also 2024-06-02 extra $3.50"
    assert count_currency_like_tokens(text) == 2
    assert count_date_like_tokens(text) == 2
    assert count_currency_like_tokens("") == 0
    assert count_date_like_tokens("no dates here") == 0
