from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.domain.email_source import EmailSourceError, EmailSourceUnavailable
from app.integrations.gmail_common.auth import TokenProvider
from app.integrations.gmail_common.errors import map_http_status

ALLOWED_TOOLS = frozenset({"search_threads", "get_message", "list_labels"})
_RETRY_DELAYS_S = (0.5, 2.0)


class McpTransport(ABC):
    @abstractmethod
    def call_tool(self, name: str, arguments: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    def list_tools(self) -> list[str]:
        raise NotImplementedError


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return _http_status(cause)
    return None


def classify_transport_error(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout", False
    if isinstance(exc, httpx.ConnectError):
        return "timeout", False
    status = _http_status(exc)
    if status is None:
        return "server", False
    mapped = map_http_status(status, None)
    if isinstance(mapped, EmailSourceUnavailable):
        reason = str(mapped)
        return reason, reason in {"rate_limited", "server"}
    return "server", False


def tool_result_as_dict(result: Any) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return dict(structured)
    texts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            texts.append(text)
    joined = "\n".join(texts).strip()
    if not joined:
        return {}
    try:
        parsed = json.loads(joined)
    except json.JSONDecodeError:
        return {"text": joined}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _tool_brief(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or {}
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    properties = schema.get("properties") or {}
    return {"name": tool.name, "input_schema_keys": list(properties.keys())}


async def _invoke_mcp_async(
    url: str,
    headers: dict[str, str],
    timeout_s: float,
    operation: Callable[[ClientSession], Awaitable[Any]],
) -> Any:
    async with streamablehttp_client(url, headers=headers, timeout=timeout_s) as streams:
        read, write, _session_id = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await operation(session)


def invoke_mcp(
    url: str,
    headers: dict[str, str],
    timeout_s: float,
    operation: Callable[[ClientSession], Awaitable[Any]],
) -> Any:
    return asyncio.run(_invoke_mcp_async(url, headers, timeout_s, operation))


class StreamableHttpMcpTransport(McpTransport):
    def __init__(self, url: str, token_provider: TokenProvider, timeout_s: float) -> None:
        self.url = url
        self.token_provider = token_provider
        self.timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        token = self.token_provider.access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }

    def _retrying(self, fn):
        last_error: EmailSourceUnavailable | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS_S, None)):
            try:
                return fn()
            except EmailSourceError:
                raise
            except EmailSourceUnavailable:
                raise
            except Exception as exc:
                reason, retryable = classify_transport_error(exc)
                last_error = EmailSourceUnavailable(reason)
                if not retryable or delay is None:
                    raise last_error from exc
                time.sleep(delay)
        raise last_error or EmailSourceUnavailable("server")

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in ALLOWED_TOOLS:
            raise EmailSourceError("tool_not_allowed")

        def once() -> Any:
            async def operation(session: ClientSession) -> Any:
                return await session.call_tool(name, arguments)

            return invoke_mcp(self.url, self._headers(), self.timeout_s, operation)

        result = self._retrying(once)
        if getattr(result, "isError", False):
            raise EmailSourceError(name)
        return tool_result_as_dict(result)

    def list_tools_detailed(self) -> list[dict[str, Any]]:
        def once() -> list[dict[str, Any]]:
            async def operation(session: ClientSession) -> list[dict[str, Any]]:
                listed = await session.list_tools()
                return [_tool_brief(tool) for tool in listed.tools]

            return invoke_mcp(self.url, self._headers(), self.timeout_s, operation)

        return self._retrying(once)

    def list_tools(self) -> list[str]:
        return [str(item["name"]) for item in self.list_tools_detailed()]
