from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.classification import NormalizationKind, TransactionType, clean_raw_value
from app.models import Account, NormalizationMapping
from app.schemas import NormalizationMappingCreate, NormalizationMappingOut

router = APIRouter(prefix="/mappings", tags=["mappings"])


def _validate_kind(kind: str) -> NormalizationKind:
    try:
        return NormalizationKind(kind)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="kind must be transaction_type, category, owner, or merchant",
        ) from None


@router.post("", response_model=NormalizationMappingOut, status_code=status.HTTP_201_CREATED)
def create_mapping(
    payload: NormalizationMappingCreate,
    db: Session = Depends(get_session),
) -> NormalizationMappingOut:
    """
    Register a raw -> canonical rule, e.g.
      {"kind": "transaction_type", "raw_value": "Sale", "canonical_value": "SPEND"}
      {"kind": "category", "raw_value": "Food & Drink", "canonical_value": "Dining"}
      {"kind": "owner", "raw_value": "John", "canonical_value": "John Smith", "account_id": 2}
    account_id omitted -> global rule; set -> overrides for that account only.

    payload.raw_value is passed through clean_raw_value() before storage,
    so it's stored the same way DbNormalizationLookup will look it up
    (trimmed, lowercased) -- "Sale", "sale", " Sale " all collapse to one rule.
    merchant is allowed only on kind=category (cleaned the same way);
    omitted means the category rule applies to every merchant.
    """
    kind = _validate_kind(payload.kind)
    if kind is NormalizationKind.TRANSACTION_TYPE:
        try:
            TransactionType(payload.canonical_value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="canonical_value must be a TransactionType",
            ) from None

    cleaned_merchant = None
    if payload.merchant is not None and payload.merchant.strip():
        if kind is not NormalizationKind.CATEGORY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="merchant scope is only allowed on category mappings",
            )
        cleaned_merchant = clean_raw_value(payload.merchant)

    if payload.account_id is not None and db.get(Account, payload.account_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"account {payload.account_id} not found",
        )

    cleaned = clean_raw_value(payload.raw_value)
    existing_q = select(NormalizationMapping).where(
        NormalizationMapping.kind == kind.value,
        NormalizationMapping.raw_value == cleaned,
    )
    if payload.account_id is None:
        existing_q = existing_q.where(NormalizationMapping.account_id.is_(None))
    else:
        existing_q = existing_q.where(NormalizationMapping.account_id == payload.account_id)
    if cleaned_merchant is None:
        existing_q = existing_q.where(NormalizationMapping.merchant.is_(None))
    else:
        existing_q = existing_q.where(NormalizationMapping.merchant == cleaned_merchant)
    if db.execute(existing_q).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="mapping already exists for this kind/raw_value/account/merchant",
        )

    row = NormalizationMapping(
        kind=kind.value,
        raw_value=cleaned,
        canonical_value=payload.canonical_value,
        account_id=payload.account_id,
        merchant=cleaned_merchant,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=list[NormalizationMappingOut])
def list_mappings(
    kind: str | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_session),
) -> list[NormalizationMappingOut]:
    stmt = select(NormalizationMapping)
    if kind is not None:
        stmt = stmt.where(NormalizationMapping.kind == _validate_kind(kind).value)
    if account_id is not None:
        stmt = stmt.where(NormalizationMapping.account_id == account_id)
    return list(db.scalars(stmt.order_by(NormalizationMapping.id)).all())


@router.delete("/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mapping(mapping_id: int, db: Session = Depends(get_session)) -> None:
    row = db.get(NormalizationMapping, mapping_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"mapping {mapping_id} not found",
        )
    db.delete(row)
    db.commit()
