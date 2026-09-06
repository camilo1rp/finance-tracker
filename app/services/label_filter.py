"""Resolve category/subcategory filter params against stored transaction labels."""
from __future__ import annotations

from dataclasses import dataclass

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.domain.label_filter import (
    UnknownLabelFilterError,
    matching_spellings,
    unknown_label_detail,
)
from app.models import Transaction, effective_category


@dataclass(frozen=True)
class ResolvedLabelFilters:
    categories: tuple[str, ...] = ()
    subcategories: tuple[str, ...] = ()


def available_categories(db: Session) -> list[str]:
    rows = db.scalars(
        select(effective_category)
        .where(effective_category.isnot(None))
        .distinct()
        .order_by(effective_category)
    ).all()
    return [str(row) for row in rows if row is not None and str(row).strip()]


def available_subcategories(db: Session) -> list[str]:
    rows = db.scalars(
        select(Transaction.subcategory)
        .where(Transaction.subcategory.isnot(None))
        .distinct()
        .order_by(Transaction.subcategory)
    ).all()
    return [str(row) for row in rows if row is not None and str(row).strip()]


def resolve_label_filters(
    db: Session,
    category: str | None = None,
    subcategory: str | None = None,
) -> ResolvedLabelFilters:
    """Validate optional filters; omitted params are not checked."""
    errors: list[str] = []
    categories: tuple[str, ...] = ()
    subcategories: tuple[str, ...] = ()

    if category is not None:
        available = available_categories(db)
        matched = matching_spellings(category, available)
        if not matched:
            errors.append(unknown_label_detail("category", category, available))
        else:
            categories = tuple(matched)

    if subcategory is not None:
        available = available_subcategories(db)
        matched = matching_spellings(subcategory, available)
        if not matched:
            errors.append(unknown_label_detail("subcategory", subcategory, available))
        else:
            subcategories = tuple(matched)

    if errors:
        raise UnknownLabelFilterError(errors)

    return ResolvedLabelFilters(categories=categories, subcategories=subcategories)


def apply_resolved_label_filters(
    stmt: Select[Any],
    resolved: ResolvedLabelFilters,
) -> Select[Any]:
    if resolved.categories:
        stmt = stmt.where(effective_category.in_(resolved.categories))
    if resolved.subcategories:
        stmt = stmt.where(Transaction.subcategory.in_(resolved.subcategories))
    return stmt
