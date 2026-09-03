from datetime import date, timedelta
import os

import pytest

from app.agent.config import email_provider, email_source_from_env, email_sender_allowlist
from app.domain.email_source import EmailQuery, parse_allowlist


@pytest.mark.live_gmail
def test_live_gmail_health_and_one_result_search() -> None:
    if email_provider() != "gmail":
        pytest.skip("EMAIL_PROVIDER is not gmail")
    if not os.environ.get("EMAIL_MCP_ACCESS_TOKEN") and not os.environ.get("GMAIL_OAUTH_REFRESH_TOKEN"):
        pytest.skip("gmail auth is not configured")
    allowlist = parse_allowlist(email_sender_allowlist())
    if not allowlist:
        pytest.skip("EMAIL_SENDER_ALLOWLIST is empty")
    source = email_source_from_env()
    status = source.health()
    assert status.available is True
    assert status.provider == "gmail"
    today = date.today()
    refs = source.search(
        EmailQuery(
            senders=allowlist,
            date_from=today - timedelta(days=7),
            date_to=today,
            max_results=1,
        )
    )
    assert len(refs) <= 1
