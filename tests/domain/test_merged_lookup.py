from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.classification import NormalizationKind
from app.domain.db_lookup import DbNormalizationLookup, merged_lookup_from_db
from app.domain.merged_lookup import MergedNormalizationLookup, RuleSpec
from app.models import NormalizationMapping

CATEGORY = NormalizationKind.CATEGORY
TYPE = NormalizationKind.TRANSACTION_TYPE
OWNER = NormalizationKind.OWNER
MERCHANT = NormalizationKind.MERCHANT


def _account(client: TestClient) -> int:
    owner = client.post("/owners", json={"name": "Pat"}).json()
    account = client.post(
        "/accounts",
        json={
            "name": "Card",
            "last4": "1111",
            "default_owner_id": owner["id"],
            "default_mapping": {
                "date_col": "Date",
                "description_col": "Description",
                "amount_col": "Amount",
                "type_col": "Type",
            },
        },
    ).json()
    return account["id"]


def test_category_precedence_account_merchant_to_global() -> None:
    lookup = MergedNormalizationLookup(
        [
            RuleSpec("category", "shopping", "Global", None, None, "db:1"),
            RuleSpec("category", "shopping", "GlobalMerch", None, "costco", "db:2"),
            RuleSpec("category", "shopping", "Account", 1, None, "db:3"),
            RuleSpec("category", "shopping", "AccountMerch", 1, "costco", "db:4"),
        ]
    )
    assert lookup.resolve(CATEGORY, "shopping", 1, "costco") == "AccountMerch"
    assert lookup.resolve(CATEGORY, "shopping", 1, "amazon") == "Account"
    assert lookup.resolve(CATEGORY, "shopping", 2, "costco") == "GlobalMerch"
    assert lookup.resolve(CATEGORY, "shopping", 2, "amazon") == "Global"
    assert lookup.resolve(CATEGORY, "shopping", 2) == "Global"
    assert lookup.resolve_with_ref(CATEGORY, "shopping", 1, "costco").ref == "db:4"


def test_proposed_global_shadowed_by_existing_account() -> None:
    lookup = MergedNormalizationLookup(
        [
            RuleSpec("category", "shopping", "AccountExisting", 1, None, "db:10"),
            RuleSpec("category", "shopping", "ProposedGlobal", None, None, "proposed:0"),
        ]
    )
    match = lookup.resolve_with_ref(CATEGORY, "shopping", 1, "amazon")
    assert match is not None
    assert match.value == "AccountExisting"
    assert match.ref == "db:10"
    other = lookup.resolve_with_ref(CATEGORY, "shopping", 2, "amazon")
    assert other is not None
    assert other.value == "ProposedGlobal"
    assert other.ref == "proposed:0"


def test_proposed_account_beats_existing_global() -> None:
    lookup = MergedNormalizationLookup(
        [
            RuleSpec("category", "shopping", "GlobalExisting", None, None, "db:1"),
            RuleSpec("category", "shopping", "ProposedAccount", 1, None, "proposed:0"),
        ]
    )
    assert lookup.resolve(CATEGORY, "shopping", 1) == "ProposedAccount"
    assert lookup.resolve_with_ref(CATEGORY, "shopping", 1).ref == "proposed:0"
    assert lookup.resolve(CATEGORY, "shopping", 2) == "GlobalExisting"


def test_proposed_account_merchant_beats_existing_account() -> None:
    lookup = MergedNormalizationLookup(
        [
            RuleSpec("category", "shopping", "AccountExisting", 1, None, "db:1"),
            RuleSpec(
                "category", "shopping", "ProposedCostco", 1, "costco", "proposed:0"
            ),
        ]
    )
    assert lookup.resolve(CATEGORY, "shopping", 1, "costco") == "ProposedCostco"
    assert lookup.resolve(CATEGORY, "shopping", 1, "amazon") == "AccountExisting"


def test_same_scope_prefers_db_rule() -> None:
    lookup = MergedNormalizationLookup(
        [
            RuleSpec("category", "shopping", "FromDb", None, None, "db:9"),
            RuleSpec("category", "shopping", "Proposed", None, None, "proposed:0"),
        ]
    )
    match = lookup.resolve_with_ref(CATEGORY, "shopping", 1)
    assert match is not None
    assert match.value == "FromDb"
    assert match.ref == "db:9"


