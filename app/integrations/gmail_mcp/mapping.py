from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parseaddr

from app.domain.email_source import (
    AttachmentRef,
    EmailMessage,
    EmailRef,
    truncate_text_bytes,
)
from app.integrations.gmail_common.query import build_search_query, redact_quoted_phrases
from app.integrations.gmail_common.text import html_to_text

__all__ = [
    "build_search_query",
    "html_to_text",
    "message_to_email",
    "normalize_sender",
    "redact_quoted_phrases",
    "thread_to_refs",
]


def normalize_sender(raw: str) -> str:
    _name, address = parseaddr(raw)
    return (address or raw).strip().lower()


def _parse_message_date(raw: object) -> date | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return date.fromisoformat(text[:10])
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def thread_to_refs(thread: dict, window: tuple[date, date]) -> list[EmailRef]:
    date_from, date_to = window
    thread_id = str(thread.get("id") or "") or None
    refs: list[EmailRef] = []
    for message in thread.get("messages") or []:
        raw_sender = message.get("sender") or message.get("from") or ""
        sender = normalize_sender(str(raw_sender))
        received_on = _parse_message_date(message.get("date"))
        message_id = message.get("id")
        if not sender or "@" not in sender or received_on is None or not message_id:
            continue
        if received_on < date_from or received_on > date_to:
            continue
        refs.append(
            EmailRef(
                message_id=str(message_id),
                thread_id=thread_id,
                sender=sender,
                subject=str(message.get("subject") or ""),
                received_at=datetime(received_on.year, received_on.month, received_on.day, tzinfo=timezone.utc),
                snippet=str(message.get("snippet") or "")[:200],
                received_at_precision="date",
            )
        )
    return refs


def message_to_email(msg: dict, ref: EmailRef, byte_cap: int) -> EmailMessage:
    plaintext = str(msg.get("plaintextBody") or "")
    if plaintext.strip():
        body = plaintext
    else:
        html_body = str(msg.get("htmlBody") or "")
        body = html_to_text(html_body) if html_body.strip() else ""
    body_text, truncated = truncate_text_bytes(body, byte_cap)
    headers: dict[str, str] = {}
    sender = msg.get("sender") or msg.get("from")
    if sender:
        headers["from"] = str(sender)
    recipients = msg.get("toRecipients") or []
    if recipients:
        headers["to"] = ", ".join(str(item) for item in recipients)
    if msg.get("date"):
        headers["date"] = str(msg["date"])
    if msg.get("subject"):
        headers["subject"] = str(msg["subject"])
    message_id = str(msg.get("id") or ref.message_id)
    attachments: list[AttachmentRef] = []
    for attachment in msg.get("attachments") or []:
        attachment_id = attachment.get("id")
        if not attachment_id:
            continue
        attachments.append(
            AttachmentRef(
                attachment_id=f"{message_id}:{attachment_id}",
                filename=str(attachment.get("filename") or ""),
                mime_type=str(attachment.get("mimeType") or ""),
                size_bytes=None,
            )
        )
    return EmailMessage(
        ref=ref,
        body_text=body_text,
        headers=headers,
        attachments=attachments,
        truncated=truncated,
    )
