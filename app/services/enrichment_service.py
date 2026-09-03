from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from time import monotonic

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.classification import clean_raw_value
from app.domain.email_source import EmailMessage, EmailQuery, EmailRef, EmailSource, EmailSourceUnavailable
from app.domain.merchant import resolved_merchant
from app.domain.receipts import (
    EvidenceKind,
    EvidenceMatch,
    MatchKind,
    ReceiptExtraction,
    ReceiptExtractor,
    SEED_SENDERS,
    SenderOrigin,
    match_receipt,
    receipt_extraction_dump,
)
from app.models import Account, MerchantSender, NormalizationMapping, Transaction, TransactionEvidence
from app.services.ingest_service import _backfill_merchant_raw
from app.agent.config import email_max_results_per_search


def _enrichment_trace_inputs(inputs: dict) -> dict:
    return {key: _redact_enrichment_value(value) for key, value in inputs.items() if key not in {"db", "source", "extractor"}}


def _enrichment_trace_outputs(outputs):
    return _redact_enrichment_value(outputs)


def _redact_enrichment_value(value):
    if isinstance(value, EmailQuery):
        return {
            "senders": list(value.senders),
            "date_from": value.date_from.isoformat(),
            "date_to": value.date_to.isoformat(),
            "text_hints": ["<redacted>"] * len(value.text_hints),
            "max_results": value.max_results,
        }
    if isinstance(value, EmailMessage):
        return {
            "ref": _redact_enrichment_value(value.ref),
            "body_text": "<redacted>",
            "headers": "<redacted>",
            "attachments": _redact_enrichment_value(value.attachments),
            "truncated": value.truncated,
        }
    if isinstance(value, EmailRef):
        data = asdict(value)
        data["snippet"] = "<redacted>"
        return data
    if isinstance(value, ReceiptExtraction):
        data = receipt_extraction_dump(value)
        for item in data.get("line_items", []):
            item["description"] = "<redacted>"
        return data
    if isinstance(value, list):
        return [_redact_enrichment_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_enrichment_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_enrichment_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class EnrichmentConfig:
    lookback_days: int = 2
    lookahead_days: int = 7
    max_candidates: int = 5
    force: bool = False
    allow_text_hint: bool = False


@dataclass(frozen=True)
class EnrichmentOutcome:
    transaction_id: int
    status: str
    best: EvidenceMatch | None = None
    evidence_ids: list[int] = field(default_factory=list)
    error_class: str | None = None


@dataclass(frozen=True)
class EnrichmentReport:
    scanned: int
    enriched: int
    unmatched: int
    skipped: int
    failed: int
    duration_s: float
    outcomes: list[EnrichmentOutcome] = field(default_factory=list)


def known_categories(db: Session) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for category in db.scalars(
        select(NormalizationMapping.canonical_value)
        .where(NormalizationMapping.kind == "category")
        .distinct()
    ):
        if category not in seen:
            values.append(category)
            seen.add(category)
    for category in db.scalars(
        select(Transaction.category_override)
        .where(Transaction.category_override.is_not(None))
        .distinct()
    ):
        if category is not None and category not in seen:
            values.append(category)
            seen.add(category)
    return values


def sender_patterns_for(db: Session, merchant_key: str) -> list[str]:
    stmt = (
        select(MerchantSender.sender_pattern)
        .where(MerchantSender.merchant_key == merchant_key)
        .order_by(MerchantSender.id)
    )
    return list(db.scalars(stmt).all())


def _merchant_key_for_transaction(db: Session, txn: Transaction) -> str | None:
    account = db.get(Account, txn.account_id)
    merchant_raw = txn.merchant_raw or _backfill_merchant_raw(txn, account)
    merchant = resolved_merchant(merchant_raw, txn.merchant_normalized, txn.merchant_override)
    if merchant is None or not merchant.strip():
        return None
    return clean_raw_value(merchant)


@traceable(process_inputs=_enrichment_trace_inputs, process_outputs=_enrichment_trace_outputs)
def find_candidates(
    db: Session,
    source: EmailSource,
    txn: Transaction,
    lookback_days: int,
    lookahead_days: int,
    allow_text_hint: bool,
) -> list[EmailRef]:
    merchant_key = _merchant_key_for_transaction(db, txn)
    senders = sender_patterns_for(db, merchant_key) if merchant_key else []
    text_hints: list[str] = []
    if not senders and allow_text_hint and merchant_key:
        text_hints = [part for part in merchant_key.split() if part]
        senders = ["*"]
    query = EmailQuery(
        senders=senders,
        date_from=txn.transaction_date - timedelta(days=lookback_days),
        date_to=txn.transaction_date + timedelta(days=lookahead_days),
        text_hints=text_hints,
        max_results=email_max_results_per_search(),
    )
    return source.search(query)


def sibling_transactions(
    db: Session, txn: Transaction, window_days: int = 5, limit: int = 4
) -> list[tuple[int, Decimal, date]]:
    merchant_key = _merchant_key_for_transaction(db, txn)
    if merchant_key is None:
        return []
    rows = list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.account_id == txn.account_id,
                Transaction.id != txn.id,
                Transaction.transaction_date >= txn.transaction_date - timedelta(days=window_days),
                Transaction.transaction_date <= txn.transaction_date + timedelta(days=window_days),
            )
            .order_by(Transaction.transaction_date, Transaction.id)
        ).all()
    )
    matches: list[tuple[int, Decimal, date]] = []
    for sibling in rows:
        if _merchant_key_for_transaction(db, sibling) != merchant_key:
            continue
        matches.append((sibling.id, sibling.amount, sibling.transaction_date))
        if len(matches) >= limit:
            break
    return matches


