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
    merchant_scope_matches,
    pattern_matches,
    pattern_rank_key,
    pick_best_pattern_match,
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
        caller (classify_* functions do this) -- stored patterns are likewise
        cleaned at write time. `merchant` is likewise cleaned when kind uses
        merchant scope.

        For category and transaction_type: account+merchant, then account,
        then global+merchant, then global. Merchant kind and owner: account
        then global. Patterns may include ``%`` wildcards; exact patterns
        beat wildcards; longer literal text wins among wildcards.
        """
        if allows_merchant_scope(kind):
            if merchant is not None:
                row = self._best_merchant_scoped(
                    kind, raw_value, account_id, merchant
                )
                if row is not None:
                    return row.canonical_value
            row = self._best_unscoped(kind, raw_value, account_id)
            if row is not None:
                return row.canonical_value
            if merchant is not None:
                row = self._best_merchant_scoped(kind, raw_value, None, merchant)
                if row is not None:
                    return row.canonical_value
            row = self._best_unscoped(kind, raw_value, None)
            if row is not None:
                return row.canonical_value
            return None

        row = self._best_unscoped(kind, raw_value, account_id)
        if row is not None:
            return row.canonical_value
        row = self._best_unscoped(kind, raw_value, None)
        if row is not None:
            return row.canonical_value
        return None

    def resolve_empty_category(self, account_id: int, merchant: str) -> str | None:
        row = self._best_empty_category_merchant(account_id, merchant)
        if row is not None:
            return row.canonical_value
        row = self._best_empty_category_merchant(None, merchant)
        if row is not None:
            return row.canonical_value
        return None

    def _rules_at_scope(
        self,
        kind: NormalizationKind,
        account_id: int | None,
        *,
        merchant_scoped: bool,
    ) -> list[NormalizationMapping]:
        stmt = select(NormalizationMapping).where(
            NormalizationMapping.kind == kind.value,
        )
        if account_id is None:
            stmt = stmt.where(NormalizationMapping.account_id.is_(None))
        else:
            stmt = stmt.where(NormalizationMapping.account_id == account_id)
        if merchant_scoped:
            stmt = stmt.where(NormalizationMapping.merchant.isnot(None))
        else:
            stmt = stmt.where(NormalizationMapping.merchant.is_(None))
        return list(self.db.scalars(stmt).all())

    def _best_unscoped(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int | None,
    ) -> NormalizationMapping | None:
        rules = [
            row
            for row in self._rules_at_scope(kind, account_id, merchant_scoped=False)
            if row.raw_value is not None
        ]
        return pick_best_pattern_match(rules, raw_value, lambda row: row.raw_value)

    def _best_merchant_scoped(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int | None,
        row_merchant: str,
    ) -> NormalizationMapping | None:
        rules = self._rules_at_scope(kind, account_id, merchant_scoped=True)
        matches = [
            row
            for row in rules
            if row.raw_value is not None
            and pattern_matches(raw_value, row.raw_value)
            and row.merchant is not None
            and merchant_scope_matches(row_merchant, row.merchant, kind)
        ]
        if not matches:
            return None
        return min(
            matches,
            key=lambda row: (
                pattern_rank_key(row.raw_value),
                pattern_rank_key(row.merchant or ""),
            ),
        )

    def _best_empty_category_merchant(
        self,
        account_id: int | None,
        row_merchant: str,
    ) -> NormalizationMapping | None:
        rules = self._rules_at_scope(
            NormalizationKind.CATEGORY, account_id, merchant_scoped=True
        )
        matches = [
            row
            for row in rules
            if row.raw_value is None
            and row.merchant is not None
            and merchant_scope_matches(row_merchant, row.merchant, NormalizationKind.CATEGORY)
        ]
        if not matches:
            return None
        return min(matches, key=lambda row: pattern_rank_key(row.merchant or ""))



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
