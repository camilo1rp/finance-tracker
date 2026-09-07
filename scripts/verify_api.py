#!/usr/bin/env python3
"""HTTP verification of every implemented finance-tracker endpoint.

Default: FastAPI TestClient + isolated SQLite (no Docker).
Live:    python scripts/verify_api.py --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class Check:
    name: str
    expected: str
    actual: str
    passed: bool
    skipped: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def record(
        self,
        name: str,
        expected: Any,
        actual: Any,
        passed: bool,
        skipped: bool = False,
    ) -> None:
        self.checks.append(
            Check(
                name=name,
                expected=str(expected),
                actual=str(actual),
                passed=passed,
                skipped=skipped,
            )
        )

    def expect_status(self, name: str, response, status: int) -> bool:
        ok = response.status_code == status
        self.record(name, f"HTTP {status}", f"HTTP {response.status_code}", ok)
        return ok

    def expect_eq(self, name: str, actual: Any, expected: Any) -> bool:
        ok = actual == expected
        self.record(name, expected, actual, ok)
        return ok

    def expect_in(self, name: str, member: Any, container: Any) -> bool:
        ok = member in container
        self.record(name, f"{member!r} in collection", ok, ok)
        return ok

    def skip(self, name: str, reason: str) -> None:
        self.record(name, "stub / out of scope", reason, passed=True, skipped=True)

    def print_table(self) -> int:
        width = max(len(c.name) for c in self.checks)
        print()
        print(f"{'CHECK':<{width}}  RESULT  EXPECTED  ACTUAL")
        print("-" * (width + 40))
        failures = 0
        skips = 0
        for check in self.checks:
            if check.skipped:
                label = "SKIP"
                skips += 1
            elif check.passed:
                label = "PASS"
            else:
                label = "FAIL"
                failures += 1
            print(
                f"{check.name:<{width}}  {label:<6}  {check.expected}  ||  {check.actual}"
            )
        print("-" * (width + 40))
        print(
            f"{len(self.checks) - failures - skips} passed, "
            f"{failures} failed, {skips} skipped (stubs)"
        )
        return failures


def _json(response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _txn_rows(response) -> list[dict]:
    body = _json(response)
    if isinstance(body, dict):
        return list(body.get("transactions") or [])
    return []


def _summary_groups(response) -> list[dict]:
    body = _json(response)
    if isinstance(body, dict):
        return list(body.get("groups") or [])
    return []


def _txn_detail(client, transaction_id: int) -> dict:
    return _json(client.get(f"/transactions/{transaction_id}"))


def _import_file(client, account_id: int, filename: str, *, allow_duplicates: bool = False):
    path = FIXTURES / filename
    params: dict = {"account_id": account_id}
    if allow_duplicates:
        params["allow_duplicates"] = True
    return client.post(
        "/imports",
        params=params,
        files={"file": (filename, path.read_bytes(), "text/csv")},
    )


def run(client, suffix: str, report: Report) -> None:
    camilo = f"Camilo{suffix}"
    partner = f"Partner{suffix}"

    # --- health ---
    health = client.get("/health")
    report.expect_status("GET /health", health, 200)
    if health.status_code == 200:
        report.expect_eq("GET /health body", health.json(), {"status": "ok"})

    # --- owners ---
    r_camilo = client.post("/owners", json={"name": camilo})
    report.expect_status("POST /owners Camilo", r_camilo, 201)
    r_partner = client.post("/owners", json={"name": partner})
    report.expect_status("POST /owners Partner", r_partner, 201)
    camilo_id = r_camilo.json()["id"] if r_camilo.status_code == 201 else None
    partner_id = r_partner.json()["id"] if r_partner.status_code == 201 else None

    listed = client.get("/owners")
    report.expect_status("GET /owners", listed, 200)
    names = {row["name"] for row in listed.json()} if listed.status_code == 200 else set()
    report.expect_in("GET /owners contains Camilo", camilo, names)
    report.expect_in("GET /owners contains Partner", partner, names)

    dup_owner = client.post("/owners", json={"name": camilo})
    report.expect_status("POST /owners duplicate", dup_owner, 409)

    # --- accounts ---
    chase_mapping = {
        "date_col": "Transaction Date",
        "description_col": "Description",
        "amount_col": "Amount",
        "type_col": "Type",
        "category_col": "Category",
    }
    apple_mapping = {
        **chase_mapping,
        "owner_col": "Purchased By",
        "merchant_col": "Merchant",
    }
    debit_mapping = {
        "date_col": "Date",
        "description_col": "Description",
        "amount_col": "Amount",
        "sign_convention": "negative_is_spend",
    }

    r_chase = client.post(
        "/accounts",
        json={
            "name": f"Chase Sapphire{suffix}",
            "last4": "1111",
            "default_owner_id": camilo_id,
            "account_kind": "credit_card",
            "default_mapping": chase_mapping,
        },
    )
    report.expect_status("POST /accounts Chase", r_chase, 201)
    chase_id = r_chase.json()["id"] if r_chase.status_code == 201 else None

    r_apple = client.post(
        "/accounts",
        json={
            "name": f"Apple Card{suffix}",
            "last4": "2222",
            "default_owner_id": camilo_id,
            "account_kind": "credit_card",
            "default_mapping": apple_mapping,
        },
    )
    report.expect_status("POST /accounts Apple", r_apple, 201)
    apple_id = r_apple.json()["id"] if r_apple.status_code == 201 else None

    r_debit = client.post(
        "/accounts",
        json={
            "name": f"Checking{suffix}",
            "last4": "3333",
            "default_owner_id": camilo_id,
            "account_kind": "depository",
            "default_mapping": debit_mapping,
        },
    )
    report.expect_status("POST /accounts Debit sign-only", r_debit, 201)
    debit_id = r_debit.json()["id"] if r_debit.status_code == 201 else None

    listed_accts = client.get("/accounts")
    report.expect_status("GET /accounts", listed_accts, 200)
    if listed_accts.status_code == 200:
        last4s = {row["last4"] for row in listed_accts.json()}
        report.expect_in("GET /accounts has Chase last4", "1111", last4s)

    unknown_owner = client.post(
        "/accounts",
        json={
            "name": "Ghost",
            "last4": "0000",
            "default_owner_id": 999999,
            "account_kind": "credit_card",
            "default_mapping": chase_mapping,
        },
    )
    report.expect_status("POST /accounts unknown owner", unknown_owner, 404)

    bad_mapping = client.post(
        "/accounts",
        json={
            "name": "Broken",
            "last4": "0001",
            "default_mapping": {
                "date_col": "Date",
                "description_col": "Description",
                "amount_col": "Amount",
            },
        },
    )
    report.expect_status("POST /accounts mapping missing type/sign", bad_mapping, 422)

    # --- mappings ---
    mapping_payloads = [
        {"kind": "transaction_type", "raw_value": "Sale", "canonical_value": "SPEND"},
        {"kind": "transaction_type", "raw_value": "Return", "canonical_value": "REFUND"},
        {"kind": "transaction_type", "raw_value": "Purchase", "canonical_value": "SPEND"},
        {"kind": "category", "raw_value": "Food & Drink", "canonical_value": "Dining"},
        {"kind": "category", "raw_value": "Shopping", "canonical_value": "Shopping"},
        {"kind": "category", "raw_value": "Groceries", "canonical_value": "Groceries"},
        {"kind": "category", "raw_value": "Transportation", "canonical_value": "Transportation"},
        {"kind": "category", "raw_value": "Other", "canonical_value": "Misc"},
        {"kind": "owner", "raw_value": "Camilo", "canonical_value": camilo},
        {"kind": "owner", "raw_value": "Partner", "canonical_value": partner},
    ]
    created_mapping_ids: list[int] = []
    for payload in mapping_payloads:
        resp = client.post("/mappings", json=payload)
        label = f"POST /mappings {payload['kind']}:{payload['raw_value']}"
        if report.expect_status(label, resp, 201) and resp.status_code == 201:
            created_mapping_ids.append(resp.json()["id"])
            if payload["raw_value"] == "Sale":
                report.expect_eq("mapping Sale cleaned to sale", resp.json()["raw_value"], "sale")

    if apple_id is not None:
        override = client.post(
            "/mappings",
            json={
                "kind": "category",
                "raw_value": "Other",
                "canonical_value": "Subscriptions",
                "account_id": apple_id,
            },
        )
        report.expect_status("POST /mappings Apple override Other", override, 201)

    invalid_kind = client.post(
        "/mappings",
        json={"kind": "nope", "raw_value": "x", "canonical_value": "y"},
    )
    report.expect_status("POST /mappings invalid kind", invalid_kind, 422)

    invalid_type = client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "Bonus",
            "canonical_value": "NOT_A_TYPE",
        },
    )
    report.expect_status("POST /mappings invalid type canonical", invalid_type, 422)

    unknown_acct = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Ghost",
            "canonical_value": "X",
            "account_id": 999999,
        },
    )
    report.expect_status("POST /mappings unknown account", unknown_acct, 404)

    dup_map = client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": " sale ",
            "canonical_value": "SPEND",
        },
    )
    report.expect_status("POST /mappings duplicate cleaned Sale", dup_map, 409)

    listed_maps = client.get("/mappings", params={"kind": "transaction_type"})
    report.expect_status("GET /mappings?kind=transaction_type", listed_maps, 200)
    if listed_maps.status_code == 200:
        kinds = {row["kind"] for row in listed_maps.json()}
        report.expect_eq("filtered mappings are transaction_type", kinds, {"transaction_type"})

    scoped_cat = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Warehouse",
            "canonical_value": "Household",
            "merchant": " Costco ",
        },
    )
    if report.expect_status("POST /mappings category merchant scope", scoped_cat, 201):
        report.expect_eq("scoped merchant cleaned", scoped_cat.json()["merchant"], "costco")

    unscoped_cat = client.post(
        "/mappings",
        json={
            "kind": "category",
            "raw_value": "Warehouse",
            "canonical_value": "Shopping",
        },
    )
    report.expect_status("POST /mappings same category unscoped", unscoped_cat, 201)

    merchant_on_type = client.post(
        "/mappings",
        json={
            "kind": "transaction_type",
            "raw_value": "Fee",
            "canonical_value": "ADJUSTMENT",
            "merchant": "Costco",
        },
    )
    report.expect_status("POST /mappings merchant on type rejected", merchant_on_type, 422)

    # --- imports ---
    if chase_id is not None:
        chase_imp = _import_file(client, chase_id, "chase.csv")
        if report.expect_status("POST /imports chase.csv", chase_imp, 200):
            body = chase_imp.json()
            report.expect_eq("chase total_rows_read", body["total_rows_read"], 4)
            report.expect_eq("chase inserted", body["inserted"], 4)
            report.expect_eq("chase errors", body["errors"], [])
            report.expect_eq("chase unmapped types", body["unmapped"]["transaction_types"], [])
            report.expect_eq("chase unmapped categories", body["unmapped"]["categories"], [])

        replay = _import_file(client, chase_id, "chase.csv")
        if report.expect_status("POST /imports chase.csv replay", replay, 200):
            body = replay.json()
            report.expect_eq("chase replay inserted", body["inserted"], 0)
            report.expect_eq("chase replay duplicates_skipped", body["duplicates_skipped"], 4)

        dirty = _import_file(client, chase_id, "dirty_rows.csv")
        if report.expect_status("POST /imports dirty_rows.csv", dirty, 200):
            body = dirty.json()
            report.expect_eq("dirty total_rows_read", body["total_rows_read"], 4)
            report.expect_eq("dirty inserted", body["inserted"], 2)
            report.expect_eq("dirty error count", len(body["errors"]), 2)
            report.expect_eq("dirty unmapped types", body["unmapped"]["transaction_types"], ["Fee"])
            report.expect_eq(
                "dirty unmapped categories",
                body["unmapped"]["categories"],
                ["Mystery Category"],
            )

        dupes = _import_file(client, chase_id, "dupes.csv")
        if report.expect_status("POST /imports dupes.csv", dupes, 200):
            body = dupes.json()
            report.expect_eq("dupes inserted", body["inserted"], 2)
            report.expect_eq("dupes duplicates_skipped", body["duplicates_skipped"], 1)

        allowed = _import_file(client, chase_id, "dupes.csv", allow_duplicates=True)
        if report.expect_status("POST /imports dupes.csv allow_duplicates", allowed, 200):
            body = allowed.json()
            report.expect_eq("dupes allow inserted", body["inserted"], 1)
            report.expect_eq("dupes allow duplicates_skipped", body["duplicates_skipped"], 2)

    if apple_id is not None:
        apple_imp = _import_file(client, apple_id, "apple_card.csv")
        if report.expect_status("POST /imports apple_card.csv", apple_imp, 200):
            body = apple_imp.json()
            report.expect_eq("apple inserted", body["inserted"], 3)
            report.expect_eq("apple errors", body["errors"], [])
            report.expect_eq("apple unmapped", body["unmapped"]["owners"], [])

    if debit_id is not None:
        sign_imp = _import_file(client, debit_id, "sign_only.csv")
        if report.expect_status("POST /imports sign_only.csv", sign_imp, 200):
            body = sign_imp.json()
            report.expect_eq("sign_only inserted", body["inserted"], 3)
            report.expect_eq("sign_only errors", body["errors"], [])

    unknown_import = _import_file(client, 999999, "chase.csv")
    report.expect_status("POST /imports unknown account", unknown_import, 404)

    # --- transactions ---
    all_tx = client.get("/transactions", params={"limit": 100})
    report.expect_status("GET /transactions", all_tx, 200)
    rows = _txn_rows(all_tx) if all_tx.status_code == 200 else []

    if chase_id is not None:
        chase_rows = client.get(
            "/transactions", params={"account_id": chase_id, "limit": 100}
        )
        if report.expect_status("GET /transactions?account_id=chase", chase_rows, 200):
            report.expect_eq("chase account row count", len(_txn_rows(chase_rows)), 8)

    if camilo_id is not None:
        by_owner = client.get(
            "/transactions", params={"owner_id": camilo_id, "limit": 100}
        )
        if report.expect_status("GET /transactions?owner_id=Camilo", by_owner, 200):
            report.expect_eq(
                "all Camilo rows belong to Camilo",
                {row["owner_id"] for row in _txn_rows(by_owner)},
                {camilo_id},
            )

    by_date = client.get(
        "/transactions",
        params={"date_from": "2024-06-10", "date_to": "2024-06-12", "limit": 100},
    )
    if report.expect_status("GET /transactions date range", by_date, 200):
        date_rows = _txn_rows(by_date)
        report.expect_eq("date range row count", len(date_rows), 3)
        report.expect_eq(
            "date range descriptions",
            sorted(row["description"] for row in date_rows),
            ["APPLE.COM", "UBER TRIP", "WHOLE FOODS"],
        )

    dining = client.get("/transactions", params={"category": "Dining", "limit": 100})
    if report.expect_status("GET /transactions?category=Dining", dining, 200):
        dining_rows = _txn_rows(dining)
        report.expect_eq("Dining count before override", len(dining_rows), 1)
        report.expect_eq(
            "Dining is Starbucks",
            dining_rows[0]["description"] if dining_rows else None,
            "STARBUCKS STORE 123",
        )

    apple_com = next((row for row in rows if row["description"] == "APPLE.COM"), None)
    if apple_com is None:
        report.record("Apple override category", "Subscriptions", "APPLE.COM missing", False)
    else:
        apple_detail = _txn_detail(client, apple_com["id"])
        report.expect_eq(
            "Apple account override Other→Subscriptions",
            apple_detail["category_normalized"],
            "Subscriptions",
        )
        report.expect_eq("APPLE.COM owner is Camilo", apple_com["owner_id"], camilo_id)

    uber = next((row for row in rows if row["description"] == "UBER TRIP"), None)
    if uber is None:
        report.record("UBER owner", partner_id, "UBER TRIP missing", False)
    else:
        report.expect_eq("UBER owner is Partner", uber["owner_id"], partner_id)

    grocery = next((row for row in rows if row["description"] == "GROCERY STORE"), None)
    paycheck = next((row for row in rows if row["description"] == "PAYCHECK"), None)
    reversal = next((row for row in rows if row["description"] == "ATM REVERSAL"), None)
    if grocery is None or paycheck is None or reversal is None:
        report.record("sign_only rows present", "3 rows", "missing", False)
    else:
        grocery_d = _txn_detail(client, grocery["id"])
        paycheck_d = _txn_detail(client, paycheck["id"])
        reversal_d = _txn_detail(client, reversal["id"])
        report.expect_eq("GROCERY is_spend", grocery_d["is_spend"], True)
        report.expect_eq("GROCERY type SPEND", grocery_d["transaction_type"], "SPEND")
        report.expect_eq("PAYCHECK is_spend", paycheck_d["is_spend"], False)
        report.expect_eq("PAYCHECK type INCOME", paycheck_d["transaction_type"], "INCOME")
        report.expect_eq("ATM REVERSAL is_spend", reversal_d["is_spend"], True)
        report.expect_eq("PAYCHECK amount", str(paycheck["amount"]), "1234.56")
        report.expect_eq("GROCERY amount", str(grocery["amount"]), "-54.32")
        report.expect_eq("ATM REVERSAL amount", str(reversal["amount"]), "-12.00")

    starbucks = next((row for row in rows if row["description"] == "STARBUCKS STORE 123"), None)
    if starbucks is None:
        report.record("PATCH Starbucks", "found", "missing", False)
    else:
        starbucks_d = _txn_detail(client, starbucks["id"])
        report.expect_eq(
            "Chase extracts STARBUCKS from description",
            starbucks_d.get("merchant_raw"),
            "STARBUCKS",
        )
        by_merchant = client.get(
            "/transactions", params={"merchant": "STARBUCKS", "limit": 100}
        )
        if report.expect_status("GET /transactions?merchant=STARBUCKS", by_merchant, 200):
            report.expect_eq(
                "merchant filter finds Starbucks without identity map",
                [row["id"] for row in _txn_rows(by_merchant)],
                [starbucks["id"]],
            )
        raw_before = starbucks_d["category_raw"]
        patched = client.patch(
            f"/transactions/{starbucks['id']}",
            json={"category_override": "Cafes"},
        )
        if report.expect_status("PATCH category_override", patched, 200):
            body = patched.json()
            report.expect_eq("override set", body["category_override"], "Cafes")
            report.expect_eq("category_raw unchanged", body["category_raw"], raw_before)

        cafes = client.get("/transactions", params={"category": "Cafes", "limit": 100})
        if report.expect_status("GET /transactions?category=Cafes", cafes, 200):
            report.expect_eq(
                "override wins category filter",
                [row["id"] for row in _txn_rows(cafes)],
                [starbucks["id"]],
            )

        if partner_id is not None:
            moved = client.patch(
                f"/transactions/{starbucks['id']}",
                json={"owner_id": partner_id},
            )
            if report.expect_status("PATCH owner_id to Partner", moved, 200):
                report.expect_eq("owner_id updated", moved.json()["owner_id"], partner_id)
            by_partner = client.get(
                "/transactions", params={"owner_id": partner_id, "limit": 100}
            )
            if report.expect_status("GET /transactions?owner_id=Partner", by_partner, 200):
                ids = {row["id"] for row in _txn_rows(by_partner)}
                report.expect_in("Partner list includes patched Starbucks", starbucks["id"], ids)

        missing_txn = client.patch("/transactions/999999", json={"category_override": "X"})
        report.expect_status("PATCH missing transaction", missing_txn, 404)

        bad_owner = client.patch(
            f"/transactions/{starbucks['id']}",
            json={"owner_id": 999999},
        )
        report.expect_status("PATCH unknown owner_id", bad_owner, 404)

    # --- mapping list/delete ---
    disposable = client.post(
        "/mappings",
        json={"kind": "category", "raw_value": "ToDelete", "canonical_value": "Temp"},
    )
    if report.expect_status("POST disposable mapping", disposable, 201):
        mid = disposable.json()["id"]
        deleted = client.delete(f"/mappings/{mid}")
        report.expect_status("DELETE /mappings/{id}", deleted, 200)
        if deleted.status_code == 200:
            report.expect_eq("DELETE returns deleted_id", deleted.json().get("deleted_id"), mid)
        again = client.delete(f"/mappings/{mid}")
        report.expect_status("DELETE /mappings/{id} missing", again, 404)

    # --- analytics ---
    invalid_group = client.get("/analytics/summary", params={"group_by": "nope"})
    report.expect_status("GET /analytics/summary invalid group_by", invalid_group, 422)

    by_cat = client.get("/analytics/by-category")
    if report.expect_status("GET /analytics/by-category", by_cat, 200):
        cats = {row["group_value"]: row for row in _summary_groups(by_cat)}
        report.expect_in("category Cafes after override", "Cafes", cats)
        report.expect_eq("Dining absent after override", "Dining" in cats, False)
        if "Shopping" in cats:
            report.expect_eq(
                "Shopping spend total",
                Decimal(str(cats["Shopping"]["total"])),
                Decimal("67.00"),
            )

    summary_cat = client.get("/analytics/summary", params={"group_by": "category"})
    if report.expect_status("GET /analytics/summary?group_by=category", summary_cat, 200):
        report.expect_eq("summary matches by-category", summary_cat.json(), by_cat.json())

    by_sub = client.get("/analytics/by-subcategory")
    summary_sub = client.get("/analytics/summary", params={"group_by": "subcategory"})
    if report.expect_status("GET /analytics/by-subcategory", by_sub, 200):
        report.expect_status(
            "GET /analytics/summary?group_by=subcategory", summary_sub, 200
        )
        report.expect_eq("summary matches by-subcategory", summary_sub.json(), by_sub.json())

    by_owner = client.get("/analytics/by-owner")
    if report.expect_status("GET /analytics/by-owner", by_owner, 200):
        owners = {row["group_value"]: row for row in _summary_groups(by_owner)}
        report.expect_in("owner grouping has Partner", partner, owners)
        report.expect_in("owner grouping has Camilo", camilo, owners)

    by_month = client.get("/analytics/by-month")
    if report.expect_status("GET /analytics/by-month", by_month, 200):
        months = [row["group_value"] for row in _summary_groups(by_month)]
        report.expect_in("month 2024-06 present", "2024-06", months)

    by_account = client.get("/analytics/summary", params={"group_by": "account"})
    if report.expect_status("GET /analytics/summary?group_by=account", by_account, 200):
        names = {row["group_value"] for row in _summary_groups(by_account)}
        report.expect_in("account grouping has Apple Card", f"Apple Card{suffix}", names)

    totals = client.get("/analytics/total")
    if report.expect_status("GET /analytics/total", totals, 200):
        body = totals.json()
        report.expect_eq("total count is positive", body["count"] > 0, True)
        report.expect_eq("total amount is positive", float(body["total"]) > 0, True)

    merchants = client.get("/analytics/top-merchants", params={"limit": 20})
    if report.expect_status("GET /analytics/top-merchants", merchants, 200):
        names = [row["merchant"] for row in merchants.json()]
        report.expect_in("top-merchants uses extracted STARBUCKS", "STARBUCKS", names)

    merchant_total = client.get("/analytics/total", params={"merchant": "STARBUCKS"})
    if report.expect_status("GET /analytics/total?merchant=STARBUCKS", merchant_total, 200):
        report.expect_eq(
            "Starbucks merchant total count",
            merchant_total.json()["count"],
            1,
        )

    dining_total = client.get("/analytics/total", params={"category": "Dining"})
    if report.expect_status("GET /analytics/total?category=Dining", dining_total, 200):
        report.expect_eq("Dining category total count", dining_total.json()["count"] > 0, True)

    by_merchant_group = client.get("/analytics/summary", params={"group_by": "merchant"})
    if report.expect_status("GET /analytics/summary?group_by=merchant", by_merchant_group, 200):
        merchant_names = {row["group_value"] for row in _summary_groups(by_merchant_group)}
        report.expect_in("group_by merchant has STARBUCKS", "STARBUCKS", merchant_names)
        report.expect_in("group_by merchant has Apple column value", "Apple", merchant_names)

    largest = client.get("/analytics/largest", params={"limit": 5})
    if report.expect_status("GET /analytics/largest", largest, 200):
        body = largest.json()
        if body.get("transactions"):
            first = Decimal(str(body["transactions"][0]["amount"]))
            report.expect_eq(
                "largest first row is max abs amount",
                abs(first) >= Decimal("54.32"),
                True,
            )
        totals = body.get("totals", {})
        if totals:
            purchases = Decimal(str(totals.get("purchases", "0")))
            refunds = Decimal(str(totals.get("refunds", "0")))
            spend = Decimal(str(totals.get("spend", "0")))
            report.expect_eq(
                "largest totals spend equals purchases minus refunds",
                spend,
                (purchases - refunds).quantize(Decimal("0.01")),
            )

    search = client.get("/analytics/search", params={"query": "amazon"})
    if report.expect_status("GET /analytics/search?query=amazon", search, 200):
        body = search.json()
        descs = {row["description"] for row in body.get("transactions", [])}
        report.expect_in("search finds AMAZON MARKETPLACE", "AMAZON MARKETPLACE", descs)
        types = {row["effective_type"] for row in body.get("transactions", [])}
        report.expect_in("search includes REFUND", "REFUND", types)

    values = client.get("/analytics/values", params={"dimension": "category"})
    if report.expect_status("GET /analytics/values?dimension=category", values, 200):
        names = {row["value"] for row in values.json().get("values", [])}
        report.expect_in("values catalog has Cafes", "Cafes", names)

    unmapped = client.get("/analytics/unmapped")
    if report.expect_status("GET /analytics/unmapped", unmapped, 200):
        body = unmapped.json()
        report.expect_in("unmapped type Fee", "Fee", body["transaction_types"])
        report.expect_in(
            "unmapped category Mystery Category",
            "Mystery Category",
            body["categories"],
        )


def build_test_client():
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import get_session
    from app.main import app
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    return client, session


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify finance-tracker HTTP API")
    parser.add_argument(
        "--base-url",
        help="Live server URL (e.g. http://localhost:8000). Default: in-process TestClient.",
    )
    args = parser.parse_args()
    report = Report()

    if args.base_url:
        import httpx

        suffix = f"-{int(time.time())}"
        with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
            run(client, suffix, report)
    else:
        client, session = build_test_client()
        try:
            run(client, "", report)
        finally:
            client.close()
            session.close()

    failures = report.print_table()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
