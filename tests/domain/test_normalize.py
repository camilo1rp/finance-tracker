from datetime import date
from decimal import Decimal

from app.domain.classification import AccountKind, NormalizationKind, TransactionType
from app.domain.mapping import ImportMapping, SignConvention
from app.domain.normalize import normalize_row, normalize_rows
from tests.fakes import InMemoryNormalizationLookup

LOOKUP = InMemoryNormalizationLookup(
    global_rules={
        (NormalizationKind.TRANSACTION_TYPE, "sale"): "SPEND",
        (NormalizationKind.TRANSACTION_TYPE, "return"): "REFUND",
        (NormalizationKind.TRANSACTION_TYPE, "payment"): "TRANSFER",
        (NormalizationKind.CATEGORY, "food & drink"): "Dining",
        (NormalizationKind.OWNER, "john"): "John Smith",
    }
)

TYPE_MAPPING = ImportMapping(
    date_col="Date",
    description_col="Description",
    amount_col="Amount",
    category_col="Category",
    owner_col="Purchased By",
    type_col="Type",
)


def test_normalize_row_classifies_via_lookup() -> None:
    row = {
        "Date": "2024-03-01",
        "Description": "Coffee Shop",
        "Amount": "4.50",
        "Type": " Sale ",
        "Category": "Food & Drink",
        "Purchased By": "John",
    }
    txn = normalize_row(row, TYPE_MAPPING, account_id=1, default_owner="Pat", lookup=LOOKUP)
    assert txn.transaction_date == date(2024, 3, 1)
    assert txn.amount == Decimal("4.50")
    assert txn.transaction_type is TransactionType.SPEND
    assert txn.is_spend is True
    assert txn.raw_type == "Sale"
    assert txn.category_raw == "Food & Drink"
    assert txn.category_normalized == "Dining"
    assert txn.owner == "John Smith"
    assert txn.owner_raw == "John"


def test_normalize_row_uses_default_owner_when_owner_col_empty() -> None:
    row = {
        "Date": "03/02/2024",
        "Description": "Payment",
        "Amount": -100,
        "Type": "Payment",
        "Category": "",
        "Purchased By": "",
    }
    txn = normalize_row(
        row, TYPE_MAPPING, account_id=1, default_owner="Pat", lookup=LOOKUP,
        account_kind=AccountKind.CREDIT_CARD,
    )
    assert txn.owner == "Pat"
    assert txn.owner_raw is None
    assert txn.transaction_type is TransactionType.TRANSFER
    assert txn.is_spend is False
    assert txn.category_raw is None
    assert txn.category_normalized is None


def test_normalize_row_sign_convention_fallback() -> None:
    mapping = ImportMapping(
        date_col="Date",
        description_col="Description",
        amount_col="Amount",
        sign_convention=SignConvention.NEGATIVE_IS_SPEND,
    )
    spend = normalize_row(
        {"Date": "2024-01-01", "Description": "Store", "Amount": "-12.00"},
        mapping,
        account_id=1,
        default_owner=None,
        lookup=InMemoryNormalizationLookup(),
    )
    income = normalize_row(
        {"Date": "2024-01-01", "Description": "Refund-ish", "Amount": "12.00"},
        mapping,
        account_id=1,
        default_owner=None,
        lookup=InMemoryNormalizationLookup(),
        account_kind=AccountKind.DEPOSITORY,
    )
    assert spend.transaction_type is TransactionType.SPEND
    assert income.transaction_type is TransactionType.INCOME
    assert spend.raw_type is None


def test_normalize_rows_collects_errors_and_unmapped() -> None:
    rows = [
        {
            "Date": "not-a-date",
            "Description": "Bad",
            "Amount": "1.00",
            "Type": "Sale",
            "Category": "Food & Drink",
            "Purchased By": "John",
        },
        {
            "Date": "2024-03-04",
            "Description": "Unknown Merchant",
            "Amount": "20.00",
            "Type": "Sale",
            "Category": "Mystery",
            "Purchased By": "Jane",
        },
        {
            "Date": "2024-03-05",
            "Description": "Weird Type",
            "Amount": "1.00",
            "Type": "Adjustment-ish",
            "Category": "Food & Drink",
            "Purchased By": "John",
        },
    ]
    canonical, unmapped, errors = normalize_rows(
        rows, TYPE_MAPPING, account_id=1, default_owner="Pat", lookup=LOOKUP
    )
    assert len(canonical) == 2
    assert len(errors) == 1
    assert "row 0" in errors[0]
    assert unmapped.categories == ["Mystery"]
    assert unmapped.owners == ["Jane"]
    assert unmapped.transaction_types == ["Adjustment-ish"]
    assert unmapped.merchants == ["Unknown Merchant", "Weird Type"]


