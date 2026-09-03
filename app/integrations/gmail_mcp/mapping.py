from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from html.parser import HTMLParser
import html
import re

from app.domain.email_source import (
    AttachmentRef,
    EmailMessage,
    EmailQuery,
    EmailRef,
    truncate_text_bytes,
)

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "thead",
    "tbody",
    "blockquote",
    "pre",
    "hr",
    "section",
    "article",
    "header",
    "footer",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
}
_SKIP_TAGS = {"script", "style", "head"}
_QUOTED_PHRASE = re.compile(r'"[^"]*"')


def normalize_sender(raw: str) -> str:
    _name, address = parseaddr(raw)
    return (address or raw).strip().lower()


def redact_quoted_phrases(query: str) -> str:
    return _QUOTED_PHRASE.sub('"<redacted>"', query)


def _gmail_date(value: date) -> str:
    return value.strftime("%Y/%m/%d")


def _from_clause(pattern: str) -> str | None:
    token = pattern.strip().lower()
    if not token or token == "*":
        return None
    if "*" not in token:
        return f"from:{token}"
    if token.startswith("*@*."):
        domain = token[4:]
        if domain and "*" not in domain:
            return f"from:{domain}"
        return None
    if token.startswith("*@"):
        domain = token[2:]
        if domain and "*" not in domain:
            return f"from:{domain}"
        return None
    return None


def build_search_query(query: EmailQuery) -> str:
    parts: list[str] = []
    senders = [item.strip().lower() for item in query.senders if item.strip()]
    if senders != ["*"]:
        clauses = [clause for sender in senders if (clause := _from_clause(sender))]
        if len(clauses) == 1:
            parts.append(clauses[0])
        elif len(clauses) >= 2:
            parts.append("{" + " ".join(clauses) + "}")
    parts.append(f"after:{_gmail_date(query.date_from - timedelta(days=1))}")
    parts.append(f"before:{_gmail_date(query.date_to + timedelta(days=1))}")
    hints = [hint.strip() for hint in query.text_hints if hint.strip()]
    if hints:
        quoted = [f'"{hint}"' for hint in hints]
        if len(quoted) == 1:
            parts.append(quoted[0])
        else:
            parts.append("{" + " ".join(quoted) + "}")
    parts.append("-in:draft")
    return " ".join(parts)


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


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag in _BLOCK_TAGS and tag != "br":
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)


def html_to_text(html_str: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html_str)
    parser.close()
    text = html.unescape("".join(parser._chunks))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
