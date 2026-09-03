import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.config import in_memory_checkpointer
from app.agent.steward_graph import build_steward_builder, build_steward_graph
from app.schemas import CreateMappingOp
from tests.agent.helpers import (
    OPS,
    ScriptedChatModel,
    agent_sessions,
    capture_apply,
    seed_coffee,
)


def test_steward_interrupt_flow(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_unmapped_values", "args": {}, "id": "call-1"}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "preview_mapping_rules",
                        "args": {"ops": OPS, "account_id": None},
                        "id": "call-2",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "ops": OPS,
                            "account_id": None,
                            "rationale": "map remaining categories",
                        },
                        "id": "call-3",
                    }
                ],
            ),
            AIMessage(content="Applied the approved subset. Unmapped categories are clear."),
        ]
    )
    graph = build_steward_graph(model=model, checkpointer=in_memory_checkpointer())
    config = {"configurable": {"thread_id": "steward-test"}, "recursion_limit": 25}

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "clean up unmapped categories"}]},
        config,
    )
    interrupts = result.get("__interrupt__") or ()
    assert interrupts, f"expected interrupt, got keys {result.keys()}"
    payload = interrupts[0].value
    assert payload["preview"] is not None
    assert payload["rationale"] == "map remaining categories"
    assert len(payload["ops"]) == 2

    subset = [payload["ops"][0]]
    resumed = graph.invoke(
        Command(resume={"decision": "approve", "ops": subset}),
        config,
    )
    assert "plan" in captured
    received = captured["plan"].ops
    assert [op.model_dump() for op in received] == [
        CreateMappingOp.model_validate(subset[0]).model_dump()
    ]
    assert received[0].raw_value == "Food & Drink"
    assert len(received) == 1

    state = graph.get_state(config)
    apply_result = state.values.get("apply_result")
    assert apply_result is not None
    assert apply_result["created_ids"] == [1]
    assert apply_result["reclass_updated"] == 1
    assert not resumed.get("__interrupt__")


def test_steward_standalone_compile_still_uses_checkpointer() -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="idle")])
    graph = build_steward_graph(model=model, checkpointer=in_memory_checkpointer())
    assert graph.checkpointer is not None


def test_steward_compiles_without_checkpointer_for_subagent_use() -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="idle")])
    builder = build_steward_builder(model=model)
    graph = builder.compile()
    assert graph.checkpointer is None
    nested = build_steward_graph(model=model, checkpointer=None)
    assert nested.checkpointer is None
