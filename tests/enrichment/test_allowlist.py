from datetime import date, datetime

import pytest

from app.domain.email_source import (
    EmailQuery,
    EmailRef,
    EmailSourceError,
    SenderNotAllowed,
    parse_allowlist,
    sender_allowed,
)
from tests.fakes import FakeEmailSource


def _ref(sender: str, message_id: str = "m1") -> EmailRef:
    return EmailRef(
        message_id=message_id,
        thread_id=None,
        sender=sender,
        subject="Your receipt",
        received_at=datetime(2024, 6, 2, 12, 0, 0),
        snippet="snippet",
    )


def _message(sender: str, message_id: str = "m1"):
    from app.domain.email_source import EmailMessage

    return EmailMessage(
        ref=_ref(sender, message_id),
        body_text="Body",
        headers={"from": sender, "subject": "Your receipt", "message-id": message_id},
        attachments=[],
        truncated=False,
    )


def test_parse_allowlist_cases() -> None:
    assert parse_allowlist("") == []
    assert parse_allowlist("*") == ["*"]
    assert parse_allowlist(" AMAZON.COM , User@Example.com ") == [
        "amazon.com",
        "user@example.com",
    ]


@pytest.mark.parametrize(
    ("patterns", "address", "expected"),
    [
        (["alerts@example.com"], "alerts@example.com", True),
        (["example.com"], "noreply@example.com", True),
        (["example.com"], "noreply@mail.example.com", True),
        (["*@receipts.example.com"], "x@receipts.example.com", True),
        (["*"], "anything@anywhere.com", True),
        (["example.com"], "noreply@other.com", False),
    ],
)
def test_sender_allowed(patterns: list[str], address: str, expected: bool) -> None:
    assert sender_allowed(patterns, address) is expected


def test_search_outside_allowlist_returns_empty_without_provider_call() -> None:
    source = FakeEmailSource([_message("orders@amazon.com")], ["apple.com"])
    result = source.search(
        EmailQuery(
            senders=["amazon.com"],
            date_from=date(2024, 6, 1),
            date_to=date(2024, 6, 3),
            text_hints=[],
            max_results=5,
        )
    )
    assert result == []
    assert source.search_calls == 0


def test_fetch_disallowed_sender_raises() -> None:
    source = FakeEmailSource([_message("orders@amazon.com")], ["apple.com"])
    with pytest.raises(SenderNotAllowed):
        source.fetch(_ref("orders@amazon.com"))


def test_fetch_forged_sender_raises() -> None:
    source = FakeEmailSource([_message("orders@amazon.com")], ["amazon.com"])
    with pytest.raises(EmailSourceError):
        source.fetch(_ref("billing@amazon.com"))
