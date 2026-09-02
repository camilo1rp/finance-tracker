from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy.orm import Session, sessionmaker

from app.agent.config import set_session_factory
from app.models import Account, Owner, Transaction
from app.schemas import ApplyMappingPlanIn, ApplyResult, UnmappedValuesOut


class ScriptedChatModel(FakeMessagesListChatModel):
    """Fake model that stays on the last scripted response and supports bind_tools."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        response = self.responses[min(self.i, len(self.responses) - 1)]
        if self.i < len(self.responses) - 1:
            self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


def tool_names(result: dict) -> list[str]:
    names: list[str] = []
    for message in result.get("messages") or []:
        for call in getattr(message, "tool_calls", None) or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if name:
                names.append(name)
    return names


RULES = [
    {"kind": "category", "raw_value": "Food & Drink", "canonical_value": "Dining"},
    {"kind": "category", "raw_value": "Shopping", "canonical_value": "Shopping"},
]

RULES_A = [RULES[0]]
RULES_B = [RULES[1]]


@pytest.fixture()
def agent_sessions(db_session: Session):
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    yield
    set_session_factory(None)


def seed_coffee(db: Session) -> None:
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


def fake_apply_result() -> ApplyResult:
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


def capture_apply(monkeypatch: pytest.MonkeyPatch) -> dict[str, ApplyMappingPlanIn]:
    captured: dict[str, ApplyMappingPlanIn] = {}

    def fake_apply(_db: Session, plan: ApplyMappingPlanIn) -> ApplyResult:
        captured["plan"] = plan
        return fake_apply_result()

    monkeypatch.setattr("app.agent.steward_graph.apply_mapping_plan", fake_apply)
    return captured
