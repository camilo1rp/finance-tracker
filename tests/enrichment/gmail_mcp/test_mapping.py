from datetime import date, datetime, timezone

from app.domain.email_source import EmailRef
from app.integrations.gmail_mcp.mapping import html_to_text, message_to_email, thread_to_refs
from tests.enrichment.gmail_mcp.conftest import load_fixture

WINDOW = (date(2024, 6, 1), date(2024, 6, 3))


def test_thread_to_refs_drops_out_of_window_and_malformed() -> None:
    payload = load_fixture("search_threads_two_threads.json")
    thread = payload["threads"][0]
    thread["messages"] = list(thread["messages"]) + [
        {"id": "msg_test_bad_date", "sender": "orders@example-shop.test", "date": "not-a-date"},
        {"id": "msg_test_no_sender", "sender": "", "date": "2024-06-02"},
        {"id": "msg_test_display", "sender": "Example Shop <orders@example-shop.test>", "date": "2024-06-01", "subject": "Hi", "snippet": "x"},
    ]
    refs = thread_to_refs(thread, WINDOW)
    ids = [ref.message_id for ref in refs]
    assert "msg_test_confirm_01" in ids
    assert "msg_test_ship_01" not in ids
    assert "msg_test_bad_date" not in ids
    assert "msg_test_no_sender" not in ids
    display = next(ref for ref in refs if ref.message_id == "msg_test_display")
    assert display.sender == "orders@example-shop.test"
    assert display.received_at_precision == "date"
    assert display.received_at == datetime(2024, 6, 1, tzinfo=timezone.utc)
    assert display.thread_id == "thread_test_aa"


def test_message_to_email_prefers_plaintext_and_html_fallback() -> None:
    plain = load_fixture("get_message_plaintext.json")
    ref = EmailRef(
        message_id=plain["id"],
        thread_id="thread_test_aa",
        sender="orders@example-shop.test",
        subject=plain["subject"],
        received_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
        snippet=plain["snippet"],
        received_at_precision="date",
    )
    message = message_to_email(plain, ref, byte_cap=65536)
    assert "Order #TEST-1001" in message.body_text
    assert "Extra HTML-only footer" not in message.body_text
    assert message.body_source == "text/plain"
    assert message.plain_bytes == 71
    assert message.html_text_bytes == 97
    assert message.headers["from"] == "orders@example-shop.test"
    assert message.headers["to"] == "buyer@example-user.test"
    assert message.headers["date"] == "2024-06-02"
    assert all(key == key.lower() for key in message.headers)

    html_only = load_fixture("get_message_html_only.json")
    html_message = message_to_email(html_only, ref, byte_cap=65536)
    assert html_message.body_source == "text/html"
    assert html_message.plain_bytes == 0
    assert html_message.html_text_bytes > 0
    assert "Hello" in html_message.body_text
    assert "Line two & more" in html_message.body_text
    assert "alert" not in html_message.body_text
    assert "color:red" not in html_message.body_text


def test_message_to_email_plain_stub_selects_html() -> None:
    payload = load_fixture("get_message_plain_stub.json")
    ref = EmailRef(
        message_id=payload["id"],
        thread_id="thread_test_aa",
        sender="orders@example-shop.test",
        subject=payload["subject"],
        received_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
        snippet=payload["snippet"],
        received_at_precision="date",
    )
    message = message_to_email(payload, ref, byte_cap=65536)
    assert message.body_source == "text/html (plain stub)"
    assert message.plain_bytes == 14
    assert message.html_text_bytes == 93
    assert "Order #TEST-1001" in message.body_text
    assert "View in HTML" not in message.body_text


def test_message_to_email_truncates_at_utf8_boundary() -> None:
    payload = load_fixture("get_message_oversize.json")
    ref = EmailRef(
        message_id=payload["id"],
        thread_id=None,
        sender="orders@example-shop.test",
        subject=payload["subject"],
        received_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
        snippet=payload["snippet"],
        received_at_precision="date",
    )
    message = message_to_email(payload, ref, byte_cap=4)
    assert message.truncated is True
    message.body_text.encode("utf-8")
    assert len(message.body_text.encode("utf-8")) <= 4
    assert "é" not in message.body_text or message.body_text.encode("utf-8")[-1] != 0xC3


def test_attachments_use_synthetic_ids_without_size() -> None:
    payload = load_fixture("get_message_with_attachments.json")
    ref = EmailRef(
        message_id=payload["id"],
        thread_id=None,
        sender="orders@example-shop.test",
        subject=payload["subject"],
        received_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
        snippet=payload["snippet"],
        received_at_precision="date",
    )
    message = message_to_email(payload, ref, byte_cap=65536)
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert attachment.attachment_id == "msg_test_attach_01:att_test_01"
    assert attachment.filename == "invoice-test.pdf"
    assert attachment.mime_type == "application/pdf"
    assert attachment.size_bytes is None


def test_html_to_text_drops_script_style_unescapes_and_collapses() -> None:
    text = html_to_text(
        "<html><head><style>p{color:red}</style></head>"
        "<body><script>alert(1)</script><p>Hello&nbsp;world</p>\n\n\n<p>Next &amp; last</p></body></html>"
    )
    assert "alert" not in text
    assert "color:red" not in text
    assert "Hello" in text
    assert "world" in text
    assert "&" in text
    assert "\n\n\n" not in text
