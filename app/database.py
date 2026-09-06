"""
Postgres engine/session setup, driven by app.config.Settings.
"""
from collections.abc import Generator

from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Creates the SQLAlchemy engine from settings.database_url."""
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url)
    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine())
    return _session_factory


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency -- yields a session, closes it after the request."""
    session = _get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Creates tables if they don't exist. Adequate for now; revisit with
    Alembic migrations if the schema starts changing after data exists."""
    from app.models import Base

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_merchant_columns(engine)
    _ensure_mapping_merchant_scope(engine)
    _ensure_mapping_nullable_raw_value(engine)
    _ensure_transaction_type_columns(engine)
    _backfill_account_kind(engine)
    _migrate_payment_mapping_canonicals(engine)
    _reclassify_legacy_payment_rows(engine)
    _seed_transaction_type_mappings(engine)


def _ensure_merchant_columns(engine: Engine) -> None:
    """Add merchant columns on existing DBs where create_all will not alter."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "transactions" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("transactions")}
    for column in (
        "merchant_raw",
        "merchant_normalized",
        "merchant_override",
        "subcategory",
    ):
        if column in existing:
            continue
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE transactions ADD COLUMN {column} VARCHAR"))


def _ensure_mapping_merchant_scope(engine: Engine) -> None:
    """Add merchant on normalization_mappings and widen the unique key.

    create_all will not alter existing tables. Live Postgres already has
    uq_normalization_rule on (kind, raw_value, account_id), which would
    block two category rules for the same raw value and different merchants.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "normalization_mappings" not in inspector.get_table_names():
        return
    existing = {
        column["name"] for column in inspector.get_columns("normalization_mappings")
    }
    if "merchant" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE normalization_mappings ADD COLUMN merchant VARCHAR")
            )

    if engine.dialect.name != "postgresql":
        return

    inspector = inspect(engine)
    uniques = {
        constraint["name"]: list(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("normalization_mappings")
    }
    wanted = ["kind", "raw_value", "account_id", "merchant"]
    current = uniques.get("uq_normalization_rule")
    if current == wanted:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE normalization_mappings "
                "DROP CONSTRAINT IF EXISTS uq_normalization_rule"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE normalization_mappings "
                "ADD CONSTRAINT uq_normalization_rule "
                "UNIQUE (kind, raw_value, account_id, merchant)"
            )
        )


def _ensure_mapping_nullable_raw_value(engine: Engine) -> None:
    """Allow null raw_value for merchant-keyed category rules on live Postgres."""
    from sqlalchemy import inspect

    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    if "normalization_mappings" not in inspector.get_table_names():
        return
    columns = {
        column["name"]: column for column in inspector.get_columns("normalization_mappings")
    }
    raw = columns.get("raw_value")
    if raw is None or raw.get("nullable"):
        return
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE normalization_mappings ALTER COLUMN raw_value DROP NOT NULL")
        )


def _ensure_transaction_type_columns(engine: Engine) -> None:
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "accounts" in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns("accounts")}
        if "account_kind" not in existing:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE accounts ADD COLUMN account_kind VARCHAR "
                        "DEFAULT 'depository' NOT NULL"
                    )
                )
    if "transactions" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("transactions")}
    if "type_override" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE transactions ADD COLUMN type_override VARCHAR")
            )


def _infer_account_kind_from_mapping(default_mapping: dict) -> str:
    from app.domain.classification import AccountKind

    type_col = default_mapping.get("type_col") if isinstance(default_mapping, dict) else None
    if type_col is not None and str(type_col).strip():
        return AccountKind.CREDIT_CARD.value
    return AccountKind.DEPOSITORY.value


def _backfill_account_kind(engine: Engine) -> None:
    from app.models import Account

    with Session(engine) as session:
        accounts = list(session.scalars(select(Account)).all())
        changed = False
        for account in accounts:
            inferred = _infer_account_kind_from_mapping(account.default_mapping)
            if account.account_kind != inferred:
                account.account_kind = inferred
                changed = True
        if changed:
            session.commit()


def _migrate_payment_mapping_canonicals(engine: Engine) -> None:
    from app.models import NormalizationMapping

    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(NormalizationMapping).where(
                    NormalizationMapping.kind == "transaction_type",
                    NormalizationMapping.canonical_value == "PAYMENT",
                )
            ).all()
        )
        if not rows:
            return
        for row in rows:
            row.canonical_value = "TRANSFER"
        session.commit()


def _reclassify_legacy_payment_rows(engine: Engine) -> None:
    from app.models import Transaction
    from app.services.ingest_service import run_reclassification

    with Session(engine) as session:
        payment_count = session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.transaction_type == "PAYMENT")
        )
        if not payment_count:
            return
        try:
            run_reclassification(session)
            session.commit()
        except Exception:
            session.rollback()
            raise


def _seed_transaction_type_mappings(engine: Engine) -> None:
    from app.services.seed_mappings_service import seed_transaction_type_mappings

    with Session(engine) as session:
        seed_transaction_type_mappings(session)
        session.commit()
