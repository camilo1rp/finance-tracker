"""Enricher tools. Observation-cache writes only; no effective-value mutations."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy import func, select

from app.agent.config import (
    email_provider,
    enrichment_confidence_threshold,
    get_enricher_deps,
    tool_session,
)
from app.agent.schemas import EnrichmentRecommendation
from app.domain.classification import clean_raw_value
from app.domain.receipts import MatchKind, ReceiptExtraction, dominant_line_item
from app.models import Transaction, TransactionEvidence, effective_category, effective_merchant
from app.services.enrichment_service import enrich_transaction
from app.services.proposal_service import store_proposal, validate_recommendation

_EMAIL_SOURCE_STATUS_DESCRIPTION = """\
Report whether the configured email source is reachable.

Performs no mailbox search. If EMAIL_PROVIDER is none, says so and does not \
construct a source.
"""

_FIND_RECEIPTS_DESCRIPTION = """\
Search the mailbox for receipt evidence for the given transactions and persist matches.

transaction_ids must contain 1–25 ids. Use only for transactions in the task scope. \
force=true re-fetches even when evidence already exists.

If the email source is unavailable, returns a single line and stops — it does not \
retry remaining ids. Writes transaction_evidence / merchant_senders only.
"""

_GET_EVIDENCE_DESCRIPTION = """\
Read persisted receipt evidence for transactions. Never contacts the mailbox.

transaction_ids must contain 1–50 ids. Returns evidence id, match_kind, confidence, \
dominant_category, dominant_category_raw, the dominant line item's product_type and \
category_hint, order id, order date, and line-item count. Never returns line-item \
descriptions.
"""

_LIST_UNMATCHED_DESCRIPTION = """\
List spend transactions in a date range that have no receipt evidence or only unmatched evidence.

Optionally filter by cleaned effective merchant. Returns id, date, amount, \
effective merchant, and effective category. Does not search email.
"""

_SUBMIT_RECOMMENDATION_DESCRIPTION = """\
Validate and store an enrichment recommendation as a proposal. Does not apply any category or mapping change.

The recommendation is validated against the database (transaction existence, evidence ownership, confidence threshold). Below-threshold overrides are moved to unresolved. The stored proposal is the only handoff to the steward.

