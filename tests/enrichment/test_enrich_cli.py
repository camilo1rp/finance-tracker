from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.cli import main
from app.agent.config import set_session_factory
from app.domain.email_source import EmailMessage, EmailRef
from app.domain.receipts import SEED_SENDERS, LineItem, ReceiptExtraction
from app.integrations.gmail_common.query import RECEIPT_SHAPE_CLAUSE
from app.models import Account, MerchantSender, Owner, Transaction, TransactionEvidence


def _seed(db: Session) -> None:
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
    db.add(
        Transaction(
            account_id=account.id,
            transaction_date=datetime(2024, 6, 2).date(),
            description="txn",
            amount=Decimal("-25.00"),
            transaction_type="SPEND",
            is_spend=True,
            category_raw="Shopping",
            merchant_raw="Amazon",
            merchant_normalized="Amazon",
            dedupe_hash="hash-cli",
            raw={"Merchant": "Amazon"},
        )
    )
    db.commit()


def _add_spend(db: Session, account_id: int, suffix: str, merchant: str) -> Transaction:
    row = Transaction(
        account_id=account_id,
        transaction_date=datetime(2024, 6, 2).date(),
        description=f"txn-{suffix}",
        amount=Decimal("-25.00"),
        transaction_type="SPEND",
        is_spend=True,
        category_raw="Shopping",
        merchant_raw=merchant,
        merchant_normalized=merchant,
        dedupe_hash=f"hash-cli-{suffix}",
        raw={"Merchant": merchant},
    )
    db.add(row)
    db.flush()
    return row


