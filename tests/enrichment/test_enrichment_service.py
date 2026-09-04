from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.email_source import EmailMessage, EmailRef, SourceStatus
from app.domain.receipts import ReceiptExtraction
from app.models import Account, MerchantSender, NormalizationMapping, Owner, Transaction, TransactionEvidence
from app.services.enrichment_service import (
    EnrichmentConfig,
    enrich_range,
    enrich_transaction,
    find_candidates,
    known_categories,
    learn_sender,
    sender_patterns_for,
)
from tests.fakes import FakeEmailSource, FakeExtractor


def _seed_account(db: Session) -> Account:
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
    db.commit()
    db.refresh(account)
    return account


def _txn(db: Session, account_id: int, txn_id: str, amount: str = "-25.00", merchant: str = "Amazon") -> Transaction:
    row = Transaction(
        account_id=account_id,
        transaction_date=date(2024, 6, 2),
        description=f"txn-{txn_id}",
        amount=Decimal(amount),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        category_normalized=None,
        category_override=None,
        merchant_raw=merchant,
        merchant_normalized=merchant,
        merchant_override=None,
        dedupe_hash=f"hash-{txn_id}",
        raw={"Merchant": merchant},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _message(message_id: str, sender: str, body: str = "Order #A1\nTotal $25.00\n2024-06-02") -> EmailMessage:
    ref = EmailRef(
        message_id=message_id,
        thread_id=None,
        sender=sender,
        subject="Receipt",
        received_at=datetime(2024, 6, 2, 12, 0, 0),
        snippet="snippet",
    )
    return EmailMessage(
        ref=ref,
        body_text=body,
        headers={"from": sender, "subject": "Receipt", "message-id": message_id},
        attachments=[],
        truncated=False,
    )


def test_enrich_transaction_writes_evidence_and_rerun_is_noop(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "1")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource([_message("m1", "orders@amazon.com"), _message("m2", "billing@amazon.com")], ["amazon.com"])
    extractor = FakeExtractor(
        {
            "m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.7),
            "m2": ReceiptExtraction(total=Decimal("20.00"), extractor_version="fake", raw_confidence=0.6),
        }
    )
    outcome = enrich_transaction(db_session, source, extractor, txn.id, EnrichmentConfig())
    assert outcome.status == "enriched"
    assert outcome.best is not None
    assert db_session.scalar(select(func.count()).select_from(TransactionEvidence)) == 2

    rerun = enrich_transaction(db_session, source, extractor, txn.id, EnrichmentConfig())
    assert rerun.status == "already_enriched"
    assert db_session.scalar(select(func.count()).select_from(TransactionEvidence)) == 2


def test_force_reruns_without_duplicates(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "2")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource([_message("m1", "orders@amazon.com")], ["amazon.com"])
    extractor = FakeExtractor({"m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="v1", raw_confidence=0.7)})
    enrich_transaction(db_session, source, extractor, txn.id, EnrichmentConfig())
    extractor2 = FakeExtractor({"m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="v2", raw_confidence=0.8)})
    outcome = enrich_transaction(db_session, source, extractor2, txn.id, EnrichmentConfig(force=True))
    assert outcome.status == "enriched"
    assert db_session.scalar(select(func.count()).select_from(TransactionEvidence)) == 1
    stored = db_session.scalars(select(TransactionEvidence)).one()
    assert stored.extractor_version == "v2"


def test_evidence_provider_name_and_shared_gmail_external_ref(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "2b")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource(
        [_message("msg_test_01", "orders@amazon.com")],
        ["amazon.com"],
        provider_name="gmail_rest",
    )
    extractor = FakeExtractor(
        {"msg_test_01": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.7)}
    )
    outcome = enrich_transaction(db_session, source, extractor, txn.id, EnrichmentConfig())
    assert outcome.status == "enriched"
    stored = db_session.scalars(select(TransactionEvidence)).one()
    assert stored.provider == "gmail_rest"
    assert stored.provider == source.provider_name
    assert stored.external_ref == "gmail:msg_test_01"


def test_failure_rolls_back_to_zero_rows(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "3")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource([_message("m1", "orders@amazon.com")], ["amazon.com"])

    class BoomExtractor(FakeExtractor):
        def extract(self, message, known_categories):
            raise RuntimeError("boom")

    outcome = enrich_transaction(db_session, source, BoomExtractor({}, None), txn.id, EnrichmentConfig())
    assert outcome.status == "failed"
    assert outcome.error_class == "RuntimeError"
    assert db_session.scalar(select(func.count()).select_from(TransactionEvidence)) == 0


def test_source_unavailable_outcome(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "4")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource(
        [_message("m1", "orders@amazon.com")],
        ["amazon.com"],
        status=SourceStatus(available=False, provider="fake"),
        raise_unavailable=True,
    )
    outcome = enrich_transaction(db_session, source, FakeExtractor({}), txn.id, EnrichmentConfig())
    assert outcome.status == "source_unavailable"


def test_text_hint_and_learning_only_with_star_allowlist(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "5", merchant="Post Oak Grill")
    source = FakeEmailSource(
        [_message("m1", "orders@postoak.com", "post oak order total $25.00")], ["*"]
    )
    extractor = FakeExtractor({"m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.7)})
    outcome = enrich_transaction(
        db_session,
        source,
        extractor,
        txn.id,
        EnrichmentConfig(allow_text_hint=True),
    )
    assert outcome.status == "enriched"
    learned = db_session.scalars(select(MerchantSender)).all()
    assert len(learned) == 1
    assert learned[0].sender_pattern == "orders@postoak.com"
    assert learned[0].merchant_key == "post oak"


def test_hint_path_learned_key_equals_hint_phrase(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(
        db_session,
        account.id,
        "bestbuy",
        merchant="Best Buy 1234 Westheimer Rd Houston TX",
    )
    source = FakeEmailSource(
        [_message("m1", "orders@bestbuy.com", "best buy order total $25.00")], ["*"]
    )
    extractor = FakeExtractor(
        {"m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.7)}
    )
    outcome = enrich_transaction(
        db_session,
        source,
        extractor,
        txn.id,
        EnrichmentConfig(allow_text_hint=True),
    )
    assert outcome.status == "enriched"
    learned = db_session.scalars(select(MerchantSender)).all()
    assert len(learned) == 1
    assert learned[0].merchant_key == "best buy"
    assert learned[0].sender_pattern == "orders@bestbuy.com"
    assert learned[0].origin == "learned"


def test_hint_path_unmatched_does_not_learn_sender(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "unmatched-hint", merchant="Post Oak Grill")
    source = FakeEmailSource(
        [_message("m1", "newsletter@nasa.gov", "post oak order total $99.00")], ["*"]
    )
    extractor = FakeExtractor(
        {"m1": ReceiptExtraction(total=Decimal("99.00"), extractor_version="fake", raw_confidence=0.9)}
    )
    outcome = enrich_transaction(
        db_session,
        source,
        extractor,
        txn.id,
        EnrichmentConfig(allow_text_hint=True),
    )
    assert outcome.status == "unmatched"
    assert db_session.scalars(select(MerchantSender)).all() == []


def test_hint_path_date_only_does_not_learn_sender(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "date-only-hint", merchant="Post Oak Grill")
    source = FakeEmailSource(
        [_message("m1", "alerts@chase.com", "post oak something")], ["*"]
    )
    extractor = FakeExtractor(
        {
            "m1": ReceiptExtraction(
                order_date=date(2024, 6, 2), extractor_version="fake", raw_confidence=0.9
            )
        }
    )
    outcome = enrich_transaction(
        db_session,
        source,
        extractor,
        txn.id,
        EnrichmentConfig(allow_text_hint=True),
    )
    assert outcome.status == "enriched"
    assert outcome.best is not None
    assert outcome.best.match_kind.value == "date_only"
    assert db_session.scalars(select(MerchantSender)).all() == []


def test_known_categories_merges_canonicals_and_overrides(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "6")
    db_session.add(
        NormalizationMapping(
            kind="category",
            raw_value="shopping",
            canonical_value="Shopping",
            account_id=None,
            merchant=None,
        )
    )
    txn.category_override = "Special"
    db_session.commit()
    assert known_categories(db_session) == ["Shopping", "Special"]


def test_learn_sender_idempotent(db_session: Session) -> None:
    first = learn_sender(db_session, "amazon", "orders@amazon.com")
    second = learn_sender(db_session, "amazon", "orders@amazon.com")
    assert first is not None
    assert second is None


def test_enrich_range_continues_past_failure(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn1 = _txn(db_session, account.id, "7")
    txn2 = _txn(db_session, account.id, "8", merchant="Apple")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.add(MerchantSender(merchant_key="apple", sender_pattern="apple.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource(
        [_message("m1", "orders@amazon.com"), _message("m2", "orders@apple.com")],
        ["amazon.com", "apple.com"],
    )

    class SometimesBoom(FakeExtractor):
        def extract(self, message, known_categories):
            if message.ref.message_id == "m2":
                raise RuntimeError("boom")
            return super().extract(message, known_categories)

    extractor = SometimesBoom(
        {"m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.7)}
    )
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    report = enrich_range(
        factory,
        source,
        extractor,
        date(2024, 6, 1),
        date(2024, 6, 3),
        EnrichmentConfig(),
    )
    assert report.scanned == 2
    assert report.enriched == 1
    assert report.failed == 1


def test_enrich_range_restricts_to_txn_ids(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn1 = _txn(db_session, account.id, "ids-a")
    txn2 = _txn(db_session, account.id, "ids-b", merchant="Apple")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.add(MerchantSender(merchant_key="apple", sender_pattern="apple.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource(
        [_message("m1", "orders@amazon.com"), _message("m2", "orders@apple.com")],
        ["amazon.com", "apple.com"],
    )
    extractor = FakeExtractor(
        {
            "m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.7),
            "m2": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.7),
        }
    )
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    report = enrich_range(
        factory,
        source,
        extractor,
        date(2024, 6, 1),
        date(2024, 6, 3),
        EnrichmentConfig(),
        txn_ids=[txn1.id],
    )
    assert report.scanned == 1
    assert report.outcomes[0].transaction_id == txn1.id
    assert txn2.id not in {outcome.transaction_id for outcome in report.outcomes}


def test_sender_patterns_for_exact_match(db_session: Session) -> None:
    db_session.add(MerchantSender(merchant_key="dollar tree", sender_pattern="dollartree.com", origin="seed"))
    db_session.commit()
    assert sender_patterns_for(db_session, "dollar tree") == ["dollartree.com"]


def test_sender_patterns_for_prefix_substring(db_session: Session) -> None:
    db_session.add(MerchantSender(merchant_key="dollar tree", sender_pattern="dollartree.com", origin="seed"))
    db_session.commit()
    assert sender_patterns_for(
        db_session, "dollar tree 9523 westheimer rd houston tx"
    ) == ["dollartree.com"]


def test_sender_patterns_for_token_boundary(db_session: Session) -> None:
    db_session.add(MerchantSender(merchant_key="tree", sender_pattern="tree.com", origin="seed"))
    db_session.commit()
    assert sender_patterns_for(db_session, "treehouse") == []
    assert sender_patterns_for(db_session, "the tree store") == ["tree.com"]


def test_sender_patterns_for_longest_wins(db_session: Session) -> None:
    db_session.add(MerchantSender(merchant_key="dollar", sender_pattern="dollar.com", origin="seed"))
    db_session.add(MerchantSender(merchant_key="dollar tree", sender_pattern="dollartree.com", origin="seed"))
    db_session.commit()
    assert sender_patterns_for(
        db_session, "dollar tree 9523 westheimer rd houston tx"
    ) == ["dollartree.com"]


def test_sender_patterns_for_no_match(db_session: Session) -> None:
    db_session.add(MerchantSender(merchant_key="dollar tree", sender_pattern="dollartree.com", origin="seed"))
    db_session.commit()
    assert sender_patterns_for(db_session, "home depot") == []


def test_find_candidates_resolves_dollar_tree_from_raw_payee(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(
        db_session,
        account.id,
        "dt",
        merchant="DOLLAR TREE 9523 WESTHEIMER RD HOUSTON TX",
    )
    db_session.add(MerchantSender(merchant_key="dollar tree", sender_pattern="dollartree.com", origin="seed"))
    db_session.commit()
    source = FakeEmailSource(
        [_message("m1", "receipts@dollartree.com")],
        ["dollartree.com"],
    )
    resolution, refs = find_candidates(db_session, source, txn, 2, 7, False)
    assert resolution == "tolerant:dollar tree"
    assert [ref.message_id for ref in refs] == ["m1"]


def test_sender_patterns_for_amazon_star_payee(db_session: Session) -> None:
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    assert sender_patterns_for(db_session, "amazon.com*568eb8rd0") == ["amazon.com"]
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "amz-star", merchant="AMAZON.COM*568EB8RD0")
    source = FakeEmailSource([_message("m1", "orders@amazon.com")], ["amazon.com"])
    resolution, refs = find_candidates(db_session, source, txn, 10, 5, False)
    assert resolution == "tolerant:amazon"
    assert [ref.message_id for ref in refs] == ["m1"]


def test_sender_patterns_for_union_does_not_shadow_seed(db_session: Session) -> None:
    db_session.add(
        MerchantSender(
            merchant_key="amazon.com*568eb8rd0",
            sender_pattern="nasa.gov",
            origin="learned",
        )
    )
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    assert sender_patterns_for(db_session, "amazon.com*568eb8rd0") == ["nasa.gov", "amazon.com"]


def test_find_candidates_skips_hint_path_with_one_token(db_session: Session) -> None:
    account = _seed_account(db_session)
    txn = _txn(db_session, account.id, "one-hint", merchant="Amazon")
    source = FakeEmailSource(
        [_message("m1", "friend@gmail.com", "amazon is having a sale")],
        ["*"],
    )
    resolution, refs = find_candidates(db_session, source, txn, 2, 7, True)
    assert resolution == "none"
    assert refs == []
    assert source.search_calls == 0
