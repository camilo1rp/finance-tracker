from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.agent.cli import main
from app.domain.email_source import EmailMessage, EmailRef
from app.domain.receipts import ReceiptExtraction
from app.models import Account, MerchantSender, Owner, Transaction
from app.agent.config import set_session_factory
from sqlalchemy.orm import Session, sessionmaker


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

    before = db_session.scalar(select(func.count()).select_from(MerchantSender))
    with pytest.raises(SystemExit) as exc:
        main(["--enrich", "--from", "2024-06-01", "--to", "2024-06-03", "--dry-run"])
    assert exc.value.code == 0
    assert "transaction_id=" in capsys.readouterr().out
    after = db_session.scalar(select(func.count()).select_from(MerchantSender))
    assert before == after


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
