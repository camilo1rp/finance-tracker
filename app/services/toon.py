"""Token-Oriented Object Notation encoder for compact LLM tool payloads.

Encode-only subset of the TOON spec: YAML-like objects, CSV-style tables for
uniform object arrays, and inline primitive arrays. No external dependency.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

INDENT = "  "
_QUOTE_SPECIALS = set(',:"\\\n\r[]{}')


def encode_toon(value: Any) -> str:
    """Encode a JSON-like value as TOON text."""
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            _encode(item, lines, depth=0, key=str(key))
    elif isinstance(value, list):
        _encode_list(value, lines, depth=0, key="items")
    else:
        lines.append(_primitive(value))
    return "\n".join(lines)


def _encode(value: Any, lines: list[str], *, depth: int, key: str) -> None:
    pad = INDENT * depth
    if isinstance(value, dict):
        lines.append(f"{pad}{key}:")
        for nested_key, nested in value.items():
            _encode(nested, lines, depth=depth + 1, key=str(nested_key))
        return
    if isinstance(value, list):
        _encode_list(value, lines, depth=depth, key=key)
        return
    lines.append(f"{pad}{key}: {_primitive(value)}")


def _encode_list(items: list[Any], lines: list[str], *, depth: int, key: str) -> None:
    pad = INDENT * depth
    n = len(items)
    if _is_tabular(items):
        fields = list(items[0].keys())
        header = ",".join(str(f) for f in fields)
        lines.append(f"{pad}{key}[{n}]{{{header}}}:")
        row_pad = INDENT * (depth + 1)
        for item in items:
            cells = [_primitive(item.get(field)) for field in fields]
            lines.append(f"{row_pad}{','.join(cells)}")
        return
    if n == 0:
        lines.append(f"{pad}{key}[0]:")
        return
    if all(_is_primitive(item) for item in items):
        encoded = [_primitive(item) for item in items]
        if any("\n" in encoded_item for encoded_item in encoded):
            lines.append(f"{pad}{key}[{n}]:")
            row_pad = INDENT * (depth + 1)
            for encoded_item in encoded:
                lines.append(f"{row_pad}{encoded_item}")
            return
        lines.append(f"{pad}{key}[{n}]: {','.join(encoded)}")
        return
    lines.append(f"{pad}{key}[{n}]:")
    for index, item in enumerate(items):
        _encode(item, lines, depth=depth + 1, key=str(index))


def _is_tabular(items: list[Any]) -> bool:
    if not items or not all(isinstance(item, dict) and item for item in items):
        return False
    keys = list(items[0].keys())
    return all(list(item.keys()) == keys for item in items)


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, Decimal))


def _primitive(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    text = str(value)
    if _needs_quotes(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'
    return text


def _needs_quotes(text: str) -> bool:
    if text == "" or text[0].isspace() or text[-1].isspace():
        return True
    if any(char in _QUOTE_SPECIALS for char in text):
        return True
    return text.lower() in {"true", "false", "null"}
