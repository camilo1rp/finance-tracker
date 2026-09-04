from datetime import date

from app.domain.email_source import EmailQuery
from app.integrations.gmail_common.auth import (
    RefreshTokenProvider,
    StaticTokenProvider,
    TokenProvider,
)
from app.integrations.gmail_common.query import build_search_query
from app.integrations.gmail_common.text import html_to_text
from app.integrations.gmail_mcp.auth import (
    RefreshTokenProvider as McpRefreshTokenProvider,
    StaticTokenProvider as McpStaticTokenProvider,
    TokenProvider as McpTokenProvider,
)
from app.integrations.gmail_mcp.mapping import (
    build_search_query as mcp_build_search_query,
    html_to_text as mcp_html_to_text,
)


def test_shared_symbols_importable_from_gmail_common_and_mcp_reexports() -> None:
    assert build_search_query is mcp_build_search_query
    assert html_to_text is mcp_html_to_text
    assert TokenProvider is McpTokenProvider
    assert StaticTokenProvider is McpStaticTokenProvider
    assert RefreshTokenProvider is McpRefreshTokenProvider

    query = EmailQuery(
        senders=["orders@example-shop.test"],
        date_from=date(2024, 6, 1),
        date_to=date(2024, 6, 3),
    )
    built = build_search_query(query)
    assert "from:orders@example-shop.test" in built
    assert built.endswith("-in:draft")
    text = html_to_text("<p>Hello&nbsp;world</p>")
    assert "Hello" in text
    assert "world" in text
    provider = StaticTokenProvider("token-test")
    assert provider.access_token() == "token-test"
