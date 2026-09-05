"""
In-memory NormalizationLookup over a merged rule list (DB + proposed).

Resolves with the same precedence as DbNormalizationLookup so a proposed
global rule is shadowed by an existing account-scoped rule (and vice versa).
When two rules share an exact scope, the `db:` rule wins over overlay
refs (`proposed:` or `create:`).
"""
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.classification import (
    NormalizationKind,
    NormalizationLookup,
    allows_merchant_scope,
    merchant_scope_matches,
    pattern_matches,
    pattern_rank_key,
)


@dataclass(frozen=True)
class RuleSpec:
    kind: str
    raw_value: str  # already cleaned (trim + lowercase); may include %
    canonical_value: str
    account_id: int | None
    merchant: str | None  # category / transaction_type; None = all merchants
    ref: str  # "db:<mapping_id>", "create:<index>", "update:<index>", or "proposed:<index>"


@dataclass(frozen=True)
class RuleMatch:
    value: str
    ref: str


def _scope_merchant(merchant: str | None) -> str | None:
    if merchant is None or not str(merchant).strip():
        return None
    return merchant


def _is_overlay_ref(ref: str) -> bool:
    return ref.startswith(("proposed:", "create:"))


def _ref_priority(ref: str) -> int:
    return 0 if ref.startswith("db:") else 1


class MergedNormalizationLookup(NormalizationLookup):
    def __init__(self, rules: Sequence[RuleSpec]) -> None:
        self._rules = list(rules)
        # Exact-scope index. On a tie, keep the db: rule (or the first one).
        self._by_scope: dict[
            tuple[str, str, int | None, str | None], RuleSpec
        ] = {}
        for rule in rules:
            key = (
                rule.kind,
                rule.raw_value,
                rule.account_id,
                _scope_merchant(rule.merchant),
            )
            existing = self._by_scope.get(key)
            if existing is None:
                self._by_scope[key] = rule
                continue
            if _is_overlay_ref(existing.ref) and rule.ref.startswith("db:"):
                self._by_scope[key] = rule

    def resolve(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int,
        merchant: str | None = None,
    ) -> str | None:
        """`raw_value` and `merchant` must already be cleaned, same as DbNormalizationLookup."""
        match = self.resolve_with_ref(kind, raw_value, account_id, merchant)
        return None if match is None else match.value

    def resolve_with_ref(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int,
        merchant: str | None = None,
    ) -> RuleMatch | None:
        kind_value = kind.value
        merchant = _scope_merchant(merchant)
        if allows_merchant_scope(kind):
            if merchant is not None:
                hit = self._best_merchant_scoped(
                    kind, raw_value, account_id, merchant
                )
                if hit is not None:
                    return RuleMatch(hit.canonical_value, hit.ref)
            hit = self._best_unscoped(kind_value, raw_value, account_id)
            if hit is not None:
                return RuleMatch(hit.canonical_value, hit.ref)
            if merchant is not None:
                hit = self._best_merchant_scoped(kind, raw_value, None, merchant)
                if hit is not None:
                    return RuleMatch(hit.canonical_value, hit.ref)
            hit = self._best_unscoped(kind_value, raw_value, None)
            if hit is not None:
                return RuleMatch(hit.canonical_value, hit.ref)
            return None

        hit = self._best_unscoped(kind_value, raw_value, account_id)
        if hit is not None:
            return RuleMatch(hit.canonical_value, hit.ref)
        hit = self._best_unscoped(kind_value, raw_value, None)
        if hit is not None:
            return RuleMatch(hit.canonical_value, hit.ref)
        return None

    def _rules_at_scope(
        self,
        kind: str,
        account_id: int | None,
        *,
        merchant_scoped: bool,
    ) -> list[RuleSpec]:
        return [
            rule
            for rule in self._rules
            if rule.kind == kind
            and rule.account_id == account_id
            and (
                (rule.merchant is not None)
                if merchant_scoped
                else rule.merchant is None
            )
        ]

    def _best_unscoped(
        self,
        kind: str,
        raw_value: str,
        account_id: int | None,
    ) -> RuleSpec | None:
        rules = self._rules_at_scope(kind, account_id, merchant_scoped=False)
        matches = [
            rule for rule in rules if pattern_matches(raw_value, rule.raw_value)
        ]
        if not matches:
            return None
        return min(
            matches,
            key=lambda rule: (
                pattern_rank_key(rule.raw_value),
                _ref_priority(rule.ref),
            ),
        )

    def _best_merchant_scoped(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int | None,
        row_merchant: str,
    ) -> RuleSpec | None:
        rules = self._rules_at_scope(kind.value, account_id, merchant_scoped=True)
        matches = [
            rule
            for rule in rules
            if pattern_matches(raw_value, rule.raw_value)
            and rule.merchant is not None
            and merchant_scope_matches(row_merchant, rule.merchant, kind)
        ]
        if not matches:
            return None
        return min(
            matches,
            key=lambda rule: (
                pattern_rank_key(rule.raw_value),
                pattern_rank_key(rule.merchant or ""),
                _ref_priority(rule.ref),
            ),
        )
