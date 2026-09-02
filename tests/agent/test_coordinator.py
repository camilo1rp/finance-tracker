import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.config import in_memory_checkpointer
from app.agent.coordinator import build_coordinator
from tests.agent.helpers import (
    RULES,
    ScriptedChatModel,
    agent_sessions,
    capture_apply,
    seed_coffee,
    tool_names,
)


def _idle(content: str = "idle") -> ScriptedChatModel:
    return ScriptedChatModel(responses=[AIMessage(content=content)])


def _coordinator(
    model: ScriptedChatModel,
    *,
    analyst_model: ScriptedChatModel | None = None,
    steward_model: ScriptedChatModel | None = None,
):
    return build_coordinator(
        model=model,
        analyst_model=analyst_model or _idle("ANALYST_SHOULD_NOT_RUN"),
        steward_model=steward_model or _idle("STEWARD_SHOULD_NOT_RUN"),
        checkpointer=in_memory_checkpointer(),
    )


def test_coordinator_answers_total_without_subagent(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_total",
                        "args": {
                            "date_from": "2024-08-01",
                            "date_to": "2024-08-31",
                            "spend_only": True,
                        },
                        "id": "total-1",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "Spent 0.00 in August. Filters: spend_only=true, "
                    "date_from=2024-08-01, date_to=2024-08-31."
                )
            ),
        ]
    )
    graph = _coordinator(model)
    config = {"configurable": {"thread_id": "t3.1-total"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "total spent in August"}]},
        config,
    )
    names = tool_names(result)
    assert "get_total" in names
    assert "ask_analyst" not in names
    assert "run_data_steward" not in names
    assert "ANALYST_SHOULD_NOT_RUN" not in str(result.get("messages"))
    assert "STEWARD_SHOULD_NOT_RUN" not in str(result.get("messages"))


def test_coordinator_delegates_compare_to_analyst(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    analyst_final = "July Dining=10.00 August Dining=20.00 delta=10.00"
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_analyst",
                        "args": {
                            "task": (
                                "Compare dining July vs August for owner_id=1 "
                                "2024-07-01..2024-07-31 vs 2024-08-01..2024-08-31 "
                                "spend_only=true"
                            )
                        },
                        "id": "ask-1",
                    }
                ],
            ),
            AIMessage(content=f"Per category: {analyst_final}"),
        ]
    )
    graph = _coordinator(model, analyst_model=_idle(analyst_final))
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "Compare July vs August dining"}]},
        {"configurable": {"thread_id": "t3.1-compare"}, "recursion_limit": 25},
    )
    names = tool_names(result)
    assert "ask_analyst" in names
    assert "run_data_steward" not in names
    assert analyst_final in (result["messages"][-1].content or "")


def test_coordinator_delegates_cleanup_to_steward(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_data_steward",
                        "args": {"task": "Clean up unmapped categories"},
                        "id": "stew-1",
                    }
                ],
            ),
            AIMessage(content="Steward reported nothing unmapped."),
        ]
    )
    graph = _coordinator(
        model, steward_model=_idle("nothing unmapped right now")
    )
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "Clean up unmapped categories"}]},
        {"configurable": {"thread_id": "t3.1-steward"}, "recursion_limit": 25},
    )
    names = tool_names(result)
    assert "run_data_steward" in names
    assert "ask_analyst" not in names


def test_steward_interrupt_propagates_to_coordinator(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    coordinator_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_data_steward",
                        "args": {"task": "Clean up unmapped categories"},
                        "id": "stew-1",
                    }
                ],
            ),
            AIMessage(
                content="Applied created_mapping_ids=[1] reclass_updated=1. Unmapped categories are clear."
            ),
        ]
    )
    steward_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "rules": RULES,
                            "account_id": None,
                            "rationale": "map remaining categories",
                        },
                        "id": "call-submit",
                    }
                ],
            ),
            AIMessage(content="Applied the approved subset."),
        ]
    )
    graph = _coordinator(
        coordinator_model, steward_model=steward_model
    )
    config = {"configurable": {"thread_id": "t3.2"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "Clean up the unmapped categories"}]},
        config,
    )
    interrupts = result.get("__interrupt__") or ()
    assert interrupts, f"expected outer interrupt, got keys {result.keys()}"
    payload = interrupts[0].value
    assert payload["rationale"] == "map remaining categories"
    assert payload["preview"] is not None
    by_raw = {impact["rule"]["raw_value"]: impact for impact in payload["preview"]["rules"]}
    assert by_raw["Food & Drink"]["would_change"] == 1
    assert by_raw["Shopping"]["would_change"] == 0

    subset = [payload["rules"][0]]
    resumed = graph.invoke(
        Command(resume={"decision": "approve", "rules": subset}),
        config,
    )
    assert "plan" in captured
    assert captured["plan"].rules[0].raw_value == "Food & Drink"
    assert len(captured["plan"].rules) == 1
    assert not resumed.get("__interrupt__")
    assert "created_mapping_ids=[1]" in (resumed["messages"][-1].content or "")
    assert "reclass_updated=1" in (resumed["messages"][-1].content or "")


def test_coordinator_history_excludes_analyst_internals(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    analyst_final = "July 10.00 vs August 20.00 delta 10.00. spend_only=true."
    coordinator_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_analyst",
                        "args": {
                            "task": "Compare July vs August 2024-07-01..2024-07-31 vs 2024-08-01..2024-08-31"
                        },
                        "id": "ask-1",
                    }
                ],
            ),
            AIMessage(content=analyst_final),
        ]
    )
    analyst_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize",
                        "args": {
                            "group_by": "category",
                            "date_from": "2024-07-01",
                            "date_to": "2024-07-31",
                        },
                        "id": "sum-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize",
                        "args": {
                            "group_by": "category",
                            "date_from": "2024-08-01",
                            "date_to": "2024-08-31",
                        },
                        "id": "sum-2",
                    }
                ],
            ),
            AIMessage(content=analyst_final),
        ]
    )
    checkpointer = in_memory_checkpointer()
    graph = build_coordinator(
        model=coordinator_model,
        analyst_model=analyst_model,
        steward_model=_idle(),
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "t3.3"}, "recursion_limit": 25}
    graph.invoke(
        {"messages": [{"role": "user", "content": "Compare July vs August dining"}]},
        config,
    )
    state = graph.get_state(config)
    messages = state.values.get("messages") or []
    names = tool_names({"messages": messages})
    assert names == ["ask_analyst"]
    tool_messages = [
        message for message in messages if getattr(message, "type", None) == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == analyst_final
    assert "group_value" not in (tool_messages[0].content or "")
    assert "summarize" not in names
