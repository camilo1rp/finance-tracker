from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.classification import AccountKind
from app.domain.mapping import ImportMapping, SignConvention
from app.models import Account, Owner
from app.schemas import AccountCreate, AccountOut, ImportMappingIn
from app.services.seed_mappings_service import seed_account_kind_type_mappings

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _validate_account_kind(value: str) -> AccountKind:
    try:
        return AccountKind(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="account_kind must be credit_card or depository",
        ) from None


def mapping_from_payload(payload: ImportMappingIn) -> ImportMapping:
    sign = None
    if payload.sign_convention is not None:
        try:
            sign = SignConvention(payload.sign_convention)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="invalid sign_convention",
            ) from None
    try:
        return ImportMapping(
            date_col=payload.date_col,
            description_col=payload.description_col,
            amount_col=payload.amount_col,
            category_col=payload.category_col,
            owner_col=payload.owner_col,
            type_col=payload.type_col,
            merchant_col=payload.merchant_col,
            sign_convention=sign,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, db: Session = Depends(get_session)) -> AccountOut:
    """Register a card once: name, last4, owner (if fixed), and its default
    ImportMapping. Re-registering isn't expected monthly -- this is a
    one-time setup step per card."""
    mapping_from_payload(payload.default_mapping)
    account_kind = _validate_account_kind(payload.account_kind)
    if payload.default_owner_id is not None and db.get(Owner, payload.default_owner_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"owner {payload.default_owner_id} not found",
        )
    account = Account(
        name=payload.name,
        last4=payload.last4,
        default_owner_id=payload.default_owner_id,
        source_format=payload.source_format,
        account_kind=account_kind.value,
        default_mapping=payload.default_mapping.model_dump(),
    )
    db.add(account)
    db.flush()
    seed_account_kind_type_mappings(db, account)
    db.commit()
    db.refresh(account)
    return account


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_session)) -> list[AccountOut]:
    return list(db.scalars(select(Account).order_by(Account.id)).all())
