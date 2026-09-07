from datetime import date
from typing import Callable, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.label_filter import UnknownLabelFilterError
from app.schemas import (
    CashFlowOut,
    GroupSummaryPage,
    MerchantSummary,
    TotalOut,
    TransactionListOut,
    UnmappedValuesOut,
    ValueListOut,
)
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])

GroupBy = Literal["category", "owner", "month", "account", "merchant", "subcategory"]
ValueDimension = Literal["category", "subcategory", "merchant"]

T = TypeVar("T")


def _call_analytics(fn: Callable[..., T], db: Session, /, **kwargs) -> T:
    try:
        return fn(db, **kwargs)
    except UnknownLabelFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from exc


@router.get("/summary", response_model=GroupSummaryPage)
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
    limit: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_session),
) -> GroupSummaryPage:
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
        limit=limit,
    )


def _summarize_alias(
    db: Session,
    group_by: GroupBy,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    owner_id: int | None,
    merchant: str | None,
    transaction_type: str | None,
    category: str | None,
    subcategory: str | None,
    limit: int | None,
) -> GroupSummaryPage:
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
        limit=limit,
    )


@router.get("/by-category", response_model=GroupSummaryPage)
def by_category(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_session),
) -> GroupSummaryPage:
    return _summarize_alias(
        db, "category", date_from, date_to, account_id, owner_id,
        merchant, transaction_type, category, subcategory, limit,
    )


@router.get("/by-subcategory", response_model=GroupSummaryPage)
def by_subcategory(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_session),
) -> GroupSummaryPage:
    return _summarize_alias(
        db, "subcategory", date_from, date_to, account_id, owner_id,
        merchant, transaction_type, category, subcategory, limit,
    )


@router.get("/by-owner", response_model=GroupSummaryPage)
def by_owner(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_session),
) -> GroupSummaryPage:
    return _summarize_alias(
        db, "owner", date_from, date_to, account_id, owner_id,
        merchant, transaction_type, category, subcategory, limit,
    )


@router.get("/by-month", response_model=GroupSummaryPage)
def by_month(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    merchant: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_session),
) -> GroupSummaryPage:
    return _summarize_alias(
        db, "month", date_from, date_to, account_id, owner_id,
        merchant, transaction_type, category, subcategory, limit,
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
    """Loose substring search across description, merchant, category, type, and owner fields.

    Optional merchant / category / subcategory are contains-matches, not exact labels.
    """
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


@router.get("/values", response_model=ValueListOut)
def values(
    dimension: ValueDimension,
    query: str | None = None,
    limit: int = Query(default=25, ge=1),
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    owner_id: int | None = None,
    db: Session = Depends(get_session),
) -> ValueListOut:
    """Distinct effective labels with counts. Catalog for category/subcategory/merchant."""
    return analytics_service.list_values(
        db,
        dimension,
        query=query,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        owner_id=owner_id,
    )


@router.get("/unmapped", response_model=UnmappedValuesOut)
def unmapped_values(db: Session = Depends(get_session)) -> UnmappedValuesOut:
    """Distinct raw type/category/owner/merchant values that have no
    normalization mapping yet -- a worklist, not a list of failures."""
    return analytics_service.unmapped_summary(db)
