from app.domain.effective import resolved_value
from app.domain.merchant import extract_merchant, resolved_merchant


def test_extract_merchant_strips_store_number() -> None:
    assert extract_merchant("STARBUCKS STORE 123") == "STARBUCKS"


def test_extract_merchant_strips_hash_suffix() -> None:
    assert extract_merchant("TARGET #99") == "TARGET"


def test_extract_merchant_strips_star_suffix() -> None:
    assert extract_merchant("COSTCO *1234") == "COSTCO"


def test_extract_merchant_already_clean() -> None:
    assert extract_merchant("AMAZON MARKETPLACE") == "AMAZON MARKETPLACE"


def test_extract_merchant_collapses_whitespace() -> None:
    assert extract_merchant("  STARBUCKS   STORE  123  ") == "STARBUCKS"


def test_extract_merchant_empty_or_none() -> None:
    assert extract_merchant(None) is None
    assert extract_merchant("") is None
    assert extract_merchant("   ") is None


def test_resolved_merchant_precedence() -> None:
    assert resolved_merchant("RAW", "Normalized", "Override") == "Override"
    assert resolved_merchant("RAW", "Normalized") == "Normalized"
    assert resolved_merchant("RAW", None) == "RAW"
    assert resolved_merchant(None, None) is None


def test_resolved_value_skips_blank() -> None:
    assert resolved_value(None, "  ", "Dining") == "Dining"
    assert resolved_value(None, None) is None
