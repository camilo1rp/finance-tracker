import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from tests.agent.helpers import ScriptedChatModel


def _load_langgraph_config() -> dict:
    path = Path(__file__).resolve().parents[2] / "langgraph.json"
    return json.loads(path.read_text())


def test_langgraph_json_references_studio_factories() -> None:
    config = _load_langgraph_config()
    assert config["dependencies"] == ["."]
    assert set(config["graphs"]) == {"coordinator", "steward", "analyst", "enricher"}
    for graph_id, ref in config["graphs"].items():
        module_path, factory_name = ref.split(":")
        assert module_path.endswith("app/agent/studio.py")
        assert factory_name.endswith("_graph")


def test_studio_import_has_no_db_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid:invalid@127.0.0.1:1/none")
    import app.database as database

    database._engine = None
    database._session_factory = None

    import app.agent.studio as studio

    assert studio is not None
    assert database._engine is None


def test_studio_factories_yield_graphs_without_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="idle")])
    monkeypatch.setattr("app.agent.config.model_name", lambda: model)

    from app.agent import studio

    for factory_name in ("coordinator_graph", "steward_graph", "analyst_graph"):
        factory = getattr(studio, factory_name)
        graph = factory()
        assert graph is not None
        assert graph.checkpointer is None
