import httpx
import pytest

from app.domain.email_source import EmailSourceError, EmailSourceUnavailable
from app.integrations.gmail_common.auth import StaticTokenProvider
from app.integrations.gmail_rest.client import GmailRestClient
from tests.enrichment.gmail_rest.conftest import load_fixture

BASE = "https://gmail.googleapis.com/gmail/v1/users/me/"


def _client() -> GmailRestClient:
    return GmailRestClient(StaticTokenProvider("token-test"), BASE, timeout_s=5.0, retries=2)


class _FakeResponse:
    def __init__(self, status: int, text: str, payload: dict | None = None) -> None:
        self.status_code = status
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _RecordingClient:
    constructed: list[dict] = []
    requests: list[dict] = []
    responses: list[_FakeResponse] = []

    def __init__(self, *args, **kwargs) -> None:
        _RecordingClient.constructed.append({"args": args, "kwargs": kwargs})
        self.timeout = kwargs.get("timeout")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def request(self, method, url, headers=None, params=None):
        _RecordingClient.requests.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "params": params}
        )
        if _RecordingClient.responses:
            return _RecordingClient.responses.pop(0)
        return _FakeResponse(200, "{}", {"ok": True})


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse] | None = None) -> None:
    _RecordingClient.constructed = []
    _RecordingClient.requests = []
    _RecordingClient.responses = list(responses or [])
    monkeypatch.setattr("app.integrations.gmail_rest.client.httpx.Client", _RecordingClient)


def test_disallowed_path_and_method_never_construct_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("httpx.Client constructed")

    monkeypatch.setattr("app.integrations.gmail_rest.client.httpx.Client", boom)
    client = _client()
    with pytest.raises(EmailSourceError, match="^endpoint_not_allowed$"):
        client._request("POST", "messages", {"q": "from:secret@example-shop.test"})
    with pytest.raises(EmailSourceError, match="^endpoint_not_allowed$"):
        client._request("GET", "drafts", None)
    with pytest.raises(EmailSourceError, match="^endpoint_not_allowed$"):
        client._request("GET", "messages/msg_test_01", {"format": "raw"})
    with pytest.raises(EmailSourceError, match="^endpoint_not_allowed$"):
        client._request("DELETE", "profile", None)


def test_http_401_maps_to_auth_without_body(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "from:secret-query@example-shop.test"
    _patch_client(monkeypatch, [_FakeResponse(401, secret, {"error": secret})])
    with pytest.raises(EmailSourceUnavailable, match="^auth$") as exc_info:
        _client().get_profile()
    assert secret not in str(exc_info.value)


def test_http_403_scope_maps_to_auth_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("error_403_scope.json")
    text = '{"error":{"reason":"ACCESS_TOKEN_SCOPE_INSUFFICIENT"}}'
    _patch_client(monkeypatch, [_FakeResponse(403, text, body)])
    with pytest.raises(EmailSourceUnavailable, match="^auth_scope$") as exc_info:
        _client().list_messages("from:orders@example-shop.test", 10, None)
    assert "ACCESS_TOKEN_SCOPE_INSUFFICIENT" not in str(exc_info.value)
    assert "from:orders" not in str(exc_info.value)


def test_http_403_other_maps_to_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, [_FakeResponse(403, '{"error":"forbidden"}', {})])
    with pytest.raises(EmailSourceUnavailable, match="^auth$"):
        _client().get_profile()


def test_http_429_retries_then_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    body = load_fixture("error_429.json")
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(429, '{"error":"rate"}', body),
            _FakeResponse(429, '{"error":"rate"}', body),
            _FakeResponse(429, '{"error":"rate"}', body),
        ],
    )
    monkeypatch.setattr("app.integrations.gmail_rest.client.time.sleep", sleeps.append)
    with pytest.raises(EmailSourceUnavailable, match="^rate_limited$"):
        _client().get_profile()
    assert len(_RecordingClient.constructed) == 3
    assert sleeps == [0.5, 2.0]


def test_http_5xx_retries_then_server(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(503, "upstream", {}),
            _FakeResponse(503, "upstream", {}),
            _FakeResponse(503, "upstream", {}),
        ],
    )
    monkeypatch.setattr("app.integrations.gmail_rest.client.time.sleep", sleeps.append)
    with pytest.raises(EmailSourceUnavailable, match="^server$") as exc_info:
        _client().get_profile()
    assert "upstream" not in str(exc_info.value)
    assert sleeps == [0.5, 2.0]


def test_http_404_is_not_found_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    _patch_client(monkeypatch, [_FakeResponse(404, "missing message body", {})])
    monkeypatch.setattr("app.integrations.gmail_rest.client.time.sleep", sleeps.append)
    with pytest.raises(EmailSourceError, match="^not_found$") as exc_info:
        _client().get_message_metadata("msg_test_missing")
    assert type(exc_info.value) is EmailSourceError
    assert "missing message body" not in str(exc_info.value)
    assert sleeps == []
    assert len(_RecordingClient.constructed) == 1


def test_no_persistent_client_and_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(
        monkeypatch,
        [_FakeResponse(200, "{}", {"emailAddress": "casey@example-user.test"}), _FakeResponse(200, "{}", {"messages": []})],
    )
    client = _client()
    assert not hasattr(client, "_client")
    client.get_profile()
    client.list_messages("in:inbox", 5, None)
    assert len(_RecordingClient.constructed) == 2
    assert all(item["headers"]["Authorization"] == "Bearer token-test" for item in _RecordingClient.requests)
    assert _RecordingClient.requests[0]["method"] == "GET"
    assert _RecordingClient.requests[0]["url"].rstrip("/").endswith("profile")


def test_timeout_maps_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class BoomClient:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(1)

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def request(self, *args, **kwargs):
            raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr("app.integrations.gmail_rest.client.httpx.Client", BoomClient)
    monkeypatch.setattr("app.integrations.gmail_rest.client.time.sleep", lambda *_args: None)
    with pytest.raises(EmailSourceUnavailable, match="^timeout$"):
        _client().get_profile()
    assert calls == [1]