def _match_rank(kind: MatchKind) -> int:
    return {
        MatchKind.EXACT_TOTAL: 3,
        MatchKind.SPLIT_PARTIAL: 2,
        MatchKind.DATE_ONLY: 1,
        MatchKind.UNMATCHED: 0,
    }[kind]


def _best_match(current: EvidenceMatch | None, candidate: EvidenceMatch) -> EvidenceMatch | None:
    if current is None:
        return candidate
    current_key = (_match_rank(current.match_kind), current.confidence)
    candidate_key = (_match_rank(candidate.match_kind), candidate.confidence)
    return candidate if candidate_key > current_key else current


def _source_provider(source: EmailSource) -> str:
    return getattr(source, "provider", None) or source.health().provider


def _evidence_external_ref(source: EmailSource, message: EmailMessage) -> str:
    return f"{_source_provider(source)}:{message.ref.message_id}"


def _upsert_evidence(
    db: Session,
    txn_id: int,
    source: EmailSource,
    message: EmailMessage,
    extraction: ReceiptExtraction,
    match: EvidenceMatch,
) -> TransactionEvidence:
    external_ref = _evidence_external_ref(source, message)
    row = db.execute(
        select(TransactionEvidence).where(
            TransactionEvidence.transaction_id == txn_id,
            TransactionEvidence.kind == EvidenceKind.EMAIL_RECEIPT.value,
            TransactionEvidence.external_ref == external_ref,
        )
    ).scalar_one_or_none()
    payload = receipt_extraction_dump(extraction)
    if row is None:
        row = TransactionEvidence(
            transaction_id=txn_id,
            kind=EvidenceKind.EMAIL_RECEIPT.value,
            provider=_source_provider(source),
            external_ref=external_ref,
            extraction=payload,
            match_kind=match.match_kind.value,
            confidence=match.confidence,
            dominant_category=match.dominant_category,
            dominant_category_raw=match.dominant_category_raw,
            extractor_version=extraction.extractor_version,
        )
        db.add(row)
    else:
        row.provider = _source_provider(source)
        row.extraction = payload
        row.match_kind = match.match_kind.value
        row.confidence = match.confidence
        row.dominant_category = match.dominant_category
        row.dominant_category_raw = match.dominant_category_raw
        row.extractor_version = extraction.extractor_version
    db.flush()
    return row


