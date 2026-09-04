"""Model name, DB sessions for tools, and checkpointer factory."""
from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

_session_factory: sessionmaker[Session] | None = None


@dataclass(frozen=True)
class EnricherDeps:
    source_factory: Callable[[], Any]
    extractor_factory: Callable[[], Any]
    config: Any


_enricher_deps: EnricherDeps | None = None

DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"
DEFAULT_CHECKPOINT_PATH = ".agent_checkpoints.sqlite"
DEFAULT_EMAIL_PROVIDER = "none"
DEFAULT_EMAIL_LOOKBACK_DAYS = 2
DEFAULT_EMAIL_LOOKAHEAD_DAYS = 7
DEFAULT_EMAIL_MAX_RESULTS_PER_SEARCH = 10
DEFAULT_EMAIL_MAX_CANDIDATES = 5
DEFAULT_EMAIL_BODY_BYTE_CAP = 65536
DEFAULT_ENRICHMENT_CONFIDENCE_THRESHOLD = 0.8
DEFAULT_EMAIL_MCP_URL = "https://gmailmcp.googleapis.com/mcp/v1"
DEFAULT_EMAIL_MCP_TIMEOUT_S = 20.0
DEFAULT_GMAIL_REST_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/"
DEFAULT_GMAIL_REST_TIMEOUT_S = 20.0


def model_name() -> str:
    return os.environ.get("STEWARD_MODEL", DEFAULT_MODEL)


def checkpoint_sqlite_path() -> str:
    return os.environ.get("AGENT_CHECKPOINT_PATH", DEFAULT_CHECKPOINT_PATH)


def email_provider() -> str:
    return os.environ.get("EMAIL_PROVIDER", DEFAULT_EMAIL_PROVIDER)


def email_sender_allowlist() -> str:
    return os.environ.get("EMAIL_SENDER_ALLOWLIST", "")


def email_lookback_days() -> int:
    return int(os.environ.get("EMAIL_LOOKBACK_DAYS", str(DEFAULT_EMAIL_LOOKBACK_DAYS)))


def email_lookahead_days() -> int:
    return int(os.environ.get("EMAIL_LOOKAHEAD_DAYS", str(DEFAULT_EMAIL_LOOKAHEAD_DAYS)))


def email_max_results_per_search() -> int:
    return int(
        os.environ.get(
            "EMAIL_MAX_RESULTS_PER_SEARCH", str(DEFAULT_EMAIL_MAX_RESULTS_PER_SEARCH)
        )
    )


def email_max_candidates() -> int:
    return int(os.environ.get("EMAIL_MAX_CANDIDATES", str(DEFAULT_EMAIL_MAX_CANDIDATES)))


def email_body_byte_cap() -> int:
    return int(os.environ.get("EMAIL_BODY_BYTE_CAP", str(DEFAULT_EMAIL_BODY_BYTE_CAP)))


def enrichment_confidence_threshold() -> float:
    return float(
        os.environ.get(
            "ENRICHMENT_CONFIDENCE_THRESHOLD",
            str(DEFAULT_ENRICHMENT_CONFIDENCE_THRESHOLD),
        )
    )


def extraction_model_name() -> str:
    return os.environ.get("EXTRACTION_MODEL", "")


def enricher_model_name() -> str:
    return os.environ.get("ENRICHER_MODEL") or model_name()


def email_mcp_url() -> str:
    return os.environ.get("EMAIL_MCP_URL", DEFAULT_EMAIL_MCP_URL)


def email_mcp_timeout_s() -> float:
    return float(os.environ.get("EMAIL_MCP_TIMEOUT_S", str(DEFAULT_EMAIL_MCP_TIMEOUT_S)))


def gmail_rest_base_url() -> str:
    return os.environ.get("GMAIL_REST_BASE_URL", DEFAULT_GMAIL_REST_BASE_URL)


def gmail_rest_timeout_s() -> float:
    return float(os.environ.get("GMAIL_REST_TIMEOUT_S", str(DEFAULT_GMAIL_REST_TIMEOUT_S)))


def token_provider_from_env():
    from app.domain.email_source import EmailSourceUnavailable
    from app.integrations.gmail_common.auth import RefreshTokenProvider, StaticTokenProvider

    static = (
        os.environ.get("GMAIL_ACCESS_TOKEN", "").strip()
        or os.environ.get("EMAIL_MCP_ACCESS_TOKEN", "").strip()
    )
    if static:
        return StaticTokenProvider(static)
    client_id = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GMAIL_OAUTH_REFRESH_TOKEN", "").strip()
    if client_id and client_secret and refresh_token:
        return RefreshTokenProvider(client_id, client_secret, refresh_token)
    raise EmailSourceUnavailable("auth_not_configured")


