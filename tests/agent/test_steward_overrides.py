import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.config import in_memory_checkpointer
from app.agent.steward_graph import build_steward_graph
from app.schemas import SetTransactionCategoryOp
from tests.agent.helpers import ScriptedChatModel, agent_sessions, capture_apply, seed_coffee


def test_steward_override_plan_interrupt_and_apply(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    ops = [
        {
            "op": "set_transaction_category",
            "transaction_id": 1,
            "category": "Dining",
            "evidence_ids": [11, 12],
            "rationale": "receipt matched",
        }
    ]
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {"ops": ops, "account_id": None, "rationale": "one txn only"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="Applied."),
        ]
    )
    graph = build_steward_graph(model=model, checkpointer=in_memory_checkpointer())
    config = {"configurable": {"thread_id": "override-steward"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "fix just that one transaction"}]},
        config,
    )
    payload = (result.get("__interrupt__") or ())[0].value
    assert payload["ops"] == ops
    assert payload["preview"]["overrides"][0]["transaction_id"] == 1
    assert payload["preview"]["overrides"][0]["action"] == "set"

    resumed = graph.invoke(Command(resume={"decision": "approve", "ops": ops}), config)
    assert [op.model_dump() for op in captured["plan"].ops] == [
        SetTransactionCategoryOp.model_validate(ops[0]).model_dump()
    ]
    state = graph.get_state(config)
    messages = state.values.get("messages") or []
    assert any("overrides_set" in (getattr(message, "content", "") or "") for message in messages)


def test_steward_edit_keeps_positional_indices_with_mixed_ops(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    ops = [
        {
            "op": "create",
            "kind": "category",
            "raw_value": "Food & Drink",
            "canonical_value": "Dining",
        },
        {
            "op": "set_transaction_category",
            "transaction_id": 1,
            "category": "Travel",
            "evidence_ids": [],
            "rationale": "edge case",
        },
    ]
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {"ops": ops, "account_id": None, "rationale": "mixed"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="Applied."),
        ]
    )
    graph = build_steward_graph(model=model, checkpointer=in_memory_checkpointer())
    config = {"configurable": {"thread_id": "override-mixed"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "mixed plan"}]},
        config,
    )
    payload = (result.get("__interrupt__") or ())[0].value
    assert len(payload["preview"]["ops"]) == 1
    assert len(payload["preview"]["overrides"]) == 1

    graph.invoke(Command(resume={"decision": "approve", "ops": [payload["ops"][1]]}), config)
    assert len(captured["plan"].ops) == 1
    assert captured["plan"].ops[0].model_dump()["op"] == "set_transaction_category"


def test_steward_reject_applies_nothing(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    ops = [{"op": "set_transaction_category", "transaction_id": 1, "category": "Dining"}]
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {"ops": ops, "account_id": None, "rationale": "mixed"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="No apply."),
        ]
    )
    graph = build_steward_graph(model=model, checkpointer=in_memory_checkpointer())
    config = {"configurable": {"thread_id": "override-reject"}, "recursion_limit": 25}
    result = graph.invoke({"messages": [{"role": "user", "content": "reject"}]}, config)
    graph.invoke(Command(resume={"decision": "reject", "ops": []}), config)
    assert "plan" not in captured
