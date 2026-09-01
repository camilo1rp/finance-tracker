"""
Raw rows -> CanonicalTransaction.

Design note: normalize_row now takes a NormalizationLookup so it can
classify type/category/owner as part of the same pass. Still a pure
function from the caller's perspective -- the lookup is injected, so tests
can pass a fake in-memory implementation instead of a DB-backed one.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.classification import (
    NormalizationLookup,
    TransactionType,
    classify_category,
    classify_merchant,
    classify_owner,
    classify_transaction_type,
)
from app.domain.mapping import ImportMapping, SignConvention
from app.domain.merchant import extract_merchant, resolved_merchant
from app.domain.transaction import CanonicalTransaction, UnmappedValues

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y/%m/%d",
    "%d/%m/%Y",
)


def _cell(row: dict[str, Any], key: str | None) -> Any:
    if key is None:
        return None
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {value!r}")


def _parse_amount(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError(f"unrecognized amount: {value!r}")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"unrecognized amount: {value!r}") from exc


def _type_from_sign(amount: Decimal, convention: SignConvention) -> TransactionType:
    if convention is SignConvention.NEGATIVE_IS_SPEND:
        return TransactionType.SPEND if amount < 0 else TransactionType.PAYMENT
    return TransactionType.SPEND if amount > 0 else TransactionType.PAYMENT


def normalize_row(
    row: dict[str, Any],
    mapping: ImportMapping,
    account_id: int,
    default_owner: str | None,
    lookup: NormalizationLookup,
) -> CanonicalTransaction:
    """
    Convert a single raw row into a CanonicalTransaction.

    Steps:
      1. Extract date/description/amount/category_raw/raw_type/owner_raw
         per `mapping`'s column names.
      2. transaction_type = classify_transaction_type(raw_type, lookup, account_id)
         if mapping.type_col is set; else derive SPEND/PAYMENT from
         mapping.sign_convention against the amount's sign.
      3. is_spend = transaction_type == TransactionType.SPEND
      4. merchant_raw from merchant_col or extract_merchant(description);
         merchant_normalized = classify_merchant(...)
      5. category_normalized = classify_category(..., resolved merchant)
      6. owner = classify_owner(owner_raw, lookup, account_id) if owner_col
         set and present, else default_owner.
    """
    date_value = _cell(row, mapping.date_col)
    description_value = _cell(row, mapping.description_col)
    amount_value = _cell(row, mapping.amount_col)
    if date_value is None:
        raise ValueError(f"missing date column {mapping.date_col!r}")
    if description_value is None:
        raise ValueError(f"missing description column {mapping.description_col!r}")
    if amount_value is None:
        raise ValueError(f"missing amount column {mapping.amount_col!r}")

    transaction_date = _parse_date(date_value)
    amount = _parse_amount(amount_value)
    description = str(description_value).strip()

    raw_type = _cell(row, mapping.type_col) if mapping.type_col else None
    if mapping.type_col:
        raw_type_str = None if raw_type is None else str(raw_type)
        transaction_type = classify_transaction_type(raw_type_str, lookup, account_id)
    else:
        raw_type_str = None
        if mapping.sign_convention is None:
            raise ValueError("ImportMapping requires type_col or sign_convention")
        transaction_type = _type_from_sign(amount, mapping.sign_convention)

    category_raw_value = _cell(row, mapping.category_col) if mapping.category_col else None
    category_raw = None if category_raw_value is None else str(category_raw_value).strip()

    owner_raw_value = _cell(row, mapping.owner_col) if mapping.owner_col else None
    owner_raw = None if owner_raw_value is None else str(owner_raw_value).strip()
    if mapping.owner_col and owner_raw is not None:
        owner = classify_owner(owner_raw, lookup, account_id)
    else:
        owner = default_owner

    if mapping.merchant_col:
        merchant_cell = _cell(row, mapping.merchant_col)
        merchant_raw = (
            " ".join(str(merchant_cell).split()) or None
            if merchant_cell is not None
            else None
        )
    else:
        merchant_raw = extract_merchant(description)
    merchant_normalized = classify_merchant(merchant_raw, lookup, account_id)
    category_normalized = classify_category(
        category_raw,
        lookup,
        account_id,
        merchant=resolved_merchant(merchant_raw, merchant_normalized),
    )

    return CanonicalTransaction(
        account_id=account_id,
        transaction_date=transaction_date,
        description=description,
        amount=amount,
        transaction_type=transaction_type,
        is_spend=transaction_type is TransactionType.SPEND,
        raw_type=None if raw_type_str is None else str(raw_type_str).strip(),
        category_raw=category_raw,
        category_normalized=category_normalized,
        owner=owner,
        owner_raw=owner_raw,
        merchant_raw=merchant_raw,
        merchant_normalized=merchant_normalized,
        raw=dict(row),
    )


def normalize_rows(
    rows: list[dict[str, Any]],
    mapping: ImportMapping,
    account_id: int,
    default_owner: str | None,
    lookup: NormalizationLookup,
) -> tuple[list[CanonicalTransaction], UnmappedValues, list[str]]:
    """
    Applies normalize_row across all rows. Row-level failures are collected,
    not raised, so one bad row doesn't kill the whole import. Also collects
    which raw type/category/owner values resolved to UNKNOWN/None, returned
    as UnmappedValues for the caller to surface in the import result.
    """
    canonical: list[CanonicalTransaction] = []
    errors: list[str] = []
    unmapped_types: set[str] = set()
    unmapped_categories: set[str] = set()
    unmapped_owners: set[str] = set()
    unmapped_merchants: set[str] = set()

    for index, row in enumerate(rows):
        try:
            txn = normalize_row(row, mapping, account_id, default_owner, lookup)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"row {index}: {exc}")
            continue

        if mapping.type_col and txn.raw_type and txn.transaction_type is TransactionType.UNKNOWN:
            unmapped_types.add(txn.raw_type)
        if txn.category_raw and txn.category_normalized is None:
            unmapped_categories.add(txn.category_raw)
        if mapping.owner_col and txn.owner_raw is not None and txn.owner is None:
            unmapped_owners.add(txn.owner_raw)
        if txn.merchant_raw and txn.merchant_normalized is None:
            unmapped_merchants.add(txn.merchant_raw)

        canonical.append(txn)

    return (
        canonical,
        UnmappedValues(
            transaction_types=sorted(unmapped_types),
            categories=sorted(unmapped_categories),
            owners=sorted(unmapped_owners),
            merchants=sorted(unmapped_merchants),
        ),
        errors,
    )
