"""
DB-backed implementation of NormalizationLookup.

Design note: this is the one file in domain/ that's allowed to touch the DB
(via an injected Session), because a lookup service is inherently a query.
Kept separate from normalize.py/classification.py so those stay testable
with a fake in-memory lookup that doesn't need this file at all.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.classification import NormalizationKind, NormalizationLookup
from app.models import NormalizationMapping


class DbNormalizationLookup(NormalizationLookup):
    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int,
        merchant: str | None = None,
    ) -> str | None:
        """
        `raw_value` must already be cleaned via clean_raw_value() by the
        caller (classify_* functions do this) -- this method does a plain
        equality match against NormalizationMapping.raw_value, which is
        stored cleaned at write time too. `merchant` is likewise cleaned
        when kind=category.

        For category: account+merchant, then account, then global+merchant,
        then global. Other kinds: account then global (merchant ignored).
        """
        if kind is NormalizationKind.CATEGORY:
            if merchant is not None:
                row = self._find(kind.value, raw_value, account_id, merchant)
                if row is not None:
                    return row.canonical_value
            row = self._find(kind.value, raw_value, account_id, None)
            if row is not None:
                return row.canonical_value
            if merchant is not None:
                row = self._find(kind.value, raw_value, None, merchant)
                if row is not None:
                    return row.canonical_value
            row = self._find(kind.value, raw_value, None, None)
            if row is not None:
                return row.canonical_value
            return None

        row = self._find(kind.value, raw_value, account_id, None)
        if row is not None:
            return row.canonical_value
        row = self._find(kind.value, raw_value, None, None)
        if row is not None:
            return row.canonical_value
        return None

    def _find(
        self,
        kind: str,
        raw_value: str,
        account_id: int | None,
        merchant: str | None,
    ) -> NormalizationMapping | None:
        stmt = select(NormalizationMapping).where(
            NormalizationMapping.kind == kind,
            NormalizationMapping.raw_value == raw_value,
        )
        if account_id is None:
            stmt = stmt.where(NormalizationMapping.account_id.is_(None))
        else:
            stmt = stmt.where(NormalizationMapping.account_id == account_id)
        if merchant is None:
            stmt = stmt.where(NormalizationMapping.merchant.is_(None))
        else:
            stmt = stmt.where(NormalizationMapping.merchant == merchant)
        return self.db.execute(stmt).scalar_one_or_none()