def test_same_scope_prefers_db_even_if_proposed_listed_first() -> None:
    lookup = MergedNormalizationLookup(
        [
            RuleSpec("category", "shopping", "Proposed", None, "costco", "proposed:0"),
            RuleSpec("category", "shopping", "FromDb", None, "costco", "db:3"),
        ]
    )
    match = lookup.resolve_with_ref(CATEGORY, "shopping", 99, "costco")
    assert match is not None
    assert match.value == "FromDb"
    assert match.ref == "db:3"


def test_non_category_account_then_global_ignores_merchant() -> None:
    lookup = MergedNormalizationLookup(
        [
            RuleSpec("transaction_type", "sale", "SPEND", None, None, "db:1"),
            RuleSpec("transaction_type", "sale", "PAYMENT", 1, None, "db:2"),
            RuleSpec("owner", "pat", "Pat Global", None, None, "db:3"),
            RuleSpec("merchant", "sbux", "Starbucks", 1, None, "proposed:0"),
        ]
    )
    assert lookup.resolve(TYPE, "sale", 1) == "PAYMENT"
    assert lookup.resolve(TYPE, "sale", 2) == "SPEND"
    assert lookup.resolve(OWNER, "pat", 1) == "Pat Global"
    assert lookup.resolve(MERCHANT, "sbux", 1) == "Starbucks"
    assert lookup.resolve(MERCHANT, "sbux", 2) is None


def test_merged_agrees_with_db_lookup_on_same_rules(
    client: TestClient, db_session: Session
) -> None:
    account_id = _account(client)
    client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Shopping",
        },
    )
    client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Household",
            "merchant": "Costco",
        },
    )
    client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Account Shop",
            "account_id": account_id,
        },
    )
    client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Warehouse",
            "account_id": account_id,
            "merchant": "Costco",
        },
    )
    client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "Sale",
            "canonical_value": "SPEND",
        },
    )
    client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "Sale",
            "canonical_value": "PAYMENT",
            "account_id": account_id,
        },
    )

    db_lookup = DbNormalizationLookup(db_session)
    merged = merged_lookup_from_db(
        db_session, proposed=[], kinds={"category", "transaction_type"}
    )
    queries = [
        (CATEGORY, "shopping", account_id, "costco"),
        (CATEGORY, "shopping", account_id, "amazon"),
        (CATEGORY, "shopping", 999, "costco"),
        (CATEGORY, "shopping", 999, "amazon"),
        (CATEGORY, "shopping", 999, None),
        (TYPE, "sale", account_id, None),
        (TYPE, "sale", 999, None),
        (TYPE, "return", account_id, None),
    ]
    for kind, raw, acct, merch in queries:
        assert merged.resolve(kind, raw, acct, merch) == db_lookup.resolve(
            kind, raw, acct, merch
        )


def test_merged_lookup_from_db_layers_proposed(
    client: TestClient, db_session: Session
) -> None:
    account_id = _account(client)
    created = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Shopping",
            "canonical_value": "Account Shop",
            "account_id": account_id,
        },
    ).json()
    proposed = RuleSpec(
        kind="category",
        raw_value="shopping",
        canonical_value="Proposed Global",
        account_id=None,
        merchant=None,
        ref="proposed:0",
    )
    merged = merged_lookup_from_db(db_session, [proposed], kinds={"category"})
    account_hit = merged.resolve_with_ref(CATEGORY, "shopping", account_id)
    assert account_hit is not None
    assert account_hit.value == "Account Shop"
    assert account_hit.ref == f"db:{created['id']}"
    global_hit = merged.resolve_with_ref(CATEGORY, "shopping", 999)
    assert global_hit is not None
    assert global_hit.value == "Proposed Global"
    assert global_hit.ref == "proposed:0"


def test_merged_lookup_from_db_restricts_kinds(
    client: TestClient, db_session: Session
) -> None:
    client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "Sale",
            "canonical_value": "SPEND",
        },
    )
    client.post(
        "/mappings",
        json={"kind": "category", "raw_value": "Food", "canonical_value": "Dining"},
    )
    merged = merged_lookup_from_db(db_session, proposed=[], kinds={"category"})
    assert merged.resolve(CATEGORY, "food", 1) == "Dining"
    assert merged.resolve(TYPE, "sale", 1) is None
    stored = db_session.scalars(select(NormalizationMapping)).all()
    assert {row.kind for row in stored} == {"transaction_type", "category"}
