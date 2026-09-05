from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Account, NormalizationMapping, Owner, Transaction
from app.schemas import DeleteMappingOp, MappingPlanIn
from app.services.mapping_preview_service import preview_mappings


def _seed_sign_account(db: Session) -> Account:
    owner = Owner(name="Pat")
    db.add(owner)
    db.flush()
    account = Account(
        name="Checking",
        last4="3333",
        default_owner_id=owner.id,
        source_format="csv",
        account_kind="depository",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "sign_convention": "negative_is_spend",
        },
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_preview_delete_type_uses_sign_fallback(db_session: Session) -> None:
    account = _seed_sign_account(db_session)
    rule = NormalizationMapping(
        kind="transaction_type",
        raw_value="sale",
        canonical_value="SPEND",
        account_id=account.id,
    )
    db_session.add(rule)
    db_session.add(
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 1),
            description="credit-sale",
            amount=Decimal("50.00"),
            transaction_type="SPEND",
            is_spend=True,
            raw_type="Sale",
            dedupe_hash="sign-fallback-delete",
            raw={},
        )
    )
    db_session.commit()
    db_session.refresh(rule)

    preview = preview_mappings(
        db_session,
        MappingPlanIn(ops=[DeleteMappingOp(op="delete", mapping_id=rule.id)]),
    )
    impact = preview.ops[0]
    assert impact.would_change == 1
    assert impact.samples[0].new_effective == "INCOME"
