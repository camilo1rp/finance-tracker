from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.classification import TransactionType
from app.models import Owner, Transaction, TransactionOverride, effective_category, effective_merchant
from app.domain.transaction_type_resolver import is_effective_spend
from app.schemas import ReclassifyResultOut, TransactionOut, TransactionPatch, UnmappedValuesOut
from app.services.ingest_service import AccountNotFoundError, reclassify_transactions

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("/reclassify", response_model=ReclassifyResultOut)
def reclassify(
    account_id: int | None = None,
    db: Session = Depends(get_session),
) -> ReclassifyResultOut:
    """Re-apply current mapping rules to stored rows (does not re-import).
    Use after adding Return/Fee/category/owner mappings so UNKNOWN rows update."""
    try:
        result = reclassify_transactions(db, account_id=account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ReclassifyResultOut(
        scanned=result.scanned,
        updated=result.updated,
        unmapped=UnmappedValuesOut(
            transaction_types=result.unmapped.transaction_types,
            categories=result.unmapped.categories,
            owners=result.unmapped.owners,
            merchants=result.unmapped.merchants,
        ),
    )


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    date_from: date | None = None,
    date_to: date | None = None,
    owner_id: int | None = None,
    category: str | None = None,
    merchant: str | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_session),
) -> list[TransactionOut]:
    """`category` filters against effective category (override, else
    normalized, else raw) -- exact precedence handled in the service layer."""
    stmt = select(Transaction)
    if date_from is not None:
        stmt = stmt.where(Transaction.transaction_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.transaction_date <= date_to)
    if owner_id is not None:
        stmt = stmt.where(Transaction.owner_id == owner_id)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if category is not None:
        stmt = stmt.where(effective_category == category)
    if merchant is not None:
        stmt = stmt.where(effective_merchant == merchant)
    stmt = stmt.order_by(Transaction.transaction_date, Transaction.id)
    return list(db.scalars(stmt).all())


@router.patch("/{transaction_id}", response_model=TransactionOut)
def patch_transaction(
    transaction_id: int,
    payload: TransactionPatch,
    db: Session = Depends(get_session),
) -> TransactionOut:
    """Fix a miscategorized transaction or correct an owner -- never touches
    category_raw, only category_override, so the original import is preserved."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"transaction {transaction_id} not found",
        )
    if "category_override" in payload.model_fields_set:
        if txn.category_override != payload.category_override:
            provenance = db.execute(
                select(TransactionOverride).where(
                    TransactionOverride.transaction_id == txn.id
                )
            ).scalar_one_or_none()
            if provenance is not None:
                db.delete(provenance)
        txn.category_override = payload.category_override
    if "merchant_override" in payload.model_fields_set:
        txn.merchant_override = payload.merchant_override
    if "owner_id" in payload.model_fields_set:
        if payload.owner_id is not None and db.get(Owner, payload.owner_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"owner {payload.owner_id} not found",
            )
        txn.owner_id = payload.owner_id
    if "type_override" in payload.model_fields_set:
        if payload.type_override is not None:
            try:
                TransactionType(payload.type_override)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="type_override must be a TransactionType",
                ) from None
        txn.type_override = payload.type_override
        txn.is_spend = is_effective_spend(txn.transaction_type, txn.type_override)
    db.commit()
    db.refresh(txn)
    return txn
