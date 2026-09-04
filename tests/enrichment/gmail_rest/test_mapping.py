from datetime import date, datetime, timezone

from app.integrations.gmail_rest.mapping import (
    full_to_message,
    metadata_to_ref,
    payload_attachments,
    payload_to_body,
)
from tests.enrichment.gmail_rest.conftest import load_fixture

WINDOW = (date(2024, 6, 1), date(2024, 6, 3))


def test_metadata_to_ref_ok_datetime_precision() -> None:
    ref = metadata_to_ref(load_fixture("metadata_ok.json"), WINDOW)
    assert ref is not None
    assert ref.message_id == "msg_test_01"
    assert ref.thread_id == "thread_test_01"
    assert ref.sender == "orders@example-shop.test"
    assert ref.subject == "Order confirmation"
    assert ref.received_at_precision == "datetime"
    assert ref.received_at == datetime(2024, 6, 2, 12, 0, tzinfo=timezone.utc)
    assert ref.snippet.startswith("Thanks for your order")


def test_metadata_malformed_from_returns_none() -> None:
    assert metadata_to_ref(load_fixture("metadata_malformed_from.json"), WINDOW) is None


def test_metadata_out_of_window_returns_none() -> None:
    assert metadata_to_ref(load_fixture("metadata_out_of_window.json"), WINDOW) is None
    ref = metadata_to_ref(load_fixture("metadata_out_of_window.json"), None)
    assert ref is not None
    assert ref.message_id == "msg_test_old"


def test_plain_preferred_over_html() -> None:
    payload = load_fixture("full_plain_and_html.json")["payload"]
    body, source = payload_to_body(payload)
    assert source == "text/plain"
    assert "Order #TEST-1001" in body
    assert "ignored because plaintext" not in body


def test_html_only_converted() -> None:
    payload = load_fixture("full_html_only.json")["payload"]
    body, source = payload_to_body(payload)
    assert source == "text/html"
    assert "Hello" in body
    assert "Line two & more" in body
    assert "alert" not in body
    assert "color:red" not in body


def test_nested_alternative_inside_mixed_and_attachment_excluded() -> None:
    msg = load_fixture("full_mixed_with_attachment.json")
    payload = msg["payload"]
    body, source = payload_to_body(payload)
    assert source == "text/plain"
    assert "Nested plain body for mixed message." in body
    assert "Nested HTML" not in body
    attachments = payload_attachments(msg["id"], payload)
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.attachment_id == "msg_test_mixed_01:att_test_01"
    assert attachment.filename == "invoice-test.pdf"
    assert attachment.mime_type == "application/pdf"
    assert attachment.size_bytes == 2048


def test_single_part_payload_body() -> None:
    payload = load_fixture("full_single_part.json")["payload"]
    body, source = payload_to_body(payload)
    assert source == "text/plain"
    assert body == "Single-part plaintext body."


def test_base64url_dash_underscore_and_non_ascii() -> None:
    msg = load_fixture("full_base64url_chars.json")
    data = msg["payload"]["body"]["data"]
    assert "-" in data and "_" in data
    body, source = payload_to_body(msg["payload"])
    assert source == "text/plain"
    assert "Ͽ" in body
    assert any(ord(ch) > 127 for ch in body)


def test_full_to_message_headers_attachments_truncation() -> None:
    msg = load_fixture("full_mixed_with_attachment.json")
    ref = metadata_to_ref(load_fixture("metadata_ok.json"), WINDOW)
    assert ref is not None
    message = full_to_message(msg, ref, byte_cap=65536)
    assert message.headers["from"] == "Example Shop <orders@example-shop.test>"
    assert message.headers["to"] == "buyer@example-user.test"
    assert "x-ignored" not in message.headers
    assert all(key == key.lower() for key in message.headers)
    assert len(message.attachments) == 1
    assert message.truncated is False
    tiny = full_to_message(msg, ref, byte_cap=4)
    assert tiny.truncated is True
    assert len(tiny.body_text.encode("utf-8")) <= 4
