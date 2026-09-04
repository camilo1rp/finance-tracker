import pytest

from app.agent.config import email_source_from_env, token_provider_from_env
from app.domain.email_source import EmailSourceUnavailable
from app.integrations.gmail_common.auth import RefreshTokenProvider, StaticTokenProvider
from app.integrations.gmail_rest.source import GmailRestEmailSource


def _clear_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GMAIL_ACCESS_TOKEN",
        "EMAIL_MCP_ACCESS_TOKEN",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_gmail_rest_without_auth_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail_rest")
    with pytest.raises(EmailSourceUnavailable, match="^auth_not_configured$"):
        email_source_from_env()


def test_gmail_access_token_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail_rest")
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "gmail-static")
    monkeypatch.setenv("EMAIL_MCP_ACCESS_TOKEN", "mcp-static")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_OAUTH_REFRESH_TOKEN", "refresh")
    provider = token_provider_from_env()
    assert isinstance(provider, StaticTokenProvider)
    assert provider.access_token() == "gmail-static"
    source = email_source_from_env()
    assert isinstance(source, GmailRestEmailSource)
    assert isinstance(source.client.token_provider, StaticTokenProvider)
    assert source.client.token_provider.access_token() == "gmail-static"


def test_mcp_access_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail_rest")
    monkeypatch.setenv("EMAIL_MCP_ACCESS_TOKEN", "mcp-static")
    source = email_source_from_env()
    assert isinstance(source, GmailRestEmailSource)
    assert source.client.token_provider.access_token() == "mcp-static"


def test_refresh_trio_when_static_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail_rest")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_OAUTH_REFRESH_TOKEN", "refresh")
    provider = token_provider_from_env()
    assert isinstance(provider, RefreshTokenProvider)
    source = email_source_from_env()
    assert isinstance(source.client.token_provider, RefreshTokenProvider)
