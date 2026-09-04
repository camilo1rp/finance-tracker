from datetime import date, datetime
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.config import EnricherDeps, set_enricher_deps
from app.agent.enricher_graph import ENRICHER_SYSTEM_PROMPT, build_enricher_graph
from app.agent.tools.enricher import find_receipts, get_evidence
from app.domain.email_source import EmailMessage, EmailRef
from app.domain.receipts import LineItem, ReceiptExtraction
from app.models import (
    EnrichmentProposal,
    MerchantSender,
    NormalizationMapping,
    ProposalStatus,
    Transaction,
    TransactionEvidence,
    TransactionOverride,
)
from app.services.enrichment_service import EnrichmentConfig
from tests.agent.helpers import ScriptedChatModel, agent_sessions, seed_coffee
from tests.fakes import FakeEmailSource, FakeExtractor

SENTINEL = "SENTINEL_LINE_ITEM_PII"


def _shop_txn(db: Session, account_id: int, suffix: str, amount: str, day: int) -> Transaction:
    row = Transaction(
        account_id=account_id,
        transaction_date=date(2024, 7, day),
        description=f"EXAMPLE SHOP {suffix}",
        amount=Decimal(amount),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        merchant_raw="Example Shop",
        merchant_normalized="Example Shop",
        dedupe_hash=f"enricher-{suffix}",
        raw={"Merchant": "Example Shop"},
    )
    db.add(row)
    db.flush()
    return row


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


def _extraction(total: str, day: int, description: str = "Latte") -> ReceiptExtraction:
    return ReceiptExtraction(
        merchant_name="Example Shop",
        order_id=f"ORD-{day}",
        order_date=date(2024, 7, day),
        total=Decimal(total),
        line_items=[
            LineItem(
                description=description,
                amount=Decimal(total),
                category_hint="Dining",
            )
        ],
        extractor_version="fake",
        raw_confidence=0.9,
    )


@pytest.fixture()
def shop_txns(db_session: Session, agent_sessions) -> tuple[Transaction, Transaction]:
    seed_coffee(db_session)
    coffee = db_session.scalars(select(Transaction)).one()
    t1 = _shop_txn(db_session, coffee.account_id, "a", "25.00", 10)
    t2 = _shop_txn(db_session, coffee.account_id, "b", "12.00", 28)
    db_session.add(
        NormalizationMapping(kind="category", raw_value="dining", canonical_value="Dining")
    )
    db_session.add(
        MerchantSender(merchant_key="example shop", sender_pattern="example.com", origin="seed")
    )
    db_session.commit()
    db_session.refresh(t1)
    db_session.refresh(t2)
    return t1, t2


@pytest.fixture()
def enricher_deps(shop_txns: tuple[Transaction, Transaction]):
    source = FakeEmailSource(
        [_message("r1", 10, "25.00"), _message("r2", 28, "12.00")],
        ["example.com"],
    )
    extractor = FakeExtractor(
        {
            "r1": _extraction("25.00", 10),
            "r2": _extraction("12.00", 28),
        }
    )
    deps = EnricherDeps(
        source_factory=lambda: source,
        extractor_factory=lambda: extractor,
        config=EnrichmentConfig(),
    )
    set_enricher_deps(deps)
    yield deps, source
    set_enricher_deps(None)


def _submit_args(t1: Transaction, t2: Transaction, e1: int, e2: int) -> dict:
    return {
        "recommendation": {
            "proposed_overrides": [
                {
                    "transaction_id": t1.id,
                    "category": "Dining",
                    "evidence_ids": [e1],
                    "confidence": 0.95,
                    "rationale": "dominant line item is dining",
                },
                {
                    "transaction_id": t2.id,
                    "category": "Dining",
                    "evidence_ids": [e2],
                    "confidence": 0.92,
                    "rationale": "second receipt also dining",
                },
            ],
            "merchant_rule_suggestions": [],
            "unresolved": [],
            "narrative": "Matched two Example Shop receipts.",
            "new_categories": [],
        }
    }


def test_scripted_enricher_happy_path(
    db_session: Session, shop_txns: tuple[Transaction, Transaction], enricher_deps
) -> None:
    t1, t2 = shop_txns
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "find_receipts",
                        "args": {"transaction_ids": [t1.id, t2.id]},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_evidence",
                        "args": {"transaction_ids": [t1.id, t2.id]},
                        "id": "call-2",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_recommendation",
                        "args": _submit_args(t1, t2, 1, 2),
                        "id": "call-3",
                    }
                ],
            ),
            AIMessage(content="SHOULD_NOT_RUN"),
        ]
    )
    graph = build_enricher_graph(model=model)
    result = graph.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": f"Research Example Shop transactions {t1.id} and {t2.id}",
                }
            ]
        },
        {"recursion_limit": 15},
    )
    evidence_ids = list(db_session.scalars(select(TransactionEvidence.id)).all())
    assert len(evidence_ids) == 2
    proposal = db_session.scalars(select(EnrichmentProposal)).one()
    assert proposal.status == ProposalStatus.OPEN.value
    assert result.get("proposal_id") == proposal.id
    last_tool = None
    for message in result.get("messages") or []:
        if getattr(message, "type", None) == "tool":
            last_tool = message
    assert last_tool is not None
    assert f"proposal #{proposal.id}:" in last_tool.content
    assert "2 overrides" in last_tool.content
    assert "SHOULD_NOT_RUN" not in str(result.get("messages"))
    assert model.i == 3


