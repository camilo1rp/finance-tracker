from datetime import datetime
from decimal import Decimal

import pytest

from app.domain.email_source import EmailMessage, EmailRef
from app.domain.receipt_extractors import ModelReceiptExtractor
from app.domain.receipts import ReceiptExtraction


class FakeStructuredRunnable:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.result


class FakeChatModel:
    def __init__(self, runnable: FakeStructuredRunnable) -> None:
        self.runnable = runnable
        self.schema = None

    def bind_tools(self, *_args, **_kwargs):
        raise AssertionError("extractor must not bind tools")

    def with_structured_output(self, schema):
        self.schema = schema
        return self.runnable


def _message(body_text: str) -> EmailMessage:
    return EmailMessage(
        ref=EmailRef(
            message_id="m1",
            thread_id=None,
            sender="orders@example.com",
            subject="Receipt",
            received_at=datetime(2024, 6, 2, 12, 0, 0),
            snippet="snippet",
        ),
        body_text=body_text,
        headers={"from": "orders@example.com", "subject": "Receipt"},
        attachments=[],
        truncated=False,
    )


def test_model_extractor_happy_path() -> None:
    runnable = FakeStructuredRunnable(
        ReceiptExtraction(total=Decimal("25.00"), extractor_version="ignored", raw_confidence=0.8)
    )
    model = FakeChatModel(runnable)
    extractor = ModelReceiptExtractor(model, model_name="fake-model", body_byte_cap=32)
    extraction = extractor.extract(_message("Body"), ["Dining", "Travel"])
    assert extraction.total == Decimal("25.00")
    assert extraction.extractor_version == "model-1:fake-model"
    assert model.schema is ReceiptExtraction
    sent = runnable.calls[0][1].content
    assert "known_categories: ['Dining', 'Travel']" in sent


def test_model_extractor_parse_failure_returns_zero_confidence() -> None:
    runnable = FakeStructuredRunnable(error=ValueError("parse fail"))
    extraction = ModelReceiptExtractor(
        FakeChatModel(runnable), model_name="fake-model", body_byte_cap=32
    ).extract(_message("Body"), [])
    assert extraction.raw_confidence == 0.0
    assert extraction.total is None
    assert extraction.extractor_version == "model-1:fake-model-parsefail"


def test_model_extractor_transport_error_propagates() -> None:
    class TransportError(RuntimeError):
        pass

    runnable = FakeStructuredRunnable(error=TransportError("transport"))
    extractor = ModelReceiptExtractor(
        FakeChatModel(runnable), model_name="fake-model", body_byte_cap=32
    )
    with pytest.raises(TransportError):
        extractor.extract(_message("Body"), [])


def test_body_text_truncated_to_cap() -> None:
    runnable = FakeStructuredRunnable(
        ReceiptExtraction(total=Decimal("25.00"), extractor_version="ignored", raw_confidence=0.8)
    )
    extractor = ModelReceiptExtractor(
        FakeChatModel(runnable), model_name="fake-model", body_byte_cap=8
    )
    extractor.extract(_message("0123456789abcdef"), [])
    sent = runnable.calls[0][1].content
    assert "01234567" in sent
    assert "0123456789abcdef" not in sent
