from datetime import date, datetime
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.config import EnricherDeps, in_memory_checkpointer, set_enricher_deps
from app.agent.coordinator import build_coordinator
from app.agent.enricher_graph import build_enricher_builder, build_enricher_graph
from app.models import (
    Account,
    EnrichmentProposal,
    MerchantSender,
    NormalizationMapping,
    Owner,
    ProposalStatus,
    Transaction,
    TransactionOverride,
)
from app.schemas import MappingPlanIn, SetTransactionCategoryOp
from app.services.enrichment_service import EnrichmentConfig
from app.services.mapping_preview_service import apply_mapping_plan
from app.services.proposal_service import proposal_to_ops
from tests.agent.helpers import ScriptedChatModel, agent_sessions, tool_names
from tests.fakes import FakeEmailSource, FakeExtractor
from app.domain.email_source import EmailMessage, EmailRef
from app.domain.receipts import LineItem, ReceiptExtraction


def _seed_shop(db: Session) -> tuple[Transaction, Transaction]:
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
            "merchant_col": "Merchant",
        },
    )
    db.add(account)
    db.flush()
    db.add(NormalizationMapping(kind="category", raw_value="dining", canonical_value="Dining"))
    db.add(MerchantSender(merchant_key="example shop", sender_pattern="example.com", origin="seed"))
    t1 = Transaction(
        account_id=account.id,
        transaction_date=date(2024, 7, 10),
        description="EXAMPLE SHOP",
        amount=Decimal("25.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        merchant_raw="Example Shop",
        merchant_normalized="Example Shop",
        dedupe_hash="e2e-1",
        raw={"Merchant": "Example Shop"},
    )
    t2 = Transaction(
        account_id=account.id,
        transaction_date=date(2024, 7, 28),
        description="EXAMPLE SHOP 2",
        amount=Decimal("12.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        merchant_raw="Example Shop",
        merchant_normalized="Example Shop",
        dedupe_hash="e2e-2",
        raw={"Merchant": "Example Shop"},
    )
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    return t1, t2


def _message(message_id: str, day: int, total: str) -> EmailMessage:
    return EmailMessage(
        ref=EmailRef(
            message_id=message_id,
            thread_id=None,
            sender="receipts@example.com",
            subject="Your Example Shop order",
            received_at=datetime(2024, 7, day, 12, 0, 0),
            snippet="receipt",
        ),
        body_text=f"Example Shop\nTotal ${total}\n2024-07-{day:02d}",
        headers={"from": "receipts@example.com", "subject": "Your Example Shop order"},
        attachments=[],
        truncated=False,
    )


def _extraction(total: str, day: int) -> ReceiptExtraction:
    return ReceiptExtraction(
        merchant_name="Example Shop",
        order_id=f"ORD-{day}",
        order_date=date(2024, 7, day),
        total=Decimal(total),
        line_items=[
            LineItem(description="Latte", amount=Decimal(total), category_hint="Dining")
        ],
        extractor_version="fake",
        raw_confidence=0.9,
    )


def _shop_deps() -> tuple[EnricherDeps, FakeEmailSource]:
    source = FakeEmailSource(
        [_message("r1", 10, "25.00"), _message("r2", 28, "12.00")],
        ["example.com"],
    )
    deps = EnricherDeps(
        source_factory=lambda: source,
        extractor_factory=lambda: FakeExtractor(
            {"r1": _extraction("25.00", 10), "r2": _extraction("12.00", 28)}
        ),
        config=EnrichmentConfig(),
    )
    return deps, source


def _override_op(txn_id: int, evidence_id: int) -> dict:
    return {
        "op": "set_transaction_category",
        "transaction_id": txn_id,
        "category": "Dining",
        "evidence_ids": [evidence_id],
        "rationale": "dominant line item is dining",
    }


def _recommendation(t1: Transaction, t2: Transaction) -> dict:
    return {
        "proposed_overrides": [
            {**_override_op(t1.id, 1), "confidence": 0.95},
            {
                "transaction_id": t2.id,
                "category": "Dining",
                "evidence_ids": [2],
                "confidence": 0.4,
                "rationale": "weak match",
            },
        ],
        "merchant_rule_suggestions": [],
        "unresolved": [],
        "narrative": "One clear dining receipt; the second is below threshold.",
        "new_categories": [],
    }


def _enricher_model(t1: Transaction, t2: Transaction) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "find_receipts",
                        "args": {"transaction_ids": [t1.id, t2.id]},
                        "id": "en-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_evidence",
                        "args": {"transaction_ids": [t1.id, t2.id]},
                        "id": "en-2",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_recommendation",
                        "args": {"recommendation": _recommendation(t1, t2)},
                        "id": "en-3",
                    }
                ],
            ),
        ]
    )


