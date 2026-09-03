from app.domain.classification import NormalizationKind, NormalizationLookup
from app.domain.email_source import (
    AllowlistedEmailSource,
    EmailMessage,
    EmailQuery,
    EmailRef,
    EmailSourceError,
    EmailSourceUnavailable,
    SourceStatus,
    sender_allowed,
    truncate_text_bytes,
)
from app.domain.receipts import ReceiptExtraction, ReceiptExtractor
from app.integrations.gmail_mcp.transport import ALLOWED_TOOLS, McpTransport


class InMemoryNormalizationLookup(NormalizationLookup):
    """Account-specific rules beat global ones. Keys are already-cleaned values.

    Merchant-scoped category rules (cleaned merchant as the last key part)
    beat the matching unscoped rule at the same account/global level.
    """

    def __init__(
        self,
        global_rules: dict[tuple[NormalizationKind, str], str] | None = None,
        account_rules: dict[tuple[int, NormalizationKind, str], str] | None = None,
        global_merchant_rules: dict[tuple[NormalizationKind, str, str], str]
        | None = None,
        account_merchant_rules: dict[tuple[int, NormalizationKind, str, str], str]
        | None = None,
    ) -> None:
        self.global_rules = global_rules or {}
        self.account_rules = account_rules or {}
        self.global_merchant_rules = global_merchant_rules or {}
        self.account_merchant_rules = account_merchant_rules or {}

    def resolve(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int,
        merchant: str | None = None,
    ) -> str | None:
        if kind is NormalizationKind.CATEGORY and merchant is not None:
            hit = self.account_merchant_rules.get(
                (account_id, kind, raw_value, merchant)
            )
            if hit is not None:
                return hit
        account_hit = self.account_rules.get((account_id, kind, raw_value))
        if account_hit is not None:
            return account_hit
        if kind is NormalizationKind.CATEGORY and merchant is not None:
            hit = self.global_merchant_rules.get((kind, raw_value, merchant))
            if hit is not None:
                return hit
        return self.global_rules.get((kind, raw_value))


class FakeEmailSource(AllowlistedEmailSource):
    def __init__(
        self,
        messages: list[EmailMessage],
        allowlist: list[str],
        *,
        status: SourceStatus | None = None,
        byte_cap: int = 65536,
        raise_unavailable: bool = False,
    ) -> None:
        super().__init__(allowlist)
        self._messages = {message.ref.message_id: message for message in messages}
        self._status = status or SourceStatus(available=True, provider="fake")
        self.provider = self._status.provider
        self._byte_cap = byte_cap
        self.raise_unavailable = raise_unavailable
        self.search_calls = 0
        self.fetch_calls = 0

    def _search(self, query: EmailQuery) -> list[EmailRef]:
        self.search_calls += 1
        if self.raise_unavailable:
            raise EmailSourceUnavailable("fake unavailable")
        hints = [hint.strip().lower() for hint in query.text_hints if hint.strip()]
        results: list[EmailRef] = []
        for message in sorted(
            self._messages.values(), key=lambda item: item.ref.received_at, reverse=True
        ):
            ref = message.ref
            if not (query.date_from <= ref.received_at.date() <= query.date_to):
                continue
            if not any(sender_allowed([sender], ref.sender) for sender in query.senders):
                continue
            haystack = f"{ref.subject}\n{message.body_text}".lower()
            if hints and not all(hint in haystack for hint in hints):
                continue
            results.append(ref)
            if len(results) >= query.max_results:
                break
        return results

    def _fetch(self, ref: EmailRef) -> EmailMessage:
        self.fetch_calls += 1
        if self.raise_unavailable:
            raise EmailSourceUnavailable("fake unavailable")
        message = self._messages.get(ref.message_id)
        if message is None:
            raise EmailSourceError("message not found")
        if message.ref.sender != ref.sender:
            raise EmailSourceError("fetched message sender mismatch")
        body_text, truncated = truncate_text_bytes(message.body_text, self._byte_cap)
        return EmailMessage(
            ref=message.ref,
            body_text=body_text,
            headers=dict(message.headers),
            attachments=list(message.attachments),
            truncated=message.truncated or truncated,
        )

    def health(self) -> SourceStatus:
        return self._status


class FakeMcpTransport(McpTransport):
    def __init__(
        self,
        responses: dict[str, list[dict]],
        fail_with: Exception | None = None,
        tools: list[str] | None = None,
    ) -> None:
        self.responses = {name: list(payloads) for name, payloads in responses.items()}
        self.fail_with = fail_with
        self.calls: list[tuple[str, dict]] = []
        self.tools = list(tools) if tools is not None else sorted(ALLOWED_TOOLS)

    def list_tools(self) -> list[str]:
        if self.fail_with is not None:
            raise self.fail_with
        return list(self.tools)

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in ALLOWED_TOOLS:
            raise EmailSourceError("tool_not_allowed")
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append((name, dict(arguments)))
        queue = self.responses.setdefault(name, [])
        if not queue:
            return {}
        return queue.pop(0)


class FakeExtractor(ReceiptExtractor):
    def __init__(
        self,
        by_message_id: dict[str, ReceiptExtraction],
        default: ReceiptExtraction | None = None,
    ) -> None:
        self.by_message_id = dict(by_message_id)
        self.default = default

    def extract(
        self, message: EmailMessage, known_categories: list[str]
    ) -> ReceiptExtraction:
        del known_categories
        hit = self.by_message_id.get(message.ref.message_id)
        if hit is not None:
            return hit
        if self.default is not None:
            return self.default
        raise KeyError(message.ref.message_id)
