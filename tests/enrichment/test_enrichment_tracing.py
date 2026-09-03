from datetime import date, datetime
from decimal import Decimal

from app.domain.email_source import EmailMessage, EmailQuery, EmailRef
from app.domain.receipt_extractors import _extract_trace_inputs, _extract_trace_outputs
from app.domain.receipts import LineItem, ReceiptExtraction, receipt_extraction_dump
from app.integrations.gmail_mcp.mapping import build_search_query
from app.integrations.gmail_mcp.source import McpEmailSource, _source_trace_inputs, _source_trace_outputs
from app.services.enrichment_service import _enrichment_trace_inputs, _enrichment_trace_outputs
from tests.fakes import FakeMcpTransport


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


def test_gmail_adapter_methods_redact_hints_and_query() -> None:
    query = EmailQuery(
        senders=["example-shop.test"],
        date_from=date(2024, 6, 1),
        date_to=date(2024, 6, 3),
        text_hints=["secret merchant phrase"],
        max_results=5,
    )
    redacted = _source_trace_inputs({"self": object(), "query": query})
    assert redacted["query"]["text_hints"] == ["<redacted>"]
    assert "secret merchant phrase" not in redacted["gmail_query"]
    assert '"<redacted>"' in redacted["gmail_query"]
    assert "from:example-shop.test" in redacted["gmail_query"]
    assert "secret merchant phrase" in build_search_query(query)

    message = EmailMessage(
        ref=EmailRef(
            message_id="msg_test_01",
            thread_id=None,
            sender="orders@example-shop.test",
            subject="Receipt",
            received_at=datetime(2024, 6, 2, 12, 0, 0),
            snippet="secret snippet",
            received_at_precision="date",
        ),
        body_text="secret body",
        headers={"from": "orders@example-shop.test"},
        attachments=[],
        truncated=False,
    )
    fetch_inputs = _source_trace_inputs({"self": object(), "ref": message.ref})
    assert fetch_inputs["ref"]["snippet"] == "<redacted>"
    fetch_outputs = _source_trace_outputs(message)
    assert fetch_outputs["body_text"] == "<redacted>"
    health_outputs = _source_trace_outputs({"available": True, "provider": "gmail"})
    assert health_outputs["provider"] == "gmail"

    transport = FakeMcpTransport({"list_labels": [{"labels": []}]})
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024)
    status = source.health()
    assert status.available is True
