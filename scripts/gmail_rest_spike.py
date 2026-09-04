"""Live probe of the Gmail REST API. Writes a redacted report; the owner reviews it.

Run: python -m scripts.gmail_rest_spike --after YYYY-MM-DD --before YYYY-MM-DD
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import re
import sys

from app.agent.config import (
    email_sender_allowlist,
    gmail_rest_base_url,
    gmail_rest_timeout_s,
    token_provider_from_env,
)
from app.domain.email_source import EmailQuery, parse_allowlist
from app.integrations.gmail_common.query import build_search_query
from app.integrations.gmail_rest.client import GmailRestClient
from app.integrations.gmail_rest.mapping import payload_attachments, payload_to_body

OUTPUT_PATH = Path("docs/email-enrichment/spike-output-rest.md")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DIGIT_RE = re.compile(r"\d")


def _charset(value: str) -> str:
    classes: list[str] = []
    if any(ch.islower() for ch in value):
        classes.append("lower")
    if any(ch.isupper() for ch in value):
        classes.append("upper")
    if any(ch.isdigit() for ch in value):
        classes.append("digit")
    if any(not ch.isalnum() for ch in value):
        classes.append("other")
    return "+".join(classes) or "empty"


def _id_meta(value: object) -> dict[str, object]:
    text = "" if value is None else str(value)
    return {"length": len(text), "charset": _charset(text)}


def _redact_text(text: str) -> str:
    cleaned = _EMAIL_RE.sub("[email]", text)
    return _DIGIT_RE.sub("#", cleaned)


def _redact_preview(text: str, limit: int = 80) -> str:
    return _redact_text(text)[:limit]


def _write_output(lines: list[str]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {OUTPUT_PATH}")


def _mask_email(address: str) -> str:
    if "@" not in address:
        return _redact_text(address)
    local, domain = address.split("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _header_value(msg: dict, name: str) -> str | None:
    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
    wanted = name.lower()
    for item in payload.get("headers") or []:
        if isinstance(item, dict) and str(item.get("name") or "").lower() == wanted:
            return str(item.get("value") or "")
    return None


def _sender_kind(raw: object) -> str:
    text = "" if raw is None else str(raw)
    if "<" in text or (" " in text and "@" in text):
        return "display-name"
    if "@" in text:
        return "bare-address"
    return "other"


def _mime_tree(payload: dict, depth: int = 0) -> dict:
    return {
        "mimeType": payload.get("mimeType"),
        "depth": depth,
        "parts": [
            _mime_tree(child, depth + 1)
            for child in payload.get("parts") or []
            if isinstance(child, dict)
        ],
    }


def _part_types(payload: dict) -> list[str]:
    types: list[str] = []
    stack = [payload]
    while stack:
        part = stack.pop()
        mime = str(part.get("mimeType") or "")
        if mime:
            types.append(mime)
        stack.extend(child for child in part.get("parts") or [] if isinstance(child, dict))
    return types


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the live Gmail REST API (four GET endpoints only).")
    parser.add_argument("--from", dest="senders", help="Comma-separated senders; default EMAIL_SENDER_ALLOWLIST")
    parser.add_argument("--after", required=True, help="Inclusive start date YYYY-MM-DD")
    parser.add_argument("--before", required=True, help="Inclusive end date YYYY-MM-DD")
    parser.add_argument("--query", help="Raw Gmail query; bypasses the query builder")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    date_from = date.fromisoformat(args.after)
    date_to = date.fromisoformat(args.before)
    senders = parse_allowlist(args.senders or email_sender_allowlist())
    if not senders and not args.query:
        print("No senders: pass --from or set EMAIL_SENDER_ALLOWLIST", file=sys.stderr)
        return 1

    client = GmailRestClient(
        token_provider_from_env(),
        gmail_rest_base_url(),
        gmail_rest_timeout_s(),
    )

    lines: list[str] = ["# Gmail REST spike output", "", "Redacted. Owner review before commit.", ""]

    print("== 1. profile ==")
    profile = client.get_profile()
    address = str(profile.get("emailAddress") or "")
    masked = _mask_email(address) if address else None
    print("account_hint:", masked)
    print("response_keys:", sorted(profile.keys()))
    lines.extend(
        [
            "## 1. profile",
            f"- account_hint: `{masked}`",
            f"- response_keys: {sorted(profile.keys())}",
        ]
    )
    _write_output(lines)

    if args.query:
        gmail_query = args.query
    else:
        query = EmailQuery(senders=senders, date_from=date_from, date_to=date_to, max_results=5)
        gmail_query = build_search_query(query)
    print("\n== 2. list_messages ==")
    print("gmail_query:", gmail_query)
    listing = client.list_messages(gmail_query, 5, None)
    messages = listing.get("messages") or []
    print("id_count:", len(messages))
    print("nextPageToken_present:", bool(listing.get("nextPageToken")))
    lines.extend(
        [
            "",
            "## 2. list_messages",
            f"- gmail_query: `{_redact_text(gmail_query)}`",
            f"- id_count: {len(messages)}",
            f"- nextPageToken_present: {bool(listing.get('nextPageToken'))}",
            f"- nextPageToken_meta: {_id_meta(listing.get('nextPageToken'))}",
            f"- resultSizeEstimate_type: {type(listing.get('resultSizeEstimate')).__name__}",
        ]
    )
    _write_output(lines)

    first_id = messages[0].get("id") if messages else None
    print("\n== 3. get_message metadata ==")
    if not first_id:
        print("no search hits; skipped metadata and full")
        lines.extend(
            [
                "",
                "## 3. metadata",
                "- skipped: no search hits",
                "",
                "## 4. full",
                "- skipped: no search hits",
            ]
        )
        _write_output(lines)
        return 0

    meta = client.get_message_metadata(str(first_id))
    from_header = _header_value(meta, "From")
    sender_kind = _sender_kind(from_header)
    internal = meta.get("internalDate")
    try:
        iso = datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        iso = None
    header_names = sorted(
        {
            str(item.get("name") or "")
            for item in (meta.get("payload") or {}).get("headers") or []
            if isinstance(item, dict)
        }
    )
    print("from_display_name:", sender_kind == "display-name")
    print("internalDate_iso:", iso)
    print("headers_present:", header_names)
    lines.extend(
        [
            "",
            "## 3. metadata",
            f"- message_id_meta: {_id_meta(first_id)}",
            f"- from_had_display_name: {sender_kind == 'display-name'}",
            f"- sender_kind: {sender_kind}",
            f"- internalDate_iso: {iso}",
            f"- headers_present: {header_names}",
        ]
    )
    _write_output(lines)

    print("\n== 4. get_message full ==")
    full = client.get_message_full(str(first_id))
    payload = full.get("payload") if isinstance(full.get("payload"), dict) else {}
    body, source = payload_to_body(payload)
    attachments = payload_attachments(str(first_id), payload)
    preview = _redact_preview(body)
    part_types = _part_types(payload)
    tree = _mime_tree(payload)
    print("part_types:", part_types)
    print("mime_tree:", tree)
    print("body_source:", source)
    print("body_bytes:", len(body.encode("utf-8")))
    print("attachment_count:", len(attachments))
    print("preview_redacted:", preview)
    lines.extend(
        [
            "",
            "## 4. full",
            f"- part_types: {part_types}",
            f"- mime_tree: `{tree}`",
            f"- body_source: {source}",
            f"- body_bytes: {len(body.encode('utf-8'))}",
            f"- attachment_count: {len(attachments)}",
            f"- plaintext_preview_redacted_80: `{preview}`",
        ]
    )
    _write_output(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
