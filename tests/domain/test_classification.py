from app.domain.classification import (
    NormalizationKind,
    TransactionType,
    classify_category,
    classify_merchant,
    classify_owner,
    classify_transaction_type,
    clean_raw_value,
    has_wildcard,
    merchant_scope_matches,
    normalize_mapping_create,
    pattern_matches,
    pattern_rank_key,
    pick_best_pattern_match,
    validate_mapping_pattern,
)
from tests.fakes import InMemoryNormalizationLookup


def test_clean_raw_value_trims_and_lowercases() -> None:
    assert clean_raw_value(" Sale ") == "sale"
    assert clean_raw_value("FOOD & DRINK") == "food & drink"
    assert clean_raw_value("%STARBUCKS%") == "%starbucks%"


def test_pattern_matches_exact_and_wildcards() -> None:
    assert pattern_matches("starbucks", "starbucks")
    assert not pattern_matches("starbucks #59", "starbucks")
    assert pattern_matches("starbucks #59", "starbucks%")
    assert pattern_matches("sq *starbucks", "%starbucks")
    assert pattern_matches("sq *starbucks store", "%starbucks%")
    assert pattern_matches("west union", "west%union")
    assert pattern_matches("western union", "west%union")
    assert pattern_matches("50_off", "50_off")  # underscore is literal
    assert not pattern_matches("starbucks", "%only%")


def test_pattern_rank_key_prefers_exact_and_longer_literals() -> None:
    assert pattern_rank_key("starbucks") < pattern_rank_key("%starbucks%")
    assert pattern_rank_key("starbucks%") < pattern_rank_key("%bucks%")


def test_validate_mapping_pattern_rejects_only_wildcards() -> None:
    assert validate_mapping_pattern("%%") == "pattern cannot be only wildcards"
    assert validate_mapping_pattern("%") == "pattern cannot be only wildcards"
    assert validate_mapping_pattern("starbucks%") is None


def test_pick_best_pattern_match() -> None:
    rules = ["%bucks%", "starbucks%", "starbucks"]
    best = pick_best_pattern_match(rules, "starbucks", lambda item: item)
    assert best == "starbucks"
    prefix_best = pick_best_pattern_match(rules, "starbucks #59", lambda item: item)
    assert prefix_best == "starbucks%"


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


def test_merchant_scope_matches_wildcards() -> None:
    assert merchant_scope_matches("western union", "western union", "transaction_type")
    assert merchant_scope_matches(
        "western union capture 623 web id: 9",
        "western union%",
        "transaction_type",
    )
    assert merchant_scope_matches(
        "western union capture 623 web id: 9",
        "%western union%",
        "category",
    )
    assert not merchant_scope_matches("west", "western union%", "transaction_type")
    assert not has_wildcard("western union")


def test_classify_transaction_type_uses_merchant_scope() -> None:
    lookup = InMemoryNormalizationLookup(
        account_rules={(1, NormalizationKind.TRANSACTION_TYPE, "misc_debit"): "SPEND"},
        account_merchant_rules={
            (
                1,
                NormalizationKind.TRANSACTION_TYPE,
                "misc_debit",
                "western union%",
            ): "TRANSFER",
        },
    )
    assert (
        classify_transaction_type(
            "MISC_DEBIT", lookup, account_id=1, merchant="Western Union"
        )
        is TransactionType.TRANSFER
    )
    assert (
        classify_transaction_type(
            "MISC_DEBIT",
            lookup,
            account_id=1,
            merchant="WESTERN UNION CAPTURE 623 WEB ID: 9",
        )
        is TransactionType.TRANSFER
    )
    assert (
        classify_transaction_type(
            "MISC_DEBIT", lookup, account_id=1, merchant="Woodlake Op"
        )
        is TransactionType.SPEND
    )


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


def test_classify_merchant_uses_raw_value_wildcard() -> None:
    lookup = InMemoryNormalizationLookup(
        account_rules={
            (3, NormalizationKind.MERCHANT, "western union%"): "Western Union",
        },
        global_rules={(NormalizationKind.MERCHANT, "marshalls%"): "Marshalls"},
    )
    assert (
        classify_merchant(
            "WESTERN UNION CAPTURE 623287974331123 WEB ID: 9222993574",
            lookup,
            account_id=3,
        )
        == "Western Union"
    )
    assert classify_merchant("marshalls #59 9425 katy fwy", lookup, account_id=1) == (
        "Marshalls"
    )
    assert classify_merchant("Woodlake Op RENT", lookup, account_id=3) is None


def test_classify_category_empty_raw_uses_merchant_rule() -> None:
    lookup = InMemoryNormalizationLookup(
        global_merchant_rules={
            (NormalizationKind.CATEGORY, None, "irs%"): "taxes",
        },
        account_merchant_rules={
            (1, NormalizationKind.CATEGORY, None, "irs%"): "account taxes",
        },
    )
    assert (
        classify_category(None, lookup, account_id=1, merchant="IRS")
        == "account taxes"
    )
    assert classify_category(None, lookup, account_id=2, merchant="IRS") == "taxes"
    assert classify_category(None, lookup, account_id=1, merchant="Costco") is None
    assert (
        classify_category("Other", lookup, account_id=1, merchant="IRS") is None
    )


def test_normalize_mapping_create_empty_category_requires_merchant() -> None:
    cleaned, merchant, error = normalize_mapping_create(
        NormalizationKind.CATEGORY, None, "irs%"
    )
    assert error is None
    assert cleaned is None
    assert merchant == "irs%"

    _, _, error = normalize_mapping_create(NormalizationKind.CATEGORY, None, None)
    assert error == "category with empty raw_value requires merchant scope"

    _, _, error = normalize_mapping_create(NormalizationKind.OWNER, None, None)
    assert error == "raw_value is empty"
