from __future__ import annotations

from app.domain.email_source import EmailSourceError, EmailSourceUnavailable


def map_http_status(status: int, body_hint: str | None) -> EmailSourceUnavailable | EmailSourceError | None:
    if status == 401:
        return EmailSourceUnavailable("auth")
    if status == 403:
        hint = body_hint or ""
        if "insufficientPermissions" in hint or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in hint:
            return EmailSourceUnavailable("auth_scope")
        return EmailSourceUnavailable("auth")
    if status == 429:
        return EmailSourceUnavailable("rate_limited")
    if status >= 500:
        return EmailSourceUnavailable("server")
    if status == 404:
        return EmailSourceError("not_found")
    return None
