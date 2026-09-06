"""
Preview and apply mapping plans (create / update / delete).

Preview is a pure read: it builds a virtual merged rule set in memory and
reports per-op impact without writing or flushing. Apply mutates the approved
ops and reclassifies in a single transaction.
"""
from dataclasses import dataclass, field
from typing import Literal

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.classification import (
    NormalizationKind,
    TransactionType,
    allows_merchant_scope,
    classify_category,
    classify_merchant,
    classify_owner,
    clean_raw_value,
    merchant_scope_matches,
    normalize_mapping_create,
    pattern_matches,
)
from app.domain.transaction_type_resolver import resolve_transaction_type
from app.domain.merged_lookup import MergedNormalizationLookup, RuleMatch, RuleSpec
from app.domain.merchant import resolved_merchant
from app.models import (
    Account,
    NormalizationMapping,
    Owner,
    Transaction,
    TransactionOverride,
    effective_category,
)
from app.schemas import (
    ApplyResult,
    CreateMappingOp,
    DeleteMappingOp,
    FallbackCount,
    MappingOp,
    MappingPlanIn,
    MappingPreview,
    OpImpact,
    OverridePreview,
    RemoveTransactionOverrideOp,
    SampleChange,
    SetTransactionCategoryOp,
    SkippedOp,
    UnmappedValuesOut,
    UpdateMappingOp,
)
from app.services.analytics_service import unmapped_summary
from app.services.ingest_service import (
    _backfill_merchant_raw,
    mapping_from_stored,
    run_reclassification,
)

_SAMPLE_CAP = 5
_OpType = Literal["create", "update", "delete"]
_OVERRIDE_OP_TYPES = {"set_transaction_category", "remove_transaction_override"}


def _service_trace_inputs(inputs: dict) -> dict:
    return {key: value for key, value in inputs.items() if key != "db"}


@dataclass
class _RowClassified:
    merchant_raw: str | None
    cleaned_resolved: str | None
    new_type: TransactionType | None
    new_owner_name: str | None
    new_category: str | None
    new_merchant: str | None
    type_match: RuleMatch | None
    owner_match: RuleMatch | None
    category_match: RuleMatch | None
    merchant_match: RuleMatch | None


@dataclass
class _OpAcc:
    index: int
    op: MappingOp
    op_type: _OpType
    spec: RuleSpec | None
    mapping_id: int | None = None
    duplicate_of_existing_id: int | None = None
    conflicts_with_existing_id: int | None = None
    existing_canonical: str | None = None
    old_canonical: str | None = None
    new_canonical: str | None = None
    would_change: int = 0
    suppressed_by_override: int = 0
    shadowed_by_existing: int = 0
    falls_back_to: dict[int, int] = field(default_factory=dict)
    would_become_unmapped: int = 0
    samples: list[SampleChange] = field(default_factory=list)
    changed_txn_ids: set[int] = field(default_factory=set)


def find_mapping_by_identity(
    db: Session,
    kind: str,
    raw_value: str | None,
    account_id: int | None,
    merchant: str | None,
) -> NormalizationMapping | None:
    stmt = select(NormalizationMapping).where(
        NormalizationMapping.kind == kind,
    )
    if raw_value is None:
        stmt = stmt.where(NormalizationMapping.raw_value.is_(None))
    else:
        stmt = stmt.where(NormalizationMapping.raw_value == raw_value)
    if account_id is None:
        stmt = stmt.where(NormalizationMapping.account_id.is_(None))
    else:
        stmt = stmt.where(NormalizationMapping.account_id == account_id)
    if merchant is None:
        stmt = stmt.where(NormalizationMapping.merchant.is_(None))
    else:
        stmt = stmt.where(NormalizationMapping.merchant == merchant)
    return db.execute(stmt).scalar_one_or_none()


def _cleaned_merchant(merchant: str | None) -> str | None:
    if merchant is None or not merchant.strip():
        return None
    return clean_raw_value(merchant)


def _create_spec(op: CreateMappingOp, index: int) -> RuleSpec:
    raw = None
    if op.raw_value is not None and str(op.raw_value).strip():
        raw = clean_raw_value(op.raw_value)
    return RuleSpec(
        kind=op.kind,
        raw_value=raw,
        canonical_value=op.canonical_value,
        account_id=op.account_id,
        merchant=_cleaned_merchant(op.merchant),
        ref=f"create:{index}",
    )


