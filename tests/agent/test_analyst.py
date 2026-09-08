from datetime import date
from decimal import Decimal
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.analyst import ANALYST_PROMPT, build_analyst
from app.agent.middleware import LEDGER_CONTEXT_PREFIX
from app.models import Account, AnalysisArtifact, Owner, Transaction
from langchain.agents.middleware.summarization import count_tokens_approximately
from tests.agent.helpers import ScriptedChatModel, agent_sessions, seed_coffee


def test_analyst_prompt_is_verbatim_in_project_map() -> None:
    text = Path("docs/PROJECT-MAP.md").read_text()
    assert ANALYST_PROMPT.strip() in text


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
        assert "Artifact #" in message.content
        assert "kind=group_summary" in message.content
        assert message.artifact is not None
        assert "artifact_id" in message.artifact


def test_analyst_model_sees_ledger_snapshot(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()
    account = db_session.scalars(select(Account)).one()
    db_session.add(
        Transaction(
            account_id=account.id,
            owner_id=owner.id,
            transaction_date=date(2024, 7, 10),
            description="COFFEE TWO",
            amount=Decimal("5.00"),
            transaction_type="SPEND",
            is_spend=True,
            category_normalized="Dining",
            subcategory="Coffee",
            merchant_normalized="Starbucks",
            dedupe_hash="analyst-snapshot-coffee",
            raw={},
        )
    )
    db_session.commit()

    seen: list[list] = []

    class CapturingChatModel(ScriptedChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(list(messages))
            return super()._generate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )

    model = CapturingChatModel(responses=[AIMessage(content="ok")])
    analyst = build_analyst(model=model)
    analyst.invoke({"messages": [{"role": "user", "content": "what categories exist?"}]})

    humans = [
        message
        for message in seen[0]
        if isinstance(message, HumanMessage)
    ]
    text = humans[-1].content
    assert isinstance(text, str)
    assert LEDGER_CONTEXT_PREFIX in text
    assert "2 transactions, 1 merchant." in text
    assert f"Owners: {owner.id}=Pat" in text
    assert "- Dining (1): Coffee (1)" in text
    assert "- Food & Drink (1)" in text
    assert text.endswith("what categories exist?")


def test_baseline_token_benchmarks_u2_u4_u10(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()
    account = db_session.scalars(select(Account)).one()
    for i in range(1, 11):
        _add_spend(
            db_session,
            account,
            day=date(2024, 7, i),
            description=f"STREAMING SUBSCRIPTION {i}",
            amount="14.99",
            category="Subscriptions",
            owner_id=owner.id,
        )
    db_session.commit()

    # Journey U2: Category breakdown ("Where did my money go in Q2?")
    u2_model = ScriptedChatModel(
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
                        "id": "sum-u2",
                    }
                ],
            ),
            AIMessage(content="You spent money on Subscriptions and Dining in July."),
        ]
    )
    analyst_u2 = build_analyst(model=u2_model)
    res_u2 = analyst_u2.invoke(
        {"messages": [{"role": "user", "content": "Where did my money go in July?"}]}
    )
    tokens_u2 = count_tokens_approximately(res_u2["messages"])
    assert tokens_u2 > 0

    # Journey U4: Subscription discovery (Iterative search_transactions)
    u4_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_transactions",
                        "args": {"query": "streaming"},
                        "id": "search-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_transactions",
                        "args": {"query": "subscription"},
                        "id": "search-2",
                    }
                ],
            ),
            AIMessage(content="Found 10 recurring subscriptions."),
        ]
    )
    analyst_u4 = build_analyst(model=u4_model)
    res_u4 = analyst_u4.invoke(
        {"messages": [{"role": "user", "content": "Find my subscriptions."}]}
    )
    tokens_u4 = count_tokens_approximately(res_u4["messages"])
    assert tokens_u4 > tokens_u2

    # Journey U10: Multi-turn coordinator thread accumulation baseline
    coord_messages = [
        HumanMessage(content="What did I spend?"),
        AIMessage(content="You spent 149.90"),
        HumanMessage(content="Show details"),
        AIMessage(content="Details here"),
        HumanMessage(content="And last month?"),
        AIMessage(content="Zero last month"),
    ]
    tokens_u10 = count_tokens_approximately(coord_messages)
    assert tokens_u10 > 0


def test_analyst_submit_analysis_updates_state_and_thread_id(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize",
                        "args": {"group_by": "category", "owner_id": owner.id},
                        "id": "call-sum",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_analysis",
                        "args": {
                            "artifact_ids": [1],
                            "narrative": "Coffee is the dominant spend.",
                        },
                        "id": "call-submit",
                    }
                ],
            ),
        ]
    )
    analyst = build_analyst(model=model)
    res = analyst.invoke(
        {"messages": [{"role": "user", "content": "Analyze my spend"}]},
        config={"configurable": {"thread_id": "thread-analysis-1"}},
    )
    assert res.get("artifact_ids") == [1]
    assert res.get("narrative") == "Coffee is the dominant spend."
    last_msg = res["messages"][-1]
    assert last_msg.type == "tool"
    assert "artifacts: [1]. Coffee is the dominant spend." in last_msg.content


