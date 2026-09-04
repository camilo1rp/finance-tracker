from __future__ import annotations

from datetime import date, timedelta
import re

from app.domain.email_source import EmailQuery

_QUOTED_PHRASE = re.compile(r'"[^"]*"')

RECEIPT_SHAPE_CLAUSE = (
    "(category:purchases OR subject:(order OR receipt OR confirmation OR invoice OR purchase OR payment))"
)


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
        parts.append(f'subject:"{" ".join(hints)}"')
    parts.append(RECEIPT_SHAPE_CLAUSE)
    parts.append("-in:draft")
    return " ".join(parts)
