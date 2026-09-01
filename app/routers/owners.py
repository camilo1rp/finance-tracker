from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Owner
from app.schemas import OwnerCreate, OwnerOut

router = APIRouter(prefix="/owners", tags=["owners"])


@router.post("", response_model=OwnerOut, status_code=status.HTTP_201_CREATED)
def create_owner(payload: OwnerCreate, db: Session = Depends(get_session)) -> OwnerOut:
    """Register a family member once. Referenced by Account.default_owner_id
    and by Transaction.owner_id (resolved via normalization mappings for
    per-row owner columns like Apple Card's 'Purchased by')."""
    owner = Owner(name=payload.name.strip())
    db.add(owner)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"owner {payload.name!r} already exists",
        ) from None
    db.refresh(owner)
    return owner


@router.get("", response_model=list[OwnerOut])
def list_owners(db: Session = Depends(get_session)) -> list[OwnerOut]:
    return list(db.scalars(select(Owner).order_by(Owner.id)).all())
