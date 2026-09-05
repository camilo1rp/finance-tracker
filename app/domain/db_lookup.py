"""
DB-backed implementation of NormalizationLookup.

Design note: this is the one file in domain/ that's allowed to touch the DB
(via an injected Session), because a lookup service is inherently a query.
Kept separate from normalize.py/classification.py so those stay testable
with a fake in-memory lookup that doesn't need this file at all.
"""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.classification import (
    NormalizationKind,
    NormalizationLookup,
    allows_merchant_scope,
    allows_raw_prefix,
    merchant_scope_matches,
    space_bounded_prefix_match,
)
from app.domain.merged_lookup import MergedNormalizationLookup, RuleSpec
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
        when kind uses merchant scope.

        For category and transaction_type: account+merchant, then account,
        then global+merchant, then global. Type merchant scope also accepts
        a space-bounded prefix. Merchant kind: account then global, with a
        space-bounded prefix on raw_value after exact miss. Other kinds:
        account then global (merchant ignored).
        """
        if allows_merchant_scope(kind):
            if merchant is not None:
                row = self._best_merchant_scoped(
                    kind, raw_value, account_id, merchant
                )
                if row is not None:
                    return row.canonical_value
            row = self._find(kind.value, raw_value, account_id, None)
            if row is not None:
                return row.canonical_value
            if merchant is not None:
                row = self._best_merchant_scoped(kind, raw_value, None, merchant)
                if row is not None:
                    return row.canonical_value
            row = self._find(kind.value, raw_value, None, None)
            if row is not None:
                return row.canonical_value
            return None

        row = self._find(kind.value, raw_value, account_id, None)
        if row is not None:
            return row.canonical_value
        if allows_raw_prefix(kind):
            row = self._best_raw_prefix(kind, raw_value, account_id)
            if row is not None:
                return row.canonical_value
        row = self._find(kind.value, raw_value, None, None)
        if row is not None:
            return row.canonical_value
        if allows_raw_prefix(kind):
            row = self._best_raw_prefix(kind, raw_value, None)
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

    def _best_merchant_scoped(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int | None,
        row_merchant: str,
    ) -> NormalizationMapping | None:
        exact = self._find(kind.value, raw_value, account_id, row_merchant)
        if exact is not None:
            return exact
        if kind is not NormalizationKind.TRANSACTION_TYPE:
            return None
        stmt = select(NormalizationMapping).where(
            NormalizationMapping.kind == kind.value,
            NormalizationMapping.raw_value == raw_value,
            NormalizationMapping.merchant.isnot(None),
        )
        if account_id is None:
            stmt = stmt.where(NormalizationMapping.account_id.is_(None))
        else:
            stmt = stmt.where(NormalizationMapping.account_id == account_id)
        matches = [
            row
            for row in self.db.scalars(stmt).all()
            if row.merchant is not None
            and merchant_scope_matches(row_merchant, row.merchant, kind)
        ]
        if not matches:
            return None
        return max(matches, key=lambda row: len(row.merchant or ""))

    def _best_raw_prefix(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int | None,
    ) -> NormalizationMapping | None:
        stmt = select(NormalizationMapping).where(
            NormalizationMapping.kind == kind.value,
            NormalizationMapping.merchant.is_(None),
        )
        if account_id is None:
            stmt = stmt.where(NormalizationMapping.account_id.is_(None))
        else:
            stmt = stmt.where(NormalizationMapping.account_id == account_id)
        matches = [
            row
            for row in self.db.scalars(stmt).all()
            if space_bounded_prefix_match(raw_value, row.raw_value)
        ]
        if not matches:
            return None
        return max(matches, key=lambda row: len(row.raw_value))


def merged_lookup_from_db(
    db: Session,
    proposed: Sequence[RuleSpec],
    kinds: set[str],
) -> MergedNormalizationLookup:
    """Load existing mappings for `kinds` once, then layer `proposed` on top."""
    stmt = select(NormalizationMapping)
    if kinds:
        stmt = stmt.where(NormalizationMapping.kind.in_(kinds))
    existing = [
        RuleSpec(
            kind=row.kind,
            raw_value=row.raw_value,
            canonical_value=row.canonical_value,
            account_id=row.account_id,
            merchant=row.merchant,
            ref=f"db:{row.id}",
        )
        for row in db.scalars(stmt).all()
    ]
    return MergedNormalizationLookup([*existing, *proposed])