def test_cli_dry_run_writes_nothing(db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _seed(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)

    message = EmailMessage(
        ref=EmailRef(
            message_id="m1",
            thread_id=None,
            sender="orders@amazon.com",
            subject="Receipt",
            received_at=datetime(2024, 6, 2, 12, 0, 0),
            snippet="snippet",
        ),
        body_text="amazon total $25.00",
        headers={"from": "orders@amazon.com", "subject": "Receipt"},
        attachments=[],
        truncated=False,
    )

    monkeypatch.setenv("EMAIL_PROVIDER", "fake")
    monkeypatch.setenv("EMAIL_SENDER_ALLOWLIST", "*")
    monkeypatch.setenv("EMAIL_FAKE_FIXTURE", "/tmp/unused")
    monkeypatch.setattr(
        "app.agent.cli.email_source_from_env",
        lambda: __import__("tests.fakes", fromlist=["FakeEmailSource"]).FakeEmailSource([message], ["*"]),
    )
    monkeypatch.setattr(
        "app.agent.cli.extractor_from_env",
        lambda: __import__("tests.fakes", fromlist=["FakeExtractor"]).FakeExtractor(
            {"m1": ReceiptExtraction(total=Decimal("25.00"), extractor_version="fake", raw_confidence=0.5)}
        ),
    )

    before_senders = db_session.scalar(select(func.count()).select_from(MerchantSender))
    before_evidence = db_session.scalar(select(func.count()).select_from(TransactionEvidence))
    with pytest.raises(SystemExit) as exc:
        main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03", "--dry-run"])
    assert exc.value.code == 0
    assert "transaction_id=" in capsys.readouterr().out
    with factory() as verify:
        after_senders = verify.scalar(select(func.count()).select_from(MerchantSender))
        after_evidence = verify.scalar(select(func.count()).select_from(TransactionEvidence))
    expected_seed = sum(len(patterns) for patterns in SEED_SENDERS.values())
    assert after_senders == before_senders + expected_seed
    assert after_evidence == before_evidence


def test_cli_dry_run_prints_each_resolution_kind(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _seed(db_session)
    account_id = db_session.scalars(select(Account.id)).one()
    _add_spend(
        db_session,
        account_id,
        "tolerant",
        "DOLLAR TREE 9523 WESTHEIMER RD HOUSTON TX",
    )
    _add_spend(db_session, account_id, "hint", "Post Oak Grill")
    _add_spend(db_session, account_id, "none", "Costco")
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.add(
        MerchantSender(merchant_key="dollar tree", sender_pattern="dollartree.com", origin="seed")
    )
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    _patch_fake_source(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03", "--dry-run"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("transaction_id=")]
    assert len(lines) == 4
    assert "resolution=exact" in out
    assert "resolution=tolerant:dollar tree" in out
    assert "resolution=hint:post oak" in out
    assert "resolution=none" in out
    assert 'merchant="dollar tree 9523 westheimer rd houston t"' in out
    for line in lines:
        assert "candidates=" in line
        assert 'merchant="' in line


def test_seed_senders_idempotent(db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    monkeypatch.setenv("EMAIL_PROVIDER", "fake")
    monkeypatch.setenv("EMAIL_SENDER_ALLOWLIST", "*")
    monkeypatch.setattr(
        "app.agent.cli.email_source_from_env",
        lambda: __import__("tests.fakes", fromlist=["FakeEmailSource"]).FakeEmailSource([], ["*"]),
    )
    monkeypatch.setattr(
        "app.agent.cli.extractor_from_env",
        lambda: __import__("tests.fakes", fromlist=["FakeExtractor"]).FakeExtractor({}, default=ReceiptExtraction(extractor_version="fake")),
    )
    with pytest.raises(SystemExit):
        main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03", "--seed-senders"])
    first = capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03", "--seed-senders"])
    second = capsys.readouterr().out
    assert "seeded_merchant_senders=" in first
    assert "seeded_merchant_senders=0" in second


def test_email_provider_none_exits_non_zero(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "none")
    with pytest.raises(SystemExit) as exc:
        main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03"])
    assert exc.value.code == 1
    assert "EMAIL_PROVIDER" in capsys.readouterr().out


def _patch_fake_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "fake")
    monkeypatch.setenv("EMAIL_SENDER_ALLOWLIST", "*")
    monkeypatch.setattr(
        "app.agent.cli.email_source_from_env",
        lambda: __import__("tests.fakes", fromlist=["FakeEmailSource"]).FakeEmailSource([], ["*"]),
    )
    monkeypatch.setattr(
        "app.agent.cli.extractor_from_env",
        lambda: __import__("tests.fakes", fromlist=["FakeExtractor"]).FakeExtractor(
            {}, default=ReceiptExtraction(extractor_version="fake")
        ),
    )


def test_cli_dry_run_succeeds_against_fresh_empty_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    set_session_factory(factory)
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    _patch_fake_source(monkeypatch)
    try:
        with pytest.raises(SystemExit) as exc:
            main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03", "--dry-run"])
        assert exc.value.code == 0
        assert "transactions" in inspect(engine).get_table_names()
    finally:
        set_session_factory(None)
        engine.dispose()


def test_cli_dry_run_seeds_senders_on_fresh_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    set_session_factory(factory)
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    _patch_fake_source(monkeypatch)
    try:
        with pytest.raises(SystemExit) as exc:
            main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03", "--dry-run"])
        assert exc.value.code == 0
        expected_seed = sum(len(patterns) for patterns in SEED_SENDERS.values())
        with factory() as db:
            rows = db.scalars(select(MerchantSender)).all()
        assert len(rows) == expected_seed
        assert {row.origin for row in rows} == {"seed"}
        assert {(row.merchant_key, row.sender_pattern) for row in rows} == {
            (key, pattern) for key, patterns in SEED_SENDERS.items() for pattern in patterns
        }
    finally:
        set_session_factory(None)
        engine.dispose()


def test_cli_dry_run_ids_restricts_transactions(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _seed(db_session)
    account_id = db_session.scalars(select(Account.id)).one()
    extra = _add_spend(db_session, account_id, "extra", "Home Depot")
    db_session.commit()
    kept_id = db_session.scalars(select(Transaction.id).order_by(Transaction.id)).first()
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    _patch_fake_source(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--enrich",
                "--from",
                "2024-06-01",
                "--to",
                "2024-06-03",
                "--dry-run",
                "--ids",
                str(kept_id),
            ]
        )
    assert exc.value.code == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("transaction_id=")]
    assert len(lines) == 1
    assert f"transaction_id={kept_id}" in lines[0]
    assert f"transaction_id={extra.id}" not in out


def test_cli_dry_run_verbose_prints_gmail_query(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _seed(db_session)
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    _patch_fake_source(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--enrich",
                "--from",
                "2024-06-01",
                "--to",
                "2024-06-03",
                "--dry-run",
                "--verbose",
            ]
        )
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "gmail_query=" in out
    assert "from:amazon.com" in out
    assert RECEIPT_SHAPE_CLAUSE in out


def test_cli_reset_learned_deletes_learned_rows(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _seed(db_session)
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.add(
        MerchantSender(merchant_key="amazon", sender_pattern="nasa.gov", origin="learned")
    )
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    with pytest.raises(SystemExit) as exc:
        main(["--reset-learned"])
    assert exc.value.code == 0
    assert "deleted_learned=1" in capsys.readouterr().out
    db_session.expire_all()
    remaining = list(db_session.scalars(select(MerchantSender)).all())
    assert len(remaining) == 1
    assert remaining[0].origin == "seed"
    assert remaining[0].sender_pattern == "amazon.com"


def test_cli_inspect_prints_stats_without_body_or_writes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _seed(db_session)
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    txn_id = db_session.scalars(select(Transaction.id).order_by(Transaction.id)).first()
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)

    secret_body = (
        "UNIQUE_SECRET_BODY Order #A-99 Total $25.00 on Jun 2, 2024 also 2024-06-02 extra $3.50"
    )
    plain = EmailMessage(
        ref=EmailRef(
            message_id="m1",
            thread_id=None,
            sender="orders@amazon.com",
            subject="Receipt",
            received_at=datetime(2024, 6, 2, 12, 0, 0),
            snippet="snippet",
        ),
        body_text=secret_body,
        headers={"from": "orders@amazon.com", "subject": "Receipt"},
        attachments=[],
        truncated=False,
        body_source="text/plain",
        plain_bytes=len(secret_body),
        html_text_bytes=0,
    )
    empty = EmailMessage(
        ref=EmailRef(
            message_id="m2",
            thread_id=None,
            sender="billing@amazon.com",
            subject="Empty",
            received_at=datetime(2024, 6, 2, 11, 0, 0),
            snippet="snippet",
        ),
        body_text="",
        headers={"from": "billing@amazon.com", "subject": "Empty"},
        attachments=[],
        truncated=False,
        body_source="none",
        plain_bytes=0,
        html_text_bytes=0,
    )
    source = __import__("tests.fakes", fromlist=["FakeEmailSource"]).FakeEmailSource(
        [plain, empty], ["*"]
    )
    monkeypatch.setenv("EMAIL_PROVIDER", "fake")
    monkeypatch.setenv("EMAIL_SENDER_ALLOWLIST", "*")
    monkeypatch.setenv("EMAIL_FAKE_FIXTURE", "/tmp/unused")
    monkeypatch.setenv("EXTRACTION_MODEL", "")
    monkeypatch.setattr("app.agent.cli.email_source_from_env", lambda: source)

    before_senders = db_session.scalar(select(func.count()).select_from(MerchantSender))
    before_evidence = db_session.scalar(select(func.count()).select_from(TransactionEvidence))
    with pytest.raises(SystemExit) as exc:
        main(["--enrich", "--inspect", str(txn_id)])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "UNIQUE_SECRET_BODY" not in out
    assert f"transaction_id={txn_id}" in out
    assert "resolution=exact" in out
    assert "candidates=2" in out
    assert "external_ref=fake:m1" in out
    assert "body_source=text/plain" in out
    assert f"body_bytes={len(secret_body.encode('utf-8'))}" in out
    assert f"plain_bytes={len(secret_body)}" in out
    assert "html_text_bytes=0" in out
    assert "truncated=False" in out
    assert "currency_tokens=2" in out
    assert "date_tokens=2" in out
    assert "total=25.00" in out
    assert "order_date=2024-06-02" in out
    assert "order_id=A-99" in out
    assert "raw_confidence=0.5" in out
    assert "product_type=None" in out
    assert "category_hint=None" in out
    assert "external_ref=fake:m2" in out
    assert "body_source=none" in out
    assert source.fetch_calls == 2
    with factory() as verify:
        after_senders = verify.scalar(select(func.count()).select_from(MerchantSender))
        after_evidence = verify.scalar(select(func.count()).select_from(TransactionEvidence))
    expected_seed = sum(len(patterns) for patterns in SEED_SENDERS.values())
    assert after_senders == expected_seed
    assert after_senders >= before_senders
    assert after_evidence == before_evidence


def test_cli_inspect_uses_configured_extractor(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _seed(db_session)
    db_session.add(MerchantSender(merchant_key="amazon", sender_pattern="amazon.com", origin="seed"))
    db_session.commit()
    txn_id = db_session.scalars(select(Transaction.id).order_by(Transaction.id)).first()
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    secret_body = "UNIQUE_SECRET_BODY Total $1.00"
    message = EmailMessage(
        ref=EmailRef(
            message_id="m1",
            thread_id=None,
            sender="orders@amazon.com",
            subject="Receipt",
            received_at=datetime(2024, 6, 2, 12, 0, 0),
            snippet="snippet",
        ),
        body_text=secret_body,
        headers={"from": "orders@amazon.com", "subject": "Receipt"},
        attachments=[],
        truncated=False,
        body_source="text/html",
    )
    source = __import__("tests.fakes", fromlist=["FakeEmailSource"]).FakeEmailSource(
        [message], ["*"]
    )
    monkeypatch.setenv("EMAIL_PROVIDER", "fake")
    monkeypatch.setenv("EMAIL_SENDER_ALLOWLIST", "*")
    monkeypatch.setenv("EMAIL_FAKE_FIXTURE", "/tmp/unused")
    monkeypatch.setattr("app.agent.cli.email_source_from_env", lambda: source)
    monkeypatch.setattr(
        "app.agent.cli.extractor_from_env",
        lambda: __import__("tests.fakes", fromlist=["FakeExtractor"]).FakeExtractor(
            {
                "m1": ReceiptExtraction(
                    total=Decimal("99.99"),
                    order_date=datetime(2024, 6, 1).date(),
                    order_id="FAKE-1",
                    extractor_version="fake",
                    raw_confidence=0.9,
                    line_items=[
                        LineItem(
                            description="55-inch OLED",
                            amount=Decimal("99.99"),
                            product_type="television",
                            category_hint="electronics",
                        )
                    ],
                )
            }
        ),
    )
    with pytest.raises(SystemExit) as exc:
        main(["--enrich", "--inspect", str(txn_id)])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "UNIQUE_SECRET_BODY" not in out
    assert "body_source=text/html" in out
    assert "total=99.99" in out
    assert "order_date=2024-06-01" in out
    assert "order_id=FAKE-1" in out
    assert "raw_confidence=0.9" in out
    assert "product_type=television" in out
    assert "category_hint=electronics" in out
    assert "total=1.00" not in out


def test_cli_inspect_missing_transaction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    factory = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    set_session_factory(factory)
    _patch_fake_source(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(["--enrich", "--inspect", "99999"])
    assert exc.value.code == 1
    assert "transaction_id=99999 not_found" in capsys.readouterr().out


def test_inspect_requires_enrich(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--inspect", "1"])
    assert str(exc.value) == "--inspect requires --enrich"
