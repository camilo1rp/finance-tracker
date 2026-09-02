from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.config import in_memory_checkpointer, sqlite_file_checkpointer
from app.agent.steward_graph import build_steward_graph, execute
from app.schemas import ProposedMappingIn
from tests.agent.helpers import (
    RULES,
    RULES_A,
    RULES_B,
    ScriptedChatModel,
    agent_sessions,
    capture_apply,
    seed_coffee,
)


def _submit_only_model(rules: list[dict], rationale: str = "submit") -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "rules": rules,
                            "account_id": None,
                            "rationale": rationale,
                        },
                        "id": "call-submit",
                    }
                ],
            ),
            AIMessage(content="Applied."),
        ]
    )


def _preview_then_submit_model(
    preview_rules: list[dict], submit_rules: list[dict]
) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "preview_mapping_rules",
                        "args": {"rules": preview_rules, "account_id": None},
                        "id": "call-preview",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "rules": submit_rules,
                            "account_id": None,
                            "rationale": "different than preview",
                        },
                        "id": "call-submit",
                    }
                ],
            ),
            AIMessage(content="Applied."),
        ]
    )


def _raw_values(preview: dict) -> list[str]:
    return [impact["rule"]["raw_value"] for impact in preview.get("rules") or []]


def test_interrupt_preview_without_preview_tool(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    capture_apply(monkeypatch)
    graph = build_steward_graph(
        model=_submit_only_model(RULES, rationale="no preview tool"),
        checkpointer=in_memory_checkpointer(),
    )
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "submit a plan"}]},
        {"configurable": {"thread_id": "t0.1"}, "recursion_limit": 25},
    )
    payload = (result.get("__interrupt__") or ())[0].value
    preview = payload["preview"]
    assert preview is not None
    assert payload["rationale"] == "no preview tool"
    assert _raw_values(preview) == ["Food & Drink", "Shopping"]
    assert preview["scanned"] == 1
    assert preview["total_would_change"] == 1
    by_raw = {impact["rule"]["raw_value"]: impact for impact in preview["rules"]}
    assert by_raw["Food & Drink"]["would_change"] == 1
    assert by_raw["Food & Drink"]["samples"][0]["new_effective"] == "Dining"
    assert by_raw["Shopping"]["would_change"] == 0


def test_interrupt_preview_matches_submitted_not_last_tool(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    capture_apply(monkeypatch)
    graph = build_steward_graph(
        model=_preview_then_submit_model(RULES_A, RULES_B),
        checkpointer=in_memory_checkpointer(),
    )
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "preview A submit B"}]},
        {"configurable": {"thread_id": "t0.2"}, "recursion_limit": 25},
    )
    payload = (result.get("__interrupt__") or ())[0].value
    preview = payload["preview"]
    assert _raw_values(preview) == ["Shopping"]
    assert preview["total_would_change"] == 0
    assert preview["rules"][0]["would_change"] == 0


def test_edited_subset_recomputes_preview_for_execute(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    seen: dict = {}

    def spy(state):
        seen["rules"] = list(state.get("proposed_rules") or [])
        seen["preview"] = state.get("pending_preview")
        return execute(state)

    monkeypatch.setattr("app.agent.steward_graph.execute", spy)

    graph = build_steward_graph(
        model=_submit_only_model(RULES),
        checkpointer=in_memory_checkpointer(),
    )
    config = {"configurable": {"thread_id": "t0.3"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "submit"}]},
        config,
    )
    payload = (result.get("__interrupt__") or ())[0].value
    assert len(payload["preview"]["rules"]) == 2
    subset = [payload["rules"][0]]
    graph.invoke(Command(resume={"decision": "approve", "rules": subset}), config)

    assert [ProposedMappingIn.model_validate(r).raw_value for r in seen["rules"]] == [
        "Food & Drink"
    ]
    preview = seen["preview"]
    assert preview is not None
    assert _raw_values(preview) == ["Food & Drink"]
    assert preview["total_would_change"] == 1
    assert preview["rules"][0]["would_change"] == 1
    assert captured["plan"].rules[0].raw_value == "Food & Drink"
    assert len(captured["plan"].rules) == 1


def test_sqlite_file_checkpointer_survives_rebuild(
    db_session: Session,
    agent_sessions,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    model = _submit_only_model(RULES_A, rationale="durable")
    path = str(tmp_path / "agent.sqlite")
    config = {"configurable": {"thread_id": "t0.4"}, "recursion_limit": 25}

    with sqlite_file_checkpointer(path) as saver:
        graph = build_steward_graph(model=model, checkpointer=saver)
        result = graph.invoke(
            {"messages": [{"role": "user", "content": "submit"}]},
            config,
        )
        payload = (result.get("__interrupt__") or ())[0].value
        assert payload["rationale"] == "durable"
        assert _raw_values(payload["preview"]) == ["Food & Drink"]

    del graph

    with sqlite_file_checkpointer(path) as saver:
        graph = build_steward_graph(model=model, checkpointer=saver)
        snapshot = graph.get_state(config)
        assert snapshot.interrupts
        resumed = graph.invoke(
            Command(resume={"decision": "approve", "rules": payload["rules"]}),
            config,
        )
        assert "plan" in captured
        assert captured["plan"].rules[0].raw_value == "Food & Drink"
        assert graph.get_state(config).values.get("apply_result") is not None
        assert not resumed.get("__interrupt__")
