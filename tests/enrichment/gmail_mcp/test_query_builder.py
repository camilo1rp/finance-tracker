from datetime import date

from app.domain.email_source import EmailQuery
from app.integrations.gmail_common.query import RECEIPT_SHAPE_CLAUSE
from app.integrations.gmail_mcp.mapping import build_search_query


def _query(**kwargs) -> EmailQuery:
    values = {
        "senders": ["orders@example-shop.test"],
        "date_from": date(2024, 6, 1),
        "date_to": date(2024, 6, 3),
        "text_hints": [],
        "max_results": 10,
    }
    values.update(kwargs)
    return EmailQuery(**values)


def test_one_sender() -> None:
    query = build_search_query(_query(senders=["orders@example-shop.test"]))
    assert "from:orders@example-shop.test" in query
    assert "{from:" not in query


def test_multiple_senders_or_grouped() -> None:
    query = build_search_query(
        _query(senders=["orders@example-shop.test", "receipts@example-shop.test"])
    )
    assert "{from:orders@example-shop.test from:receipts@example-shop.test}" in query


def test_star_omits_from_clause() -> None:
    query = build_search_query(_query(senders=["*"]))
    assert "from:" not in query
    assert query.endswith("-in:draft")


def test_domain_pattern() -> None:
    query = build_search_query(_query(senders=["example-shop.test"]))
    assert "from:example-shop.test" in query


def test_glob_at_star_domain_uses_domain_clause() -> None:
    query = build_search_query(_query(senders=["*@*.example-shop.test"]))
    assert "from:example-shop.test" in query
    assert "*" not in query.split("after:")[0]


def test_unexpressible_glob_omits_from_clause() -> None:
    query = build_search_query(_query(senders=["shop-*@mail.example-shop.test"]))
    assert "from:" not in query


def test_hints_combined_into_single_quoted_subject_phrase() -> None:
    query = build_search_query(_query(senders=["*"], text_hints=["post", "oak"]))
    assert 'subject:"post oak"' in query
    assert '{"post" "oak"}' not in query
    assert '"post" OR "oak"' not in query


def test_receipt_shape_clause_on_sender_and_hint_paths() -> None:
    sender_query = build_search_query(_query(senders=["orders@example-shop.test"]))
    hint_query = build_search_query(_query(senders=["*"], text_hints=["post", "oak"]))
    assert RECEIPT_SHAPE_CLAUSE in sender_query
    assert RECEIPT_SHAPE_CLAUSE in hint_query
    assert 'subject:"' not in sender_query
    assert 'subject:"post oak"' in hint_query


def test_date_widened_by_one_day() -> None:
    query = build_search_query(_query())
    assert "after:2024/05/31" in query
    assert "before:2024/06/04" in query


def test_not_in_draft_always_present() -> None:
    assert "-in:draft" in build_search_query(_query(senders=["*"], text_hints=["hint"]))
    assert "-in:draft" in build_search_query(_query())
