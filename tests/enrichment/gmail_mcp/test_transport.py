from types import SimpleNamespace

import httpx
import pytest

from app.domain.email_source import EmailSourceError, EmailSourceUnavailable
from app.integrations.gmail_mcp.auth import RefreshTokenProvider, StaticTokenProvider
from app.integrations.gmail_mcp.transport import StreamableHttpMcpTransport, classify_transport_error, tool_result_as_dict


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://gmailmcp.googleapis.com/mcp/v1")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def _transport() -> StreamableHttpMcpTransport:
    return StreamableHttpMcpTransport(
        "https://gmailmcp.googleapis.com/mcp/v1",
        StaticTokenProvider("token-test"),
        5.0,
    )


def test_allowlist_enforced_before_sdk_client_is_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("SDK client constructed")

    monkeypatch.setattr("app.integrations.gmail_mcp.transport.streamablehttp_client", boom)
    monkeypatch.setattr("app.integrations.gmail_mcp.transport.invoke_mcp", boom)
    with pytest.raises(EmailSourceError, match="tool_not_allowed"):
        _transport().call_tool("create_draft", {"to": "x@example-shop.test"})


def test_http_401_maps_to_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs):
        raise _http_error(401)

    monkeypatch.setattr("app.integrations.gmail_mcp.transport.invoke_mcp", fail)
    with pytest.raises(EmailSourceUnavailable, match="^auth$"):
        _transport().call_tool("list_labels", {})


def test_http_429_retries_then_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def fail(*_args, **_kwargs):
        calls.append(1)
        raise _http_error(429)

    monkeypatch.setattr("app.integrations.gmail_mcp.transport.invoke_mcp", fail)
    monkeypatch.setattr("app.integrations.gmail_mcp.transport.time.sleep", sleeps.append)
    with pytest.raises(EmailSourceUnavailable, match="^rate_limited$"):
        _transport().call_tool("list_labels", {})
    assert len(calls) == 3
    assert sleeps == [0.5, 2.0]


def test_timeout_maps_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fail(*_args, **_kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr("app.integrations.gmail_mcp.transport.invoke_mcp", fail)
    monkeypatch.setattr("app.integrations.gmail_mcp.transport.time.sleep", lambda *_args: None)
    with pytest.raises(EmailSourceUnavailable, match="^timeout$"):
        _transport().call_tool("search_threads", {"query": "x"})
    assert calls == [1]


def test_is_error_uses_tool_name_without_server_text(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    secret = "secret body from the mailbox"
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_invoke(_url, _headers, _timeout_s, operation):
        calls.append(1)

        class Session:
            async def call_tool(self, _name, _arguments):
                return SimpleNamespace(
                    isError=True,
                    structuredContent=None,
                    content=[SimpleNamespace(text=secret)],
                )

        return asyncio.run(operation(Session()))

    monkeypatch.setattr("app.integrations.gmail_mcp.transport.invoke_mcp", fake_invoke)
    monkeypatch.setattr("app.integrations.gmail_mcp.transport.time.sleep", sleeps.append)
    with pytest.raises(EmailSourceError, match="^get_message$") as exc_info:
        _transport().call_tool("get_message", {"messageId": "msg_test_01"})
    assert type(exc_info.value) is EmailSourceError
    assert secret not in str(exc_info.value)
    assert calls == [1]
    assert sleeps == []


def test_classify_5xx_is_retryable_server() -> None:
    reason, retryable = classify_transport_error(_http_error(503))
    assert reason == "server"
    assert retryable is True
    payload = SimpleNamespace(
        isError=False,
        structuredContent={"labels": []},
        content=[],
    )
    assert tool_result_as_dict(payload) == {"labels": []}


def test_refresh_token_provider_caches_until_near_expiry() -> None:
    clock = {"now": 1_000.0}
    posts: list[dict] = []

    def post(_url: str, data: dict):
        posts.append(dict(data))
        request = httpx.Request("POST", _url)
        return httpx.Response(
            200,
            request=request,
            json={"access_token": f"access-{len(posts)}", "expires_in": 3600},
        )

    provider = RefreshTokenProvider(
        "client-id",
        "client-secret",
        "refresh-token",
        clock=lambda: clock["now"],
        post=post,
    )
    assert provider.access_token() == "access-1"
    clock["now"] = 1_000.0 + 3_539
    assert provider.access_token() == "access-1"
    clock["now"] = 1_000.0 + 3_540
    assert provider.access_token() == "access-2"
    assert len(posts) == 2
    assert posts[0]["grant_type"] == "refresh_token"


def test_refresh_token_failure_is_auth() -> None:
    def post(_url: str, data: dict):
        request = httpx.Request("POST", _url)
        response = httpx.Response(400, request=request, json={"error": "invalid_grant"})
        response.raise_for_status()

    provider = RefreshTokenProvider("id", "secret", "refresh", post=post)
    with pytest.raises(EmailSourceUnavailable, match="^auth$"):
        provider.access_token()
