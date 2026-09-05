from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import (
    _backfill_account_kind,
    _migrate_payment_mapping_canonicals,
    _reclassify_legacy_payment_rows,
    _seed_transaction_type_mappings,
)
from app.models import Account, Base, NormalizationMapping, Owner, Transaction
from app.services.seed_mappings_service import GLOBAL_TYPE_SEEDS


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _seed_legacy_payment_db(session: Session) -> tuple[Account, Account]:
    owner = Owner(name="Pat")
    session.add(owner)
    session.flush()
    card = Account(
        name="Chase Card",
        last4="9470",
        default_owner_id=owner.id,
        source_format="csv",
        account_kind="depository",
        default_mapping={
            "date_col": "Transaction Date",
            "description_col": "Description",
            "amount_col": "Amount",
            "type_col": "Type",
        },
    )
    checking = Account(
        name="Checking",
        last4="0170",
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
    session.add_all([card, checking])
    session.flush()
    session.add(
        NormalizationMapping(
            kind="transaction_type",
            raw_value="payment",
            canonical_value="PAYMENT",
            account_id=card.id,
        )
    )
    session.add(
        Transaction(
            account_id=card.id,
            transaction_date=date(2024, 6, 5),
            description="Payment Thank You",
            amount=Decimal("200.00"),
            transaction_type="PAYMENT",
            is_spend=False,
            raw_type="Payment",
            dedupe_hash="legacy-card-payment",
            raw={},
        )
    )
    session.add(
        Transaction(
            account_id=checking.id,
            transaction_date=date(2024, 6, 1),
            description="PAYCHECK",
            amount=Decimal("1234.56"),
            transaction_type="PAYMENT",
            is_spend=False,
            raw_type=None,
            dedupe_hash="legacy-paycheck",
            raw={},
        )
    )
    session.commit()
    return card, checking


def test_migration_rewrites_mappings_and_reclassifies_payment_rows() -> None:
    engine = _engine()
    with Session(engine) as session:
        card, checking = _seed_legacy_payment_db(session)
        card_id = card.id
        checking_id = checking.id

    _backfill_account_kind(engine)
    _migrate_payment_mapping_canonicals(engine)
    _reclassify_legacy_payment_rows(engine)

    with Session(engine) as session:
        mapping = session.scalar(
            select(NormalizationMapping).where(
                NormalizationMapping.raw_value == "payment",
                NormalizationMapping.account_id == card_id,
            )
        )
        assert mapping is not None
        assert mapping.canonical_value == "TRANSFER"
        card = session.get(Account, card_id)
        checking = session.get(Account, checking_id)
        assert card is not None and card.account_kind == "credit_card"
        assert checking is not None and checking.account_kind == "depository"

        card_txn = session.scalar(
            select(Transaction).where(Transaction.dedupe_hash == "legacy-card-payment")
        )
        paycheck = session.scalar(
            select(Transaction).where(Transaction.dedupe_hash == "legacy-paycheck")
        )
        assert card_txn is not None and card_txn.transaction_type == "TRANSFER"
        assert paycheck is not None and paycheck.transaction_type == "INCOME"
        remaining = session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.transaction_type == "PAYMENT")
        )
        assert remaining == 0


def test_seeds_present_after_init_db_helper_absent_after_create_all() -> None:
    bare_engine = _engine()
    with Session(bare_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(NormalizationMapping)) == 0
        )

    seeded_engine = _engine()
    _seed_transaction_type_mappings(seeded_engine)
    with Session(seeded_engine) as session:
        global_count = session.scalar(
            select(func.count())
            .select_from(NormalizationMapping)
            .where(NormalizationMapping.account_id.is_(None))
        )
        assert global_count >= len(GLOBAL_TYPE_SEEDS)