def _spec_from_row(row: NormalizationMapping, ref: str) -> RuleSpec:
    return RuleSpec(
        kind=row.kind,
        raw_value=row.raw_value,
        canonical_value=row.canonical_value,
        account_id=row.account_id,
        merchant=row.merchant,
        ref=ref,
    )


def validate_create_op(db: Session, op: CreateMappingOp, index: int) -> str | None:
    """Same checks as POST /mappings; returns a message or None. `index` is 1-based."""
    try:
        kind = NormalizationKind(op.kind)
    except ValueError:
        return (
            f"op {index}: kind must be transaction_type, category, owner, or merchant"
        )
    if kind is NormalizationKind.TRANSACTION_TYPE:
        try:
            TransactionType(op.canonical_value)
        except ValueError:
            return f"op {index}: canonical_value must be a TransactionType"
    _, _, mapping_error = normalize_mapping_create(
        NormalizationKind(op.kind), op.raw_value, op.merchant
    )
    if mapping_error is not None:
        return f"op {index}: {mapping_error}"
    if op.account_id is not None and db.get(Account, op.account_id) is None:
        return f"op {index}: account {op.account_id} not found"
    return None


def _validate_type_canonical(canonical_value: str, kind: str, index: int) -> str | None:
    if kind != "transaction_type":
        return None
    try:
        TransactionType(canonical_value)
    except ValueError:
        return f"op {index}: canonical_value must be a TransactionType"
    return None


def parse_plan_ops(
    db: Session,
    plan: MappingPlanIn,
    *,
    allow_missing_delete: bool = False,
) -> tuple[list[_OpAcc], list[str]]:
    """Validate ops. Invalid ones go to errors and are omitted from the acc list."""
    errors: list[str] = []
    accs: list[_OpAcc] = []
    seen_ids: dict[int, int] = {}

    for i, op in enumerate(plan.ops):
        label = i + 1
        if isinstance(op, CreateMappingOp):
            error = validate_create_op(db, op, label)
            if error is not None:
                errors.append(error)
                continue
            spec = _create_spec(op, i)
            existing = find_mapping_by_identity(
                db, spec.kind, spec.raw_value, spec.account_id, spec.merchant
            )
            duplicate_of = None
            conflicts_with = None
            existing_canonical = None
            if existing is not None:
                if existing.canonical_value == spec.canonical_value:
                    duplicate_of = existing.id
                else:
                    conflicts_with = existing.id
                    existing_canonical = existing.canonical_value
            accs.append(
                _OpAcc(
                    index=i,
                    op=op,
                    op_type="create",
                    spec=spec,
                    duplicate_of_existing_id=duplicate_of,
                    conflicts_with_existing_id=conflicts_with,
                    existing_canonical=existing_canonical,
                )
            )
            continue

        mapping_id = op.mapping_id
        prior = seen_ids.get(mapping_id)
        if prior is not None:
            errors.append(
                f"op {label}: mapping_id {mapping_id} appears in more than one op"
            )
            continue
        seen_ids[mapping_id] = i
        row = db.get(NormalizationMapping, mapping_id)
        if row is None:
            if allow_missing_delete and isinstance(op, DeleteMappingOp):
                accs.append(
                    _OpAcc(
                        index=i,
                        op=op,
                        op_type="delete",
                        spec=None,
                        mapping_id=mapping_id,
                    )
                )
                continue
            errors.append(f"op {label}: mapping {mapping_id} not found")
            continue

        if isinstance(op, UpdateMappingOp):
            type_error = _validate_type_canonical(op.canonical_value, row.kind, label)
            if type_error is not None:
                errors.append(type_error)
                continue
            spec = RuleSpec(
                kind=row.kind,
                raw_value=row.raw_value,
                canonical_value=op.canonical_value,
                account_id=row.account_id,
                merchant=row.merchant,
                ref=f"update:{i}",
            )
            accs.append(
                _OpAcc(
                    index=i,
                    op=op,
                    op_type="update",
                    spec=spec,
                    mapping_id=row.id,
                    old_canonical=row.canonical_value,
                    new_canonical=op.canonical_value,
                )
            )
            continue

        spec = _spec_from_row(row, ref=f"db:{row.id}")
        accs.append(
            _OpAcc(
                index=i,
                op=op,
                op_type="delete",
                spec=spec,
                mapping_id=row.id,
                old_canonical=row.canonical_value,
            )
        )

    return accs, errors