def _steward_model(ops: list[dict]) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "load_proposal", "args": {"proposal_id": 1}, "id": "st-1"}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "preview_mapping_rules",
                        "args": {"ops": ops, "account_id": None},
                        "id": "st-2",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_plan",
                        "args": {
                            "ops": ops,
                            "account_id": None,
                            "rationale": "from proposal #1; new_categories=[]",
                        },
                        "id": "st-3",
                    }
                ],
            ),
            AIMessage(content="Applied overrides_set=1."),
        ]
    )


def _coordinator(*, model, enricher_model, steward_model, enricher_deps, checkpointer=None):
    return build_coordinator(
        model=model,
        analyst_model=ScriptedChatModel(responses=[AIMessage(content="ANALYST_SHOULD_NOT_RUN")]),
        steward_model=steward_model,
        enricher_model=enricher_model,
        enricher_deps=enricher_deps,
        checkpointer=checkpointer or in_memory_checkpointer(),
    )


def _capture_apply_through(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict[str, MappingPlanIn] = {}

    def wrapped(db, plan: MappingPlanIn):
        captured["plan"] = plan
        return apply_mapping_plan(db, plan)

    monkeypatch.setattr("app.agent.steward_graph.apply_mapping_plan", wrapped)
    return captured


def test_coordinator_enrichment_approve_flow(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    t1, t2 = _seed_shop(db_session)
    deps, _source = _shop_deps()
    set_enricher_deps(deps)
    captured = _capture_apply_through(monkeypatch)
    ops = [_override_op(t1.id, 1)]
    coordinator_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_enricher",
                        "args": {
                            "task": (
                                "What did I buy at Example Shop in July 2024-07-01..2024-07-31"
                            )
                        },
                        "id": "co-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_data_steward",
                        "args": {"task": "Build a plan from proposal #1 and apply the overrides."},
                        "id": "co-2",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "Applied created_ids=[] updated_ids=[] deleted_ids=[] "
                    "overrides_set=1 reclass_updated=0."
                )
            ),
        ]
    )
    graph = _coordinator(
        model=coordinator_model,
        enricher_model=_enricher_model(t1, t2),
        steward_model=_steward_model(ops),
        enricher_deps=deps,
    )
    config = {"configurable": {"thread_id": "e2e-approve"}, "recursion_limit": 25}
    result = graph.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "What did I buy at Example Shop in July, and fix the categories.",
                }
            ]
        },
        config,
    )
    enricher_tool = [
        message
        for message in result.get("messages") or []
        if getattr(message, "type", None) == "tool"
        and "proposal #" in (getattr(message, "content", "") or "")
    ]
    assert enricher_tool, result.get("messages")
    assert "proposal #1" in enricher_tool[0].content

    interrupts = result.get("__interrupt__") or ()
    assert interrupts, f"expected interrupt, got keys {result.keys()}"
    payload = interrupts[0].value
    proposal = db_session.get(EnrichmentProposal, 1)
    assert proposal is not None
    expected_ops = proposal_to_ops(proposal)
    assert payload["ops"] == expected_ops
    assert expected_ops == [
        SetTransactionCategoryOp.model_validate(ops[0]).model_dump()
    ]
    assert t2.id not in {op["transaction_id"] for op in payload["ops"]}
    assert payload["preview"]["overrides"][0]["action"] == "set"

    resumed = graph.invoke(Command(resume={"decision": "approve", "ops": payload["ops"]}), config)
    assert "plan" in captured
    assert [op.model_dump() for op in captured["plan"].ops] == expected_ops
    db_session.expire_all()
    assert db_session.get(Transaction, t1.id).category_override == "Dining"
    assert db_session.get(Transaction, t2.id).category_override is None
    assert db_session.scalar(select(func.count()).select_from(TransactionOverride)) == 1
    consumed = db_session.get(EnrichmentProposal, 1)
    assert consumed.status == ProposalStatus.CONSUMED.value
    assert consumed.consumed_plan_ref
    assert "overrides_set=1" in (resumed["messages"][-1].content or "")
    set_enricher_deps(None)


