"""Normalize and validate category/subcategory filter query params."""
from __future__ import annotations

from collections.abc import Sequence


class UnknownLabelFilterError(ValueError):
    """Raised when a filter label is not present on any stored transaction."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        self.detail = "; ".join(errors)
        super().__init__(self.detail)


def normalize_filter_label(value: str) -> str:
    """Collapse whitespace, strip, and lowercase for filter matching."""
    return " ".join(value.split()).lower()


def matching_spellings(raw: str, available: Sequence[str]) -> list[str]:
    """Return stored spellings whose normalized key equals the input."""
    key = normalize_filter_label(raw)
    if not key:
        return []
    return [label for label in available if normalize_filter_label(label) == key]


def _available_for_errors(available: Sequence[str]) -> list[str]:
    """One representative spelling per normalized key, sorted for error text."""
    by_key: dict[str, str] = {}
    for label in available:
        key = normalize_filter_label(label)
        if key and key not in by_key:
            by_key[key] = label
    return sorted(by_key.values(), key=lambda label: normalize_filter_label(label))


def unknown_label_detail(field: str, raw: str, available: Sequence[str]) -> str:
    """Single canonical error string for an unknown filter label."""
    shown = _available_for_errors(available)
    if shown:
        options = ", ".join(shown)
    else:
        options = "(none)"
    return f"unknown {field} {raw!r}; available: {options}"
