from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command
from sqlalchemy.orm import Session, sessionmaker

from app.agent.config import in_memory_checkpointer, set_session_factory
from app.agent.steward_graph import build_steward_graph
from app.models import Account, Owner, Transaction
from app.schemas import ApplyMappingPlanIn, ApplyResult, ProposedMappingIn, UnmappedValuesOut


class ScriptedChatModel(FakeMessagesListChatModel):
    """Fake model that stays on the last scripted response and supports bind_tools."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        response = self.responses[min(self.i, len(self.responses) - 1)]
        if self.i < len(self.responses) - 1:
            self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


RULES = [
    {"kind": "category", "raw_value": "Food & Drink", "canonical_value": "Dining"},
    {"kind": "category", "raw_value": "Shopping", "canonical_value": "Shopping"},
]


@pytest.fixture()
def agent_sessions(db_session: Session):
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    yield
    set_session_factory(None)


def _seed(db: Session) -> None:
    owner = Owner(name="Pat")
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
            "type_col": "Type",
        },
    )
    db.add(account)
    db.flush()
    db.add(
        Transaction(
            account_id=account.id,
            transaction_date=date(2024, 6, 1),
            description="COFFEE",
            amount=Decimal("4.50"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Food & Drink",
            category_normalized=None,
            dedupe_hash="steward-coffee",
            raw={},
        )
    )
    db.commit()


def test_steward_interrupt_flow(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db_session)
    captured: dict[str, ApplyMappingPlanIn] = {}

    def fake_apply(_db: Session, plan: ApplyMappingPlanIn) -> ApplyResult:
        captured["plan"] = plan
        return ApplyResult(
            created_mapping_ids=[1],
            skipped_duplicates=[],
            reclass_scanned=1,
            reclass_updated=1,
            unmapped_after=UnmappedValuesOut(
                transaction_types=[],
                categories=[],
                owners=[],
                merchants=[],
            ),
        )

    monkeypatch.setattr("app.agent.steward_graph.apply_mapping_plan", fake_apply)

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
                        "args": {"rules": RULES, "account_id": None},
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
                            "rules": RULES,
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
    assert len(payload["rules"]) == 2

    subset = [payload["rules"][0]]
    resumed = graph.invoke(
        Command(resume={"decision": "approve", "rules": subset}),
        config,
    )
    assert "plan" in captured
    received = captured["plan"].rules
    assert [rule.model_dump() for rule in received] == [
        ProposedMappingIn.model_validate(subset[0]).model_dump()
    ]
    assert received[0].raw_value == "Food & Drink"
    assert len(received) == 1

    state = graph.get_state(config)
    apply_result = state.values.get("apply_result")
    assert apply_result is not None
    assert apply_result["created_mapping_ids"] == [1]
    assert apply_result["reclass_updated"] == 1
    assert not resumed.get("__interrupt__")
