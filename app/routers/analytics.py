from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_session
from app.schemas import (
    CashFlowOut,
    GroupSummary,
    MerchantSummary,
    TotalOut,
    TransactionListOut,
    UnmappedValuesOut,
)
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
    transaction_type: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        group_by,
        merchant,
        transaction_type,
    )


@router.get("/by-category", response_model=list[GroupSummary])
def by_category(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        "category",
        merchant,
        transaction_type,
    )


@router.get("/by-owner", response_model=list[GroupSummary])
def by_owner(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        "owner",
        merchant,
        transaction_type,
    )


@router.get("/by-month", response_model=list[GroupSummary])
def by_month(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return analytics_service.summarize(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        "month",
        merchant,
        transaction_type,
    )


@router.get("/total", response_model=TotalOut)
def total(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    db: Session = Depends(get_session),
) -> TotalOut:
    return analytics_service.get_total(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        merchant,
        transaction_type,
    )


@router.get("/cash-flow", response_model=CashFlowOut)
def cash_flow(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    db: Session = Depends(get_session),
) -> CashFlowOut:
    """Household cash-flow by effective type.

    net_cash_flow = income + refunds - purchases - fees. Excludes transfers
    and other (ADJUSTMENT, UNKNOWN). Depository outflows that fund card
    payments may appear as SPEND until overridden — prefer account_id for one account.
    """
    return analytics_service.cash_flow(
        db, date_from, date_to, account_id, owner_id, merchant
    )


@router.get("/top-merchants", response_model=list[MerchantSummary])
def top_merchants(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_session),
) -> list[MerchantSummary]:
    return analytics_service.top_merchants(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        limit,
        merchant,
        transaction_type,
    )


@router.get("/largest", response_model=TransactionListOut)
def largest(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_session),
) -> TransactionListOut:
    return analytics_service.largest_transactions(
        db,
        date_from,
        date_to,
        account_id,
        owner_id,
        limit,
        merchant,
        transaction_type,
    )


@router.get("/search", response_model=TransactionListOut)
def search(
    query: str,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    limit: int = Query(default=50, ge=1),
    db: Session = Depends(get_session),
) -> TransactionListOut:
    return analytics_service.search_transactions(
        db, date_from, date_to, account_id, owner_id, query, limit, merchant
    )


@router.get("/unmapped", response_model=UnmappedValuesOut)
def unmapped_values(db: Session = Depends(get_session)) -> UnmappedValuesOut:
    """Distinct raw type/category/owner/merchant values that have no
    normalization mapping yet -- a worklist, not a list of failures."""
    return analytics_service.unmapped_summary(db)
