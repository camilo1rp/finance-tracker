"""Live probe of the Gmail MCP server. Writes a redacted report; the owner reviews it.

Run: python -m scripts.gmail_mcp_spike --after YYYY-MM-DD --before YYYY-MM-DD
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import sys

from app.agent.config import (
    email_mcp_timeout_s,
    email_mcp_url,
    email_sender_allowlist,
    token_provider_from_env,
)
from app.domain.email_source import EmailQuery, parse_allowlist
from app.integrations.gmail_mcp.mapping import build_search_query
from app.integrations.gmail_mcp.transport import (
    ALLOWED_TOOLS,
    StreamableHttpMcpTransport,
    invoke_mcp,
    tool_result_as_dict,
)

OUTPUT_PATH = Path("docs/email-enrichment/spike-output.md")
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


def _tool_error_text(result) -> str:
    texts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            texts.append(text)
    structured = getattr(result, "structuredContent", None)
    if structured:
        texts.append(str(structured))
    return "\n".join(texts).strip()


def _write_output(lines: list[str]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {OUTPUT_PATH}")


def _shape(value, *, depth: int = 0):
    if isinstance(value, dict):
        return {str(key): _shape(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        return [_shape(value[0], depth=depth + 1), f"...{len(value)} items"]
    return type(value).__name__


def _date_format(raw: object) -> str:
    text = "" if raw is None else str(raw)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "YYYY-MM-DD"
    if "T" in text or ":" in text:
        return "datetime"
    return "other"


def _sender_kind(raw: object) -> str:
    text = "" if raw is None else str(raw)
    if "<" in text or (" " in text and "@" in text):
        return "display-name"
    if "@" in text:
        return "bare-address"
    return "other"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the live Gmail MCP server (read tools only).")
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

    transport = StreamableHttpMcpTransport(
        email_mcp_url(),
        token_provider_from_env(),
        email_mcp_timeout_s(),
    )

    lines: list[str] = ["# Gmail MCP spike output", "", "Redacted. Owner review before commit.", ""]

    print("== 1. initialize + tools/list ==")
    detailed = transport.list_tools_detailed()
    names = [str(item["name"]) for item in detailed]
    print("tool_names:", names)
    lines.append("## 1. tools/list")
    lines.append(f"- tool_count: {len(names)}")
    lines.append(f"- tool_names: {', '.join(names)}")
    missing = [name for name in sorted(ALLOWED_TOOLS) if name not in names]
    if missing:
        print("MISSING allowed tools:", missing, file=sys.stderr)
        _write_output(lines)
        return 1
    by_name = {str(item["name"]): item.get("input_schema_keys") or [] for item in detailed}
    for name in sorted(ALLOWED_TOOLS):
        keys = by_name.get(name) or []
        print(f"  {name} input properties: {keys}")
        lines.append(f"- {name} input_schema_keys: {keys}")
    _write_output(lines)

    if args.query:
        gmail_query = args.query
    else:
        query = EmailQuery(senders=senders, date_from=date_from, date_to=date_to, max_results=5)
        gmail_query = build_search_query(query)
    print("\n== 2. search_threads ==")
    print("gmail_query:", gmail_query)

    async def _search(session):
        return await session.call_tool(
            "search_threads",
            {"query": gmail_query, "pageSize": 5, "view": "THREAD_VIEW_MINIMAL"},
        )

    raw_search = invoke_mcp(transport.url, transport._headers(), transport.timeout_s, _search)
    if getattr(raw_search, "isError", False):
        error_text = _redact_text(_tool_error_text(raw_search))
        print("isError:", error_text)
        lines.extend(
            [
                "",
                "## 2. search_threads",
                f"- gmail_query: `{_redact_text(gmail_query)}`",
                f"- isError: `{error_text}`",
            ]
        )
        _write_output(lines)
        return 1
    result = tool_result_as_dict(raw_search)
    threads = result.get("threads") or []
    date_formats: set[str] = set()
    sender_kinds: set[str] = set()
    outside_window = False
    message_count = 0
    for thread in threads:
        for message in thread.get("messages") or []:
            message_count += 1
            date_formats.add(_date_format(message.get("date")))
            sender_kinds.add(_sender_kind(message.get("sender") or message.get("from")))
            parsed = str(message.get("date") or "")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parsed):
                observed = date.fromisoformat(parsed)
                if observed < date_from or observed > date_to:
                    outside_window = True
    print("shape:", _shape(result))
    print("thread_count:", len(threads), "message_count:", message_count)
    print("date_formats:", sorted(date_formats))
    print("sender_kinds:", sorted(sender_kinds))
    print("any_message_date_outside_requested_window:", outside_window)
    print("nextPageToken_present:", bool(result.get("nextPageToken")))
    print("resultCountEstimate_type:", type(result.get("resultCountEstimate")).__name__)
    lines.extend(
        [
            "",
            "## 2. search_threads",
            f"- gmail_query: `{_redact_text(gmail_query)}`",
            f"- response_shape: `{_shape(result)}`",
            f"- thread_count: {len(threads)}",
            f"- message_count: {message_count}",
            f"- date_formats: {sorted(date_formats)}",
            f"- sender_kinds: {sorted(sender_kinds)}",
            f"- any_message_date_outside_requested_window: {outside_window}",
            f"- nextPageToken_present: {bool(result.get('nextPageToken'))}",
            f"- nextPageToken_meta: {_id_meta(result.get('nextPageToken'))}",
            f"- resultCountEstimate_type: {type(result.get('resultCountEstimate')).__name__}",
        ]
    )

    print("\n== 3. get_message FULL_CONTENT ==")
    first_id = None
    for thread in threads:
        for message in thread.get("messages") or []:
            first_id = message.get("id")
            break
        if first_id:
            break
    preview = ""
    if first_id:
        full = transport.call_tool(
            "get_message",
            {"messageId": first_id, "messageFormat": "FULL_CONTENT"},
        )
        plain = str(full.get("plaintextBody") or "")
        html_body = str(full.get("htmlBody") or "")
        attachments = full.get("attachments") or []
        preview = _redact_preview(plain)
        print("plaintextBody_populated:", bool(plain.strip()))
        print("htmlBody_populated:", bool(html_body.strip()))
        print("plaintextBody_bytes:", len(plain.encode("utf-8")))
        print("htmlBody_bytes:", len(html_body.encode("utf-8")))
        print("attachment_count:", len(attachments))
        print(
            "attachments_have_ids_and_filenames:",
            all(item.get("id") and item.get("filename") is not None for item in attachments)
            if attachments
            else None,
        )
        print("plaintext_preview_redacted:", preview)
        lines.extend(
            [
                "",
                "## 3. get_message",
                f"- message_id_meta: {_id_meta(first_id)}",
                f"- plaintextBody_populated: {bool(plain.strip())}",
                f"- htmlBody_populated: {bool(html_body.strip())}",
                f"- plaintextBody_bytes: {len(plain.encode('utf-8'))}",
                f"- htmlBody_bytes: {len(html_body.encode('utf-8'))}",
                f"- attachment_count: {len(attachments)}",
                f"- attachments_have_ids: {all(bool(item.get('id')) for item in attachments) if attachments else None}",
                f"- attachments_have_filenames: {all(item.get('filename') is not None for item in attachments) if attachments else None}",
                f"- plaintext_preview_redacted_80: `{preview}`",
            ]
        )
    else:
        print("no search hits; skipped get_message")
        lines.extend(["", "## 3. get_message", "- skipped: no search hits"])

    print("\n== 4. list_labels ==")
    labels = transport.call_tool("list_labels", {})
    label_items = labels.get("labels") or labels.get("label") or []
    if not isinstance(label_items, list):
        label_items = []
        for value in labels.values():
            if isinstance(value, list):
                label_items = value
                break
    print("label_count:", len(label_items) if label_items else f"keys={list(labels.keys())}")
    lines.extend(
        [
            "",
            "## 4. list_labels",
            f"- label_count: {len(label_items)}",
            f"- response_keys: {sorted(labels.keys())}",
            "",
            "## Observed vs Section 1 assumptions",
            f"- sender_carrying_display_name: {'display-name' in sender_kinds}",
            f"- date_carrying_time: {'datetime' in date_formats}",
            f"- plaintextBody_empty_for_this_sample: {bool(first_id) and not bool(preview.strip())}",
            f"- nextPageToken_present: {bool(result.get('nextPageToken'))}",
            f"- resultCountEstimate_type: {type(result.get('resultCountEstimate')).__name__}",
        ]
    )

    _write_output(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
