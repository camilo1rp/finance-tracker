"""
Duplicate detection as an explicit, inspectable pipeline stage --
not just a DB unique constraint caught via exception handling.

Design note: returns which rows are new vs duplicate rather than raising,
so ingest_service can report "N inserted, M skipped" rather than silently
swallowing conflicts or failing the whole batch on the first collision.
"""
import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal

from app.domain.transaction import CanonicalTransaction


def compute_dedupe_hash(txn: CanonicalTransaction) -> str:
    """Hash of (account_id, transaction_date, amount, description).
    Assigned to txn.dedupe_hash before comparison/persistence."""
    amount = txn.amount.quantize(Decimal("0.01"))
    payload = (
        f"{txn.account_id}|{txn.transaction_date.isoformat()}|{amount}|{txn.description}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assign_dedupe_hashes(
    candidates: list[CanonicalTransaction],
    *,
    allow_duplicates: bool = False,
) -> list[CanonicalTransaction]:
    """Assign identity hashes. When allow_duplicates, later copies of the
    same identity get `|occ=N` so they can persist under the unique constraint."""
    counts: dict[str, int] = {}
    assigned: list[CanonicalTransaction] = []
    for txn in candidates:
        base = compute_dedupe_hash(txn)
        if not allow_duplicates:
            assigned.append(replace(txn, dedupe_hash=base))
            continue
        n = counts.get(base, 0) + 1
        counts[base] = n
        digest = (
            base
            if n == 1
            else hashlib.sha256(f"{base}|occ={n}".encode("utf-8")).hexdigest()
        )
        assigned.append(replace(txn, dedupe_hash=digest))
    return assigned


@dataclass(frozen=True)
class DedupeSplit:
    new: list[CanonicalTransaction]
    duplicates: list[CanonicalTransaction]


def split_new_and_duplicates(
    candidates: list[CanonicalTransaction],
    existing_hashes: set[str],
) -> DedupeSplit:
    """Pure set-membership split. `existing_hashes` is fetched from the DB
    by the service layer -- this function does no I/O itself."""
    seen = set(existing_hashes)
    new: list[CanonicalTransaction] = []
    duplicates: list[CanonicalTransaction] = []
    for txn in candidates:
        if txn.dedupe_hash in seen:
            duplicates.append(txn)
        else:
            new.append(txn)
            seen.add(txn.dedupe_hash)
    return DedupeSplit(new=new, duplicates=duplicates)
