from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from app.domain.email_source import EmailQuery, EmailRef, EmailSourceError, SenderNotAllowed
from app.integrations.gmail_rest.source import GmailRestEmailSource
from tests.enrichment.gmail_rest.conftest import load_fixture
from tests.fakes import FakeGmailRestClient

WINDOW_QUERY = EmailQuery(
    senders=["example-shop.test"],
    date_from=date(2024, 6, 1),
    date_to=date(2024, 6, 3),
    max_results=10,
)


def _metadata(message_id: str, sender: str = "Example Shop <orders@example-shop.test>", thread_id: str = "thread_test_01") -> dict:
    payload = deepcopy(load_fixture("metadata_ok.json"))
    payload["id"] = message_id
    payload["threadId"] = thread_id
    for header in payload["payload"]["headers"]:
        if header["name"] == "From":
            header["value"] = sender
    return payload


def _ref(sender: str = "orders@example-shop.test", message_id: str = "msg_test_01") -> EmailRef:
    return EmailRef(
        message_id=message_id,
        thread_id="thread_test_01",
        sender=sender,
        subject="Order confirmation",
        received_at=datetime(2024, 6, 2, 12, 0, tzinfo=timezone.utc),
        snippet="snippet",
        received_at_precision="datetime",
    )


def test_search_outside_allowlist_makes_no_client_call() -> None:
    client = FakeGmailRestClient({"list_messages": [load_fixture("list_two_pages_p1.json")]})
    source = GmailRestEmailSource(client, ["apple.test"], byte_cap=1024)
    assert source.search(WINDOW_QUERY) == []
    assert client.calls == []


def test_n_plus_one_metadata_calls_match_id_count() -> None:
    listing = {
        "messages": [
            {"id": "msg_test_01", "threadId": "thread_test_01"},
            {"id": "msg_test_p2", "threadId": "thread_test_p2"},
        ]
    }
    client = FakeGmailRestClient(
        {
            "list_messages": [listing],
            "get_message_metadata": [
                _metadata("msg_test_01"),
                _metadata("msg_test_p2", thread_id="thread_test_p2"),
            ],
        }
    )
    source = GmailRestEmailSource(client, ["example-shop.test"], byte_cap=1024)
    refs = source.search(WINDOW_QUERY)
    assert [ref.message_id for ref in refs] == ["msg_test_01", "msg_test_p2"]
    assert sum(1 for name, _ in client.calls if name == "list_messages") == 1
    assert sum(1 for name, _ in client.calls if name == "get_message_metadata") == 2


def test_post_filter_drops_disallowed_sender() -> None:
    listing = {
        "messages": [
            {"id": "msg_test_01", "threadId": "thread_test_01"},
            {"id": "msg_test_other", "threadId": "thread_test_other"},
        ]
    }
    client = FakeGmailRestClient(
        {
            "list_messages": [listing],
            "get_message_metadata": [
                _metadata("msg_test_01"),
                _metadata("msg_test_other", sender="alerts@other-merchant.test", thread_id="thread_test_other"),
            ],
        }
    )
    source = GmailRestEmailSource(client, ["example-shop.test"], byte_cap=1024)
    refs = source.search(WINDOW_QUERY)
    assert [ref.sender for ref in refs] == ["orders@example-shop.test"]
    assert [ref.message_id for ref in refs] == ["msg_test_01"]


def test_pagination_stops_at_page_cap() -> None:
    extra = {"messages": [{"id": "msg_test_p3", "threadId": "thread_test_p3"}]}
    client = FakeGmailRestClient(
        {
            "list_messages": [
                load_fixture("list_two_pages_p1.json"),
                load_fixture("list_two_pages_p2.json"),
                extra,
            ],
            "get_message_metadata": [
                _metadata("msg_test_p1", thread_id="thread_test_p1"),
                _metadata("msg_test_p2", thread_id="thread_test_p2"),
                _metadata("msg_test_p3", thread_id="thread_test_p3"),
            ],
        }
    )
    source = GmailRestEmailSource(client, ["example-shop.test"], byte_cap=1024, page_cap=2)
    refs = source.search(WINDOW_QUERY)
    assert [ref.message_id for ref in refs] == ["msg_test_p1", "msg_test_p2"]
    list_calls = [params for name, params in client.calls if name == "list_messages"]
    assert len(list_calls) == 2
    assert list_calls[1]["page_token"] == "token_test_page2"


def test_pagination_stops_at_max_results() -> None:
    client = FakeGmailRestClient(
        {
            "list_messages": [
                load_fixture("list_two_pages_p1.json"),
                load_fixture("list_two_pages_p2.json"),
            ],
            "get_message_metadata": [
                _metadata("msg_test_p1", thread_id="thread_test_p1"),
                _metadata("msg_test_p2", thread_id="thread_test_p2"),
            ],
        }
    )
    source = GmailRestEmailSource(client, ["example-shop.test"], byte_cap=1024, page_cap=2)
    refs = source.search(
        EmailQuery(
            senders=["example-shop.test"],
            date_from=date(2024, 6, 1),
            date_to=date(2024, 6, 3),
            max_results=1,
        )
    )
    assert [ref.message_id for ref in refs] == ["msg_test_p1"]
    assert sum(1 for name, _ in client.calls if name == "list_messages") == 1
    assert sum(1 for name, _ in client.calls if name == "get_message_metadata") == 1


def test_not_found_on_one_metadata_skips_id() -> None:
    listing = {
        "messages": [
            {"id": "msg_test_missing", "threadId": "thread_test_missing"},
            {"id": "msg_test_01", "threadId": "thread_test_01"},
        ]
    }
    client = FakeGmailRestClient(
        {
            "list_messages": [listing],
            "get_message_metadata": [
                EmailSourceError("not_found"),
                _metadata("msg_test_01"),
            ],
        }
    )
    source = GmailRestEmailSource(client, ["example-shop.test"], byte_cap=1024)
    refs = source.search(WINDOW_QUERY)
    assert [ref.message_id for ref in refs] == ["msg_test_01"]


def test_fetch_sender_mismatch_raises() -> None:
    full = deepcopy(load_fixture("full_plain_and_html.json"))
    for header in full["payload"]["headers"]:
        if header["name"] == "From":
            header["value"] = "alerts@other-merchant.test"
    client = FakeGmailRestClient({"get_message_full": [full]})
    source = GmailRestEmailSource(client, ["example-shop.test", "other-merchant.test"], byte_cap=1024)
    with pytest.raises(EmailSourceError, match="sender_mismatch"):
        source.fetch(_ref())


def test_fetch_disallowed_ref_makes_no_client_call() -> None:
    client = FakeGmailRestClient({"get_message_full": [load_fixture("full_plain_and_html.json")]})
    source = GmailRestEmailSource(client, ["example-shop.test"], byte_cap=1024)
    with pytest.raises(SenderNotAllowed):
        source.fetch(_ref(sender="alerts@other-merchant.test"))
    assert client.calls == []


def test_health_masks_local_part() -> None:
    client = FakeGmailRestClient({"get_profile": [load_fixture("profile.json")]})
    source = GmailRestEmailSource(client, ["example-shop.test"], byte_cap=1024)
    status = source.health()
    assert status.available is True
    assert status.provider == "gmail_rest"
    assert status.account_hint == "c***@example-user.test"


def test_provider_name_is_gmail_rest() -> None:
    source = GmailRestEmailSource(FakeGmailRestClient({}), ["example-shop.test"], byte_cap=1024)
    assert source.provider_name == "gmail_rest"
