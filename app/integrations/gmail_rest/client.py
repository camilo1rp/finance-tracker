from __future__ import annotations

from urllib.parse import urljoin
import time

import httpx

from app.domain.email_source import EmailSourceError, EmailSourceUnavailable
from app.integrations.gmail_common.auth import TokenProvider
from app.integrations.gmail_common.errors import map_http_status

ALLOWED_ENDPOINTS = frozenset(
    {
        ("GET", "messages"),
        ("GET", "messages/{id}"),
        ("GET", "profile"),
    }
)
ALLOWED_MESSAGE_FORMATS = frozenset({"metadata", "full"})
_RETRY_DELAYS_S = (0.5, 2.0)
_RETRYABLE_REASONS = frozenset({"rate_limited", "server"})


def _canonical_path(path: str) -> str:
    stripped = path.strip("/")
    if stripped in {"messages", "profile"}:
        return stripped
    if stripped.startswith("messages/"):
        rest = stripped[len("messages/") :]
        if rest and "/" not in rest:
            return "messages/{id}"
    return stripped


def _format_param(params: dict | list | tuple | None) -> str | None:
    if params is None:
        return None
    if isinstance(params, dict):
        value = params.get("format")
        return None if value is None else str(value)
    for key, value in params:
        if key == "format":
            return str(value)
    return None


class GmailRestClient:
    def __init__(
        self,
        token_provider: TokenProvider,
        base_url: str,
        timeout_s: float,
        retries: int = 2,
    ) -> None:
        self.token_provider = token_provider
        self.base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.timeout_s = timeout_s
        self.retries = retries

    def list_messages(self, q: str, max_results: int, page_token: str | None) -> dict:
        params: dict[str, str | int] = {
            "q": q,
            "maxResults": max_results,
            "includeSpamTrash": "false",
        }
        if page_token:
            params["pageToken"] = page_token
        return self._request("GET", "messages", params)

    def get_message_metadata(self, message_id: str) -> dict:
        params = [
            ("format", "metadata"),
            ("metadataHeaders", "From"),
            ("metadataHeaders", "To"),
            ("metadataHeaders", "Subject"),
            ("metadataHeaders", "Date"),
            ("metadataHeaders", "Message-ID"),
        ]
        return self._request("GET", f"messages/{message_id}", params)

    def get_message_full(self, message_id: str) -> dict:
        return self._request("GET", f"messages/{message_id}", {"format": "full"})

    def get_profile(self) -> dict:
        return self._request("GET", "profile", None)

    def _request(self, method: str, path: str, params) -> dict:
        canonical = _canonical_path(path)
        if (method, canonical) not in ALLOWED_ENDPOINTS:
            raise EmailSourceError("endpoint_not_allowed")
        if canonical == "messages/{id}":
            fmt = _format_param(params)
            if fmt not in ALLOWED_MESSAGE_FORMATS:
                raise EmailSourceError("endpoint_not_allowed")

        delays = _RETRY_DELAYS_S[: max(self.retries, 0)]
        last_error: EmailSourceError | None = None
        for delay in (*delays, None):
            try:
                return self._once(method, path, params)
            except EmailSourceUnavailable as exc:
                last_error = exc
                if str(exc) not in _RETRYABLE_REASONS or delay is None:
                    raise
                time.sleep(delay)
            except EmailSourceError:
                raise
        raise last_error or EmailSourceUnavailable("server")

    def _once(self, method: str, path: str, params) -> dict:
        headers = {"Authorization": f"Bearer {self.token_provider.access_token()}"}
        url = urljoin(self.base_url, path)
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.request(method, url, headers=headers, params=params)
        except (httpx.TimeoutException, TimeoutError, httpx.ConnectError) as exc:
            raise EmailSourceUnavailable("timeout") from exc
        except EmailSourceError:
            raise
        except Exception as exc:
            raise EmailSourceUnavailable("server") from exc

        if response.status_code == 200:
            try:
                payload = response.json()
            except Exception as exc:
                raise EmailSourceUnavailable("server") from exc
            return payload if isinstance(payload, dict) else {"value": payload}

        mapped = map_http_status(response.status_code, response.text)
        if mapped is not None:
            raise mapped
        raise EmailSourceUnavailable("server")
