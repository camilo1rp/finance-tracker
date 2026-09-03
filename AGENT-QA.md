# Quick QA pass — agent CLI

One sitting, live model, data already in the ledger. Goal: prove coordinator routing → analyst comparisons → steward approval (including kill/restart) → numbers match the API. Not every tool edge.

Do the [Chase + Apple import pass](QA.md) first so you have owners, accounts, spend rows, and a real unmapped-category worklist.

```mermaid
flowchart LR
    Setup[Env and thread id] --> Total[Direct total]
    Total --> Compare[Owner comparison]
    Compare --> Cleanup[Mapping cleanup]
    Cleanup --> Pause[Approval interrupt]
    Pause --> Restart[Kill and resume]
    Restart --> Approve[edit subset]
    Approve --> Check[Spot-check API]
```

---

## 0. Setup (3 min)

API and Postgres from compose:

```bash
docker compose up --build
```

The CLI runs **on the host**, not inside the `api` container. Point it at published Postgres (`localhost`, not `db`):

```bash
cp .env.example .env
# DATABASE_URL=postgresql+psycopg://finance:finance@localhost:5433/finance
# STEWARD_MODEL=anthropic:claude-sonnet-4-6
# AGENT_CHECKPOINT_PATH=.agent_checkpoints.sqlite
```

Export `ANTHROPIC_API_KEY`. Install deps in the project venv if you have not:

```bash
.venv/bin/pip install -r requirements.txt
```

Pick a **thread id** you will reuse for the whole sitting (example `qa-agent-1`). Pick a **calendar month that exists in your import** (call it Month A) and the month before it (Month B). Write down one **owner name** and one **account name** from `GET /owners` / `GET /accounts`.

Write down `GET /analytics/unmapped` — you need leftover `categories` (or merchants) for the steward steps. If unmapped is empty, skip to a throwaway account or add a mapping you can later delete; the interrupt steps need something to propose.

Start the coordinator:

```bash
python -m app.agent.cli qa-agent-1
```

You should see `entrypoint=agent` and `thread_id=qa-agent-1`.

**Pass:** CLI reaches `you>` without a checkpointer or API-key error.

---

## 1. Coordinator answers a total itself (3 min)

Prompt (use Month A’s real `YYYY-MM` range in follow-ups if the model picks the wrong year):

```text
How much did we spend in <Month A>?
```

**Pass:**

- Answer includes a decimal total and a count
- It states filters: `spend_only` (should be true unless you asked otherwise) and the date range it used
- Dates are concrete `YYYY-MM-DD`, not “last month”
- Total is in the same ballpark as `GET /analytics/total?date_from=...&date_to=...&spend_only=true`

**Fail here:** invented numbers, no date range, or a pause for mapping approval (this question must not hit the steward).

Optional check that it resolved names before math:

```text
How much did <owner name> spend in <Month A>?
```

**Pass:** it looked up the owner (answer mentions the name or id) and the total matches `GET /analytics/total?owner_id=...` for that month.

---

## 2. Comparison goes through the analyst (5 min)

```text
Compare <Month B> vs <Month A> by category for <owner name>
```

**Pass:**

- Per-category lines with **two** month totals and a delta
- Filters stated (owner, dates, `spend_only`)
- Deltas match subtracting the two `GET /analytics/summary?group_by=category&owner_id=...` calls for those date ranges (spot-check 2–3 categories, including `(unassigned)` if it appears)
- No approval interrupt

Follow-up on the **same thread** (context should still know the owner/month):

```text
What were the top merchants in <Month A>?
```

**Pass:** names you recognize from `GET /analytics/top-merchants`; amounts are decimals from tools, not round guesses.

One search / listing check:

```text
Find transactions whose description contains <a merchant fragment you know>
```

**Pass:** hits include refunds/payments if they match (search is **not** spend-only). Matches `GET /analytics/search?query=...`.

---

## 3. Steward pause on the same conversation (5 min)

Stay on `qa-agent-1`. Do **not** start a new process yet.

```text
Clean up the unmapped categories
```

Wait until the CLI prints `=== Approval needed (nothing applied yet) ===`.

Write down:

- Rule indexes and `raw_value -> canonical_value`
- `Preview: scanned=... would_change=...`
- Per-rule `would_change` / `shadowed` / `duplicate_of`

Leave the prompt sitting at `decision>`.

**Pass:**

- Nothing was applied yet: `GET /analytics/unmapped` is unchanged from step 0; `GET /mappings` does not show the proposed canonical names as new rows
- Preview `would_change` is plausible (not all zeros if you still have unmapped spend with that `category_raw`)
- Rationale is non-empty