def learn_sender(db: Session, merchant_key: str, sender_address: str) -> MerchantSender | None:
    sender = sender_address.strip().lower()
    existing = db.execute(
        select(MerchantSender).where(
            MerchantSender.merchant_key == merchant_key,
            MerchantSender.sender_pattern == sender,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None
    row = MerchantSender(
        merchant_key=merchant_key,
        sender_pattern=sender,
        origin=SenderOrigin.LEARNED.value,
    )
    db.add(row)
    db.flush()
    return row


@traceable(process_inputs=_enrichment_trace_inputs, process_outputs=_enrichment_trace_outputs)
def enrich_transaction(
    db: Session,
    source: EmailSource,
    extractor: ReceiptExtractor,
    txn_id: int,
    config: EnrichmentConfig,
) -> EnrichmentOutcome:
    txn = db.get(Transaction, txn_id)
    if txn is None:
        return EnrichmentOutcome(transaction_id=txn_id, status="not_found")
    existing = db.scalars(
        select(TransactionEvidence).where(
            TransactionEvidence.transaction_id == txn_id,
            TransactionEvidence.match_kind != MatchKind.UNMATCHED.value,
        )
    ).first()
    if existing is not None and not config.force:
        return EnrichmentOutcome(transaction_id=txn_id, status="already_enriched")
    try:
        merchant_key = _merchant_key_for_transaction(db, txn)
        used_text_hint = (
            config.allow_text_hint
            and merchant_key is not None
            and not sender_patterns_for(db, merchant_key)
        )
        refs = find_candidates(
            db,
            source,
            txn,
            config.lookback_days,
            config.lookahead_days,
            config.allow_text_hint,
        )[: config.max_candidates]
        categories = known_categories(db)
        siblings = sibling_transactions(db, txn)
        evidence_ids: list[int] = []
        best: EvidenceMatch | None = None
        best_via_text_hint = False
        for ref in refs:
            message = source.fetch(ref)
            extraction = extractor.extract(message, categories)
            match = match_receipt(
                txn.amount,
                txn.transaction_date,
                extraction,
                siblings,
                transaction_id=txn.id,
                message_id=message.ref.message_id,
                known_categories=categories,
            )
            row = _upsert_evidence(db, txn.id, source, message, extraction, match)
            evidence_ids.append(row.id)
            next_best = _best_match(best, match)
            if next_best is match:
                best = match
                best_via_text_hint = used_text_hint
        if best is not None and best_via_text_hint:
            if merchant_key is not None:
                best_ref = next((ref for ref in refs if ref.message_id == best.message_id), None)
                if best_ref is not None:
                    learn_sender(db, merchant_key, best_ref.sender)
        db.commit()
        status = "enriched" if best is not None and best.match_kind != MatchKind.UNMATCHED else "unmatched"
        return EnrichmentOutcome(
            transaction_id=txn.id,
            status=status,
            best=best,
            evidence_ids=evidence_ids,
        )
    except EmailSourceUnavailable:
        db.rollback()
        return EnrichmentOutcome(
            transaction_id=txn.id,
            status="source_unavailable",
            error_class="EmailSourceUnavailable",
        )
    except Exception as exc:
        db.rollback()
        return EnrichmentOutcome(
            transaction_id=txn.id,
            status="failed",
            error_class=exc.__class__.__name__,
        )


def enrich_range(
    session_factory: sessionmaker[Session],
    source: EmailSource,
    extractor: ReceiptExtractor,
    date_from: date,
    date_to: date,
    config: EnrichmentConfig,
) -> EnrichmentReport:
    started = monotonic()
    with session_factory() as db:
        stmt = select(Transaction.id).where(
            Transaction.is_spend.is_(True),
            Transaction.transaction_date >= date_from,
            Transaction.transaction_date <= date_to,
        )
        if not config.force:
            matched_txn_ids = select(TransactionEvidence.transaction_id).where(
                TransactionEvidence.match_kind != MatchKind.UNMATCHED.value
            )
            stmt = stmt.where(Transaction.id.not_in(matched_txn_ids))
        txn_ids = list(db.scalars(stmt.order_by(Transaction.transaction_date, Transaction.id)).all())
    outcomes: list[EnrichmentOutcome] = []
    counts = {"enriched": 0, "unmatched": 0, "already_enriched": 0, "not_found": 0, "source_unavailable": 0, "failed": 0}
    for txn_id in txn_ids:
        with session_factory() as db:
            outcome = enrich_transaction(db, source, extractor, txn_id, config)
        outcomes.append(outcome)
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    return EnrichmentReport(
        scanned=len(txn_ids),
        enriched=counts["enriched"],
        unmatched=counts["unmatched"],
        skipped=counts["already_enriched"] + counts["not_found"],
        failed=counts["failed"] + counts["source_unavailable"],
        duration_s=round(monotonic() - started, 3),
        outcomes=outcomes,
    )


def seed_merchant_senders(db: Session) -> int:
    inserted = 0
    for merchant_key, patterns in SEED_SENDERS.items():
        for pattern in patterns:
            existing = db.execute(
                select(MerchantSender).where(
                    MerchantSender.merchant_key == merchant_key,
                    MerchantSender.sender_pattern == pattern,
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            db.add(
                MerchantSender(
                    merchant_key=merchant_key,
                    sender_pattern=pattern,
                    origin=SenderOrigin.SEED.value,
                )
            )
            inserted += 1
    return inserted
