from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, NormalizationMapping, Owner, Transaction
from app.schemas import CreateMappingOp, DeleteMappingOp, MappingPlanIn, UpdateMappingOp
from app.services.mapping_preview_service import preview_mappings


def _seed_account(db: Session, name: str = "Card") -> tuple[Owner, Account]:
    owner = Owner(name=f"Owner-{name}")
    db.add(owner)
    db.flush()
    account = Account(
        name=name,
        last4="0000",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
            "category_col": "Category",
        },
    )
    db.add(account)
    db.commit()
    db.refresh(owner)
    db.refresh(account)
    return owner, account


def _add_txn(db: Session, account_id: int, suffix: str, **kwargs) -> Transaction:
    values = {
        "account_id": account_id,
        "transaction_date": date(2024, 6, 1),
        "description": suffix,
        "amount": Decimal("10.00"),
        "transaction_type": "SPEND",
        "is_spend": True,
        "dedupe_hash": f"hash-{suffix}",
        "raw": {},
    }
    values.update(kwargs)
    txn = Transaction(**values)
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _add_mapping(db: Session, **kwargs) -> NormalizationMapping:
    row = NormalizationMapping(**kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _plan(*ops, account_id=None) -> MappingPlanIn:
    return MappingPlanIn(ops=list(ops), account_id=account_id)


def _create(**kwargs) -> CreateMappingOp:
    return CreateMappingOp(op="create", **kwargs)


def test_preview_is_pure(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    txn = _add_txn(
        db_session,
        account.id,
        "COFFEE",
        category_raw="Food & Drink",
        category_normalized=None,
        merchant_raw="Starbucks",
    )
    existing = _add_mapping(
        db_session,
        kind="merchant",
        raw_value="starbucks",
        canonical_value="Starbucks",
        account_id=None,
        merchant=None,
    )
    mapping_count = db_session.scalar(select(func.count()).select_from(NormalizationMapping))
    txn_count = db_session.scalar(select(func.count()).select_from(Transaction))
    snapshot = (
        txn.category_raw,
        txn.category_normalized,
        txn.merchant_raw,
        txn.merchant_normalized,
        txn.transaction_type,
        existing.canonical_value,
    )

    preview = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="category",
                raw_value="Food & Drink",
                canonical_value="Dining",
            )
        ),
    )

    assert preview.scanned == 1
    assert preview.total_would_change == 1
    assert preview.ops[0].would_change == 1
    assert preview.ops[0].samples[0].current_effective == "Food & Drink"
    assert preview.ops[0].samples[0].new_effective == "Dining"

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted

    db_session.expire_all()
    txn = db_session.get(Transaction, txn.id)
    existing = db_session.get(NormalizationMapping, existing.id)
    assert db_session.scalar(select(func.count()).select_from(NormalizationMapping)) == mapping_count
    assert db_session.scalar(select(func.count()).select_from(Transaction)) == txn_count
    assert (
        txn.category_raw,
        txn.category_normalized,
        txn.merchant_raw,
        txn.merchant_normalized,
        txn.transaction_type,
        existing.canonical_value,
    ) == snapshot


