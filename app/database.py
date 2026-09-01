"""
Postgres engine/session setup, driven by app.config.Settings.
"""
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
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


def _ensure_merchant_columns(engine: Engine) -> None:
    """Add merchant columns on existing DBs where create_all will not alter."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "transactions" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("transactions")}
    for column in ("merchant_raw", "merchant_normalized", "merchant_override"):
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
    from sqlalchemy import inspect, text

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
