from datetime import date, datetime, timezone

import pytest

from app.domain.email_source import EmailQuery, EmailRef, EmailSourceError, EmailSourceUnavailable, SenderNotAllowed
from app.integrations.gmail_mcp.source import McpEmailSource
from app.integrations.gmail_mcp.transport import ALLOWED_TOOLS
from tests.enrichment.gmail_mcp.conftest import load_fixture
from tests.fakes import FakeMcpTransport

WINDOW_QUERY = EmailQuery(
    senders=["example-shop.test"],
    date_from=date(2024, 6, 1),
    date_to=date(2024, 6, 3),
    max_results=10,
)


def _ref(sender: str = "orders@example-shop.test", message_id: str = "msg_test_confirm_01") -> EmailRef:
    return EmailRef(
        message_id=message_id,
        thread_id="thread_test_aa",
        sender=sender,
        subject="Order confirmation",
        received_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
        snippet="snippet",
        received_at_precision="date",
    )


def test_search_outside_allowlist_makes_no_transport_call() -> None:
    transport = FakeMcpTransport({"search_threads": [load_fixture("search_threads_two_threads.json")]})
    source = McpEmailSource(transport, ["apple.test"], byte_cap=1024)
    result = source.search(WINDOW_QUERY)
    assert result == []
    assert transport.calls == []


def test_post_filter_removes_disallowed_senders() -> None:
    transport = FakeMcpTransport({"search_threads": [load_fixture("search_threads_two_threads.json")]})
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024)
    refs = source.search(WINDOW_QUERY)
    assert [ref.sender for ref in refs] == ["orders@example-shop.test"]
    assert [ref.message_id for ref in refs] == ["msg_test_confirm_01"]
    assert all(name in ALLOWED_TOOLS for name, _args in transport.calls)


def test_pagination_stops_at_page_cap() -> None:
    p3 = {
        "threads": [
            {
                "id": "thread_test_p3",
                "messages": [
                    {
                        "id": "msg_test_p3",
                        "snippet": "Third page.",
                        "subject": "Page three",
                        "sender": "orders@example-shop.test",
                        "date": "2024-06-01",
                    }
                ],
            }
        ],
        "nextPageToken": None,
    }
    transport = FakeMcpTransport(
        {
            "search_threads": [
                load_fixture("search_threads_paged_p1.json"),
                load_fixture("search_threads_paged_p2.json"),
                p3,
            ]
        }
    )
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024, page_cap=2)
    refs = source.search(EmailQuery(senders=["example-shop.test"], date_from=date(2024, 6, 1), date_to=date(2024, 6, 3), max_results=10))
    assert [ref.message_id for ref in refs] == ["msg_test_p1", "msg_test_p2"]
    assert len(transport.calls) == 2
    assert transport.calls[1][1]["pageToken"] == "token_test_page2"


def test_pagination_stops_at_max_results() -> None:
    transport = FakeMcpTransport(
        {
            "search_threads": [
                load_fixture("search_threads_paged_p1.json"),
                load_fixture("search_threads_paged_p2.json"),
            ]
        }
    )
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024, page_cap=2)
    refs = source.search(
        EmailQuery(
            senders=["example-shop.test"],
            date_from=date(2024, 6, 1),
            date_to=date(2024, 6, 3),
            max_results=1,
        )
    )
    assert [ref.message_id for ref in refs] == ["msg_test_p1"]
    assert len(transport.calls) == 1


def test_fetch_sender_mismatch_raises() -> None:
    transport = FakeMcpTransport({"get_message": [load_fixture("get_message_sender_mismatch.json")]})
    source = McpEmailSource(transport, ["example-shop.test", "other-merchant.test"], byte_cap=1024)
    with pytest.raises(EmailSourceError, match="sender_mismatch"):
        source.fetch(_ref())


def test_fetch_disallowed_ref_raises_before_transport() -> None:
    transport = FakeMcpTransport({"get_message": [load_fixture("get_message_plaintext.json")]})
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024)
    with pytest.raises(SenderNotAllowed):
        source.fetch(_ref(sender="alerts@other-merchant.test"))
    assert transport.calls == []


def test_health_maps_unavailable() -> None:
    transport = FakeMcpTransport({}, fail_with=EmailSourceUnavailable("auth"))
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024)
    status = source.health()
    assert status.available is False
    assert status.provider == "gmail"
    assert status.detail == "auth"


def test_verify_tools_fails_when_missing() -> None:
    transport = FakeMcpTransport({}, tools=["search_threads", "list_labels"])
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024)
    with pytest.raises(EmailSourceUnavailable, match="tools_missing"):
        source.verify_tools()


def test_recorded_calls_stay_inside_allowlist() -> None:
    transport = FakeMcpTransport(
        {
            "search_threads": [load_fixture("search_threads_two_threads.json")],
            "get_message": [load_fixture("get_message_plaintext.json")],
            "list_labels": [{"labels": []}],
        }
    )
    source = McpEmailSource(transport, ["example-shop.test"], byte_cap=1024)
    source.search(WINDOW_QUERY)
    source.fetch(_ref())
    source.health()
    with pytest.raises(EmailSourceError, match="tool_not_allowed"):
        transport.call_tool("create_draft", {})
    assert transport.calls
    assert all(name in ALLOWED_TOOLS for name, _arguments in transport.calls)
