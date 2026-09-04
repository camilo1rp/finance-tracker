from __future__ import annotations

from langsmith import traceable

from app.domain.email_source import (
    AllowlistedEmailSource,
    EmailMessage,
    EmailQuery,
    EmailRef,
    EmailSourceError,
    EmailSourceUnavailable,
    SourceStatus,
    sender_allowed,
)
from app.integrations.gmail_mcp.mapping import (
    build_search_query,
    message_to_email,
    normalize_sender,
    redact_quoted_phrases,
    thread_to_refs,
)
from app.integrations.gmail_mcp.transport import ALLOWED_TOOLS, McpTransport
from app.services.enrichment_service import _redact_enrichment_value


def _source_trace_inputs(inputs: dict) -> dict:
    redacted = {
        key: _redact_enrichment_value(value)
        for key, value in inputs.items()
        if key not in {"self", "transport"}
    }
    query = inputs.get("query")
    if isinstance(query, EmailQuery):
        redacted["gmail_query"] = redact_quoted_phrases(build_search_query(query))
    return redacted


def _source_trace_outputs(outputs):
    return _redact_enrichment_value(outputs)


class McpEmailSource(AllowlistedEmailSource):
    def __init__(
        self,
        transport: McpTransport,
        allowlist: list[str],
        byte_cap: int,
        page_cap: int = 2,
    ) -> None:
        super().__init__(allowlist)
        self.transport = transport
        self.byte_cap = byte_cap
        self.page_cap = page_cap
        self.provider_name = "gmail"
        self.provider = self.provider_name

    @traceable(process_inputs=_source_trace_inputs, process_outputs=_source_trace_outputs)
    def search(self, query: EmailQuery) -> list[EmailRef]:
        return super().search(query)

    @traceable(process_inputs=_source_trace_inputs, process_outputs=_source_trace_outputs)
    def fetch(self, ref: EmailRef) -> EmailMessage:
        return super().fetch(ref)

    def _search(self, query: EmailQuery) -> list[EmailRef]:
        gmail_query = build_search_query(query)
        collected: list[EmailRef] = []
        page_token: str | None = None
        window = (query.date_from, query.date_to)
        for page_index in range(self.page_cap):
            arguments: dict = {
                "query": gmail_query,
                "pageSize": min(query.max_results, 50),
                "view": "THREAD_VIEW_MINIMAL",
            }
            if page_token:
                arguments["pageToken"] = page_token
            result = self.transport.call_tool("search_threads", arguments)
            for thread in result.get("threads") or []:
                collected.extend(thread_to_refs(thread, window))
            collected = [ref for ref in collected if sender_allowed(self.allowlist, ref.sender)]
            if len(collected) >= query.max_results:
                break
            page_token = result.get("nextPageToken") or None
            if not page_token or page_index >= self.page_cap - 1:
                break
        collected.sort(key=lambda item: item.received_at, reverse=True)
        return collected[: query.max_results]

    def _fetch(self, ref: EmailRef) -> EmailMessage:
        message = self.transport.call_tool(
            "get_message",
            {"messageId": ref.message_id, "messageFormat": "FULL_CONTENT"},
        )
        returned = normalize_sender(str(message.get("sender") or message.get("from") or ""))
        expected = normalize_sender(ref.sender)
        if (
            not returned
            or not sender_allowed(self.allowlist, returned)
            or returned != expected
        ):
            raise EmailSourceError("sender_mismatch")
        return message_to_email(message, ref, self.byte_cap)

    @traceable(process_inputs=_source_trace_inputs, process_outputs=_source_trace_outputs)
    def health(self) -> SourceStatus:
        try:
            self.transport.call_tool("list_labels", {})
        except EmailSourceUnavailable as exc:
            return SourceStatus(
                available=False,
                provider="gmail",
                account_hint=None,
                detail=str(exc),
            )
        return SourceStatus(available=True, provider="gmail", account_hint=None)

    def verify_tools(self) -> None:
        names = set(self.transport.list_tools())
        if not ALLOWED_TOOLS.issubset(names):
            raise EmailSourceUnavailable("tools_missing")
