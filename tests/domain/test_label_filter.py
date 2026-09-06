import pytest

from app.domain.label_filter import (
    UnknownLabelFilterError,
    matching_spellings,
    normalize_filter_label,
    unknown_label_detail,
)


def test_normalize_filter_label_collapses_whitespace_and_lowercases() -> None:
    assert normalize_filter_label(" Dining ") == "dining"
    assert normalize_filter_label("DINING") == "dining"
    assert normalize_filter_label("  card   payment  ") == "card payment"


def test_normalize_filter_label_empty_after_collapse() -> None:
    assert normalize_filter_label("   ") == ""


def test_matching_spellings_returns_all_stored_spellings_for_key() -> None:
    available = ["Dining", "dining", "Shopping"]
    assert matching_spellings(" DINING ", available) == ["Dining", "dining"]
    assert matching_spellings("shopping", available) == ["Shopping"]


def test_matching_spellings_unknown_or_blank() -> None:
    available = ["Dining"]
    assert matching_spellings("Travel", available) == []
    assert matching_spellings("   ", available) == []


def test_unknown_label_detail_lists_available() -> None:
    detail = unknown_label_detail("category", "foo", ["Shopping", "Dining"])
    assert detail == "unknown category 'foo'; available: Dining, Shopping"


def test_unknown_label_detail_deduplicates_case_variants() -> None:
    detail = unknown_label_detail("subcategory", "x", ["card_payment", "Card_Payment"])
    assert detail == "unknown subcategory 'x'; available: card_payment"


def test_unknown_label_detail_none_available() -> None:
    detail = unknown_label_detail("category", "foo", [])
    assert detail == "unknown category 'foo'; available: (none)"


def test_unknown_label_filter_error_joins_multiple_errors() -> None:
    err = UnknownLabelFilterError(
        [
            "unknown category 'foo'; available: Dining",
            "unknown subcategory 'bar'; available: (none)",
        ]
    )
    assert err.errors == [
        "unknown category 'foo'; available: Dining",
        "unknown subcategory 'bar'; available: (none)",
    ]
    assert err.detail == (
        "unknown category 'foo'; available: Dining; "
        "unknown subcategory 'bar'; available: (none)"
    )
    with pytest.raises(UnknownLabelFilterError):
        raise err
