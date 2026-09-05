from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.classification import (
    NormalizationKind,
    TransactionType,
    normalize_mapping_create,
)
from app.models import Account, NormalizationMapping
from app.schemas import (
    ApplyResult,
    DeleteMappingOp,
    MappingDeleteOut,
    MappingPatchIn,
    MappingPatchOut,
    MappingPlanIn,
    MappingPreview,
    NormalizationMappingCreate,
    NormalizationMappingOut,
    UpdateMappingOp,
)
from app.services.ingest_service import AccountNotFoundError
from app.services.mapping_preview_service import (
    MappingPlanValidationError,
    apply_mapping_plan,
    preview_mappings,
)

router = APIRouter(prefix="/mappings", tags=["mappings"])


def _validate_kind(kind: str) -> NormalizationKind:
    try:
        return NormalizationKind(kind)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="kind must be transaction_type, category, owner, or merchant",
        ) from None


def _apply_or_raise(db: Session, plan: MappingPlanIn) -> ApplyResult:
    try:
        return apply_mapping_plan(db, plan)
    except MappingPlanValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors,
        ) from exc
    except AccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


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
    merchant is allowed on kind=category and kind=transaction_type
    (cleaned the same way); omitted means the rule applies to every merchant.
    """
    kind = _validate_kind(payload.kind)
    if kind is NormalizationKind.TRANSACTION_TYPE:
        if payload.canonical_value == "PAYMENT":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="PAYMENT is deprecated; use TRANSFER or INCOME",
            )
        try:
            TransactionType(payload.canonical_value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="canonical_value must be a TransactionType",
            ) from None

    cleaned_merchant = None
    cleaned, cleaned_merchant, mapping_error = normalize_mapping_create(
        kind, payload.raw_value, payload.merchant
    )
    if mapping_error is not None:
        if "merchant scope" in mapping_error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=mapping_error,
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=mapping_error,
        ) from None

    if payload.account_id is not None and db.get(Account, payload.account_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"account {payload.account_id} not found",
        )

    existing_q = select(NormalizationMapping).where(
        NormalizationMapping.kind == kind.value,
    )
    if cleaned is None:
        existing_q = existing_q.where(NormalizationMapping.raw_value.is_(None))
    else:
        existing_q = existing_q.where(NormalizationMapping.raw_value == cleaned)
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


@router.post(
    "/preview",
    response_model=MappingPreview,
    summary="Preview mapping plan impact",
)
def preview_mapping_plan(
    payload: MappingPlanIn,
    db: Session = Depends(get_session),
) -> MappingPreview:
    """Compute per-op impact against stored transactions. Performs no writes."""
    return preview_mappings(db, payload)


@router.post("/apply", response_model=ApplyResult, summary="Apply a mapping plan")
def apply_approved_plan(
    payload: MappingPlanIn,
    db: Session = Depends(get_session),
) -> ApplyResult:
    """Apply create/update/delete ops and reclassify in one transaction."""
    return _apply_or_raise(db, payload)


@router.patch(
    "/{mapping_id}",
    response_model=MappingPatchOut,
    summary="Update a mapping canonical and reclassify",
)
def patch_mapping(
    mapping_id: int,
    payload: MappingPatchIn,
    db: Session = Depends(get_session),
) -> MappingPatchOut:
    row = db.get(NormalizationMapping, mapping_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"mapping {mapping_id} not found",
        )
    result = _apply_or_raise(
        db,
        MappingPlanIn(
            ops=[
                UpdateMappingOp(
                    op="update",
                    mapping_id=mapping_id,
                    canonical_value=payload.canonical_value,
                )
            ]
        ),
    )
    updated = db.get(NormalizationMapping, mapping_id)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"mapping {mapping_id} not found",
        )
    body = NormalizationMappingOut.model_validate(updated).model_dump()
    body["reclass_scanned"] = result.reclass_scanned
    body["reclass_updated"] = result.reclass_updated
    return MappingPatchOut.model_validate(body)


@router.delete(
    "/{mapping_id}",
    response_model=MappingDeleteOut,
    summary="Delete a mapping and reclassify",
)
def delete_mapping(
    mapping_id: int, db: Session = Depends(get_session)
) -> MappingDeleteOut:
    row = db.get(NormalizationMapping, mapping_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"mapping {mapping_id} not found",
        )
    result = _apply_or_raise(
        db,
        MappingPlanIn(ops=[DeleteMappingOp(op="delete", mapping_id=mapping_id)]),
    )
    return MappingDeleteOut(
        deleted_id=mapping_id,
        reclass_scanned=result.reclass_scanned,
        reclass_updated=result.reclass_updated,
    )
