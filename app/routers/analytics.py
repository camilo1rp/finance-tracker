from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_session
from app.schemas import GroupSummary, MerchantSummary, TotalOut, TransactionOut, UnmappedValuesOut
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])

GroupBy = Literal["category", "owner", "month", "account", "merchant"]


@router.get("/summary", response_model=list[GroupSummary])
def summary(
    group_by: GroupBy,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    spend_only: bool = True,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        group_by,
        spend_only,
        merchant,
    )


@router.get("/by-category", response_model=list[GroupSummary])
def by_category(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    spend_only: bool = True,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        "category",
        spend_only,
        merchant,
    )


@router.get("/by-owner", response_model=list[GroupSummary])
def by_owner(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    spend_only: bool = True,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        "owner",
        spend_only,
        merchant,
    )


@router.get("/by-month", response_model=list[GroupSummary])
def by_month(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    spend_only: bool = True,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        "month",
        spend_only,
        merchant,
    )


@router.get("/total", response_model=TotalOut)
def total(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    spend_only: bool = True,
    db: Session = Depends(get_session),
) -> TotalOut:
    return analytics_service.get_total(
        db, date_from, date_to, account_id, owner_id, spend_only, merchant
    )


@router.get("/top-merchants", response_model=list[MerchantSummary])
def top_merchants(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    spend_only: bool = True,
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_session),
) -> list[MerchantSummary]:
    return analytics_service.top_merchants(
        db, date_from, date_to, account_id, owner_id, spend_only, limit, merchant
    )


@router.get("/largest", response_model=list[TransactionOut])
def largest(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    spend_only: bool = True,
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_session),
) -> list[TransactionOut]:
    return analytics_service.largest_transactions(
        db, date_from, date_to, account_id, owner_id, spend_only, limit, merchant
    )


@router.get("/search", response_model=list[TransactionOut])
def search(
    query: str,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    limit: int = Query(default=50, ge=1),
    db: Session = Depends(get_session),
) -> list[TransactionOut]:
    return analytics_service.search_transactions(
        db, date_from, date_to, account_id, owner_id, query, limit, merchant
    )


@router.get("/unmapped", response_model=UnmappedValuesOut)
def unmapped_values(db: Session = Depends(get_session)) -> UnmappedValuesOut:
    """Distinct raw type/category/owner/merchant values that have no
    normalization mapping yet -- a worklist, not a list of failures."""
    return analytics_service.unmapped_summary(db)
