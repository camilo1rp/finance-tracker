# Quick QA pass — Chase + Apple

One sitting, two real CSVs. Goal: prove setup → import → classify → dedupe → analytics, not exhaust every edge.

```mermaid
flowchart LR
    Peek[Peek CSV headers] --> Owners[Create owners]
    Owners --> Accounts[Create Chase and Apple accounts]
    Accounts --> Import1[Import both files]
    Import1 --> Unmapped[Register unmapped rules]
    Unmapped --> Import2[Re-import both]
    Import2 --> Check[Spot-check txns and analytics]
```

Use Swagger at `http://localhost:8000/docs` or curl. Start the API first:

```bash
docker compose up --build
```

---

## 0. Peek the files (2 min)

Open each CSV and write down the **exact header names**. Real exports drift.

Typical Chase:

| Our mapping field | Usual Chase header |
|---|---|
| `date_col` | `Transaction Date` |
| `description_col` | `Description` |
| `amount_col` | `Amount` |
| `type_col` | `Type` |
| `category_col` | `Category` |

Typical Apple Card (Wallet export — not a random Apple Pay bank dump):

| Our mapping field | Usual Apple header |
|---|---|
| `date_col` | `Transaction Date` |
| `description_col` | `Description` |
| `amount_col` | `Amount (USD)` |
| `type_col` | `Type` |
| `category_col` | `Category` |
| `owner_col` | `Purchased By` |

If Apple has no `Purchased By`, leave `owner_col` off and use `default_owner_id`.

Skim 5 rows: note distinct `Type` values (`Sale` / `Return` / `Payment` vs `Purchase` / `Payment`) and a few `Purchased By` names.

---

## 1. One-time setup

**Owners** — `POST /owners` once per person you saw in Apple `Purchased By`, plus yourself for Chase default.

**Chase account** — `POST /accounts`

```json
{
  "name": "Chase",
  "last4": "XXXX",
  "default_owner_id": 1,
  "account_kind": "credit_card",
  "default_mapping": {
    "date_col": "Transaction Date",
    "description_col": "Description",
    "amount_col": "Amount",
    "type_col": "Type",
    "category_col": "Category"
  }
}
```

**Apple account** — same, plus `"account_kind": "credit_card"`, `"owner_col": "Purchased By"`, and `amount_col` matching the file (`Amount (USD)` if that is the header).

**Checking (sign-only)** — `"account_kind": "depository"` and mapping without `type_col`:

```json
{
  "name": "Checking",
  "last4": "XXXX",
  "default_owner_id": 1,
  "account_kind": "depository",
  "default_mapping": {
    "date_col": "Date",
    "description_col": "Description",
    "amount_col": "Amount",
    "sign_convention": "negative_is_spend"
  }
}
```

Kind-scoped type seeds are inserted on account create (`payment`→`TRANSFER` on cards, `ach_credit`→`INCOME` on depository, etc.). You usually **do not** need to POST those identities manually.

**Optional manual rule (checking card-pay):** if your checking export uses `LOAN_PMT` for card payments and you want them out of spend totals / into transfers:

```json
{
  "kind": "transaction_type",
  "raw_value": "loan_pmt",
  "canonical_value": "TRANSFER",
  "account_id": "<checking_account_id>"
}
```

Not seeded by default — Chase also uses `LOAN_PMT` for mortgage/auto.

**Merchant-scoped type rule (checking remittances):** Western Union (and similar) often arrives as `MISC_DEBIT` / SPEND. Scope TRANSFER to that merchant so rent and insurance stay spend:

```json
{
  "kind": "transaction_type",
  "raw_value": "misc_debit",
  "canonical_value": "TRANSFER",
  "account_id": "<checking_account_id>",
  "merchant": "Western Union"
}
```

`merchant` matches the resolved label exactly, or as a leading prefix (`WESTERN UNION CAPTURE 623…` hits `western union`). Then `POST /transactions/reclassify?account_id=…`.

Do **not** create one merchant alias per capture id. One prefix alias is enough:

```json
{
  "kind": "merchant",
  "raw_value": "western union",
  "canonical_value": "Western Union",
  "account_id": "<checking_account_id>"
}
```

Same prefix applies to Marshalls-style store-number suffixes (`marshalls` hits `marshalls #59 …`).

**Type mappings** — `POST /mappings` only for values **not** covered by seeds (global):

