from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.domain.classification import TransactionType
from app.domain.dedupe import compute_dedupe_hash, split_new_and_duplicates
from app.domain.transaction import CanonicalTransaction


def _txn(
    *,
    account_id: int = 1,
    day: int = 1,
    amount: str = "4.50",
    description: str = "Coffee",
) -> CanonicalTransaction:
    return CanonicalTransaction(
        account_id=account_id,
        transaction_date=date(2024, 3, day),
        description=description,
        amount=Decimal(amount),
        transaction_type=TransactionType.SPEND,
        is_spend=True,
    )


def test_same_identity_produces_same_hash() -> None:
    a = _txn()
    b = _txn(amount="4.5")
    assert compute_dedupe_hash(a) == compute_dedupe_hash(b)


def test_different_identity_produces_different_hash() -> None:
    assert compute_dedupe_hash(_txn()) != compute_dedupe_hash(_txn(description="Tea"))
    assert compute_dedupe_hash(_txn(account_id=1)) != compute_dedupe_hash(_txn(account_id=2))


def test_split_new_and_duplicates_including_within_batch() -> None:
    first = replace(_txn(), dedupe_hash=compute_dedupe_hash(_txn()))
    same = replace(_txn(), dedupe_hash=first.dedupe_hash)
    other = _txn(description="Tea")
    other = replace(other, dedupe_hash=compute_dedupe_hash(other))

    split = split_new_and_duplicates([first, same, other], existing_hashes=set())
    assert split.new == [first, other]
    assert split.duplicates == [same]

    again = split_new_and_duplicates([first, other], existing_hashes={first.dedupe_hash})
    assert again.new == [other]
    assert again.duplicates == [first]
