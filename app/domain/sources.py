"""
Source-agnostic raw row extraction.

Design note: named TransactionSource, not "FileExtractor" or "CsvParser",
specifically so that a future API-based source (Plaid, SimpleFIN, a bank's
own API) can implement the same interface. `fetch()` takes **kwargs rather
than a single `file` argument because a CSV source needs a file path/handle,
while an API source would need credentials + a date range instead -- the
interface only guarantees "you get raw rows back," not how they're obtained.

Raw rows are list[dict[str, Any]] -- i.e. whatever keys the source's native
format uses (CSV column names, or an API's JSON field names). normalize()
is the only place that knows how to read a raw row via an ImportMapping.
"""
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class TransactionSource(ABC):
    """Interface every raw-row provider must implement."""

    @abstractmethod
    def fetch(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return raw rows exactly as the source produced them, no transformation."""
        raise NotImplementedError


def _to_native(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    if hasattr(value, "item"):
        value = value.item()
        if isinstance(value, float) and value != value:
            return None
        return value
    return value


class CsvSource(TransactionSource):
    """Reads a CSV file (path or file-like object) into raw dict rows."""

    def fetch(self, **kwargs: Any) -> list[dict[str, Any]]:
        # kwargs expected: file_path: str | IO
        file_path = kwargs.get("file_path", kwargs.get("file"))
        if file_path is None:
            raise ValueError("CsvSource.fetch requires file_path")
        frame = pd.read_csv(file_path)
        frame = frame.astype(object).where(pd.notnull(frame), None)
        rows: list[dict[str, Any]] = []
        for record in frame.to_dict(orient="records"):
            rows.append({key: _to_native(value) for key, value in record.items()})
        return rows


# Future, not implemented now -- included only to show where it slots in:
#
# class PdfSource(TransactionSource):
#     """Extracts tabular rows from a text-based PDF statement."""
#     def fetch(self, **kwargs: Any) -> list[dict[str, Any]]: ...
#
# class ApiSource(TransactionSource):
#     """Pulls transactions from a bank/aggregator API (e.g. Plaid, SimpleFIN)."""
#     def fetch(self, **kwargs: Any) -> list[dict[str, Any]]: ...