def _conflict_errors(accs: list[_OpAcc]) -> list[str]:
    errors: list[str] = []
    for acc in accs:
        if acc.conflicts_with_existing_id is None or acc.spec is None:
            continue
        errors.append(
            f"op {acc.index + 1}: conflicts with mapping {acc.conflicts_with_existing_id} "
            f"(existing canonical {acc.existing_canonical!r}, "
            f"proposed {acc.spec.canonical_value!r})"
        )
    return errors


def _partition_ops(
    ops: list[MappingOp],
) -> tuple[list[MappingOp], list[SetTransactionCategoryOp | RemoveTransactionOverrideOp]]:
    rule_ops: list[MappingOp] = []
    override_ops: list[SetTransactionCategoryOp | RemoveTransactionOverrideOp] = []
    for op in ops:
        if isinstance(op, (SetTransactionCategoryOp, RemoveTransactionOverrideOp)):
            override_ops.append(op)
        else:
            rule_ops.append(op)
    return rule_ops, override_ops


_MUTATING_OVERRIDE_ACTIONS = frozenset({"set", "remove"})


def _override_change_ids(overrides: list[OverridePreview]) -> set[int]:
    return {
        item.transaction_id
        for item in overrides
        if item.action in _MUTATING_OVERRIDE_ACTIONS
    }


def preview_override_ops(
    db: Session, ops: list[SetTransactionCategoryOp | RemoveTransactionOverrideOp]
) -> list[OverridePreview]:
    previews: list[OverridePreview] = []
    for op in ops:
        txn = db.get(Transaction, op.transaction_id)
        if txn is None:
            previews.append(
                OverridePreview(
                    transaction_id=op.transaction_id,
                    exists=False,
                    current_override=None,
                    current_effective_category=None,
                    proposed=op.category if isinstance(op, SetTransactionCategoryOp) else None,
                    action="missing",
                    evidence_ids=list(getattr(op, "evidence_ids", []) or []),
                )
            )
            continue
        current_effective = db.scalar(
            select(effective_category).where(Transaction.id == txn.id)
        )
        if isinstance(op, SetTransactionCategoryOp):
            proposed = op.category
            if txn.category_override == proposed:
                action = "noop"
            elif txn.category_override is not None:
                action = "replace_conflict"
            else:
                action = "set"
        else:
            proposed = None
            action = "remove" if txn.category_override is not None else "remove_noop"
        previews.append(
            OverridePreview(
                transaction_id=txn.id,
                exists=True,
                current_override=txn.category_override,
                current_effective_category=current_effective,
                proposed=proposed,
                action=action,
                evidence_ids=list(getattr(op, "evidence_ids", []) or []),
            )
        )
    return previews


def _db_specs(rows: list[NormalizationMapping]) -> list[RuleSpec]:
    return [_spec_from_row(row, ref=f"db:{row.id}") for row in rows]


def _virtual_specs(
    db_rows: list[NormalizationMapping], accs: list[_OpAcc]
) -> list[RuleSpec]:
    deleted_ids = {acc.mapping_id for acc in accs if acc.op_type == "delete"}
    updates = {
        acc.mapping_id: acc for acc in accs if acc.op_type == "update" and acc.spec
    }
    specs: list[RuleSpec] = []
    for row in db_rows:
        if row.id in deleted_ids:
            continue
        updated = updates.get(row.id)
        if updated is not None and updated.spec is not None:
            specs.append(updated.spec)
            continue
        specs.append(_spec_from_row(row, ref=f"db:{row.id}"))
    for acc in accs:
        if acc.op_type != "create" or acc.spec is None:
            continue
        if acc.duplicate_of_existing_id is not None or acc.conflicts_with_existing_id:
            continue
        specs.append(acc.spec)
    return specs


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


def _category_raw_empty(raw: str | None) -> bool:
    return raw is None or not str(raw).strip()


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
    if spec.kind == "category" and spec.raw_value is None:
        if not _category_raw_empty(txn.category_raw):
            return False
        if spec.merchant is None or resolved_merchant_cleaned is None:
            return False
        if not merchant_scope_matches(
            resolved_merchant_cleaned, spec.merchant, spec.kind
        ):
            return False
        if spec.account_id is not None and txn.account_id != spec.account_id:
            return False
        return True
    raw = _row_raw(spec.kind, txn, merchant_raw)
    if raw is None or not str(raw).strip():
        return False
    cleaned_raw = clean_raw_value(str(raw))
    if not pattern_matches(cleaned_raw, spec.raw_value):
        return False
    if spec.account_id is not None and txn.account_id != spec.account_id:
        return False
    if spec.merchant is not None and allows_merchant_scope(spec.kind):
        if not merchant_scope_matches(
            resolved_merchant_cleaned, spec.merchant, spec.kind
        ):
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


