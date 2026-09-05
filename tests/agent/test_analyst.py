from datetime import date
from decimal import Decimal

from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.analyst import build_analyst
from app.models import Account, Owner, Transaction
from tests.agent.helpers import ScriptedChatModel, agent_sessions, seed_coffee


def _tool_names(result: dict) -> list[str]:
    names: list[str] = []
    for message in result.get("messages") or []:
        for call in getattr(message, "tool_calls", None) or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if name:
                names.append(name)
    return names


def _final_text(result: dict) -> str:
    """Subagent-as-tool wrapper shape: only the last message content."""
    return result["messages"][-1].content


def _add_spend(
    db: Session,
    account: Account,
    *,
    day: date,
    description: str,
    amount: str,
    category: str,
    owner_id: int,
) -> None:
    db.add(
        Transaction(
            account_id=account.id,
            owner_id=owner_id,
            transaction_date=day,
            description=description,
            amount=Decimal(amount),
            transaction_type="SPEND",
            is_spend=True,
            category_raw=category,
            category_normalized=category,
            dedupe_hash=f"analyst-{description}-{day.isoformat()}",
            raw={},
        )
    )


def test_analyst_two_summarize_calls_wrapper_returns_final_only(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()
    account = db_session.scalars(select(Account)).one()
    _add_spend(
        db_session,
        account,
        day=date(2024, 7, 10),
        description="JULY DINE",
        amount="10.00",
        category="Dining",
        owner_id=owner.id,
    )
    _add_spend(
        db_session,
        account,
        day=date(2024, 8, 10),
        description="AUG DINE",
        amount="20.00",
        category="Dining",
        owner_id=owner.id,
    )
    db_session.commit()

    final = (
        "July Dining=10.00 August Dining=20.00 delta=10.00. "
        "Filters: owner_id=1, 2024-07-01..2024-07-31 vs 2024-08-01..2024-08-31."
    )
    model = ScriptedChatModel(
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
                            "owner_id": owner.id,
                        },
                        "id": "sum-july",
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
                            "owner_id": owner.id,
                        },
                        "id": "sum-aug",
                    }
                ],
            ),
            AIMessage(content=final),
        ]
    )
    analyst = build_analyst(model=model)
    result = analyst.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Compare July vs August dining for owner_id={owner.id} "
                        "using 2024-07-01..2024-07-31 and 2024-08-01..2024-08-31."
                    ),
                }
            ]
        }
    )

    assert _tool_names(result) == ["summarize", "summarize"]
    tool_messages = [
        message
        for message in result["messages"]
        if getattr(message, "type", None) == "tool"
    ]
    assert len(tool_messages) == 2
    assert all("Dining" in (message.content or "") for message in tool_messages)

    wrapped = _final_text(result)
    assert wrapped == final
    for message in tool_messages:
        assert message.content != wrapped
        assert "group_value" in message.content
