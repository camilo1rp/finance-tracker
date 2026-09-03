from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from fnmatch import fnmatch
import json
from pathlib import Path


@dataclass(frozen=True)
class EmailQuery:
    senders: list[str]
    date_from: date
    date_to: date
    text_hints: list[str] = field(default_factory=list)
    max_results: int = 10


@dataclass(frozen=True)
class EmailRef:
    message_id: str
    thread_id: str | None
    sender: str
    subject: str
    received_at: datetime
    snippet: str


@dataclass(frozen=True)
class AttachmentRef:
    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class EmailMessage:
    ref: EmailRef
    body_text: str
    headers: dict[str, str]
    attachments: list[AttachmentRef] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class SourceStatus:
    available: bool
    provider: str
    account_hint: str | None = None
    detail: str | None = None


class EmailSourceError(RuntimeError):
    pass


class EmailSourceUnavailable(EmailSourceError):
    pass


class SenderNotAllowed(EmailSourceError):
    pass


def parse_allowlist(raw: str) -> list[str]:
    tokens = [part.strip().lower() for part in raw.split(",")]
    tokens = [token for token in tokens if token]
    if tokens == ["*"]:
        return ["*"]
    return tokens


def sender_allowed(patterns: list[str], address: str) -> bool:
    if not patterns:
        return False
    lowered = address.strip().lower()
    for pattern in patterns:
        if pattern == "*":
            return True
        if "*" in pattern:
            if fnmatch(lowered, pattern.lower()):
                return True
            continue
        if "@" in pattern:
            if lowered == pattern.lower():
                return True
            continue
        if "@" not in lowered:
            continue
        _local, domain = lowered.rsplit("@", 1)
        wanted = pattern.lower()
        if domain == wanted or domain.endswith(f".{wanted}"):
            return True
    return False


def truncate_text_bytes(text: str, byte_cap: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_cap:
        return text, False
    trimmed = encoded[:byte_cap]
    while True:
        try:
            return trimmed.decode("utf-8"), True
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]


def _requested_sender_might_match_allowlist(requested: str, allowlist: list[str]) -> bool:
    requested = requested.strip().lower()
    if not requested:
        return False
    if "*" in allowlist:
        return True
    if requested == "*":
        return False
    if "*" in requested:
        return any(
            requested == allowed
            or fnmatch(allowed, requested)
            or fnmatch(f"user@{allowed}", requested)
            or fnmatch(f"user@sub.{allowed}", requested)
            for allowed in allowlist
        )
    if "@" in requested:
        return sender_allowed(allowlist, requested)
    return sender_allowed(allowlist, f"user@{requested}") or sender_allowed(
        allowlist, f"user@sub.{requested}"
    )


class EmailSource(ABC):
    @abstractmethod
    def search(self, query: EmailQuery) -> list[EmailRef]:
        raise NotImplementedError

    @abstractmethod
    def fetch(self, ref: EmailRef) -> EmailMessage:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> SourceStatus:
        raise NotImplementedError


class AllowlistedEmailSource(EmailSource, ABC):
    def __init__(self, allowlist: list[str]) -> None:
        self.allowlist = [item.lower() for item in allowlist]

    def _intersect_senders(self, senders: list[str]) -> list[str]:
        if "*" in self.allowlist:
            return [sender.strip().lower() for sender in senders if sender.strip()]
        kept = [
            sender.strip().lower()
            for sender in senders
            if _requested_sender_might_match_allowlist(sender, self.allowlist)
        ]
        if not kept:
            return []
        return kept

    def search(self, query: EmailQuery) -> list[EmailRef]:
        senders = self._intersect_senders(query.senders)
        if not senders:
            return []
        refs = self._search(
            EmailQuery(
                senders=senders,
                date_from=query.date_from,
                date_to=query.date_to,
                text_hints=list(query.text_hints),
                max_results=query.max_results,
            )
        )
        return [ref for ref in refs if sender_allowed(self.allowlist, ref.sender)]

    def fetch(self, ref: EmailRef) -> EmailMessage:
        if not sender_allowed(self.allowlist, ref.sender):
            raise SenderNotAllowed(f"sender not allowed: {ref.sender}")
        message = self._fetch(ref)
        if not sender_allowed(self.allowlist, message.ref.sender):
            raise EmailSourceError("fetched message sender not allowed")
        if message.ref.message_id != ref.message_id:
            raise EmailSourceError("fetched message id mismatch")
        return message

    @abstractmethod
    def _search(self, query: EmailQuery) -> list[EmailRef]:
        raise NotImplementedError

    @abstractmethod
    def _fetch(self, ref: EmailRef) -> EmailMessage:
        raise NotImplementedError


class FixtureEmailSource(AllowlistedEmailSource):
    def __init__(
        self,
        messages: list[EmailMessage],
        allowlist: list[str],
        *,
        provider: str = "fake",
        byte_cap: int = 65536,
    ) -> None:
        super().__init__(allowlist)
        self._messages = {message.ref.message_id: message for message in messages}
        self._provider = provider
        self._byte_cap = byte_cap

    @classmethod
    def from_json_fixture(
        cls, path: str, allowlist: list[str], *, byte_cap: int = 65536
    ) -> "FixtureEmailSource":
        payload = json.loads(Path(path).read_text())
        return cls(
            [email_message_from_dict(item, byte_cap=byte_cap) for item in payload],
            allowlist,
            byte_cap=byte_cap,
        )

    def _search(self, query: EmailQuery) -> list[EmailRef]:
        lowered_hints = [hint.strip().lower() for hint in query.text_hints if hint.strip()]
        results: list[EmailRef] = []
        for message in sorted(
            self._messages.values(), key=lambda item: item.ref.received_at, reverse=True
        ):
            ref = message.ref
            if not (query.date_from <= ref.received_at.date() <= query.date_to):
                continue
            if not any(sender_allowed([pattern], ref.sender) for pattern in query.senders):
                continue
            haystack = f"{ref.subject}\n{message.body_text}".lower()
            if lowered_hints and not all(hint in haystack for hint in lowered_hints):
                continue
            results.append(ref)
            if len(results) >= query.max_results:
                break
        return results

    def _fetch(self, ref: EmailRef) -> EmailMessage:
        message = self._messages.get(ref.message_id)
        if message is None:
            raise EmailSourceError("message not found")
        if message.ref.sender.strip().lower() != ref.sender.strip().lower():
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
        return SourceStatus(available=True, provider=self._provider)


def email_message_from_dict(data: dict, *, byte_cap: int = 65536) -> EmailMessage:
    ref_data = data["ref"]
    ref = EmailRef(
        message_id=ref_data["message_id"],
        thread_id=ref_data.get("thread_id"),
        sender=ref_data["sender"].strip().lower(),
        subject=ref_data["subject"],
        received_at=datetime.fromisoformat(ref_data["received_at"]),
        snippet=str(ref_data.get("snippet", ""))[:200],
    )
    headers = {
        key.lower(): value
        for key, value in dict(data.get("headers") or {}).items()
        if key.lower() in {"from", "to", "date", "subject", "message-id"}
    }
    attachments = [
        AttachmentRef(
            attachment_id=item["attachment_id"],
            filename=item["filename"],
            mime_type=item["mime_type"],
            size_bytes=int(item["size_bytes"]),
        )
        for item in data.get("attachments") or []
    ]
    body_text, truncated = truncate_text_bytes(str(data.get("body_text", "")), byte_cap)
    return EmailMessage(
        ref=ref,
        body_text=body_text,
        headers=headers,
        attachments=attachments,
        truncated=bool(data.get("truncated")) or truncated,
    )
