from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from time import monotonic

from langsmith import traceable
from sqlalchemy import delete, select
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
    count_currency_like_tokens,
    count_date_like_tokens,
    dominant_line_item,
    match_receipt,
    merchant_hint_tokens,
    merchant_tokens,
    receipt_extraction_dump,
)
from app.models import Account, MerchantSender, NormalizationMapping, Transaction, TransactionEvidence
from app.services.ingest_service import _backfill_merchant_raw
from app.agent.config import email_max_results_per_search, enrichment_confidence_threshold


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
            "body_source": value.body_source,
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
    lookback_days: int = 10
    lookahead_days: int = 5
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


@dataclass(frozen=True)
class CandidateInspection:
    external_ref: str
    body_source: str
    body_bytes: int
    truncated: bool
    currency_token_count: int
    date_token_count: int
    total: Decimal | None
    order_date: date | None
    order_id: str | None
    raw_confidence: float
    plain_bytes: int = 0
    html_text_bytes: int = 0
    product_type: str | None = None
    category_hint: str | None = None


@dataclass(frozen=True)
class InspectReport:
    transaction_id: int
    found: bool
    resolution: str | None = None
    candidates: list[CandidateInspection] = field(default_factory=list)


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


_LEARNABLE_MATCH_KINDS = frozenset({MatchKind.EXACT_TOTAL, MatchKind.SPLIT_PARTIAL})


def _sender_patterns_for_exact(db: Session, merchant_key: str) -> list[str]:
    stmt = (
        select(MerchantSender.sender_pattern)
        .where(MerchantSender.merchant_key == merchant_key)
        .order_by(MerchantSender.id)
    )
    return list(db.scalars(stmt).all())


def _is_token_boundary_substring(needle: str, haystack: str) -> bool:
    needle_tokens = merchant_tokens(needle)
    hay_tokens = merchant_tokens(haystack)
    width = len(needle_tokens)
    if width == 0:
        return False
    return any(
        hay_tokens[index : index + width] == needle_tokens
        for index in range(len(hay_tokens) - width + 1)
    )


def _longest_token_boundary_key(merchant_key: str, keys: list[str]) -> str | None:
    matches = [
        key
        for key in keys
        if key != merchant_key and _is_token_boundary_substring(key, merchant_key)
    ]
    if not matches:
        return None
    return max(matches, key=len)


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _resolve_sender_patterns(db: Session, merchant_key: str) -> tuple[str, list[str]]:
    exact = _sender_patterns_for_exact(db, merchant_key)
    keys = list(db.scalars(select(MerchantSender.merchant_key).distinct()).all())
    best = _longest_token_boundary_key(merchant_key, keys)
    tolerant = _sender_patterns_for_exact(db, best) if best is not None else []
    patterns = _dedupe_preserve(exact + tolerant)
    if exact:
        return "exact", patterns
    if best is not None:
        return f"tolerant:{best}", patterns
    return "none", []


def sender_patterns_for(db: Session, merchant_key: str) -> list[str]:
    _resolution, patterns = _resolve_sender_patterns(db, merchant_key)
    return patterns


def _merchant_key_for_transaction(db: Session, txn: Transaction) -> str | None:
    account = db.get(Account, txn.account_id)
    merchant_raw = txn.merchant_raw or _backfill_merchant_raw(txn, account)
    merchant = resolved_merchant(merchant_raw, txn.merchant_normalized, txn.merchant_override)
    if merchant is None or not merchant.strip():
        return None
    return clean_raw_value(merchant)


def plan_candidate_search(
    db: Session,
    txn: Transaction,
    lookback_days: int,
    lookahead_days: int,
    allow_text_hint: bool,
) -> tuple[str, EmailQuery | None]:
    merchant_key = _merchant_key_for_transaction(db, txn)
    senders: list[str] = []
    text_hints: list[str] = []
    resolution = "none"
    if merchant_key:
        resolution, senders = _resolve_sender_patterns(db, merchant_key)
        if resolution == "none" and allow_text_hint:
            hints = merchant_hint_tokens(merchant_key)
            if len(hints) >= 2:
                text_hints = hints
                senders = ["*"]
                resolution = f"hint:{' '.join(hints)}"
    if resolution == "none":
        return resolution, None
    return resolution, EmailQuery(
        senders=senders,
        date_from=txn.transaction_date - timedelta(days=lookback_days),
        date_to=txn.transaction_date + timedelta(days=lookahead_days),
        text_hints=text_hints,
        max_results=email_max_results_per_search(),
    )


@traceable(process_inputs=_enrichment_trace_inputs, process_outputs=_enrichment_trace_outputs)
def find_candidates(
    db: Session,
    source: EmailSource,
    txn: Transaction,
    lookback_days: int,
    lookahead_days: int,
    allow_text_hint: bool,
) -> tuple[str, list[EmailRef]]:
    resolution, query = plan_candidate_search(
        db, txn, lookback_days, lookahead_days, allow_text_hint
    )
    if query is None:
        return resolution, []
    return resolution, source.search(query)


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


