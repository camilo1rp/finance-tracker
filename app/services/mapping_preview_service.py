"""
Preview and apply proposed normalization mappings.

Preview is a pure read: it layers proposed rules over existing ones in memory
and reports per-rule impact without writing or flushing. Apply inserts the
approved rules and reclassifies in a single transaction.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.classification import (
    NormalizationKind,
    TransactionType,
    classify_category,
    classify_merchant,
    classify_owner,
    classify_transaction_type,
    clean_raw_value,
)
from app.domain.db_lookup import merged_lookup_from_db
from app.domain.merged_lookup import MergedNormalizationLookup, RuleMatch, RuleSpec
from app.domain.merchant import resolved_merchant
from app.models import Account, NormalizationMapping, Owner, Transaction
from app.schemas import (
    ApplyMappingPlanIn,
    ApplyResult,
    MappingPreview,
    ProposedMappingIn,
    RuleImpact,
    SampleChange,
    UnmappedValuesOut,
)
from app.services.analytics_service import unmapped_summary
from app.services.ingest_service import _backfill_merchant_raw, run_reclassification

_SAMPLE_CAP = 5


@dataclass
class _RuleAcc:
    index: int
    rule: ProposedMappingIn
    spec: RuleSpec
    duplicate_of_existing_id: int | None
    would_change: int = 0
    suppressed_by_override: int = 0
    shadowed_by_existing: int = 0
    samples: list[SampleChange] = field(default_factory=list)
    changed_txn_ids: set[int] = field(default_factory=set)


def find_mapping_by_identity(
    db: Session,
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
    return db.execute(stmt).scalar_one_or_none()


def validate_proposed_rule(
    db: Session, rule: ProposedMappingIn, index: int
) -> str | None:
    """Same checks as POST /mappings; returns a message or None. `index` is 1-based."""
    try:
        kind = NormalizationKind(rule.kind)
    except ValueError:
        return (
            f"rule {index}: kind must be transaction_type, category, owner, or merchant"
        )
    if kind is NormalizationKind.TRANSACTION_TYPE:
        try:
            TransactionType(rule.canonical_value)
        except ValueError:
            return f"rule {index}: canonical_value must be a TransactionType"
    if rule.merchant is not None and rule.merchant.strip():
        if kind is not NormalizationKind.CATEGORY:
            return f"rule {index}: merchant only valid for category"
    if not str(rule.raw_value).strip():
        return f"rule {index}: raw_value is empty"
    if rule.account_id is not None and db.get(Account, rule.account_id) is None:
        return f"rule {index}: account {rule.account_id} not found"
    return None


def _cleaned_merchant(rule: ProposedMappingIn) -> str | None:
    if rule.merchant is None or not rule.merchant.strip():
        return None
    return clean_raw_value(rule.merchant)


def _rule_spec(rule: ProposedMappingIn, index: int) -> RuleSpec:
    return RuleSpec(
        kind=rule.kind,
        raw_value=clean_raw_value(rule.raw_value),
        canonical_value=rule.canonical_value,
        account_id=rule.account_id,
        merchant=_cleaned_merchant(rule),
        ref=f"proposed:{index}",
    )


def _effective_category(txn: Transaction, normalized: str | None) -> str | None:
    for value in (txn.category_override, normalized, txn.category_raw):
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _winning_db_id(match: RuleMatch | None) -> int | None:
    if match is None or not match.ref.startswith("db:"):
        return None
    return int(match.ref.split(":", 1)[1])


def _row_raw(kind: str, txn: Transaction, merchant_raw: str | None) -> str | None:
    if kind == "transaction_type":
        return txn.raw_type
    if kind == "owner":
        return txn.owner_raw
    if kind == "category":
        return txn.category_raw
    return merchant_raw


def _rule_in_scope(
    spec: RuleSpec,
    txn: Transaction,
    merchant_raw: str | None,
    resolved_merchant_cleaned: str | None,
) -> bool:
    if spec.kind == "transaction_type" and not txn.raw_type:
        return False
    if spec.kind == "owner" and not txn.owner_raw:
        return False
    raw = _row_raw(spec.kind, txn, merchant_raw)
    if raw is None or not str(raw).strip():
        return False
    if clean_raw_value(str(raw)) != spec.raw_value:
        return False
    if spec.account_id is not None and txn.account_id != spec.account_id:
        return False
    if spec.kind == "category" and spec.merchant is not None:
        if resolved_merchant_cleaned != spec.merchant:
            return False
    return True


def _match_for_kind(
    kind: str,
    type_match: RuleMatch | None,
    owner_match: RuleMatch | None,
    category_match: RuleMatch | None,
    merchant_match: RuleMatch | None,
) -> RuleMatch | None:
    return {
        "transaction_type": type_match,
        "owner": owner_match,
        "category": category_match,
        "merchant": merchant_match,
    }[kind]


def preview_mappings(
    db: Session,
    proposed: list[ProposedMappingIn],
    account_id: int | None = None,
) -> MappingPreview:
    """Recompute classification under proposed rules. Read-only: no writes, no flushes."""
    validation_errors: list[str] = []
    accs: list[_RuleAcc] = []
    for i, rule in enumerate(proposed):
        error = validate_proposed_rule(db, rule, i + 1)
        if error is not None:
            validation_errors.append(error)
            continue
        spec = _rule_spec(rule, i)
        existing = find_mapping_by_identity(
            db, spec.kind, spec.raw_value, spec.account_id, spec.merchant
        )
        accs.append(
            _RuleAcc(
                index=i,
                rule=rule,
                spec=spec,
                duplicate_of_existing_id=None if existing is None else existing.id,
            )
        )

    if not accs:
        return MappingPreview(
            scanned=0,
            total_would_change=0,
            rules=[],
            validation_errors=validation_errors,
        )

    kinds = {acc.spec.kind for acc in accs}
    lookup = merged_lookup_from_db(db, [acc.spec for acc in accs], kinds)

    stmt = select(Transaction)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    rows = list(db.scalars(stmt).all())

    accounts = {account.id: account for account in db.scalars(select(Account)).all()}
    owners_by_id = {owner.id: owner.name for owner in db.scalars(select(Owner)).all()}
    owner_ids_by_name = {name: owner_id for owner_id, name in owners_by_id.items()}

    for txn in rows:
        _accumulate_row(
            txn, lookup, accs, accounts, owners_by_id, owner_ids_by_name
        )

    changed_ids: set[int] = set()
    impacts: list[RuleImpact] = []
    for acc in accs:
        changed_ids.update(acc.changed_txn_ids)
        impacts.append(
            RuleImpact(
                rule=acc.rule,
                would_change=acc.would_change,
                suppressed_by_override=acc.suppressed_by_override,
                shadowed_by_existing=acc.shadowed_by_existing,
                duplicate_of_existing_id=acc.duplicate_of_existing_id,
                samples=acc.samples,
            )
        )
    return MappingPreview(
        scanned=len(rows),
        total_would_change=len(changed_ids),
        rules=impacts,
        validation_errors=validation_errors,
    )


def _accumulate_row(
    txn: Transaction,
    lookup: MergedNormalizationLookup,
    accs: list[_RuleAcc],
    accounts: dict[int, Account],
    owners_by_id: dict[int, str],
    owner_ids_by_name: dict[str, int],
) -> None:
    merchant_raw = txn.merchant_raw
    if not merchant_raw:
        merchant_raw = _backfill_merchant_raw(txn, accounts.get(txn.account_id))

    new_type = None
    type_match: RuleMatch | None = None
    if txn.raw_type:
        new_type = classify_transaction_type(txn.raw_type, lookup, txn.account_id)
        type_match = lookup.resolve_with_ref(
            NormalizationKind.TRANSACTION_TYPE,
            clean_raw_value(str(txn.raw_type)),
            txn.account_id,
        )

    new_owner_name = None
    owner_match: RuleMatch | None = None
    if txn.owner_raw:
        new_owner_name = classify_owner(txn.owner_raw, lookup, txn.account_id)
        owner_match = lookup.resolve_with_ref(
            NormalizationKind.OWNER,
            clean_raw_value(str(txn.owner_raw)),
            txn.account_id,
        )

    new_merchant = classify_merchant(merchant_raw, lookup, txn.account_id)
    merchant_match: RuleMatch | None = None
    if merchant_raw and str(merchant_raw).strip():
        merchant_match = lookup.resolve_with_ref(
            NormalizationKind.MERCHANT,
            clean_raw_value(str(merchant_raw)),
            txn.account_id,
        )

    resolved = resolved_merchant(
        merchant_raw, new_merchant, txn.merchant_override
    )
    cleaned_resolved = (
        clean_raw_value(str(resolved)) if resolved and str(resolved).strip() else None
    )
    new_category = classify_category(
        txn.category_raw, lookup, txn.account_id, merchant=resolved
    )
    category_match: RuleMatch | None = None
    if txn.category_raw and str(txn.category_raw).strip():
        category_match = lookup.resolve_with_ref(
            NormalizationKind.CATEGORY,
            clean_raw_value(str(txn.category_raw)),
            txn.account_id,
            cleaned_resolved,
        )

    for acc in accs:
        if not _rule_in_scope(acc.spec, txn, merchant_raw, cleaned_resolved):
            continue
        match = _match_for_kind(
            acc.spec.kind, type_match, owner_match, category_match, merchant_match
        )
        if match is None or match.ref != acc.spec.ref:
            winning_id = _winning_db_id(match)
            if winning_id is None:
                continue
            if acc.duplicate_of_existing_id == winning_id:
                continue
            acc.shadowed_by_existing += 1
            continue

        stored_diff, suppressed, current_eff, new_eff = _field_delta(
            acc.spec.kind,
            txn,
            new_type,
            new_owner_name,
            new_category,
            new_merchant,
            merchant_raw,
            owners_by_id,
            owner_ids_by_name,
        )
        if not stored_diff:
            continue
        if suppressed:
            acc.suppressed_by_override += 1
            continue
        acc.would_change += 1
        acc.changed_txn_ids.add(txn.id)
        if len(acc.samples) < _SAMPLE_CAP:
            acc.samples.append(
                SampleChange(
                    transaction_id=txn.id,
                    description=txn.description,
                    field=acc.spec.kind,  # type: ignore[arg-type]
                    current_effective=current_eff,
                    new_effective=new_eff,
                )
            )


def _field_delta(
    kind: str,
    txn: Transaction,
    new_type: TransactionType | None,
    new_owner_name: str | None,
    new_category: str | None,
    new_merchant: str | None,
    merchant_raw: str | None,
    owners_by_id: dict[int, str],
    owner_ids_by_name: dict[str, int],
) -> tuple[bool, bool, str | None, str | None]:
    if kind == "transaction_type":
        assert new_type is not None
        return (
            txn.transaction_type != new_type.value,
            False,
            txn.transaction_type,
            new_type.value,
        )
    if kind == "owner":
        new_owner_id = owner_ids_by_name.get(new_owner_name) if new_owner_name else None
        current_name = owners_by_id.get(txn.owner_id) if txn.owner_id else None
        return txn.owner_id != new_owner_id, False, current_name, new_owner_name
    if kind == "category":
        stored_diff = txn.category_normalized != new_category
        suppressed = stored_diff and txn.category_override is not None
        return (
            stored_diff,
            suppressed,
            _effective_category(txn, txn.category_normalized),
            _effective_category(txn, new_category),
        )
    stored_diff = txn.merchant_normalized != new_merchant
    suppressed = stored_diff and txn.merchant_override is not None
    current_eff = resolved_merchant(
        txn.merchant_raw, txn.merchant_normalized, txn.merchant_override
    )
    new_eff = resolved_merchant(merchant_raw, new_merchant, txn.merchant_override)
    return stored_diff, suppressed, current_eff, new_eff


class MappingPlanValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def apply_mapping_plan(db: Session, plan: ApplyMappingPlanIn) -> ApplyResult:
    """Insert approved rules and reclassify. Commits once; rolls back on failure."""
    errors = [
        err
        for i, rule in enumerate(plan.rules)
        if (err := validate_proposed_rule(db, rule, i + 1)) is not None
    ]
    if errors:
        raise MappingPlanValidationError(errors)

    created_mapping_ids: list[int] = []
    skipped_duplicates: list[ProposedMappingIn] = []
    pending: list[NormalizationMapping] = []
    try:
        for i, rule in enumerate(plan.rules):
            spec = _rule_spec(rule, i)
            existing = find_mapping_by_identity(
                db, spec.kind, spec.raw_value, spec.account_id, spec.merchant
            )
            if existing is not None:
                skipped_duplicates.append(rule)
                continue
            row = NormalizationMapping(
                kind=spec.kind,
                raw_value=spec.raw_value,
                canonical_value=spec.canonical_value,
                account_id=spec.account_id,
                merchant=spec.merchant,
            )
            db.add(row)
            pending.append(row)
        db.flush()
        created_mapping_ids = [row.id for row in pending]

        reclass = run_reclassification(db, account_id=plan.account_id)
        db.flush()
        unmapped = unmapped_summary(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ApplyResult(
        created_mapping_ids=created_mapping_ids,
        skipped_duplicates=skipped_duplicates,
        reclass_scanned=reclass.scanned,
        reclass_updated=reclass.updated,
        unmapped_after=UnmappedValuesOut(
            transaction_types=unmapped["transaction_types"],
            categories=unmapped["categories"],
            owners=unmapped["owners"],
            merchants=unmapped["merchants"],
        ),
    )
