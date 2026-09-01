from app.domain.classification import NormalizationKind, NormalizationLookup


class InMemoryNormalizationLookup(NormalizationLookup):
    """Account-specific rules beat global ones. Keys are already-cleaned values.

    Merchant-scoped category rules (cleaned merchant as the last key part)
    beat the matching unscoped rule at the same account/global level.
    """

    def __init__(
        self,
        global_rules: dict[tuple[NormalizationKind, str], str] | None = None,
        account_rules: dict[tuple[int, NormalizationKind, str], str] | None = None,
        global_merchant_rules: dict[tuple[NormalizationKind, str, str], str]
        | None = None,
        account_merchant_rules: dict[tuple[int, NormalizationKind, str, str], str]
        | None = None,
    ) -> None:
        self.global_rules = global_rules or {}
        self.account_rules = account_rules or {}
        self.global_merchant_rules = global_merchant_rules or {}
        self.account_merchant_rules = account_merchant_rules or {}

    def resolve(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int,
        merchant: str | None = None,
    ) -> str | None:
        if kind is NormalizationKind.CATEGORY and merchant is not None:
            hit = self.account_merchant_rules.get(
                (account_id, kind, raw_value, merchant)
            )
            if hit is not None:
                return hit
        account_hit = self.account_rules.get((account_id, kind, raw_value))
        if account_hit is not None:
            return account_hit
        if kind is NormalizationKind.CATEGORY and merchant is not None:
            hit = self.global_merchant_rules.get((kind, raw_value, merchant))
            if hit is not None:
                return hit
        return self.global_rules.get((kind, raw_value))