Call this exactly once when the proposal is complete.
"""


def _dominant_line_item_fields(extraction: dict) -> tuple[str | None, str | None]:
    try:
        parsed = ReceiptExtraction.model_validate(extraction)
    except (TypeError, ValueError):
        return None, None
    dominant = dominant_line_item(parsed.line_items)
    if dominant is None:
        return None, None
    return dominant.product_type, dominant.category_hint


def _task_from_state(state: dict | None) -> str:
    for message in (state or {}).get("messages") or []:
        if isinstance(message, HumanMessage) or getattr(message, "type", None) == "human":
            content = getattr(message, "content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict) and block.get("text"):
                        parts.append(str(block["text"]))
                return "".join(parts)
            return str(content)
    return ""


def _config_for_call(deps, source, force: bool):
    allow_text_hint = getattr(source, "allowlist", []) == ["*"] or bool(
        getattr(deps.config, "allow_text_hint", False)
    )
    return replace(deps.config, force=force, allow_text_hint=allow_text_hint)


@tool(description=_EMAIL_SOURCE_STATUS_DESCRIPTION)
def email_source_status() -> str:
    deps = get_enricher_deps()
    source = deps.source_factory()
    if source is None:
        return f"EMAIL_PROVIDER is {email_provider()}"
    status = source.health()
    return (
        f"provider={status.provider} available={status.available} "
        f"account_hint={status.account_hint} detail={status.detail}"
    )


@tool(description=_FIND_RECEIPTS_DESCRIPTION)
def find_receipts(transaction_ids: list[int], force: bool = False) -> str:
    if not 1 <= len(transaction_ids) <= 25:
        return "transaction_ids must contain between 1 and 25 ids"
    deps = get_enricher_deps()
    source = deps.source_factory()
    if source is None:
        return f"email source unavailable: EMAIL_PROVIDER is {email_provider()}"
    extractor = deps.extractor_factory()
    config = _config_for_call(deps, source, force)
    lines: list[str] = []
    for txn_id in transaction_ids:
        with tool_session() as db:
            outcome = enrich_transaction(db, source, extractor, txn_id, config)
        if outcome.status == "source_unavailable":
            return "email source unavailable: EmailSourceUnavailable"
        best = outcome.best
        match_kind = best.match_kind.value if best is not None else "-"
        confidence = best.confidence if best is not None else "-"
        category = best.dominant_category if best is not None else "-"
        evidence = ",".join(str(item) for item in outcome.evidence_ids) or "-"
        lines.append(
            f"txn {outcome.transaction_id}: {outcome.status} match={match_kind} "
            f"confidence={confidence} category={category} evidence={evidence}"
        )
    return "\n".join(lines)


@tool(description=_GET_EVIDENCE_DESCRIPTION)
def get_evidence(transaction_ids: list[int]) -> str:
    if not 1 <= len(transaction_ids) <= 50:
        return "transaction_ids must contain between 1 and 50 ids"
    lines: list[str] = []
    with tool_session() as db:
        for txn_id in transaction_ids:
            rows = list(
                db.scalars(
                    select(TransactionEvidence)
                    .where(TransactionEvidence.transaction_id == txn_id)
                    .order_by(TransactionEvidence.id)
                ).all()
            )
            if not rows:
                lines.append(f"txn {txn_id}: no evidence")
                continue
            for row in rows:
                extraction = row.extraction if isinstance(row.extraction, dict) else {}
                line_items = extraction.get("line_items") or []
                product_type, category_hint = _dominant_line_item_fields(extraction)
                lines.append(
                    f"txn {txn_id} evidence={row.id} match={row.match_kind} "
                    f"confidence={row.confidence} category={row.dominant_category} "
                    f"category_raw={row.dominant_category_raw} "
                    f"product_type={product_type} "
                    f"category_hint={category_hint} "
                    f"order_id={extraction.get('order_id')} "
                    f"order_date={extraction.get('order_date')} "
                    f"line_item_count={len(line_items)}"
                )
    return "\n".join(lines)


@tool(description=_LIST_UNMATCHED_DESCRIPTION)
def list_unmatched(
    date_from: date,
    date_to: date,
    merchant: str | None = None,
    limit: int = 50,
) -> str:
    if limit < 1:
        return "limit must be >= 1"
    matched_ids = select(TransactionEvidence.transaction_id).where(
        TransactionEvidence.match_kind != MatchKind.UNMATCHED.value
    )
    with tool_session() as db:
        stmt = select(Transaction).where(
            Transaction.is_spend.is_(True),
            Transaction.transaction_date >= date_from,
            Transaction.transaction_date <= date_to,
            Transaction.id.not_in(matched_ids),
        )
        if merchant is not None and merchant.strip():
            stmt = stmt.where(
                func.lower(func.trim(effective_merchant)) == clean_raw_value(merchant)
            )
        rows = list(
            db.scalars(
                stmt.order_by(Transaction.transaction_date, Transaction.id).limit(limit)
            ).all()
        )
        if not rows:
            return "no unmatched spend transactions"
        lines: list[str] = []
        for row in rows:
            merchant_value = db.scalar(
                select(effective_merchant).where(Transaction.id == row.id)
            )
            category_value = db.scalar(
                select(effective_category).where(Transaction.id == row.id)
            )
            lines.append(
                f"id={row.id} date={row.transaction_date.isoformat()} "
                f"amount={row.amount} merchant={merchant_value} category={category_value}"
            )
        return "\n".join(lines)


@tool(description=_SUBMIT_RECOMMENDATION_DESCRIPTION, return_direct=True)
def submit_recommendation(
    recommendation: EnrichmentRecommendation,
    runtime: ToolRuntime,
) -> Command:
    parsed = (
        recommendation
        if isinstance(recommendation, EnrichmentRecommendation)
        else EnrichmentRecommendation.model_validate(recommendation)
    )
    with tool_session() as db:
        normalized, adjustments = validate_recommendation(
            db, parsed, enrichment_confidence_threshold()
        )
        task = _task_from_state(getattr(runtime, "state", None))
        proposal = store_proposal(db, task, normalized)
        db.commit()
        db.refresh(proposal)
        proposal_id = proposal.id
    counts = (
        f"proposal #{proposal_id}: {len(normalized.proposed_overrides)} overrides, "
        f"{len(normalized.merchant_rule_suggestions)} rule suggestions, "
        f"{len(normalized.unresolved)} unresolved"
    )
    if adjustments:
        counts = counts + "\n" + "\n".join(adjustments)
    return Command(
        update={
            "recommendation": normalized.model_dump(mode="json"),
            "proposal_id": proposal_id,
            "messages": [
                ToolMessage(
                    content=counts,
                    tool_call_id=runtime.tool_call_id or "",
                )
            ],
        }
    )


ENRICHER_TOOLS = [
    email_source_status,
    find_receipts,
    get_evidence,
    list_unmatched,
    submit_recommendation,
]