def test_analyst_emits_custom_ui_event_stream(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize",
                        "args": {"group_by": "category", "owner_id": owner.id},
                        "id": "c1",
                    }
                ],
            ),
            AIMessage(content="Finished"),
        ]
    )
    analyst = build_analyst(model=model)
    events = []
    for mode, chunk in analyst.stream(
        {"messages": [{"role": "user", "content": "summarize category"}]},
        stream_mode=["custom"],
    ):
        if mode == "custom":
            events.append(chunk)

    assert len(events) >= 1
    ui_event = events[0]
    assert ui_event["type"] == "ui"
    assert ui_event["name"] == "grouped_table"
    props = ui_event["props"]
    assert props["type"] == "artifact_ref"
    assert props["component"] == "grouped_table"
    assert props["kind"] == "group_summary"
    assert "artifact_id" in props
    assert props["fetch"] == f"/artifacts/{props['artifact_id']}/rows"


def test_open_artifact_unwraps_large_tool_output_without_double_escape(
    db_session: Session, agent_sessions
) -> None:
    import json

    from app.agent.tools.read import open_artifact
    from app.services import artifact_service

    inner = {
        "transactions": [
            {"id": 1, "description": "COFFEE", "amount": "-4.50"},
            {"id": 2, "description": "TEA", "amount": "-3.00"},
            {"id": 3, "description": "SHOP, MAIN", "amount": "-10.00"},
        ]
    }
    nested_raw = json.dumps({"raw": json.dumps(inner)})
    art_id, _ = artifact_service.persist_artifact(
        db_session,
        thread_id="thread-open-1",
        kind="large_tool_output",
        title="Offloaded open_artifact output",
        spec={"tool": "open_artifact", "kwargs": {"artifact_id": 3, "limit": 31}},
        result={"raw": nested_raw},
        produced_by="analyst",
    )
    text = open_artifact.func(art_id, None, 2, 0)
    assert "\\\\" not in text
    assert "transactions[2]{id,description,amount}:" in text
    assert "COFFEE" in text
    assert "TEA" in text
    assert "SHOP" not in text
    assert "total: 3" in text
    assert "kind: large_tool_output" in text


def test_open_artifact_slices_non_json_large_tool_output(
    db_session: Session, agent_sessions
) -> None:
    from app.agent.tools.read import open_artifact
    from app.services import artifact_service

    raw = "line-one\nline-two\nline-three\nline-four"
    art_id, _ = artifact_service.persist_artifact(
        db_session,
        thread_id="thread-open-2",
        kind="large_tool_output",
        title="Offloaded text",
        spec={"tool": "search_transactions", "kwargs": {"query": "x"}},
        result={"raw": raw},
        produced_by="analyst",
    )
    text = open_artifact.func(art_id, None, 2, 1)
    assert "\\\\" not in text
    assert "line-two" in text
    assert "line-three" in text
    assert "line-one" not in text
    assert "line-four" not in text
    assert "total: 4" in text


def test_analyst_provisional_and_promote_lifecycle(
    db_session: Session, agent_sessions
) -> None:
    seed_coffee(db_session)
    owner = db_session.scalars(select(Owner)).one()

    model = ScriptedChatModel(
        responses=[
            # 1. Exploratory probe: list_values
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_values",
                        "args": {"dimension": "category"},
                        "id": "probe-call-1",
                    }
                ],
            ),
            # 2. Key deliverable: summarize
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize",
                        "args": {"group_by": "category", "owner_id": owner.id},
                        "id": "deliverable-call-2",
                    }
                ],
            ),
            # 3. JIT inspect deliverable before submitting
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "open_artifact",
                        "args": {"artifact_id": 2, "limit": 10},
                        "id": "open-call-3",
                    }
                ],
            ),
            # 4. Final finish: submit_analysis citing only deliverable (artifact 2)
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_analysis",
                        "args": {
                            "artifact_ids": [2],
                            "narrative": "Found dominant category.",
                        },
                        "id": "submit-call-4",
                    }
                ],
            ),
        ]
    )
    analyst = build_analyst(model=model)
    res = analyst.invoke(
        {"messages": [{"role": "user", "content": "Analyze categories"}]},
        config={"configurable": {"thread_id": "thread-lifecycle-test"}},
    )

    assert res.get("artifact_ids") == [2]
    art_probe = db_session.get(AnalysisArtifact, 1)
    art_deliverable = db_session.get(AnalysisArtifact, 2)

    assert art_probe is not None
    assert art_deliverable is not None

    # Probe was not cited -> superseded
    assert art_probe.status == "superseded"
    assert art_probe.expires_at is not None

    # Deliverable was cited -> promoted to open and durable
    assert art_deliverable.status == "open"
    assert art_deliverable.expires_at is None





