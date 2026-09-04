from __future__ import annotations

from pydantic import ValidationError
from langsmith import traceable
from sqlalchemy.orm import Session

from app.agent.schemas import (
    EnrichmentRecommendation,
    ProposedOverride,
    UnresolvedTransaction,
)
from app.models import EnrichmentProposal, ProposalStatus, Transaction, TransactionEvidence
from app.services.enrichment_service import (
    _enrichment_trace_inputs,
    _enrichment_trace_outputs,
    known_categories,
)


@traceable(process_inputs=_enrichment_trace_inputs, process_outputs=_enrichment_trace_outputs)
def validate_recommendation(
    db: Session,
    rec: EnrichmentRecommendation | dict,
    threshold: float,
) -> tuple[EnrichmentRecommendation, list[str]]:
    if not isinstance(rec, EnrichmentRecommendation):
        try:
            rec = EnrichmentRecommendation.model_validate(rec)
        except ValidationError as exc:
            raise ValueError(f"structurally invalid recommendation: {exc}") from exc

    adjustments: list[str] = []
    collapsed = _collapse_duplicate_overrides(rec.proposed_overrides, adjustments)
    known = set(known_categories(db))
    kept: list[ProposedOverride] = []
    unresolved = list(rec.unresolved)
    new_categories: list[str] = []
    seen_new: set[str] = set()

    for override in collapsed:
        txn = db.get(Transaction, override.transaction_id)
        if txn is None:
            unresolved.append(
                UnresolvedTransaction(
                    transaction_id=override.transaction_id,
                    reason="unknown_transaction",
                )
            )
            adjustments.append(
                f"moved transaction {override.transaction_id} to unresolved (unknown_transaction)"
            )
            continue
        if not _evidence_belongs_to_transaction(db, override.transaction_id, override.evidence_ids):
            unresolved.append(
                UnresolvedTransaction(
                    transaction_id=override.transaction_id,
                    reason="evidence_mismatch",
                )
            )
            adjustments.append(
                f"moved transaction {override.transaction_id} to unresolved (evidence_mismatch)"
            )
            continue
        if override.confidence < threshold:
            unresolved.append(
                UnresolvedTransaction(
                    transaction_id=override.transaction_id,
                    reason="below_threshold",
                )
            )
            adjustments.append(
                f"moved transaction {override.transaction_id} to unresolved (below_threshold)"
            )
            continue
        kept.append(override)
        if override.category not in known and override.category not in seen_new:
            new_categories.append(override.category)
            seen_new.add(override.category)
            adjustments.append(
                f"category {override.category!r} is new for transaction {override.transaction_id}"
            )

    normalized = EnrichmentRecommendation(
        proposed_overrides=kept,
        merchant_rule_suggestions=list(rec.merchant_rule_suggestions),
        unresolved=unresolved,
        narrative=rec.narrative,
        new_categories=new_categories,
    )
    return normalized, adjustments


def _collapse_duplicate_overrides(
    overrides: list[ProposedOverride], adjustments: list[str]
) -> list[ProposedOverride]:
    winners: list[ProposedOverride] = []
    chosen: dict[int, int] = {}
    for override in overrides:
        index = chosen.get(override.transaction_id)
        if index is None:
            chosen[override.transaction_id] = len(winners)
            winners.append(override)
            continue
        current = winners[index]
        if override.confidence > current.confidence:
            winners[index] = override
            kept = override.confidence
        else:
            kept = current.confidence
        adjustments.append(
            f"dropped duplicate override for transaction {override.transaction_id} "
            f"(kept confidence {kept})"
        )
    return winners


def _evidence_belongs_to_transaction(
    db: Session, transaction_id: int, evidence_ids: list[int]
) -> bool:
    for evidence_id in evidence_ids:
        row = db.get(TransactionEvidence, evidence_id)
        if row is None or row.transaction_id != transaction_id:
            return False
    return True


@traceable(process_inputs=_enrichment_trace_inputs, process_outputs=_enrichment_trace_outputs)
def store_proposal(
    db: Session, task: str, rec: EnrichmentRecommendation
) -> EnrichmentProposal:
    row = EnrichmentProposal(
        task=task,
        recommendation=rec.model_dump(mode="json"),
        status=ProposalStatus.OPEN.value,
        consumed_plan_ref=None,
    )
    db.add(row)
    db.flush()
    return row


def load_proposal(db: Session, proposal_id: int) -> EnrichmentProposal | None:
    return db.get(EnrichmentProposal, proposal_id)


def proposal_to_ops(proposal: EnrichmentProposal) -> list[dict]:
    rec = EnrichmentRecommendation.model_validate(proposal.recommendation)
    ops: list[dict] = []
    for override in rec.proposed_overrides:
        ops.append(
            {
                "op": "set_transaction_category",
                "transaction_id": override.transaction_id,
                "category": override.category,
                "evidence_ids": list(override.evidence_ids),
                "rationale": override.rationale,
            }
        )
    for rule in rec.merchant_rule_suggestions:
        ops.append(rule.model_dump())
    return ops


def mark_consumed(db: Session, proposal_id: int, plan_ref: str) -> None:
    row = db.get(EnrichmentProposal, proposal_id)
    if row is None:
        return
    if row.status == ProposalStatus.CONSUMED.value:
        return
    row.status = ProposalStatus.CONSUMED.value
    row.consumed_plan_ref = plan_ref
