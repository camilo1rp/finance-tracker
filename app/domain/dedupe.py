"""
Duplicate detection as an explicit, inspectable pipeline stage --
not just a DB unique constraint caught via exception handling.

Design note: returns which rows are new vs duplicate rather than raising,
so ingest_service can report "N inserted, M skipped" rather than silently
swallowing conflicts or failing the whole batch on the first collision.
"""
import hashlib
from dataclasses import dataclass
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