def test_preview_gates(db_session: Session) -> None:
    owner, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "sign-derived",
        transaction_type="SPEND",
        raw_type=None,
        category_raw="Misc",
        owner_id=owner.id,
        owner_raw=None,
    )
    _add_txn(
        db_session,
        account.id,
        "has-raw-type",
        transaction_type="UNKNOWN",
        raw_type="Sale",
        category_raw="Misc",
        owner_id=owner.id,
        owner_raw=None,
    )
    _add_txn(
        db_session,
        account.id,
        "has-owner-raw",
        transaction_type="SPEND",
        raw_type=None,
        category_raw="Misc",
        owner_id=None,
        owner_raw="Pat",
    )
    _add_txn(
        db_session,
        account.id,
        "overridden",
        transaction_type="SPEND",
        raw_type=None,
        category_raw="Food & Drink",
        category_normalized=None,
        category_override="Keep Me",
        owner_id=owner.id,
        owner_raw=None,
    )

    preview = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="transaction_type",
                raw_value="Sale",
                canonical_value="SPEND",
            ),
            _create(
                kind="owner",
                raw_value="Pat",
                canonical_value=owner.name,
            ),
            _create(
                kind="category",
                raw_value="Food & Drink",
                canonical_value="Dining",
            ),
        ),
    )

    by_kind = {impact.op.kind: impact for impact in preview.ops}  # type: ignore[union-attr]
    assert by_kind["transaction_type"].would_change == 1
    assert by_kind["transaction_type"].samples[0].description == "has-raw-type"
    assert by_kind["owner"].would_change == 1
    assert by_kind["owner"].samples[0].description == "has-owner-raw"
    assert by_kind["category"].would_change == 0
    assert by_kind["category"].suppressed_by_override == 1
    assert by_kind["category"].samples == []


def test_preview_shadowing_and_duplicates(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    other_owner = Owner(name="Other")
    db_session.add(other_owner)
    db_session.flush()
    other = Account(
        name="OtherCard",
        last4="2222",
        default_owner_id=other_owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
        },
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    _add_txn(
        db_session,
        account.id,
        "acct-row",
        category_raw="Shopping",
        category_normalized=None,
        merchant_raw="Amazon",
    )
    _add_txn(
        db_session,
        other.id,
        "other-row",
        category_raw="Shopping",
        category_normalized=None,
        merchant_raw="Amazon",
    )

    existing_account = _add_mapping(
        db_session,
        kind="category",
        raw_value="shopping",
        canonical_value="Account Shop",
        account_id=account.id,
        merchant=None,
    )
    existing_global = _add_mapping(
        db_session,
        kind="category",
        raw_value="groceries",
        canonical_value="Groceries",
        account_id=None,
        merchant=None,
    )

    shadowed = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="category",
                raw_value="Shopping",
                canonical_value="Global Shop",
            )
        ),
    )
    impact = shadowed.ops[0]
    assert impact.duplicate_of_existing_id is None
    assert impact.shadowed_by_existing == 1
    assert impact.would_change == 1
    assert impact.samples[0].description == "other-row"
    assert impact.samples[0].new_effective == "Global Shop"

    duplicate = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="category",
                raw_value="Groceries",
                canonical_value="Groceries",
            )
        ),
    )
    dup = duplicate.ops[0]
    assert dup.duplicate_of_existing_id == existing_global.id
    assert dup.conflicts_with_existing_id is None
    assert dup.would_change == 0
    assert dup.suppressed_by_override == 0
    assert dup.shadowed_by_existing == 0
    assert dup.samples == []
    assert existing_account.id != existing_global.id


def test_preview_conflict_split(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    existing = _add_mapping(
        db_session,
        kind="category",
        raw_value="groceries",
        canonical_value="Groceries",
        account_id=None,
        merchant=None,
    )
    _add_txn(
        db_session,
        account.id,
        "groceries-row",
        category_raw="Groceries",
        category_normalized="Groceries",
    )
    _add_txn(
        db_session,
        account.id,
        "grocery-row",
        category_raw="grocery",
        category_normalized=None,
    )

    conflict = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="category",
                raw_value="groceries",
                canonical_value="Grocery",
            )
        ),
    )
    hit = conflict.ops[0]
    assert hit.conflicts_with_existing_id == existing.id
    assert hit.existing_canonical == "Groceries"
    assert hit.duplicate_of_existing_id is None
    assert hit.would_change == 0
    assert hit.shadowed_by_existing == 0
    assert conflict.total_would_change == 0

    mixed = preview_mappings(
        db_session,
        _plan(
            UpdateMappingOp(
                op="update", mapping_id=existing.id, canonical_value="Grocery"
            ),
            _create(kind="category", raw_value="grocery", canonical_value="Grocery"),
        ),
    )
    by_op = {impact.op.op: impact for impact in mixed.ops}
    update = by_op["update"]
    create = by_op["create"]
    assert update.old_canonical == "Groceries"
    assert update.new_canonical == "Grocery"
    assert update.would_change == 1
    assert update.samples[0].description == "groceries-row"
    assert create.would_change == 1
    assert create.samples[0].description == "grocery-row"
    assert mixed.total_would_change == 2


