from __future__ import annotations

from email.utils import parseaddr
from html.parser import HTMLParser
import html
import re

from app.domain.email_source import truncate_text_bytes

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
_HEADER_KEEP = frozenset({"from", "to", "date", "subject", "message-id"})


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


def normalize_headers(pairs) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in pairs:
        lowered = str(key).strip().lower()
        if lowered not in _HEADER_KEEP or lowered in result:
            continue
        result[lowered] = str(value)
    return result


def parse_sender(from_header: str | None) -> str | None:
    if from_header is None:
        return None
    raw = str(from_header).strip()
    if not raw:
        return None
    _name, address = parseaddr(raw)
    address = (address or "").strip().lower()
    if not address or "@" not in address:
        return None
    local, _, domain = address.partition("@")
    if not local or not domain or "." not in domain:
        return None
    return address


def truncate_utf8(text: str, byte_cap: int) -> tuple[str, bool]:
    return truncate_text_bytes(text, byte_cap)
