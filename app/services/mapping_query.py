"""Read-side queries for normalization mapping rules."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import NormalizationMapping


def list_normalization_mappings(
    db: Session,
    kind: str | None = None,
    account_id: int | None = None,
    include_global: bool = True,
) -> list[NormalizationMapping]:
    """List rules. ``account_id`` plus ``include_global`` returns scoped + global."""
    stmt = select(NormalizationMapping)
    if kind is not None:
        stmt = stmt.where(NormalizationMapping.kind == kind)
    if account_id is not None:
        if include_global:
            stmt = stmt.where(
                or_(
                    NormalizationMapping.account_id == account_id,
                    NormalizationMapping.account_id.is_(None),
                )
            )
        else:
            stmt = stmt.where(NormalizationMapping.account_id == account_id)
    return list(db.scalars(stmt.order_by(NormalizationMapping.id)).all())