def test_preview_update_and_delete_impact(db_session: Session) -> None:
    _owner, account = _seed_account(db_session)
    global_rule = _add_mapping(
        db_session,
        kind="category",
        raw_value="shopping",
        canonical_value="Shopping",
        account_id=None,
        merchant=None,
    )
    account_rule = _add_mapping(
        db_session,
        kind="category",
        raw_value="shopping",
        canonical_value="Account Shop",
        account_id=account.id,
        merchant=None,
    )
    _add_txn(
        db_session,
        account.id,
        "acct-hit",
        category_raw="Shopping",
        category_normalized="Account Shop",
    )
    _add_txn(
        db_session,
        account.id,
        "overridden",
        category_raw="Shopping",
        category_normalized="Account Shop",
        category_override="Keep Me",
    )
    other_owner = Owner(name="Other2")
    db_session.add(other_owner)
    db_session.flush()
    other = Account(
        name="Other2",
        last4="3333",
        default_owner_id=other_owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
        },
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    _add_txn(
        db_session,
        other.id,
        "global-hit",
        category_raw="Shopping",
        category_normalized="Shopping",
    )

    update_preview = preview_mappings(
        db_session,
        _plan(
            UpdateMappingOp(
                op="update",
                mapping_id=account_rule.id,
                canonical_value="Warehouse",
            )
        ),
    )
    update = update_preview.ops[0]
    assert update.old_canonical == "Account Shop"
    assert update.new_canonical == "Warehouse"
    assert update.would_change == 1
    assert update.suppressed_by_override == 1
    assert update.samples[0].description == "acct-hit"

    delete_preview = preview_mappings(
        db_session,
        _plan(DeleteMappingOp(op="delete", mapping_id=account_rule.id)),
    )
    delete = delete_preview.ops[0]
    assert delete.would_change == 1
    assert delete.suppressed_by_override == 1
    assert delete.would_become_unmapped == 0
    assert delete.falls_back_to[0].mapping_id == global_rule.id
    assert delete.falls_back_to[0].count == 1
    assert delete.samples[0].description == "acct-hit"
    assert delete.samples[0].new_effective == "Shopping"

    unmapped_preview = preview_mappings(
        db_session,
        _plan(DeleteMappingOp(op="delete", mapping_id=global_rule.id)),
    )
    gone = unmapped_preview.ops[0]
    assert gone.would_become_unmapped == 1
    assert gone.falls_back_to == []
    assert gone.samples[0].description == "global-hit"


def test_preview_delete_type_reverts_to_unknown(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    rule = _add_mapping(
        db_session,
        kind="transaction_type",
        raw_value="sale",
        canonical_value="SPEND",
        account_id=None,
        merchant=None,
    )
    _add_txn(
        db_session,
        account.id,
        "typed",
        transaction_type="SPEND",
        raw_type="Sale",
    )
    preview = preview_mappings(
        db_session, _plan(DeleteMappingOp(op="delete", mapping_id=rule.id))
    )
    impact = preview.ops[0]
    assert impact.would_change == 1
    assert impact.would_become_unmapped == 1
    assert impact.samples[0].new_effective == "UNKNOWN"


def test_preview_validation_excludes_invalid_and_continues(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "row",
        category_raw="Food",
        category_normalized=None,
    )
    preview = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="merchant",
                raw_value="x",
                canonical_value="X",
                merchant="Nope",
            ),
            _create(
                kind="category",
                raw_value="Food",
                canonical_value="Dining",
            ),
            _create(
                kind="transaction_type",
                raw_value="Sale",
                canonical_value="NOT_A_TYPE",
            ),
        ),
    )
    assert preview.validation_errors == [
        "op 1: merchant scope is only allowed on category or transaction_type mappings",
        "op 3: canonical_value must be a TransactionType",
    ]
    assert len(preview.ops) == 1
    assert preview.ops[0].would_change == 1
    assert preview.ops[0].op.canonical_value == "Dining"  # type: ignore[union-attr]


