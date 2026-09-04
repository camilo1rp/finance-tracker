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
from app.integrations.gmail_common.query import build_search_query, redact_quoted_phrases
from app.integrations.gmail_common.text import parse_sender
from app.integrations.gmail_rest.mapping import full_to_message, metadata_to_ref
from app.services.enrichment_service import _redact_enrichment_value


def _source_trace_inputs(inputs: dict) -> dict:
    redacted = {
        key: _redact_enrichment_value(value)
        for key, value in inputs.items()
        if key not in {"self", "client"}
    }
    query = inputs.get("query")
    if isinstance(query, EmailQuery):
        redacted["gmail_query"] = redact_quoted_phrases(build_search_query(query))
    return redacted


def _source_trace_outputs(outputs):
    return _redact_enrichment_value(outputs)


def _mask_email(address: str) -> str:
    if "@" not in address:
        return address
    local, domain = address.split("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


class GmailRestEmailSource(AllowlistedEmailSource):
    def __init__(
        self,
        client,
        allowlist: list[str],
        byte_cap: int,
        page_cap: int = 2,
        provider_name: str = "gmail_rest",
    ) -> None:
        super().__init__(allowlist)
        self.client = client
        self.byte_cap = byte_cap
        self.page_cap = page_cap
        self.provider_name = provider_name
        self.provider = provider_name

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
        page_size = min(query.max_results, 50)
        for page_index in range(self.page_cap):
            result = self.client.list_messages(gmail_query, page_size, page_token)
            for item in result.get("messages") or []:
                message_id = item.get("id") if isinstance(item, dict) else None
                if not message_id:
                    continue
                try:
                    meta = self.client.get_message_metadata(str(message_id))
                except EmailSourceError as exc:
                    if str(exc) == "not_found":
                        continue
                    raise
                ref = metadata_to_ref(meta, window)
                if ref is None:
                    continue
                if not sender_allowed(self.allowlist, ref.sender):
                    continue
                collected.append(ref)
                if len(collected) >= query.max_results:
                    break
            if len(collected) >= query.max_results:
                break
            page_token = result.get("nextPageToken") or None
            if not page_token or page_index >= self.page_cap - 1:
                break
        collected.sort(key=lambda item: item.received_at, reverse=True)
        return collected[: query.max_results]

    def _fetch(self, ref: EmailRef) -> EmailMessage:
        message = self.client.get_message_full(ref.message_id)
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        headers = payload.get("headers") or []
        from_value = None
        for item in headers:
            if isinstance(item, dict) and str(item.get("name") or "").lower() == "from":
                from_value = item.get("value")
                break
        returned = parse_sender(from_value if from_value is not None else "")
        expected = parse_sender(ref.sender) or ref.sender.strip().lower()
        if (
            not returned
            or not sender_allowed(self.allowlist, returned)
            or returned != expected
        ):
            raise EmailSourceError("sender_mismatch")
        return full_to_message(message, ref, self.byte_cap)

    @traceable(process_inputs=_source_trace_inputs, process_outputs=_source_trace_outputs)
    def health(self) -> SourceStatus:
        try:
            profile = self.client.get_profile()
        except EmailSourceUnavailable as exc:
            return SourceStatus(
                available=False,
                provider=self.provider_name,
                account_hint=None,
                detail=str(exc),
            )
        address = str(profile.get("emailAddress") or "")
        hint = _mask_email(address) if address else None
        return SourceStatus(
            available=True,
            provider=self.provider_name,
            account_hint=hint,
        )