def test_normalize_apple_uses_merchant_column() -> None:
    mapping = ImportMapping(
        date_col="Transaction Date",
        description_col="Description",
        amount_col="Amount",
        type_col="Type",
        merchant_col="Merchant",
    )
    txn = normalize_row(
        {
            "Transaction Date": "2024-06-10",
            "Description": "WHOLE FOODS STORE 99",
            "Amount": "67.20",
            "Type": "Purchase",
            "Merchant": "Whole Foods",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=InMemoryNormalizationLookup(),
    )
    assert txn.merchant_raw == "Whole Foods"
    assert txn.merchant_normalized is None


def test_normalize_empty_merchant_cell_stays_none() -> None:
    mapping = ImportMapping(
        date_col="Transaction Date",
        description_col="Description",
        amount_col="Amount",
        type_col="Type",
        merchant_col="Merchant",
    )
    txn = normalize_row(
        {
            "Transaction Date": "2024-06-05",
            "Description": "ACH Payment",
            "Amount": "-200.00",
            "Type": "Payment",
            "Merchant": "",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=InMemoryNormalizationLookup(),
    )
    assert txn.merchant_raw is None
    assert txn.merchant_normalized is None


def test_normalize_chase_extracts_merchant_from_description() -> None:
    mapping = ImportMapping(
        date_col="Transaction Date",
        description_col="Description",
        amount_col="Amount",
        type_col="Type",
        category_col="Category",
    )
    lookup = InMemoryNormalizationLookup(
        global_rules={(NormalizationKind.MERCHANT, "starbucks"): "Starbucks"}
    )
    mapped = normalize_row(
        {
            "Transaction Date": "2024-06-01",
            "Description": "STARBUCKS STORE 123",
            "Amount": "12.45",
            "Type": "Sale",
            "Category": "Food & Drink",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=lookup,
    )
    unmapped = normalize_row(
        {
            "Transaction Date": "2024-06-02",
            "Description": "AMAZON MARKETPLACE",
            "Amount": "45.00",
            "Type": "Sale",
            "Category": "Shopping",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=lookup,
    )
    assert mapped.merchant_raw == "STARBUCKS"
    assert mapped.merchant_normalized == "Starbucks"
    assert unmapped.merchant_raw == "AMAZON MARKETPLACE"
    assert unmapped.merchant_normalized is None


def test_normalize_category_uses_resolved_merchant() -> None:
    mapping = ImportMapping(
        date_col="Transaction Date",
        description_col="Description",
        amount_col="Amount",
        type_col="Type",
        category_col="Category",
    )
    lookup = InMemoryNormalizationLookup(
        global_rules={
            (NormalizationKind.MERCHANT, "starbucks"): "Starbucks",
            (NormalizationKind.CATEGORY, "shopping"): "Shopping",
        },
        global_merchant_rules={
            (NormalizationKind.CATEGORY, "shopping", "starbucks"): "Dining",
        },
    )
    coffee = normalize_row(
        {
            "Transaction Date": "2024-06-01",
            "Description": "STARBUCKS STORE 123",
            "Amount": "12.45",
            "Type": "Sale",
            "Category": "Shopping",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=lookup,
    )
    amazon = normalize_row(
        {
            "Transaction Date": "2024-06-02",
            "Description": "AMAZON MARKETPLACE",
            "Amount": "45.00",
            "Type": "Sale",
            "Category": "Shopping",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=lookup,
    )
    assert coffee.merchant_normalized == "Starbucks"
    assert coffee.category_normalized == "Dining"
    assert amazon.merchant_normalized is None
    assert amazon.category_normalized == "Shopping"


def test_normalize_type_uses_resolved_merchant() -> None:
    mapping = ImportMapping(
        date_col="Date",
        description_col="Description",
        amount_col="Amount",
        type_col="Type",
    )
    lookup = InMemoryNormalizationLookup(
        account_rules={(1, NormalizationKind.TRANSACTION_TYPE, "misc_debit"): "SPEND"},
        account_merchant_rules={
            (1, NormalizationKind.TRANSACTION_TYPE, "misc_debit", "western union%"): (
                "TRANSFER"
            ),
        },
    )
    wu = normalize_row(
        {
            "Date": "2026-08-20",
            "Description": "WESTERN UNION       CAPTURE 623287974331123 WEB ID: 9222993574",
            "Amount": "-2000.00",
            "Type": "MISC_DEBIT",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=lookup,
    )
    rent = normalize_row(
        {
            "Date": "2026-08-04",
            "Description": "Woodlake Op      RENT       270209230       WEB ID: 1861072180",
            "Amount": "-2146.00",
            "Type": "MISC_DEBIT",
        },
        mapping,
        account_id=1,
        default_owner="Pat",
        lookup=lookup,
    )
    assert wu.transaction_type is TransactionType.TRANSFER
    assert wu.is_spend is False
    assert rent.transaction_type is TransactionType.SPEND
    assert rent.is_spend is True
