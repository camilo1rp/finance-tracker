"""Conservative merchant name extraction from a description or column value."""

import re

_STORE_NOISE = re.compile(
    r"""
    (?:
        \s+STORE\s+\d+
        | \s+\#\d+
        | \s+\*\d+
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_merchant(description: str | None) -> str | None:
    """
    Derive a merchant label from a free-text description.

    Trims and collapses whitespace, then strips trailing store-number noise
    (STORE 123, #1234, *1234). If nothing useful remains, returns the
    trimmed original description. Empty/whitespace input returns None.
    """
    if description is None:
        return None
    trimmed = " ".join(description.split())
    if not trimmed:
        return None
    cleaned = _STORE_NOISE.sub("", trimmed).strip()
    return cleaned or trimmed


def resolved_merchant(
    raw: str | None,
    normalized: str | None,
    override: str | None = None,
) -> str | None:
    """Effective merchant label: override > normalized > raw."""
    for value in (override, normalized, raw):
        if value is not None and str(value).strip():
            return str(value).strip()
    return None
