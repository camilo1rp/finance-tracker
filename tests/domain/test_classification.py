from app.domain.classification import (
    NormalizationKind,
    TransactionType,
    classify_category,
    classify_merchant,
    classify_owner,
    classify_transaction_type,
    clean_raw_value,
)
from tests.fakes import InMemoryNormalizationLookup


def test_clean_raw_value_trims_and_lowercases() -> None:
    assert clean_raw_value(" Sale ") == "sale"
    assert clean_raw_value("FOOD & DRINK") == "food & drink"


def test_classify_transaction_type_uses_cleaned_raw_value() -> None:
    lookup = InMemoryNormalizationLookup(
        global_rules={(NormalizationKind.TRANSACTION_TYPE, "sale"): "SPEND"}
    )
    assert classify_transaction_type(" Sale ", lookup, account_id=1) is TransactionType.SPEND


def test_classify_transaction_type_unknown_when_unmapped_or_absent() -> None:
    lookup = InMemoryNormalizationLookup()
    assert classify_transaction_type("Sale", lookup, account_id=1) is TransactionType.UNKNOWN
    assert classify_transaction_type(None, lookup, account_id=1) is TransactionType.UNKNOWN
    assert classify_transaction_type("  ", lookup, account_id=1) is TransactionType.UNKNOWN


def test_classify_transaction_type_unknown_when_canonical_is_invalid() -> None:
    lookup = InMemoryNormalizationLookup(
        global_rules={(NormalizationKind.TRANSACTION_TYPE, "sale"): "NOT_A_TYPE"}
    )
    assert classify_transaction_type("sale", lookup, account_id=1) is TransactionType.UNKNOWN


def test_classify_category_and_owner_passthrough_none() -> None:
    lookup = InMemoryNormalizationLookup(
        global_rules={
            (NormalizationKind.CATEGORY, "food & drink"): "Dining",
            (NormalizationKind.OWNER, "john"): "John Smith",
        }
    )
    assert classify_category(" Food & Drink ", lookup, account_id=1) == "Dining"
    assert classify_category("Mystery", lookup, account_id=1) is None
    assert classify_category(None, lookup, account_id=1) is None
    assert classify_owner(" John ", lookup, account_id=1) == "John Smith"
    assert classify_owner("Jane", lookup, account_id=1) is None


def test_classify_category_uses_merchant_scope() -> None:
    lookup = InMemoryNormalizationLookup(
        global_rules={(NormalizationKind.CATEGORY, "shopping"): "Shopping"},
        global_merchant_rules={
            (NormalizationKind.CATEGORY, "shopping", "costco"): "Household",
        },
        account_rules={(1, NormalizationKind.CATEGORY, "shopping"): "Account Shop"},
        account_merchant_rules={
            (1, NormalizationKind.CATEGORY, "shopping", "costco"): "Account Costco",
        },
    )
    assert classify_category("Shopping", lookup, account_id=1, merchant="Costco") == (
        "Account Costco"
    )
    assert classify_category("Shopping", lookup, account_id=1, merchant="Amazon") == (
        "Account Shop"
    )
    assert classify_category("Shopping", lookup, account_id=2, merchant="Costco") == (
        "Household"
    )
    assert classify_category("Shopping", lookup, account_id=2, merchant="Amazon") == (
        "Shopping"
    )
    assert classify_category("Shopping", lookup, account_id=2) == "Shopping"


def test_classify_merchant_maps_or_passthrough_none() -> None:
    lookup = InMemoryNormalizationLookup(
        global_rules={(NormalizationKind.MERCHANT, "starbucks"): "Starbucks"}
    )
    assert classify_merchant(" STARBUCKS ", lookup, account_id=1) == "Starbucks"
    assert classify_merchant("AMAZON MARKETPLACE", lookup, account_id=1) is None
    assert classify_merchant(None, lookup, account_id=1) is None
    assert classify_merchant("  ", lookup, account_id=1) is None
