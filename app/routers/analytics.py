from datetime import date
from typing import Callable, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.label_filter import UnknownLabelFilterError
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

GroupBy = Literal["category", "owner", "month", "account", "merchant", "subcategory"]

T = TypeVar("T")


def _call_analytics(fn: Callable[..., T], db: Session, /, **kwargs) -> T:
    try:
        return fn(db, **kwargs)
    except UnknownLabelFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from exc


@router.get("/summary", response_model=list[GroupSummary])
def summary(
    group_by: GroupBy,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return _call_analytics(
        analytics_service.summarize,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        group_by=group_by,
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/by-category", response_model=list[GroupSummary])
def by_category(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return _call_analytics(
        analytics_service.summarize,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        group_by="category",
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/by-subcategory", response_model=list[GroupSummary])
def by_subcategory(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return _call_analytics(
        analytics_service.summarize,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        group_by="subcategory",
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/by-owner", response_model=list[GroupSummary])
def by_owner(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return _call_analytics(
        analytics_service.summarize,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        group_by="owner",
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/by-month", response_model=list[GroupSummary])
def by_month(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    db: Session = Depends(get_session),
) -> list[GroupSummary]:
    return _call_analytics(
        analytics_service.summarize,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        group_by="month",
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/total", response_model=TotalOut)
def total(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    db: Session = Depends(get_session),
) -> TotalOut:
    return _call_analytics(
        analytics_service.get_total,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/cash-flow", response_model=CashFlowOut)
def cash_flow(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    db: Session = Depends(get_session),
) -> CashFlowOut:
    """Household cash-flow by effective type.

    net_cash_flow = income + refunds - purchases - fees. Excludes transfers
    and other (ADJUSTMENT, UNKNOWN). Depository outflows that fund card
    payments may appear as SPEND until overridden — prefer account_id for one account.
    """
    return _call_analytics(
        analytics_service.cash_flow,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        merchant=merchant,
        category=category,
        subcategory=subcategory,
    )


@router.get("/top-merchants", response_model=list[MerchantSummary])
def top_merchants(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_session),
) -> list[MerchantSummary]:
    return _call_analytics(
        analytics_service.top_merchants,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        limit=limit,
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/largest", response_model=TransactionListOut)
def largest(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int = Query(default=10, ge=1),
    db: Session = Depends(get_session),
) -> TransactionListOut:
    return _call_analytics(
        analytics_service.largest_transactions,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        limit=limit,
        merchant=merchant,
        transaction_type=transaction_type,
        category=category,
        subcategory=subcategory,
    )


@router.get("/search", response_model=TransactionListOut)
def search(
    query: str,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int = Query(default=50, ge=1),
    db: Session = Depends(get_session),
) -> TransactionListOut:
    return _call_analytics(
        analytics_service.search_transactions,
        db,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
        query=query,
        limit=limit,
        merchant=merchant,
        category=category,
        subcategory=subcategory,
    )


@router.get("/unmapped", response_model=UnmappedValuesOut)
def unmapped_values(db: Session = Depends(get_session)) -> UnmappedValuesOut:
    """Distinct raw type/category/owner/merchant values that have no
    normalization mapping yet -- a worklist, not a list of failures."""
    return analytics_service.unmapped_summary(db)
