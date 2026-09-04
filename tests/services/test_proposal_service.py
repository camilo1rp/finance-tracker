from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.agent.schemas import EnrichmentRecommendation, ProposedOverride, UnresolvedTransaction
from app.models import (
    Account,
    EnrichmentProposal,
    NormalizationMapping,
    Owner,
    ProposalStatus,
    Transaction,
    TransactionEvidence,
)
from app.schemas import CreateMappingOp, parse_mapping_op
from app.services.proposal_service import (
    proposal_to_ops,
    store_proposal,
    validate_recommendation,
)


def _seed(db: Session) -> tuple[Transaction, Transaction, TransactionEvidence, TransactionEvidence]:
    owner = Owner(name="Pat")
    db.add(owner)
    db.flush()
    account = Account(
        name="Card",
        last4="1111",
        default_owner_id=owner.id,
        source_format="csv",
        default_mapping={"date_col": "Date", "description_col": "Description", "amount_col": "Amount"},
    )
    db.add(account)
    db.flush()
    db.add(
        NormalizationMapping(
            kind="category",
            raw_value="dining",
            canonical_value="Dining",
        )
    )
    t1 = Transaction(
        account_id=account.id,
        transaction_date=date(2024, 7, 10),
        description="SHOP A",
        amount=Decimal("25.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        merchant_raw="Example Shop",
        dedupe_hash="prop-1",
        raw={},
    )
    t2 = Transaction(
        account_id=account.id,
        transaction_date=date(2024, 7, 15),
        description="SHOP B",
        amount=Decimal("12.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        merchant_raw="Example Shop",
        dedupe_hash="prop-2",
        raw={},
    )
    db.add_all([t1, t2])
    db.flush()
    e1 = TransactionEvidence(
        transaction_id=t1.id,
        kind="email_receipt",
        provider="fake",
        external_ref="fake:a",
        extraction={},
        match_kind="exact_total",
        confidence=0.9,
        dominant_category="Dining",
        dominant_category_raw="Dining",
        extractor_version="fake",
    )
    e2 = TransactionEvidence(
        transaction_id=t2.id,
        kind="email_receipt",
        provider="fake",
        external_ref="fake:b",
        extraction={},
        match_kind="exact_total",
        confidence=0.9,
        dominant_category="Dining",
        dominant_category_raw="Dining",
        extractor_version="fake",
    )
    db.add_all([e1, e2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    db.refresh(e1)
    db.refresh(e2)
    return t1, t2, e1, e2


def _override(txn_id: int, evidence_id: int, **kwargs) -> ProposedOverride:
    payload = {
        "transaction_id": txn_id,
        "category": "Dining",
        "evidence_ids": [evidence_id],
        "confidence": 0.9,
        "rationale": "receipt matched",
    }
    payload.update(kwargs)
    return ProposedOverride.model_validate(payload)


def test_unknown_transaction_moves_to_unresolved(db_session: Session) -> None:
    t1, _t2, e1, _e2 = _seed(db_session)
    rec = EnrichmentRecommendation(
        proposed_overrides=[_override(9999, e1.id)],
        merchant_rule_suggestions=[],
        unresolved=[],
        narrative="x",
    )
    normalized, adjustments = validate_recommendation(db_session, rec, 0.8)
    assert normalized.proposed_overrides == []
    assert normalized.unresolved == [
        UnresolvedTransaction(transaction_id=9999, reason="unknown_transaction")
    ]
    assert any("unknown_transaction" in item for item in adjustments)


def test_evidence_mismatch_moves_to_unresolved(db_session: Session) -> None:
    t1, t2, _e1, e2 = _seed(db_session)
    rec = EnrichmentRecommendation(
        proposed_overrides=[_override(t1.id, e2.id)],
        merchant_rule_suggestions=[],
        unresolved=[],
        narrative="x",
    )
    normalized, _adjustments = validate_recommendation(db_session, rec, 0.8)
    assert normalized.proposed_overrides == []
    assert normalized.unresolved[0].reason == "evidence_mismatch"
    assert normalized.unresolved[0].transaction_id == t1.id


def test_below_threshold_moves_to_unresolved(db_session: Session) -> None:
    t1, _t2, e1, _e2 = _seed(db_session)
    rec = EnrichmentRecommendation(
        proposed_overrides=[_override(t1.id, e1.id, confidence=0.4)],
        merchant_rule_suggestions=[],
        unresolved=[],
        narrative="x",
    )
    normalized, _adjustments = validate_recommendation(db_session, rec, 0.8)
    assert normalized.proposed_overrides == []
    assert normalized.unresolved[0].reason == "below_threshold"


def test_unknown_category_kept_and_listed(db_session: Session) -> None:
    t1, _t2, e1, _e2 = _seed(db_session)
    rec = EnrichmentRecommendation(
        proposed_overrides=[_override(t1.id, e1.id, category="Widgets")],
        merchant_rule_suggestions=[],
        unresolved=[],
        narrative="x",
        new_categories=[],
    )
    normalized, adjustments = validate_recommendation(db_session, rec, 0.8)
    assert len(normalized.proposed_overrides) == 1
    assert normalized.proposed_overrides[0].category == "Widgets"
    assert normalized.new_categories == ["Widgets"]
    assert any("Widgets" in item for item in adjustments)


def test_duplicate_transaction_ids_keep_highest_confidence(db_session: Session) -> None:
    t1, _t2, e1, _e2 = _seed(db_session)
    rec = EnrichmentRecommendation(
        proposed_overrides=[
            _override(t1.id, e1.id, confidence=0.82, rationale="low"),
            _override(t1.id, e1.id, confidence=0.95, rationale="high"),
        ],
        merchant_rule_suggestions=[],
        unresolved=[],
        narrative="x",
    )
    normalized, adjustments = validate_recommendation(db_session, rec, 0.8)
    assert len(normalized.proposed_overrides) == 1
    assert normalized.proposed_overrides[0].confidence == 0.95
    assert normalized.proposed_overrides[0].rationale == "high"
    assert any("duplicate" in item for item in adjustments)


def test_structurally_invalid_raises_value_error(db_session: Session) -> None:
    with pytest.raises(ValueError, match="structurally invalid"):
        validate_recommendation(
            db_session,
            {"proposed_overrides": "not-a-list", "narrative": "x"},
            0.8,
        )


def test_proposal_to_ops_ordering_and_shapes(db_session: Session) -> None:
    t1, t2, e1, e2 = _seed(db_session)
    rec = EnrichmentRecommendation(
        proposed_overrides=[
            _override(t1.id, e1.id, category="Dining"),
            _override(t2.id, e2.id, category="Dining"),
        ],
        merchant_rule_suggestions=[
            CreateMappingOp(
                kind="category",
                raw_value="example shop",
                canonical_value="Dining",
                merchant="example shop",
            )
        ],
        unresolved=[],
        narrative="two overrides and a rule",
    )
    proposal = store_proposal(db_session, "task", rec)
    db_session.commit()
    assert proposal.status == ProposalStatus.OPEN.value
    ops = proposal_to_ops(proposal)
    assert [parse_mapping_op(op).op for op in ops] == [
        "set_transaction_category",
        "set_transaction_category",
        "create",
    ]
    assert ops[0]["evidence_ids"] == [e1.id]
    assert ops[0]["rationale"] == "receipt matched"
    assert ops[2]["kind"] == "category"
    assert isinstance(db_session.get(EnrichmentProposal, proposal.id), EnrichmentProposal)
