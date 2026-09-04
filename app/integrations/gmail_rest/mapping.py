from __future__ import annotations

import base64
from datetime import date, datetime, timezone

from app.domain.email_source import AttachmentRef, EmailMessage, EmailRef
from app.integrations.gmail_common.text import (
    html_to_text,
    normalize_headers,
    parse_sender,
    truncate_utf8,
)


def _payload(msg: dict) -> dict:
    payload = msg.get("payload")
    return payload if isinstance(payload, dict) else {}


def _header_pairs(payload: dict) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in payload.get("headers") or []:
        if not isinstance(item, dict):
            continue
        pairs.append((str(item.get("name") or ""), str(item.get("value") or "")))
    return pairs


def _headers_lookup(payload: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name, value in _header_pairs(payload):
        key = name.lower()
        if key not in lookup:
            lookup[key] = value
    return lookup


def _received_at(msg: dict) -> datetime | None:
    raw = msg.get("internalDate")
    if raw is None or raw == "":
        return None
    try:
        millis = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def metadata_to_ref(msg: dict, window: tuple[date, date] | None) -> EmailRef | None:
    message_id = msg.get("id")
    if not message_id:
        return None
    headers = _headers_lookup(_payload(msg))
    sender = parse_sender(headers.get("from"))
    if sender is None:
        return None
    received_at = _received_at(msg)
    if received_at is None:
        return None
    if window is not None:
        date_from, date_to = window
        received_on = received_at.date()
        if received_on < date_from or received_on > date_to:
            return None
    thread_id = msg.get("threadId")
    return EmailRef(
        message_id=str(message_id),
        thread_id=str(thread_id) if thread_id else None,
        sender=sender,
        subject=str(headers.get("subject") or ""),
        received_at=received_at,
        snippet=str(msg.get("snippet") or "")[:200],
        received_at_precision="datetime",
    )


def _decode_b64url(data: str) -> str:
    padded = data + "=" * ((4 - len(data) % 4) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    return raw.decode("utf-8", errors="replace")


def _part_is_attachment(part: dict) -> bool:
    filename = part.get("filename")
    if isinstance(filename, str) and filename.strip():
        return True
    for header in part.get("headers") or []:
        if not isinstance(header, dict):
            continue
        if str(header.get("name") or "").lower() != "content-disposition":
            continue
        disposition = str(header.get("value") or "").split(";", 1)[0].strip().lower()
        if disposition == "attachment":
            return True
    return False


def _walk_parts(part: dict, *, skip_attachments: bool):
    if skip_attachments and _part_is_attachment(part):
        return
    yield part
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            yield from _walk_parts(child, skip_attachments=skip_attachments)


def _part_body_text(part: dict) -> str:
    body = part.get("body")
    if not isinstance(body, dict):
        return ""
    data = body.get("data")
    if not data:
        return ""
    return _decode_b64url(str(data))


def payload_to_body(payload: dict) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in _walk_parts(payload, skip_attachments=True):
        mime = str(part.get("mimeType") or "").split(";", 1)[0].strip().lower()
        if mime == "text/plain":
            text = _part_body_text(part)
            if text:
                plain_parts.append(text)
        elif mime == "text/html":
            text = _part_body_text(part)
            if text:
                html_parts.append(text)
    if plain_parts:
        return "\n\n".join(plain_parts), "text/plain"
    if html_parts:
        return html_to_text("\n\n".join(html_parts)), "text/html"
    return "", "none"


def payload_attachments(msg_id: str, payload: dict) -> list[AttachmentRef]:
    attachments: list[AttachmentRef] = []
    for part in _walk_parts(payload, skip_attachments=False):
        filename = part.get("filename")
        if not (isinstance(filename, str) and filename.strip()):
            continue
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        attachment_key = body.get("attachmentId") or part.get("partId") or ""
        size = body.get("size")
        try:
            size_bytes = int(size) if size is not None else None
        except (TypeError, ValueError):
            size_bytes = None
        attachments.append(
            AttachmentRef(
                attachment_id=f"{msg_id}:{attachment_key}",
                filename=filename,
                mime_type=str(part.get("mimeType") or ""),
                size_bytes=size_bytes,
            )
        )
    return attachments


def full_to_message(msg: dict, ref: EmailRef, byte_cap: int) -> EmailMessage:
    payload = _payload(msg)
    body, _source = payload_to_body(payload)
    body_text, truncated = truncate_utf8(body, byte_cap)
    message_id = str(msg.get("id") or ref.message_id)
    return EmailMessage(
        ref=ref,
        body_text=body_text,
        headers=normalize_headers(_header_pairs(payload)),
        attachments=payload_attachments(message_id, payload),
        truncated=truncated,
    )
