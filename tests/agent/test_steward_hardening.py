from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.config import in_memory_checkpointer, sqlite_file_checkpointer
from app.agent.steward_graph import build_steward_graph, execute
from app.models import Account, NormalizationMapping, Owner, Transaction
from app.schemas import CreateMappingOp
from tests.agent.helpers import (
    OPS,
    OPS_A,
    OPS_B,
    ScriptedChatModel,
    agent_sessions,
    capture_apply,
    seed_coffee,
)


def _submit_only_model(ops: list[dict], rationale: str = "submit") -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "ops": ops,
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
    preview_ops: list[dict], submit_ops: list[dict]
) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "preview_mapping_rules",
                        "args": {"ops": preview_ops, "account_id": None},
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
                            "ops": submit_ops,
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
    out: list[str] = []
    for impact in preview.get("ops") or []:
        op = impact.get("op") or {}
        if op.get("op") == "create":
            out.append(op.get("raw_value"))
        elif op.get("op") == "update":
            out.append(f"update:{op.get('mapping_id')}")
        else:
            out.append(f"delete:{op.get('mapping_id')}")
    return out


def test_interrupt_preview_without_preview_tool(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    capture_apply(monkeypatch)
    graph = build_steward_graph(
        model=_submit_only_model(OPS, rationale="no preview tool"),
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
    by_raw = {
        (impact.get("op") or {}).get("raw_value"): impact for impact in preview["ops"]
    }
    assert by_raw["Food & Drink"]["would_change"] == 1
    assert by_raw["Food & Drink"]["samples"][0]["new_effective"] == "Dining"
    assert by_raw["Shopping"]["would_change"] == 0


def test_interrupt_preview_matches_submitted_not_last_tool(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    capture_apply(monkeypatch)
    graph = build_steward_graph(
        model=_preview_then_submit_model(OPS_A, OPS_B),
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
    assert preview["ops"][0]["would_change"] == 0


def test_edited_subset_recomputes_preview_for_execute(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    seen: dict = {}

    def spy(state):
        seen["ops"] = list(state.get("proposed_ops") or [])
        seen["preview"] = state.get("pending_preview")
        return execute(state)

    monkeypatch.setattr("app.agent.steward_graph.execute", spy)

    graph = build_steward_graph(
        model=_submit_only_model(OPS),
        checkpointer=in_memory_checkpointer(),
    )
    config = {"configurable": {"thread_id": "t0.3"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "submit"}]},
        config,
    )
    payload = (result.get("__interrupt__") or ())[0].value
    assert len(payload["preview"]["ops"]) == 2
    subset = [payload["ops"][0]]
    graph.invoke(Command(resume={"decision": "approve", "ops": subset}), config)

    assert [CreateMappingOp.model_validate(op).raw_value for op in seen["ops"]] == [
        "Food & Drink"
    ]
    preview = seen["preview"]
    assert preview is not None
    assert _raw_values(preview) == ["Food & Drink"]
    assert preview["total_would_change"] == 1
    assert preview["ops"][0]["would_change"] == 1
    assert captured["plan"].ops[0].raw_value == "Food & Drink"
    assert len(captured["plan"].ops) == 1


def test_sqlite_file_checkpointer_survives_rebuild(
    db_session: Session,
    agent_sessions,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seed_coffee(db_session)
    captured = capture_apply(monkeypatch)
    model = _submit_only_model(OPS_A, rationale="durable")
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
            Command(resume={"decision": "approve", "ops": payload["ops"]}),
            config,
        )
        assert "plan" in captured
        assert captured["plan"].ops[0].raw_value == "Food & Drink"
        assert graph.get_state(config).values.get("apply_result") is not None
        assert not resumed.get("__interrupt__")


def _seed_grocery_split(db: Session) -> NormalizationMapping:
    owner = Owner(name="Pat-groc")
    db.add(owner)
    db.flush()
    account = Account(
        name="Card",
        last4="1111",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={
            "date_col": "Date",
            "description_col": "Description",
            "amount_col": "Amount",
        },
    )
    db.add(account)
    db.flush()
    mapping = NormalizationMapping(
        kind="category",
        raw_value="groceries",
        canonical_value="Groceries",
        account_id=None,
        merchant=None,
    )
    db.add(mapping)
    db.flush()
    db.add(
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 1),
            description="EXISTING",
            amount=Decimal("-4.50"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Groceries",
            category_normalized="Groceries",
            merchant_raw="Store",
            dedupe_hash="groc-existing",
            raw={},
        )
    )
    db.add(
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 1),
            description="NEWKEY",
            amount=Decimal("-3.00"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="grocery",
            category_normalized=None,
            merchant_raw="Store",
            dedupe_hash="groc-newkey",
            raw={},
        )
    )
    db.commit()
    db.refresh(mapping)
    return mapping


def _execute_summary(result: dict) -> str:
    for message in result.get("messages") or []:
        content = getattr(message, "content", "") or ""
        if isinstance(content, str) and content.startswith("Plan executed."):
            return content
    raise AssertionError("execute summary not found")


def test_steward_conflict_then_update_create_reports_counts(
    db_session: Session, agent_sessions
) -> None:
    mapping = _seed_grocery_split(db_session)
    conflict_create = [
        {
            "op": "create",
            "kind": "category",
            "raw_value": "groceries",
            "canonical_value": "Grocery",
        }
    ]
    corrected = [
        {
            "op": "update",
            "mapping_id": mapping.id,
            "canonical_value": "Grocery",
        },
        {
            "op": "create",
            "kind": "category",
            "raw_value": "grocery",
            "canonical_value": "Grocery",
        },
    ]
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "ops": conflict_create,
                            "account_id": None,
                            "rationale": "rename groceries",
                        },
                        "id": "call-conflict",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "ops": corrected,
                            "account_id": None,
                            "rationale": "update existing plus new key",
                        },
                        "id": "call-fixed",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_steward_graph(model=model, checkpointer=in_memory_checkpointer())
    config = {"configurable": {"thread_id": "t5-conflict"}, "recursion_limit": 25}
    first = graph.invoke(
        {"messages": [{"role": "user", "content": "merge grocery buckets"}]},
        config,
    )
    payload = (first.get("__interrupt__") or ())[0].value
    impact = payload["preview"]["ops"][0]
    assert impact["conflicts_with_existing_id"] == mapping.id
    assert impact["existing_canonical"] == "Groceries"
    assert impact["would_change"] == 0

    rejected = graph.invoke(
        Command(resume={"decision": "reject", "ops": []}), config
    )
    payload = (rejected.get("__interrupt__") or ())[0].value
    by_op = {(item.get("op") or {}).get("op"): item for item in payload["preview"]["ops"]}
    assert by_op["update"]["old_canonical"] == "Groceries"
    assert by_op["update"]["new_canonical"] == "Grocery"
    assert by_op["update"]["would_change"] == 1
    assert by_op["create"]["would_change"] == 1

    resumed = graph.invoke(
        Command(resume={"decision": "approve", "ops": payload["ops"]}), config
    )
    summary = _execute_summary(resumed)
    assert f"updated_ids=[{mapping.id}]" in summary
    assert "reclass_updated=2" in summary
    assert "created_ids=" in summary
    apply_result = graph.get_state(config).values.get("apply_result")
    assert apply_result["reclass_updated"] == 2
    assert apply_result["updated_ids"] == [mapping.id]


def test_steward_noop_apply_reports_reclass_updated_zero(
    db_session: Session, agent_sessions
) -> None:
    mapping = _seed_grocery_split(db_session)
    duplicate = [
        {
            "op": "create",
            "kind": "category",
            "raw_value": "groceries",
            "canonical_value": "Groceries",
        }
    ]
    graph = build_steward_graph(
        model=_submit_only_model(duplicate, rationale="already exists"),
        checkpointer=in_memory_checkpointer(),
    )
    config = {"configurable": {"thread_id": "t5-noop"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "submit duplicate"}]},
        config,
    )
    payload = (result.get("__interrupt__") or ())[0].value
    assert payload["preview"]["ops"][0]["duplicate_of_existing_id"] == mapping.id
    resumed = graph.invoke(
        Command(resume={"decision": "approve", "ops": payload["ops"]}), config
    )
    summary = _execute_summary(resumed)
    assert "reclass_updated=0" in summary
    assert "created_ids=[]" in summary
    assert graph.get_state(config).values["apply_result"]["reclass_updated"] == 0
