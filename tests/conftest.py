import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GMAIL_ACCESS_TOKEN", "")

# Set before any app import so load_dotenv() cannot refill tracing from .env.
_TRACING_DISABLED = {
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_API_KEY": "",
    "LANGSMITH_PROJECT": "",
    "LANGSMITH_ENDPOINT": "",
    "LANGSMITH_HIDE_INPUTS": "false",
    "LANGSMITH_HIDE_OUTPUTS": "false",
    "LANGSMITH_HIDE_METADATA": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGCHAIN_API_KEY": "",
    "LANGCHAIN_PROJECT": "",
    "LANGCHAIN_ENDPOINT": "",
    "LANGCHAIN_HIDE_INPUTS": "false",
    "LANGCHAIN_HIDE_OUTPUTS": "false",
    "LANGCHAIN_HIDE_METADATA": "false",
}
for _key, _value in _TRACING_DISABLED.items():
    os.environ[_key] = _value

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import Base

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture(autouse=True)
def _disable_tracing_env() -> None:
    for key, value in _TRACING_DISABLED.items():
        os.environ[key] = value


@pytest.fixture()
def db_session() -> Session:
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