| raw_value | canonical_value |
|---|---|
| Sale | SPEND |
| Purchase | SPEND |
| Return | REFUND |

Do **not** POST `Payment` → `PAYMENT` (rejected). Card payments are `TRANSFER` via account seed; paychecks on sign-only checking are `INCOME`.

Add any other Type you saw in step 0 (`Adjustment`, `Fee`, …).

**Owner mappings** — raw Apple name → registered `Owner.name` (exact canonical name).

Skip category mappings on the first import; let `unmapped` tell you what showed up.

---

## 2. Import

`POST /imports?account_id={chase_id}` with the Chase file.  
`POST /imports?account_id={apple_id}` with the Apple file.

For each response, write down:

- `total_rows_read` vs lines in the file (minus header)
- `inserted`
- `duplicates_skipped` (should be 0 first time)
- `errors` (should be `[]`; if not, one bad date/amount format)
- `unmapped.transaction_types` / `categories` / `owners`

**Pass:** inserted ≈ rows minus errors; no unexpected errors.

**Then:** `POST /mappings` for anything in `unmapped` you care about (at least types and owners). Categories can wait except 2–3 you will check in analytics.

Already-imported rows do not pick up new rules by themselves. After adding mappings:

`POST /transactions/reclassify?account_id={chase_id}`

**Pass:** `updated` > 0; `unmapped.transaction_types` no longer lists rules you just added.

**Re-import both files.**

**Pass:** `inserted == 0`, `duplicates_skipped` equals first `inserted`.

---

## 3. Spot-check data (5 min)

`GET /transactions?account_id={chase_id}`

- A `Sale` / `Purchase` is `transaction_type=SPEND`, `is_spend=true`
- A card `Payment` is `TRANSFER`, `is_spend=false`
- Sign-only paycheck credits are `INCOME`, `is_spend=false`
- `category_raw` matches the file; `category_normalized` is set only if you mapped it
- Chase rows use the default owner
- Apple rows with `Purchased By` have the right `owner_id`

`PATCH /transactions/{id}` — set `category_override` on one miscategorized row.

**Pass:** `category_raw` unchanged; `GET /transactions?category=...` finds it via override.

---

## 4. Analytics smoke

All of these default to `spend_only=true` except search.

| Call | What to look for |
|---|---|
| `GET /analytics/by-category` | Override category appears; mapped name not the raw Chase string if you mapped it |
| `GET /analytics/by-owner` | Chase default owner + Apple people; no silent drop (nulls show as `(unassigned)`) |
| `GET /analytics/by-month` | `YYYY-MM` buckets, chronological |
| `GET /analytics/summary?group_by=account` | Chase vs Apple totals |
| `GET /analytics/total` | `spend` = purchases − refunds; `net_cash_flow` = income + refunds − purchases − fees |
| `GET /analytics/top-merchants?limit=10` | Descriptions you recognize |
| `GET /analytics/largest?limit=5` | Biggest **absolute** spends, not a large payment |
| `GET /analytics/search?query=` | A merchant fragment; refunds/payments **do** appear |
| `GET /analytics/unmapped` | Shrinks after you added rules; leftover is the real worklist |
| `GET /analytics/cash-flow` | Buckets by effective type; `other` holds ADJUSTMENT/UNKNOWN; `net` excludes transfers and `other` |

**Fix mis-kinded account (SQL + reclassify):**

```sql
UPDATE accounts SET account_kind = 'depository' WHERE id = <checking_id>;
-- or 'credit_card' for card accounts
```

Then `POST /transactions/reclassify?account_id=<id>`.

Filter one call with `account_id` and one with `date_from` / `date_to` for a month you know is in the file.

---

## Fail = stop

- Import `errors` on most rows → column names or date/amount format wrong
- Everything `UNKNOWN` → type mappings not hitting (`Sale` vs `sale` is fine; `Sale ` extra words is not)
- Apple everyone is the Chase default owner → `owner_col` missing or names unmapped
- Re-import inserts again → dedupe hash identity changed (date/amount/description parsed differently)
- `/analytics/total` much lower than spend rows → `spend_only` hiding `UNKNOWN` types you never mapped
- Largest row is a payment → you passed `spend_only=false` by accident

Do not need: sign-only accounts, dirty-row fixtures, or a third card. That is a later pass.
