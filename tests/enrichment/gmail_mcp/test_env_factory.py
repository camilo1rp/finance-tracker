import pytest

from app.agent.config import email_source_from_env, token_provider_from_env
from app.domain.email_source import EmailSourceUnavailable
from app.integrations.gmail_mcp.auth import RefreshTokenProvider, StaticTokenProvider
from app.integrations.gmail_mcp.source import McpEmailSource


def _clear_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "EMAIL_MCP_ACCESS_TOKEN",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_gmail_without_auth_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    with pytest.raises(EmailSourceUnavailable, match="^auth_not_configured$"):
        email_source_from_env()


def test_static_token_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    monkeypatch.setenv("EMAIL_MCP_ACCESS_TOKEN", "static-token")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_OAUTH_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(
        "app.integrations.gmail_mcp.transport.StreamableHttpMcpTransport.list_tools",
        lambda self: ["search_threads", "get_message", "list_labels"],
    )
    provider = token_provider_from_env()
    assert isinstance(provider, StaticTokenProvider)
    source = email_source_from_env()
    assert isinstance(source, McpEmailSource)
    assert isinstance(source.transport.token_provider, StaticTokenProvider)


def test_refresh_trio_when_static_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_OAUTH_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(
        "app.integrations.gmail_mcp.transport.StreamableHttpMcpTransport.list_tools",
        lambda self: ["search_threads", "get_message", "list_labels"],
    )
    provider = token_provider_from_env()
    assert isinstance(provider, RefreshTokenProvider)
    source = email_source_from_env()
    assert isinstance(source.transport.token_provider, RefreshTokenProvider)