def test_scripted_enricher_source_unavailable(
    db_session: Session, shop_txns: tuple[Transaction, Transaction], agent_sessions
) -> None:
    t1, t2 = shop_txns
    source = FakeEmailSource(
        [_message("r1", 10, "25.00")],
        ["example.com"],
        raise_unavailable=True,
    )
    deps = EnricherDeps(
        source_factory=lambda: source,
        extractor_factory=lambda: FakeExtractor({}),
        config=EnrichmentConfig(),
    )
    set_enricher_deps(deps)
    try:
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "find_receipts",
                            "args": {"transaction_ids": [t1.id, t2.id]},
                            "id": "call-1",
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
                                        {
                                            "transaction_id": t1.id,
                                            "reason": "source_unavailable",
                                        },
                                        {
                                            "transaction_id": t2.id,
                                            "reason": "source_unavailable",
                                        },
                                    ],
                                    "narrative": "Mailbox unavailable.",
                                    "new_categories": [],
                                }
                            },
                            "id": "call-2",
                        }
                    ],
                ),
            ]
        )
        graph = build_enricher_graph(model=model, deps=deps)
        result = graph.invoke(
            {"messages": [{"role": "user", "content": f"Research {t1.id} and {t2.id}"}]},
            {"recursion_limit": 15},
        )
        find_result = None
        for message in result.get("messages") or []:
            if getattr(message, "type", None) == "tool" and "unavailable" in (
                getattr(message, "content", "") or ""
            ):
                find_result = message.content
                break
        assert find_result is not None
        assert find_result.count("\n") == 0
        assert source.search_calls == 1
        proposal = db_session.scalars(select(EnrichmentProposal)).one()
        assert result.get("proposal_id") == proposal.id
        unresolved = proposal.recommendation["unresolved"]
        assert {item["reason"] for item in unresolved} == {"source_unavailable"}
    finally:
        set_enricher_deps(None)


def test_find_receipts_dead_source_stops_after_one_call(
    db_session: Session, shop_txns: tuple[Transaction, Transaction], agent_sessions
) -> None:
    t1, _t2 = shop_txns
    source = FakeEmailSource([], ["example.com"], raise_unavailable=True)
    set_enricher_deps(
        EnricherDeps(
            source_factory=lambda: source,
            extractor_factory=lambda: FakeExtractor({}),
            config=EnrichmentConfig(),
        )
    )
    try:
        ids = [t1.id, *range(1000, 1024)]
        assert len(ids) == 25
        text = find_receipts.invoke({"transaction_ids": ids, "force": False})
        assert "unavailable" in text
        assert text.count("\n") == 0
        assert source.search_calls == 1
    finally:
        set_enricher_deps(None)


def test_get_evidence_omits_line_item_descriptions(
    db_session: Session, shop_txns: tuple[Transaction, Transaction], agent_sessions
) -> None:
    t1, _t2 = shop_txns
    db_session.add(
        TransactionEvidence(
            transaction_id=t1.id,
            kind="email_receipt",
            provider="fake",
            external_ref="fake:sentinel",
            extraction={
                "order_id": "ORD-10",
                "order_date": "2024-07-10",
                "line_items": [{"description": SENTINEL, "amount": "25.00"}],
            },
            match_kind="exact_total",
            confidence=0.9,
            dominant_category="Dining",
            dominant_category_raw="Dining",
            extractor_version="fake",
        )
    )
    db_session.commit()
    text = get_evidence.invoke({"transaction_ids": [t1.id]})
    assert SENTINEL not in text
    assert "line_item_count=1" in text
    assert "ORD-10" in text


def test_enricher_writes_no_effective_values(
    db_session: Session, shop_txns: tuple[Transaction, Transaction], enricher_deps
) -> None:
    t1, t2 = shop_txns
    before_txns = [
        (
            row.id,
            row.category_override,
            row.category_normalized,
            row.category_raw,
            row.merchant_override,
            row.merchant_normalized,
        )
        for row in db_session.scalars(select(Transaction).order_by(Transaction.id)).all()
    ]
    before_mappings = [
        (row.id, row.kind, row.raw_value, row.canonical_value)
        for row in db_session.scalars(select(NormalizationMapping).order_by(NormalizationMapping.id)).all()
    ]
    before_overrides = db_session.scalar(select(func.count()).select_from(TransactionOverride))
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "find_receipts",
                        "args": {"transaction_ids": [t1.id, t2.id]},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_recommendation",
                        "args": _submit_args(t1, t2, 1, 2),
                        "id": "call-2",
                    }
                ],
            ),
        ]
    )
    graph = build_enricher_graph(model=model)
    graph.invoke(
        {"messages": [{"role": "user", "content": "research those two"}]},
        {"recursion_limit": 15},
    )
    db_session.expire_all()
    after_txns = [
        (
            row.id,
            row.category_override,
            row.category_normalized,
            row.category_raw,
            row.merchant_override,
            row.merchant_normalized,
        )
        for row in db_session.scalars(select(Transaction).order_by(Transaction.id)).all()
    ]
    after_mappings = [
        (row.id, row.kind, row.raw_value, row.canonical_value)
        for row in db_session.scalars(select(NormalizationMapping).order_by(NormalizationMapping.id)).all()
    ]
    after_overrides = db_session.scalar(select(func.count()).select_from(TransactionOverride))
    assert after_txns == before_txns
    assert after_mappings == before_mappings
    assert after_overrides == before_overrides
    assert db_session.scalar(select(func.count()).select_from(EnrichmentProposal)) == 1
    assert db_session.scalar(select(func.count()).select_from(TransactionEvidence)) >= 1


def test_system_prompt_contains_required_constraints() -> None:
    prompt = ENRICHER_SYSTEM_PROMPT
    assert "submit_recommendation" in prompt
    assert "never apply" in prompt.lower() or "never apply category" in prompt
    assert "find_receipts" in prompt
    assert "get_evidence" in prompt
    assert "source_unavailable" in prompt
    assert "merchant_rule_suggestions" in prompt