def test_preview_missing_mapping_id_is_validation_error(db_session: Session) -> None:
    preview = preview_mappings(
        db_session,
        _plan(UpdateMappingOp(op="update", mapping_id=999, canonical_value="X")),
    )
    assert preview.validation_errors == ["op 1: mapping 999 not found"]
    assert preview.ops == []


def test_preview_type_merchant_scope_only_hits_matching_merchant(
    db_session: Session,
) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "WESTERN UNION CAPTURE 623 WEB ID: 9",
        transaction_type="SPEND",
        raw_type="MISC_DEBIT",
        merchant_raw="WESTERN UNION CAPTURE 623 WEB ID: 9",
    )
    _add_txn(
        db_session,
        account.id,
        "Woodlake Op RENT",
        transaction_type="SPEND",
        raw_type="MISC_DEBIT",
        merchant_raw="Woodlake Op RENT 270209230 WEB ID: 1861072180",
    )
    preview = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="transaction_type",
                raw_value="MISC_DEBIT",
                canonical_value="TRANSFER",
                account_id=account.id,
                merchant="Western Union%",
            ),
            account_id=account.id,
        ),
    )
    assert preview.validation_errors == []
    assert preview.ops[0].would_change == 1
    assert preview.ops[0].samples[0].description == "WESTERN UNION CAPTURE 623 WEB ID: 9"
    assert preview.ops[0].samples[0].new_effective == "TRANSFER"


def test_preview_merchant_wildcard_covers_variants(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "wu-a",
        merchant_raw="WESTERN UNION CAPTURE 623 WEB ID: 9",
        merchant_normalized=None,
    )
    _add_txn(
        db_session,
        account.id,
        "wu-b",
        merchant_raw="WESTERN UNION CAPTURE 611 WEB ID: 9",
        merchant_normalized=None,
    )
    _add_txn(
        db_session,
        account.id,
        "rent",
        merchant_raw="Woodlake Op RENT 270209230 WEB ID: 1861072180",
        merchant_normalized=None,
    )
    preview = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="merchant",
                raw_value="Western Union%",
                canonical_value="Western Union",
                account_id=account.id,
            ),
            account_id=account.id,
        ),
    )
    assert preview.validation_errors == []
    assert preview.ops[0].would_change == 2
    assert {sample.description for sample in preview.ops[0].samples} == {
        "wu-a",
        "wu-b",
    }


def test_preview_empty_category_merchant_rule(db_session: Session) -> None:
    _, account = _seed_account(db_session)
    _add_txn(
        db_session,
        account.id,
        "irs-payment",
        category_raw=None,
        category_normalized=None,
        merchant_raw="IRS USATAXPYMT",
        merchant_normalized="IRS",
    )
    _add_txn(
        db_session,
        account.id,
        "has-category",
        category_raw="Other",
        category_normalized=None,
        merchant_raw="IRS USATAXPYMT",
        merchant_normalized="IRS",
    )
    preview = preview_mappings(
        db_session,
        _plan(
            _create(
                kind="category",
                canonical_value="taxes",
                merchant="irs%",
                account_id=account.id,
            ),
            account_id=account.id,
        ),
    )
    assert preview.validation_errors == []
    assert preview.ops[0].would_change == 1
    assert preview.ops[0].samples[0].description == "irs-payment"
