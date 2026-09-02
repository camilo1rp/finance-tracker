"""Model name, DB sessions for tools, and checkpointer factory."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

_session_factory: sessionmaker[Session] | None = None

DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"
DEFAULT_CHECKPOINT_PATH = ".agent_checkpoints.sqlite"


def model_name() -> str:
    return os.environ.get("STEWARD_MODEL", DEFAULT_MODEL)


def checkpoint_sqlite_path() -> str:
    return os.environ.get("AGENT_CHECKPOINT_PATH", DEFAULT_CHECKPOINT_PATH)


def set_session_factory(factory: sessionmaker[Session] | None) -> None:
    """Tests inject the pytest engine so tools share the same SQLite database."""
    global _session_factory
    _session_factory = factory


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