**Fail here:** CLI returns to `you>` and claims it already applied mappings; or interrupt payload has `preview` missing/`would_change` empty while unmapped categories clearly exist.

---

## 4. Kill / restart resumes the approval (3 min)

At `decision>`, **Ctrl-C** (or kill the process). Do not type `approve` yet.

Start again with the **same** thread id:

```bash
python -m app.agent.cli qa-agent-1
```

**Pass:** it reprints `=== Approval needed ===` with the **same ops and preview** before asking `you>` for a new chat turn. `GET /analytics/unmapped` still unchanged.

If it starts a blank `you>` with no interrupt, the checkpointer did not persist (wrong `DATABASE_URL`, wiped `.agent_checkpoints.sqlite`, or a new thread id). **Stop.**

Postgres sitting: `DATABASE_URL` is `postgresql+...` — checkpoints live in that database.

SQLite-file sitting (optional second pass): point `DATABASE_URL` at something non-Postgres and set `AGENT_CHECKPOINT_PATH`; repeat steps 3–4 once.

---

## 5. Approve a subset, then keep talking (5 min)

At `decision>`:

- If 2+ ops: type `edit 0` (or `edit 0,2` if you want two). Apply **less than all** listed ops
- If only one op: type `approve`

**Pass:**

- CLI leaves the interrupt, steward/coordinator reports what ran (`created_ids`, `updated_ids`, `deleted_ids`, `reclass_updated`)
- `GET /mappings` contains **only the approved subset**, not rejected indexes
- `POST /transactions/reclassify` is not required; apply already reclassified. Spot-check: a sample txn from the preview now has `category_normalized` equal to the canonical you approved
- `GET /analytics/unmapped` categories **shrink** for approved raw values and still list the ones you skipped
- Conversation returns to `you>` on **the same thread**. Ask `How much did we spend in <Month A>?` again — it should still answer (history intact)

Then, still on this thread, reject a second plan if the steward proposes more:

```text
Propose mappings for whatever categories are still unmapped
```

At `decision>`, type `reject`.

**Pass:** `GET /mappings` count unchanged by that second plan; agent says nothing was applied.

---

## 6. Standalone steward (optional, 5 min)

New thread, no coordinator:

```bash
python -m app.agent.cli --steward qa-agent-steward
```

```text
Clean up remaining unmapped merchants
```

**Pass:** same interrupt UX (`approve` / `reject` / `edit`). Prompt prefix is `steward>`. Kill/restart with `qa-agent-steward` still resumes.

Skip if step 5 already proved apply + reject.

---

## 7. Cross-check quirks (2 min)

These are the domain footguns. After a total/summary answer, confirm against the API:

| Quirk | How to catch it |
|---|---|
| Analytics default `spend_only=true` (abs amount, SPEND only) | Agent total ≈ `/analytics/total`; much higher than SPEND count → it turned `spend_only` off or counted payments |
| Analytics default `date_to=today` | A total with no end date should not include future-dated rows; range should be stated |
| `list` / `search` are **not** spend-only | Search for a known refund description; it should appear |
| Category/merchant filters use **effective** value (override → normalized → raw) | After a mapping apply, `/analytics/summary?group_by=category` uses the canonical name, not the bank raw string |
| `(unassigned)` | Null owner/category groups appear under that label, not dropped |
| Month buckets `YYYY-MM`, ascending | A `by month` answer is chronological, not largest-first |

Ask one question that **should** stay on the coordinator (`total for Month A`) and one that **should not** (`compare Month B vs Month A by merchant`). If the compare answer has no per-group deltas, the analyst path is not actually composing two summaries.

---

## Fail = stop

- CLI cannot start: missing `ANTHROPIC_API_KEY`, bad `DATABASE_URL` (host `db` from the host), or missing `langgraph-checkpoint-sqlite` / Postgres saver
- Totals that do not match `/analytics/total` for the same dates/owner/`spend_only` → fabricating or wrong filters
- Compare question never names categories/merchants or uses round numbers that are not in `/analytics/summary` → analyst skipped tools
- Cleanup applies mappings **before** `decision>` → interrupt not propagating
- Restart with the same thread id loses the approval → checkpointer not durable (in-memory fallback, new file path, or new thread id)
- `edit 0` still inserts every listed op → subset not forwarded to `apply_mapping_plan`
- `reject` still inserts rows → execute ran anyway
- After approve, `/analytics/unmapped` unchanged **and** `/mappings` unchanged → apply rolled back or never ran
- After approve, conversation dies or you must pass a new thread id to keep chatting → outer graph did not resume

Do not need: a third agent, chat UI, or importing a new CSV in this sitting. That is the [import QA](QA.md) pass.
