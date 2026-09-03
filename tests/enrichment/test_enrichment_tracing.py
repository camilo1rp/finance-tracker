from datetime import datetime
from decimal import Decimal

from app.domain.email_source import EmailMessage, EmailRef
from app.domain.receipt_extractors import _extract_trace_inputs, _extract_trace_outputs
from app.domain.receipts import (
    LineItem,
    ReceiptExtraction,
    receipt_extraction_dump,
)
from app.services.enrichment_service import _enrichment_trace_inputs, _enrichment_trace_outputs


def test_tracing_redacts_email_content() -> None:
    message = EmailMessage(
        ref=EmailRef(
            message_id="m1",
            thread_id=None,
            sender="orders@example.com",
            subject="Receipt",
            received_at=datetime(2024, 6, 2, 12, 0, 0),
            snippet="secret snippet",
        ),
        body_text="secret body",
        headers={"from": "orders@example.com", "subject": "Receipt"},
        attachments=[],
        truncated=False,
    )
    extraction = ReceiptExtraction(
        total=Decimal("10.00"),
        line_items=[LineItem(description="secret item", amount=Decimal("10.00"))],
        extractor_version="fake",
        raw_confidence=0.5,
    )
    inputs = _enrichment_trace_inputs({"message": message, "extraction": extraction, "db": object()})
    outputs = _enrichment_trace_outputs({"message": message, "extraction": extraction})
    assert inputs["message"]["body_text"] == "<redacted>"
    assert inputs["message"]["ref"]["snippet"] == "<redacted>"
    assert inputs["message"]["headers"] == "<redacted>"
    assert outputs["extraction"]["line_items"][0]["description"] == "<redacted>"
    assert "body_text" not in receipt_extraction_dump(extraction)

    model_inputs = _extract_trace_inputs({"message": message, "known_categories": ["Dining"]})
    model_outputs = _extract_trace_outputs(extraction)
    assert model_inputs["message"]["body_text"] == "<redacted>"
    assert model_outputs["line_items"][0]["description"] == "<redacted>"
