"""
Orchestrates: source.fetch() -> normalize (+ classify) -> dedupe -> persist.

Design note: this is the ONLY place that knows the pipeline's stage order.
Routers call this service; they never call domain functions directly.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from langsmith import traceable

from app.domain.classification import (
    TransactionType,
    classify_category,
    classify_merchant,
    classify_owner,
)
from app.domain.db_lookup import DbNormalizationLookup
from app.domain.dedupe import assign_dedupe_hashes, split_new_and_duplicates
from app.domain.mapping import ImportMapping, SignConvention, resolve_mapping
from app.domain.merchant import extract_merchant, resolved_merchant
from app.domain.normalize import normalize_rows
from app.domain.sources import TransactionSource
from app.domain.transaction import CanonicalTransaction, IngestResult, ReclassifyResult, UnmappedValues
from app.domain.transaction_type_resolver import is_effective_spend, resolve_transaction_type
from app.models import Account, ImportBatch, Owner, Transaction


class AccountNotFoundError(ValueError):
    pass


def _service_trace_inputs(inputs: dict) -> dict:
    return {key: value for key, value in inputs.items() if key != "db"}


def mapping_from_stored(data: dict) -> ImportMapping:
    sign = data.get("sign_convention")
    type_col = data.get("type_col")
    sign_enum = SignConvention(sign) if sign else None
    if not type_col and sign_enum is None:
        sign_enum = SignConvention.NEGATIVE_IS_SPEND
    return ImportMapping(
        date_col=data["date_col"],
        description_col=data["description_col"],
        amount_col=data["amount_col"],
        category_col=data.get("category_col"),
        owner_col=data.get("owner_col"),
        type_col=type_col,
        merchant_col=data.get("merchant_col"),
        sign_convention=sign_enum,
    )


def ingest_from_source(
    db: Session,
    account_id: int,
    source: TransactionSource,
    fetch_kwargs: dict,
    filename: str,
    allow_duplicates: bool = False,
) -> IngestResult:
    """
    Full pipeline for one import:
      1. Load Account (+ its ImportMapping, default_owner) from DB.
      2. rows = source.fetch(**fetch_kwargs)
      3. lookup = DbNormalizationLookup(db)
      4. canonical, unmapped = normalize_rows(rows, mapping, account_id,
         default_owner, lookup, account_kind)
      5. assign dedupe_hash for each canonical row (occurrence suffix if
         allow_duplicates)
      6. existing_hashes = query DB for hashes already stored for this account
      7. split = split_new_and_duplicates(canonical, existing_hashes)
      8. resolve owner name -> Owner.id for each row being persisted
      9. create ImportBatch record
      10. bulk insert split.new, tagged with the batch id
      11. return IngestResult (includes `unmapped` for the caller to surface)
    """
    account = db.get(Account, account_id)
    if account is None:
        raise AccountNotFoundError(f"account {account_id} not found")

    mapping = resolve_mapping(mapping_from_stored(account.default_mapping))
    default_owner = None
    if account.default_owner_id is not None:
        owner = db.get(Owner, account.default_owner_id)
        if owner is not None:
            default_owner = owner.name

    rows = source.fetch(**fetch_kwargs)
    lookup = DbNormalizationLookup(db)
    canonical, unmapped, errors = normalize_rows(
        rows,
        mapping,
        account_id,
        default_owner,
        lookup,
        account.account_kind,
    )

    hashed = assign_dedupe_hashes(canonical, allow_duplicates=allow_duplicates)
    existing_hashes = set(
        db.scalars(
            select(Transaction.dedupe_hash).where(Transaction.account_id == account_id)
        ).all()
    )
    split = split_new_and_duplicates(hashed, existing_hashes)

    owner_ids = _owner_ids_by_name(db, split.new)

    batch = ImportBatch(
        account_id=account_id,
        filename=filename,
        imported_at=datetime.now(timezone.utc).replace(tzinfo=None),
        row_count=len(rows),
    )
    db.add(batch)
    db.flush()

    db.add_all(
        [
            Transaction(
                account_id=account_id,
                import_batch_id=batch.id,
                owner_id=owner_ids.get(txn.owner) if txn.owner else None,
                owner_raw=txn.owner_raw,
                transaction_date=txn.transaction_date,
                description=txn.description,
                amount=txn.amount,
                transaction_type=txn.transaction_type.value,
                type_override=None,
                is_spend=txn.is_spend,
                raw_type=txn.raw_type,
                category_raw=txn.category_raw,
                category_normalized=txn.category_normalized,
                category_override=None,
                merchant_raw=txn.merchant_raw,
                merchant_normalized=txn.merchant_normalized,
                merchant_override=None,
                dedupe_hash=txn.dedupe_hash,
                raw=txn.raw,
            )
            for txn in split.new
        ]
    )
    db.commit()

    return IngestResult(
        account_id=account_id,
        import_batch_id=batch.id,
        total_rows_read=len(rows),
        inserted=len(split.new),
        duplicates_skipped=len(split.duplicates),
        unmapped=unmapped,
        errors=errors,
    )


@traceable(process_inputs=_service_trace_inputs)
def run_reclassification(
    db: Session,
    account_id: int | None = None,
) -> ReclassifyResult:
    """Reclassify stored rows without committing. Caller owns the transaction."""
    if account_id is not None and db.get(Account, account_id) is None:
        raise AccountNotFoundError(f"account {account_id} not found")

    stmt = select(Transaction)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    rows = list(db.scalars(stmt).all())

    lookup = DbNormalizationLookup(db)
    owner_ids = {
        owner.name: owner.id
        for owner in db.scalars(select(Owner)).all()
    }
    accounts = {
        account.id: account
        for account in db.scalars(select(Account)).all()
    }

    updated = 0
    unmapped_types: set[str] = set()
    unmapped_categories: set[str] = set()
    unmapped_owners: set[str] = set()
    unmapped_merchants: set[str] = set()
    unmapped_merchants_without_category: set[str] = set()

    for txn in rows:
        changed = False
        account = accounts.get(txn.account_id)
        mapping = (
            mapping_from_stored(account.default_mapping)
            if account is not None
            else ImportMapping(
                date_col="Date",
                description_col="Description",
                amount_col="Amount",
                sign_convention=SignConvention.NEGATIVE_IS_SPEND,
            )
        )
        account_kind = account.account_kind if account is not None else "depository"

        if txn.owner_raw:
            owner_name = classify_owner(txn.owner_raw, lookup, txn.account_id)
            new_owner_id = owner_ids.get(owner_name) if owner_name else None
            if txn.owner_id != new_owner_id:
                txn.owner_id = new_owner_id
                changed = True
            if new_owner_id is None:
                unmapped_owners.add(txn.owner_raw)

        merchant_raw = txn.merchant_raw
        if not merchant_raw:
            merchant_raw = _backfill_merchant_raw(txn, accounts.get(txn.account_id))
            if merchant_raw != txn.merchant_raw:
                txn.merchant_raw = merchant_raw
                changed = True
        new_merchant = classify_merchant(merchant_raw, lookup, txn.account_id)
        if txn.merchant_normalized != new_merchant:
            txn.merchant_normalized = new_merchant
            changed = True
        if merchant_raw and new_merchant is None:
            unmapped_merchants.add(merchant_raw)

        merchant_for_type = resolved_merchant(
            merchant_raw, new_merchant, txn.merchant_override
        )
        new_type = resolve_transaction_type(
            txn.raw_type,
            txn.amount,
            mapping,
            account_kind,
            lookup,
            txn.account_id,
            merchant=merchant_for_type,
        )
        if txn.transaction_type != new_type.value:
            txn.transaction_type = new_type.value
            changed = True
        new_is_spend = is_effective_spend(new_type.value, txn.type_override)
        if txn.is_spend != new_is_spend:
            txn.is_spend = new_is_spend
            changed = True
        if (
            new_type is TransactionType.UNKNOWN
            and txn.raw_type
            and txn.type_override is None
        ):
            unmapped_types.add(txn.raw_type)

        new_category = classify_category(
            txn.category_raw,
            lookup,
            txn.account_id,
            merchant=merchant_for_type,
        )
        if txn.category_normalized != new_category:
            txn.category_normalized = new_category
            changed = True
        if txn.category_raw and new_category is None:
            unmapped_categories.add(txn.category_raw)
        elif (
            (txn.category_raw is None or not str(txn.category_raw).strip())
            and new_category is None
            and txn.category_override is None
            and merchant_for_type
        ):
            unmapped_merchants_without_category.add(str(merchant_for_type).strip())

        if changed:
            updated += 1

    return ReclassifyResult(
        scanned=len(rows),
        updated=updated,
        unmapped=UnmappedValues(
            transaction_types=sorted(unmapped_types),
            categories=sorted(unmapped_categories),
            owners=sorted(unmapped_owners),
            merchants=sorted(unmapped_merchants),
            merchants_without_category=sorted(unmapped_merchants_without_category),
        ),
    )


def reclassify_transactions(
    db: Session,
    account_id: int | None = None,
) -> ReclassifyResult:
    """
    Re-apply current normalization mappings to stored transactions.
    Does not re-import: raw columns stay put. category_override and
    type_override are left alone. Type is always recomputed via the
    resolver (lookup if raw_type, else sign+kind when sign_convention is
    set). is_spend follows effective type. Owner only when owner_raw present.
    """
    result = run_reclassification(db, account_id=account_id)
    db.commit()
    return result


def _backfill_merchant_raw(txn: Transaction, account: Account | None) -> str | None:
    raw = txn.raw or {}
    merchant_col = None
    if account is not None and isinstance(account.default_mapping, dict):
        merchant_col = account.default_mapping.get("merchant_col")
    if merchant_col:
        value = raw.get(merchant_col)
        if value is not None and str(value).strip():
            return " ".join(str(value).split())
    fallback = raw.get("Merchant")
    if fallback is not None and str(fallback).strip():
        return " ".join(str(fallback).split())
    return extract_merchant(txn.description)


def _owner_ids_by_name(
    db: Session, rows: list[CanonicalTransaction]
) -> dict[str, int]:
    names = {txn.owner for txn in rows if txn.owner}
    if not names:
        return {}
    owners = db.scalars(select(Owner).where(Owner.name.in_(names))).all()
    return {owner.name: owner.id for owner in owners}