def test_coordinator_enrichment_reject_keeps_proposal_open(
    db_session: Session, agent_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    t1, t2 = _seed_shop(db_session)
    deps, _source = _shop_deps()
    set_enricher_deps(deps)
    captured = _capture_apply_through(monkeypatch)
    ops = [_override_op(t1.id, 1)]
    coordinator_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_enricher",
                        "args": {"task": "Research Example Shop July"},
                        "id": "co-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_data_steward",
                        "args": {"task": "Build a plan from proposal #1"},
                        "id": "co-2",
                    }
                ],
            ),
            AIMessage(content="Rejected. Nothing was applied."),
        ]
    )
    graph = _coordinator(
        model=coordinator_model,
        enricher_model=_enricher_model(t1, t2),
        steward_model=_steward_model(ops),
        enricher_deps=deps,
    )
    config = {"configurable": {"thread_id": "e2e-reject"}, "recursion_limit": 25}
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "research and fix Example Shop"}]},
        config,
    )
    graph.invoke(Command(resume={"decision": "reject", "ops": []}), config)
    assert "plan" not in captured
    db_session.expire_all()
    proposal = db_session.get(EnrichmentProposal, 1)
    assert proposal.status == ProposalStatus.OPEN.value
    assert db_session.get(Transaction, t1.id).category_override is None
    assert db_session.scalar(select(func.count()).select_from(TransactionOverride)) == 0
    set_enricher_deps(None)
    assert result.get("__interrupt__")


def test_coordinator_source_unavailable_does_not_call_steward(
    db_session: Session, agent_sessions
) -> None:
    t1, t2 = _seed_shop(db_session)
    deps = EnricherDeps(
        source_factory=lambda: None,
        extractor_factory=lambda: FakeExtractor({}),
        config=EnrichmentConfig(),
    )
    set_enricher_deps(deps)
    enricher_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "find_receipts",
                        "args": {"transaction_ids": [t1.id, t2.id]},
                        "id": "en-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_recommendation",
                        "args": {
                            "recommendation": {
                                "proposed_overrides": [],
                                "merchant_rule_suggestions": [],
                                "unresolved": [
                                    {"transaction_id": t1.id, "reason": "source_unavailable"},
                                    {"transaction_id": t2.id, "reason": "source_unavailable"},
                                ],
                                "narrative": "Mailbox unavailable.",
                                "new_categories": [],
                            }
                        },
                        "id": "en-2",
                    }
                ],
            ),
        ]
    )
    coordinator_model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_enricher",
                        "args": {"task": "Research Example Shop July"},
                        "id": "co-1",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "The email source is unavailable. Set EMAIL_PROVIDER "
                    "(none/fake/gmail) to enable it. I will not retry."
                )
            ),
        ]
    )
    graph = _coordinator(
        model=coordinator_model,
        enricher_model=enricher_model,
        steward_model=ScriptedChatModel(responses=[AIMessage(content="STEWARD_SHOULD_NOT_RUN")]),
        enricher_deps=deps,
    )
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "What did I buy at Example Shop?"}]},
        {"configurable": {"thread_id": "e2e-none"}, "recursion_limit": 25},
    )
    names = tool_names(result)
    assert "run_enricher" in names
    assert "run_data_steward" not in names
    proposal = db_session.scalars(select(EnrichmentProposal)).one()
    assert {item["reason"] for item in proposal.recommendation["unresolved"]} == {
        "source_unavailable"
    }
    assert "STEWARD_SHOULD_NOT_RUN" not in str(result.get("messages"))
    set_enricher_deps(None)


def test_coordinator_has_one_checkpointer_enricher_has_none() -> None:
    idle = ScriptedChatModel(responses=[AIMessage(content="idle")])
    graph = build_coordinator(
        model=idle,
        analyst_model=idle,
        steward_model=idle,
        enricher_model=idle,
        checkpointer=in_memory_checkpointer(),
    )
    assert graph.checkpointer is not None
    builder = build_enricher_builder(model=idle)
    compiled = builder.compile()
    assert compiled.checkpointer is None
    nested = build_enricher_graph(model=idle)
    assert nested.checkpointer is None


def test_studio_enricher_factory_has_no_checkpointer(monkeypatch: pytest.MonkeyPatch) -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="idle")])
    monkeypatch.setattr("app.agent.config.model_name", lambda: model)
    monkeypatch.setattr("app.agent.config.enricher_model_name", lambda: model)
    from app.agent import studio

    graph = studio.enricher_graph()
    assert graph.checkpointer is None
