from dataclasses import dataclass

from app.domain.classification import (
    NormalizationKind,
    NormalizationLookup,
    allows_merchant_scope,
    merchant_scope_matches,
    pattern_matches,
    pattern_rank_key,
    pick_best_pattern_match,
)
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


@dataclass(frozen=True)
class _FakeRule:
    kind: NormalizationKind
    raw_value: str | None
    canonical_value: str
    account_id: int | None
    merchant: str | None


class InMemoryNormalizationLookup(NormalizationLookup):
    """Account-specific rules beat global ones. Keys/patterns are already cleaned.

    Merchant-scoped category and transaction_type rules (cleaned merchant
    pattern as the last field) beat the matching unscoped rule at the same
    account/global level. Patterns may include ``%`` wildcards.
    """

    def __init__(
        self,
        global_rules: dict[tuple[NormalizationKind, str], str] | None = None,
        account_rules: dict[tuple[int, NormalizationKind, str], str] | None = None,
        global_merchant_rules: dict[tuple[NormalizationKind, str | None, str], str]
        | None = None,
        account_merchant_rules: dict[tuple[int, NormalizationKind, str | None, str], str]
        | None = None,
    ) -> None:
        self._rules: list[_FakeRule] = []
        for (kind, raw_value), canonical in (global_rules or {}).items():
            self._rules.append(
                _FakeRule(kind, raw_value, canonical, None, None)
            )
        for (account_id, kind, raw_value), canonical in (
            account_rules or {}
        ).items():
            self._rules.append(
                _FakeRule(kind, raw_value, canonical, account_id, None)
            )
        for (kind, raw_value, merchant), canonical in (
            global_merchant_rules or {}
        ).items():
            self._rules.append(
                _FakeRule(kind, raw_value, canonical, None, merchant)
            )
        for (account_id, kind, raw_value, merchant), canonical in (
            account_merchant_rules or {}
        ).items():
            self._rules.append(
                _FakeRule(kind, raw_value, canonical, account_id, merchant)
            )

    def resolve(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int,
        merchant: str | None = None,
    ) -> str | None:
        if allows_merchant_scope(kind) and merchant is not None:
            hit = self._best_merchant_scoped(kind, raw_value, account_id, merchant)
            if hit is not None:
                return hit.canonical_value
        hit = self._best_unscoped(kind, raw_value, account_id)
        if hit is not None:
            return hit.canonical_value
        if allows_merchant_scope(kind) and merchant is not None:
            hit = self._best_merchant_scoped(kind, raw_value, None, merchant)
            if hit is not None:
                return hit.canonical_value
        hit = self._best_unscoped(kind, raw_value, None)
        if hit is not None:
            return hit.canonical_value
        return None

    def resolve_empty_category(self, account_id: int, merchant: str) -> str | None:
        hit = self._best_empty_category_merchant(account_id, merchant)
        if hit is not None:
            return hit.canonical_value
        hit = self._best_empty_category_merchant(None, merchant)
        if hit is not None:
            return hit.canonical_value
        return None

    def _rules_at_scope(
        self,
        kind: NormalizationKind,
        account_id: int | None,
        *,
        merchant_scoped: bool,
    ) -> list[_FakeRule]:
        return [
            rule
            for rule in self._rules
            if rule.kind is kind
            and rule.account_id == account_id
            and (
                (rule.merchant is not None)
                if merchant_scoped
                else rule.merchant is None
            )
        ]

    def _best_unscoped(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int | None,
    ) -> _FakeRule | None:
        rules = [
            rule
            for rule in self._rules_at_scope(kind, account_id, merchant_scoped=False)
            if rule.raw_value is not None
        ]
        return pick_best_pattern_match(rules, raw_value, lambda rule: rule.raw_value)

    def _best_merchant_scoped(
        self,
        kind: NormalizationKind,
        raw_value: str,
        account_id: int | None,
        row_merchant: str,
    ) -> _FakeRule | None:
        rules = self._rules_at_scope(kind, account_id, merchant_scoped=True)
        matches = [
            rule
            for rule in rules
            if rule.raw_value is not None
            and pattern_matches(raw_value, rule.raw_value)
            and rule.merchant is not None
            and merchant_scope_matches(row_merchant, rule.merchant, kind)
        ]
        if not matches:
            return None
        return min(
            matches,
            key=lambda rule: (
                pattern_rank_key(rule.raw_value),
                pattern_rank_key(rule.merchant or ""),
            ),
        )

    def _best_empty_category_merchant(
        self,
        account_id: int | None,
        row_merchant: str,
    ) -> _FakeRule | None:
        rules = self._rules_at_scope(
            NormalizationKind.CATEGORY, account_id, merchant_scoped=True
        )
        matches = [
            rule
            for rule in rules
            if rule.raw_value is None
            and rule.merchant is not None
            and merchant_scope_matches(
                row_merchant, rule.merchant, NormalizationKind.CATEGORY
            )
        ]
        if not matches:
            return None
        return min(matches, key=lambda rule: pattern_rank_key(rule.merchant or ""))


class FakeEmailSource(AllowlistedEmailSource):
    def __init__(
        self,
        messages: list[EmailMessage],
        allowlist: list[str],
        *,
        status: SourceStatus | None = None,
        byte_cap: int = 65536,
        raise_unavailable: bool = False,
        provider_name: str | None = None,
    ) -> None:
        super().__init__(allowlist)
        self._messages = {message.ref.message_id: message for message in messages}
        self._status = status or SourceStatus(available=True, provider="fake")
        self.provider_name = provider_name if provider_name is not None else self._status.provider
        self.provider = self.provider_name
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
            body_source=message.body_source,
            plain_bytes=message.plain_bytes,
            html_text_bytes=message.html_text_bytes,
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


class FakeGmailRestClient:
    _KNOWN = frozenset(
        {"list_messages", "get_message_metadata", "get_message_full", "get_profile"}
    )

    def __init__(self, responses: dict[str, list[dict | Exception]]) -> None:
        self.responses = {name: list(payloads) for name, payloads in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def _unknown(*_args, **_kwargs):
            raise EmailSourceError("endpoint_not_allowed")

        return _unknown

    def _dispatch(self, name: str, **params):
        if name not in self._KNOWN:
            raise EmailSourceError("endpoint_not_allowed")
        self.calls.append((name, dict(params)))
        queue = self.responses.setdefault(name, [])
        if not queue:
            return {}
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def list_messages(self, q: str, max_results: int, page_token: str | None) -> dict:
        return self._dispatch(
            "list_messages", q=q, max_results=max_results, page_token=page_token
        )

    def get_message_metadata(self, message_id: str) -> dict:
        return self._dispatch("get_message_metadata", id=message_id)

    def get_message_full(self, message_id: str) -> dict:
        return self._dispatch("get_message_full", id=message_id)

    def get_profile(self) -> dict:
        return self._dispatch("get_profile")