def _classify_row(
    txn: Transaction,
    lookup: MergedNormalizationLookup,
    accounts: dict[int, Account],
) -> _RowClassified:
    merchant_raw = txn.merchant_raw
    if not merchant_raw:
        merchant_raw = _backfill_merchant_raw(txn, accounts.get(txn.account_id))

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

    new_type = None
    type_match: RuleMatch | None = None
    account = accounts.get(txn.account_id)
    if account is not None:
        mapping = mapping_from_stored(account.default_mapping)
        new_type = resolve_transaction_type(
            txn.raw_type,
            txn.amount,
            mapping,
            account.account_kind,
            lookup,
            txn.account_id,
            merchant=resolved,
        )
    if txn.raw_type:
        type_match = lookup.resolve_with_ref(
            NormalizationKind.TRANSACTION_TYPE,
            clean_raw_value(str(txn.raw_type)),
            txn.account_id,
            cleaned_resolved,
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
    elif cleaned_resolved is not None:
        category_match = lookup.resolve_empty_category_with_ref(
            txn.account_id, cleaned_resolved
        )

    return _RowClassified(
        merchant_raw=merchant_raw,
        cleaned_resolved=cleaned_resolved,
        new_type=new_type,
        new_owner_name=new_owner_name,
        new_category=new_category,
        new_merchant=new_merchant,
        type_match=type_match,
        owner_match=owner_match,
        category_match=category_match,
        merchant_match=merchant_match,
    )


@traceable(process_inputs=_service_trace_inputs)
def preview_mappings(db: Session, plan: MappingPlanIn) -> MappingPreview:
    """Recompute classification under the plan's virtual rule set. Read-only."""
    rule_ops, override_ops = _partition_ops(plan.ops)
    accs, validation_errors = parse_plan_ops(
        db, MappingPlanIn(ops=rule_ops, account_id=plan.account_id)
    )
    override_preview = preview_override_ops(db, override_ops)
    override_changed_ids = _override_change_ids(override_preview)
    if not accs:
        return MappingPreview(
            scanned=0,
            total_would_change=len(override_changed_ids),
            ops=[],
            overrides=override_preview,
            validation_errors=validation_errors,
        )

    db_rows = list(db.scalars(select(NormalizationMapping)).all())
    current_lookup = MergedNormalizationLookup(_db_specs(db_rows))
    merged_lookup = MergedNormalizationLookup(_virtual_specs(db_rows, accs))
    has_deletes = any(acc.op_type == "delete" for acc in accs)

    stmt = select(Transaction)
    if plan.account_id is not None:
        stmt = stmt.where(Transaction.account_id == plan.account_id)
    rows = list(db.scalars(stmt).all())

    accounts = {account.id: account for account in db.scalars(select(Account)).all()}
    owners_by_id = {owner.id: owner.name for owner in db.scalars(select(Owner)).all()}
    owner_ids_by_name = {name: owner_id for owner_id, name in owners_by_id.items()}

    for txn in rows:
        merged = _classify_row(txn, merged_lookup, accounts)
        current = (
            _classify_row(txn, current_lookup, accounts)
            if has_deletes
            else merged
        )
        _accumulate_row(
            txn, merged, current, accs, owners_by_id, owner_ids_by_name
        )

    changed_ids: set[int] = set(override_changed_ids)
    impacts: list[OpImpact] = []
    for acc in accs:
        changed_ids.update(acc.changed_txn_ids)
        impacts.append(_impact_from_acc(acc))
    return MappingPreview(
        scanned=len(rows),
        total_would_change=len(changed_ids),
        ops=impacts,
        overrides=override_preview,
        validation_errors=validation_errors,
    )


def _impact_from_acc(acc: _OpAcc) -> OpImpact:
    return OpImpact(
        index=acc.index,
        op=acc.op,
        would_change=acc.would_change,
        suppressed_by_override=acc.suppressed_by_override,
        shadowed_by_existing=acc.shadowed_by_existing,
        duplicate_of_existing_id=acc.duplicate_of_existing_id,
        conflicts_with_existing_id=acc.conflicts_with_existing_id,
        existing_canonical=acc.existing_canonical,
        old_canonical=acc.old_canonical,
        new_canonical=acc.new_canonical,
        falls_back_to=[
            FallbackCount(mapping_id=mapping_id, count=count)
            for mapping_id, count in sorted(acc.falls_back_to.items())
        ],
        would_become_unmapped=acc.would_become_unmapped,
        samples=acc.samples,
    )


def _accumulate_row(
    txn: Transaction,
    merged: _RowClassified,
    current: _RowClassified,
    accs: list[_OpAcc],
    owners_by_id: dict[int, str],
    owner_ids_by_name: dict[str, int],
) -> None:
    for acc in accs:
        if acc.op_type == "delete":
            _accumulate_delete(
                txn, merged, current, acc, owners_by_id, owner_ids_by_name
            )
            continue
        if acc.duplicate_of_existing_id is not None or acc.conflicts_with_existing_id:
            continue
        if acc.spec is None:
            continue
        if not _rule_in_scope(
            acc.spec, txn, merged.merchant_raw, merged.cleaned_resolved
        ):
            continue
        match = _match_for_kind(
            acc.spec.kind,
            merged.type_match,
            merged.owner_match,
            merged.category_match,
            merged.merchant_match,
        )
        if match is None or match.ref != acc.spec.ref:
            if acc.op_type == "create":
                winning_id = _winning_db_id(match)
                if winning_id is None:
                    continue
                acc.shadowed_by_existing += 1
            continue
        _count_change(txn, merged, acc, owners_by_id, owner_ids_by_name)


def _accumulate_delete(
    txn: Transaction,
    merged: _RowClassified,
    current: _RowClassified,
    acc: _OpAcc,
    owners_by_id: dict[int, str],
    owner_ids_by_name: dict[str, int],
) -> None:
    if acc.spec is None or acc.mapping_id is None:
        return
    current_match = _match_for_kind(
        acc.spec.kind,
        current.type_match,
        current.owner_match,
        current.category_match,
        current.merchant_match,
    )
    if current_match is None or current_match.ref != f"db:{acc.mapping_id}":
        return
    merged_match = _match_for_kind(
        acc.spec.kind,
        merged.type_match,
        merged.owner_match,
        merged.category_match,
        merged.merchant_match,
    )
    if merged_match is not None and (
        merged_match.ref.startswith("create:") or merged_match.ref.startswith("update:")
    ):
        return
    counted = _count_change(txn, merged, acc, owners_by_id, owner_ids_by_name)
    if not counted:
        return
    if merged_match is None:
        if (
            acc.spec.kind == "transaction_type"
            and merged.new_type is not None
            and merged.new_type is not TransactionType.UNKNOWN
        ):
            return
        acc.would_become_unmapped += 1
        return
    fallback_id = _winning_db_id(merged_match)
    if fallback_id is not None:
        acc.falls_back_to[fallback_id] = acc.falls_back_to.get(fallback_id, 0) + 1
        return
    acc.would_become_unmapped += 1


def _count_change(
    txn: Transaction,
    merged: _RowClassified,
    acc: _OpAcc,
    owners_by_id: dict[int, str],
    owner_ids_by_name: dict[str, int],
) -> bool:
    if acc.spec is None:
        return False
    stored_diff, suppressed, current_eff, new_eff = _field_delta(
        acc.spec.kind,
        txn,
        merged.new_type,
        merged.new_owner_name,
        merged.new_category,
        merged.new_merchant,
        merged.merchant_raw,
        owners_by_id,
        owner_ids_by_name,
    )
    if not stored_diff:
        return False
    if suppressed:
        acc.suppressed_by_override += 1
        return False
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
    return True


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


def _override_conflict_errors(
    db: Session,
    ops: list[SetTransactionCategoryOp | RemoveTransactionOverrideOp],
) -> list[str]:
    errors: list[str] = []
    simulated: dict[int, str | None] = {}
    for index, op in enumerate(ops, start=1):
        label = f"op {index}"
        txn = db.get(Transaction, op.transaction_id)
        if txn is None:
            errors.append(f"{label}: transaction {op.transaction_id} not found")
            continue
        current_override = simulated.get(op.transaction_id, txn.category_override)
        if isinstance(op, SetTransactionCategoryOp):
            if not op.category.strip():
                errors.append(f"{label}: category is empty")
                continue
            if current_override is not None and current_override != op.category:
                errors.append(
                    f"{label}: transaction {op.transaction_id} has category_override "
                    f"{current_override!r}, proposed {op.category!r}"
                )
                continue
            simulated[op.transaction_id] = op.category
        else:
            simulated[op.transaction_id] = None
    return errors


@traceable(process_inputs=_service_trace_inputs)
def apply_mapping_plan(db: Session, plan: MappingPlanIn) -> ApplyResult:
    """Apply approved ops and reclassify. Commits once; rolls back on failure."""
    rule_ops, override_ops = _partition_ops(plan.ops)
    accs, errors = parse_plan_ops(
        db,
        MappingPlanIn(ops=rule_ops, account_id=plan.account_id),
        allow_missing_delete=True,
    )
    errors.extend(_conflict_errors(accs))
    errors.extend(_override_conflict_errors(db, override_ops))
    if errors:
        raise MappingPlanValidationError(errors)

    created_ids: list[int] = []
    updated_ids: list[int] = []
    deleted_ids: list[int] = []
    skipped: list[SkippedOp] = []
    overrides_set = 0
    overrides_removed = 0
    pending: list[NormalizationMapping] = []
    try:
        for acc in accs:
            if acc.op_type != "delete" or acc.mapping_id is None:
                continue
            row = db.get(NormalizationMapping, acc.mapping_id)
            if row is None:
                skipped.append(SkippedOp(op=acc.op, reason="missing"))
                continue
            db.delete(row)
            deleted_ids.append(acc.mapping_id)
        db.flush()

        for acc in accs:
            if acc.op_type != "update" or acc.mapping_id is None:
                continue
            row = db.get(NormalizationMapping, acc.mapping_id)
            if row is None:
                raise MappingPlanValidationError(
                    [f"op {acc.index + 1}: mapping {acc.mapping_id} not found"]
                )
            row.canonical_value = acc.new_canonical or ""
            updated_ids.append(row.id)
        db.flush()

        for acc in accs:
            if acc.op_type != "create" or acc.spec is None:
                continue
            spec = acc.spec
            existing = find_mapping_by_identity(
                db, spec.kind, spec.raw_value, spec.account_id, spec.merchant
            )
            if existing is not None:
                if existing.canonical_value == spec.canonical_value:
                    skipped.append(SkippedOp(op=acc.op, reason="duplicate"))
                    continue
                raise MappingPlanValidationError(
                    [
                        f"op {acc.index + 1}: conflicts with mapping {existing.id} "
                        f"(existing canonical {existing.canonical_value!r}, "
                        f"proposed {spec.canonical_value!r})"
                    ]
                )
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
        created_ids = [row.id for row in pending]

        for op in override_ops:
            txn = db.get(Transaction, op.transaction_id)
            if txn is None:
                raise MappingPlanValidationError(
                    [f"transaction {op.transaction_id} not found"]
                )
            provenance = db.execute(
                select(TransactionOverride).where(
                    TransactionOverride.transaction_id == txn.id
                )
            ).scalar_one_or_none()
            if isinstance(op, SetTransactionCategoryOp):
                if txn.category_override == op.category:
                    skipped.append(SkippedOp(op=op, reason="duplicate"))
                    continue
                txn.category_override = op.category
                if provenance is None:
                    provenance = TransactionOverride(
                        transaction_id=txn.id,
                        category=op.category,
                        evidence_ids=list(op.evidence_ids),
                        plan_source=None,
                    )
                    db.add(provenance)
                else:
                    provenance.category = op.category
                    provenance.evidence_ids = list(op.evidence_ids)
                    provenance.plan_source = None
                overrides_set += 1
                continue
            if txn.category_override is None:
                skipped.append(SkippedOp(op=op, reason="missing"))
                continue
            txn.category_override = None
            if provenance is not None:
                db.delete(provenance)
                db.flush()
            overrides_removed += 1
        db.flush()

        reclass = run_reclassification(db, account_id=plan.account_id)
        db.flush()
        unmapped = unmapped_summary(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ApplyResult(
        created_ids=created_ids,
        updated_ids=updated_ids,
        deleted_ids=deleted_ids,
        skipped=skipped,
        overrides_set=overrides_set,
        overrides_removed=overrides_removed,
        reclass_scanned=reclass.scanned,
        reclass_updated=reclass.updated,
        unmapped_after=UnmappedValuesOut(
            transaction_types=unmapped["transaction_types"],
            categories=unmapped["categories"],
            owners=unmapped["owners"],
            merchants=unmapped["merchants"],
            merchants_without_category=unmapped["merchants_without_category"],
        ),
    )