_GMAIL_EXTERNAL_REF_PREFIXES = frozenset({"gmail", "gmail_rest"})


def _source_provider(source: EmailSource) -> str:
    return source.provider_name


def _evidence_external_ref(source: EmailSource, message: EmailMessage) -> str:
    name = _source_provider(source)
    prefix = "gmail" if name in _GMAIL_EXTERNAL_REF_PREFIXES else name
    return f"{prefix}:{message.ref.message_id}"


def inspect_transaction(
    db: Session,
    source: EmailSource,
    extractor: ReceiptExtractor,
    txn_id: int,
    config: EnrichmentConfig,
) -> InspectReport:
    txn = db.get(Transaction, txn_id)
    if txn is None:
        return InspectReport(transaction_id=txn_id, found=False)
    resolution, refs = find_candidates(
        db,
        source,
        txn,
        config.lookback_days,
        config.lookahead_days,
        config.allow_text_hint,
    )
    refs = refs[: config.max_candidates]
    categories = known_categories(db)
    candidates: list[CandidateInspection] = []
    for ref in refs:
        message = source.fetch(ref)
        extraction = extractor.extract(message, categories)
        dominant = dominant_line_item(extraction.line_items)
        candidates.append(
            CandidateInspection(
                external_ref=_evidence_external_ref(source, message),
                body_source=message.body_source,
                body_bytes=len(message.body_text.encode("utf-8")),
                truncated=message.truncated,
                currency_token_count=count_currency_like_tokens(message.body_text),
                date_token_count=count_date_like_tokens(message.body_text),
                total=extraction.total,
                order_date=extraction.order_date,
                order_id=extraction.order_id,
                raw_confidence=extraction.raw_confidence,
                plain_bytes=message.plain_bytes,
                html_text_bytes=message.html_text_bytes,
                product_type=dominant.product_type if dominant is not None else None,
                category_hint=dominant.category_hint if dominant is not None else None,
            )
        )
    return InspectReport(
        transaction_id=txn_id,
        found=True,
        resolution=resolution,
        candidates=candidates,
    )


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


def _should_learn_sender(best: EvidenceMatch | None, via_hint: bool) -> bool:
    if not via_hint or best is None:
        return False
    if best.match_kind not in _LEARNABLE_MATCH_KINDS:
        return False
    return best.confidence >= enrichment_confidence_threshold()


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
        resolution, refs = find_candidates(
            db,
            source,
            txn,
            config.lookback_days,
            config.lookahead_days,
            config.allow_text_hint,
        )
        refs = refs[: config.max_candidates]
        used_text_hint = resolution.startswith("hint:")
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
        if _should_learn_sender(best, best_via_text_hint) and merchant_key is not None:
            assert best is not None
            best_ref = next((ref for ref in refs if ref.message_id == best.message_id), None)
            if best_ref is not None:
                learn_key = (
                    resolution.removeprefix("hint:") if used_text_hint else merchant_key
                )
                learn_sender(db, learn_key, best_ref.sender)
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
    date_from: date | None,
    date_to: date | None,
    config: EnrichmentConfig,
    txn_ids: list[int] | None = None,
) -> EnrichmentReport:
    started = monotonic()
    with session_factory() as db:
        stmt = select(Transaction.id).where(Transaction.is_spend.is_(True))
        if date_from is not None:
            stmt = stmt.where(Transaction.transaction_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Transaction.transaction_date <= date_to)
        if txn_ids is not None:
            stmt = stmt.where(Transaction.id.in_(txn_ids))
        if not config.force:
            matched_txn_ids = select(TransactionEvidence.transaction_id).where(
                TransactionEvidence.match_kind != MatchKind.UNMATCHED.value
            )
            stmt = stmt.where(Transaction.id.not_in(matched_txn_ids))
        selected_ids = list(db.scalars(stmt.order_by(Transaction.transaction_date, Transaction.id)).all())
    outcomes: list[EnrichmentOutcome] = []
    counts = {"enriched": 0, "unmatched": 0, "already_enriched": 0, "not_found": 0, "source_unavailable": 0, "failed": 0}
    for txn_id in selected_ids:
        with session_factory() as db:
            outcome = enrich_transaction(db, source, extractor, txn_id, config)
        outcomes.append(outcome)
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    return EnrichmentReport(
        scanned=len(selected_ids),
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


def reset_learned_senders(db: Session) -> int:
    result = db.execute(
        delete(MerchantSender).where(MerchantSender.origin == SenderOrigin.LEARNED.value)
    )
    return int(result.rowcount or 0)