def email_source_from_env():
    from app.domain.email_source import FixtureEmailSource, parse_allowlist

    provider = email_provider()
    if provider == "none":
        return None
    if provider == "gmail_rest":
        from app.integrations.gmail_rest.client import GmailRestClient
        from app.integrations.gmail_rest.source import GmailRestEmailSource

        client = GmailRestClient(
            token_provider_from_env(),
            gmail_rest_base_url(),
            gmail_rest_timeout_s(),
        )
        return GmailRestEmailSource(
            client,
            parse_allowlist(email_sender_allowlist()),
            email_body_byte_cap(),
        )
    if provider == "gmail":
        from app.integrations.gmail_mcp.source import McpEmailSource
        from app.integrations.gmail_mcp.transport import StreamableHttpMcpTransport

        transport = StreamableHttpMcpTransport(
            email_mcp_url(),
            token_provider_from_env(),
            email_mcp_timeout_s(),
        )
        source = McpEmailSource(
            transport,
            parse_allowlist(email_sender_allowlist()),
            email_body_byte_cap(),
        )
        source.verify_tools()
        return source
    if provider != "fake":
        raise ValueError(f"unsupported EMAIL_PROVIDER={provider!r}")
    fixture = os.environ.get("EMAIL_FAKE_FIXTURE")
    if not fixture:
        raise ValueError("EMAIL_FAKE_FIXTURE is required when EMAIL_PROVIDER=fake")
    return FixtureEmailSource.from_json_fixture(
        fixture,
        parse_allowlist(email_sender_allowlist()),
        byte_cap=email_body_byte_cap(),
    )


def extractor_from_env():
    from langchain.chat_models import init_chat_model

    from app.domain.receipt_extractors import ModelReceiptExtractor
    from app.domain.receipts import RegexReceiptExtractor

    model = extraction_model_name()
    if not model:
        return RegexReceiptExtractor()
    return ModelReceiptExtractor(
        init_chat_model(model),
        model_name=model,
        body_byte_cap=email_body_byte_cap(),
    )


def set_session_factory(factory: sessionmaker[Session] | None) -> None:
    """Tests inject the pytest engine so tools share the same SQLite database."""
    global _session_factory
    _session_factory = factory


def set_enricher_deps(deps: EnricherDeps | None) -> None:
    """Tests inject FakeEmailSource / FakeExtractor; production uses env defaults."""
    global _enricher_deps
    _enricher_deps = deps


def get_enricher_deps() -> EnricherDeps:
    if _enricher_deps is not None:
        return _enricher_deps
    from app.services.enrichment_service import EnrichmentConfig

    return EnricherDeps(
        source_factory=email_source_from_env,
        extractor_factory=extractor_from_env,
        config=EnrichmentConfig(
            lookback_days=email_lookback_days(),
            lookahead_days=email_lookahead_days(),
            max_candidates=email_max_candidates(),
        ),
    )


def get_tool_session_factory() -> sessionmaker[Session]:
    factory = _session_factory
    if factory is not None:
        return factory
    from app.database import _get_session_factory

    return _get_session_factory()


@contextmanager
def tool_session() -> Iterator[Session]:
    factory = _session_factory
    if factory is None:
        from app.database import _get_session_factory

        factory = _get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def checkpoint_conn_string(database_url: str) -> str:
    return (
        database_url.replace("postgresql+psycopg2://", "postgresql://", 1).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
    )


@contextmanager
def sqlite_file_checkpointer(path: str | None = None) -> Iterator[Any]:
    from langgraph.checkpoint.sqlite import SqliteSaver

    with SqliteSaver.from_conn_string(path or checkpoint_sqlite_path()) as saver:
        saver.setup()
        yield saver


@contextmanager
def open_checkpointer(*, in_memory: bool = False) -> Iterator[Any]:
    if in_memory:
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    from app.config import settings

    url = settings.database_url
    if url.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(checkpoint_conn_string(url)) as saver:
            saver.setup()
            yield saver
        return

    with sqlite_file_checkpointer() as saver:
        yield saver


def in_memory_checkpointer():
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()
