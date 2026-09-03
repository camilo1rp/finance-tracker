# PROJECT-MAP

Planning reference for `finance-tracker-skeleton`. Derived from source and tests. No code bodies.

## 0. Freshness

| Field | Value |
|---|---|
| Generated | 2026-09-03 |
| Branch | `main` |
| HEAD SHA | (post observability slice) |
| Working tree | LangSmith tracing + LangGraph Studio entrypoints |
| Python (venv) | 3.14.5 (Studio requires ≥3.11 and &lt;3.14; use Dockerfile 3.12 or a 3.11–3.13 venv) |
| Dockerfile base | `python:3.12-slim` |
| Tests | **119 passed** (`pytest -q`) |

### Reconciled counts

| Item | Count |
|---|---|
| HTTP app endpoints | **24** (health 1 + accounts 2 + owners 2 + mappings 6 + imports 1 + transactions 3 + analytics 9). Excludes FastAPI `/docs`, `/redoc`, `/openapi.json`. |
| Tables | **5** (`owners`, `accounts`, `normalization_mappings`, `import_batches`, `transactions`) |
| Agent tools | **14** (12 read / 2 gate-or-delegate; **0** DB-apply tools) |
| Graphs in `langgraph.json` | **3** (`coordinator`, `steward`, `analyst`) |
| Tests passing | **119** |

### Versions

Pinned in `requirements.txt` (ranges/unpinned) vs installed in `.venv`:

| Package | requirements.txt | Installed |
|---|---|---|
| fastapi | unpinned | 0.141.1 |
| sqlalchemy | `>=2.0` | 2.0.52 |
| pydantic | (transitive) | 2.13.5 |
| langchain | `>=1.0,<2` | 1.3.18 |
| langgraph | `>=1.0,<2` | 1.2.11 |
| langgraph-checkpoint | (transitive of sqlite/postgres extras) | 4.2.0 |
| langgraph-checkpoint-sqlite | unpinned | 3.1.1 |
| langgraph-checkpoint-postgres | unpinned | 3.1.2 |
| langgraph-cli | `requirements-dev.txt` (`[inmem]`) | install via dev deps |
| langsmith | **not listed** (transitive via LangChain) | 0.12.1 |
| langchain-anthropic | unpinned | 1.7.0 |
| langchain-openai | unpinned | 1.6.0 |
| uvicorn | `uvicorn[standard]` | 0.52.4 |
| pydantic-settings | unpinned | 2.15.0 |
| psycopg | `psycopg[binary]` | 3.3.4 |
| pytest | unpinned | 9.1.1 |

Also installed (used, not in the required version table): `langchain-core==1.6.1`, `python-dotenv==1.2.3` (not in `requirements.txt`; pulled via `pydantic-settings`), `pandas==3.0.5`, `httpx==0.28.1`.

---

## 1. Executive summary

Personal family ledger: ingest card CSVs, normalize/classify rows, query spend, and clean mapping rules via a CLI agent.

Three layers:

1. **FastAPI CRUD/analytics** — owners, accounts, mappings, imports, transactions, analytics. Run: `docker compose up --build` (uvicorn on `:8000`) or `uvicorn app.main:app`. App factory `app/main.py::create_app`; `lifespan` calls `app/database.py::init_db`.
2. **Staged CSV ingest + normalization** — `TransactionSource.fetch` → `normalize_rows` (classify via `NormalizationLookup`) → `compute_dedupe_hash` / `split_new_and_duplicates` → persist. HTTP: `POST /imports`. Orchestrator: `app/services/ingest_service.py::ingest_from_source`.
3. **LangGraph multi-agent** — coordinator (entrypoint) → tools + `ask_analyst` / `run_data_steward`; steward graph `propose → preview → interrupt → apply`. Run: `python -m app.agent.cli [thread_id]` or `--steward`. Recursion limit 25 (`app/agent/cli.py::RECURSION_LIMIT`). **Studio:** `make studio` / `langgraph dev` via `langgraph.json` (three graphs). **Tracing:** optional LangSmith env vars; CLI passes `run_name`/`tags`/metadata on invoke and resume.

Tests: `pytest` (`pytest.ini`: `pythonpath=.`, `testpaths=tests`). SQLite in-process; scripted fake chat models; no live LLM.

---

## 2. Annotated file tree

Regenerated from disk. One-line purpose each.

```
finance-tracker-skeleton/
├── .env.example                 # DATABASE_URL, STEWARD_MODEL, AGENT_CHECKPOINT_PATH, LANGSMITH_* (commented)
├── .gitignore                   # venv, pytest cache, .env, sqlite checkpoints
├── AGENT-QA.md                  # live-model CLI sitting (may be stale)
├── Dockerfile                   # python:3.12-slim; pip install; uvicorn app.main:app
├── Makefile                     # make studio → langgraph dev
├── langgraph.json               # Studio graphs: coordinator, steward, analyst
├── QA.md                        # Chase+Apple HTTP import sitting (may be stale)
├── README.md                    # API + agent CLI + Observability
├── STRUCTURE.md                 # tree + agent notes
├── requirements-dev.txt         # langgraph-cli[inmem] for Studio
├── docker-compose.yml           # postgres:16 + api (uvicorn --reload :8000)
├── pytest.ini                   # pythonpath=., testpaths=tests
├── requirements.txt             # Python deps (mostly unpinned)
├── app/__init__.py              # empty
├── app/main.py                  # FastAPI factory, router mount, GET /health
├── app/config.py                # Settings.database_url from env
├── app/database.py              # engine/session, init_db + additive ALTER helpers
├── app/models.py                # ORM + effective_category / effective_merchant
├── app/schemas.py               # API Pydantic models + MappingOp union
├── app/domain/__init__.py       # empty
├── app/domain/transaction.py    # CanonicalTransaction, UnmappedValues, results
├── app/domain/mapping.py        # ImportMapping, SignConvention, resolve_mapping
├── app/domain/sources.py        # TransactionSource ABC + CsvSource
├── app/domain/normalize.py      # raw row → CanonicalTransaction
├── app/domain/dedupe.py         # hash + new/duplicate split
├── app/domain/classification.py # enums, NormalizationLookup, classify_*, clean_raw_value
├── app/domain/db_lookup.py      # DbNormalizationLookup + merged_lookup_from_db
├── app/domain/merged_lookup.py  # in-memory lookup over RuleSpec list
├── app/domain/merchant.py       # extract_merchant, resolved_merchant
├── app/services/__init__.py     # empty
├── app/services/ingest_service.py
├── app/services/analytics_service.py
├── app/services/mapping_preview_service.py
├── app/routers/__init__.py      # empty
├── app/routers/accounts.py
├── app/routers/owners.py
├── app/routers/mappings.py
├── app/routers/imports.py
├── app/routers/transactions.py
├── app/routers/analytics.py
├── app/agent/__init__.py
├── app/agent/cli.py             # REPL; approve/reject/edit; resume by thread_id; trace config
├── app/agent/studio.py          # LangGraph Studio factories (no compile-time checkpointer)
├── app/agent/coordinator.py     # outer create_agent; checkpointer required for CLI
├── app/agent/analyst.py         # read-only create_agent; no checkpointer
├── app/agent/steward_graph.py   # StateGraph propose→interrupt→apply
├── app/agent/config.py          # model, tool_session, checkpointer factory
├── app/agent/middleware.py      # CurrentDateMiddleware
├── app/agent/schemas.py         # StewardState
├── app/agent/tools/__init__.py  # STEWARD_AGENT_TOOLS
├── app/agent/tools/read.py      # owners/accounts/mappings/txns + analytics tools
├── app/agent/tools/steward.py   # preview_mapping_rules, submit_plan (no apply)
├── app/agent/tools/subagents.py # ask_analyst, run_data_steward wrappers
├── scripts/verify_api.py        # HTTP walkthrough (TestClient or --base-url)
├── scripts/fixtures/*.csv       # Chase/Apple/dirty/dupes/sign_only sample files
├── tests/conftest.py            # SQLite engine, client, tracing disabled (autouse)
├── tests/test_tracing_isolation.py
├── tests/fakes.py               # InMemoryNormalizationLookup
├── tests/test_smoke.py          # health, docs, tables
├── tests/agent/helpers.py       # ScriptedChatModel, seed, capture_apply
└── tests/{domain,routers,services,agent}/test_*.py
```

### STRUCTURE.md vs disk

**On disk, absent from STRUCTURE.md:** `app/domain/merchant.py`, `app/agent/middleware.py`, `app/agent/schemas.py`, `app/agent/tools/__init__.py`, empty `__init__.py` files, `pytest.ini`, `.gitignore`, `scripts/` (and fixtures), individual test modules (`tests/fakes.py`, `tests/agent/helpers.py`, …).

**In STRUCTURE.md, absent from disk:** none (listed paths exist). STRUCTURE's agent note about compiling without a checkpointer is directionally right for nested steward; analyst is not a compiled subgraph of the coordinator — it is a tool that `invoke`s a separate graph (see §8).

**`langgraph.json`:** on disk at repo root; three graphs via `app/agent/studio.py` factories.

---

## 3. Data model

Five tables. No Alembic; `app/database.py::init_db` runs `Base.metadata.create_all` then additive ALTERs.

### 3.1 `owners` — `app/models.py::Owner`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | Integer PK | no | auto | |
| name | String | no | — | `unique=True`. HTTP create stores `payload.name.strip()` (`app/routers/owners.py::create_owner`). |

### 3.2 `accounts` — `app/models.py::Account`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | Integer PK | no | auto | |
| name | String | no | — | |
| last4 | String | no | — | |
| default_owner_id | Integer FK → `owners.id` | yes | — | |
| source_format | String | no | HTTP `"csv"` | comment: extensible `"pdf"`/`"api"` |
| default_mapping | JSON | no | — | serialized `ImportMapping` dict. Comment says JSONB; type is `JSON`. |

Relationship: `default_owner`, `transactions`.

### 3.3 `normalization_mappings` — `app/models.py::NormalizationMapping`

Constraint (verbatim):

```
UniqueConstraint(
    "kind",
    "raw_value",
    "account_id",
    "merchant",
    name="uq_normalization_rule",
)
```

Postgres live DBs: `app/database.py::_ensure_mapping_merchant_scope` drops/re-adds that name as `UNIQUE (kind, raw_value, account_id, merchant)` if the old 3-column unique remains.

| Column | Type | Null | Notes |
|---|---|---|---|
| id | Integer PK | no | |
| kind | String | no | `NormalizationKind` value |
| raw_value | String | no | **stored cleaned** (trim+lowercase) |
| canonical_value | String | no | not cleaned; `transaction_type` must be a `TransactionType` at write |
| account_id | Integer FK → `accounts.id` | yes | NULL = global |
| merchant | String | yes | category only; NULL = all merchants; **stored cleaned** |

NULL in unique keys: Postgres/SQLite treat NULLs as distinct, so DB unique does **not** by itself prevent two global rules with `account_id IS NULL` / `merchant IS NULL`. App-layer identity checks do (`app/routers/mappings.py::create_mapping`, `app/services/mapping_preview_service.py::find_mapping_by_identity`).

### 3.4 `import_batches` — `app/models.py::ImportBatch`

| Column | Type | Null | Notes |
|---|---|---|---|
| id | Integer PK | no | |
| account_id | Integer FK → `accounts.id` | no | |
| filename | String | no | |
| imported_at | DateTime | no | set in ingest as UTC-now with tzinfo stripped |
| row_count | Integer | no | `len(source rows)`, not inserted count |

A batch row is created even when every CSV row is a duplicate (`app/services/ingest_service.py::ingest_from_source`).

### 3.5 `transactions` — `app/models.py::Transaction`

Constraint (verbatim): `UniqueConstraint("dedupe_hash", name="uq_transaction_dedupe_hash")`. Also `dedupe_hash` `index=True`.

| Column | Type | Null | Notes |
|---|---|---|---|
| id | Integer PK | no | |
| account_id | Integer FK → `accounts.id` | no | |
| import_batch_id | Integer FK → `import_batches.id` | yes | set on ingest |
| owner_id | Integer FK → `owners.id` | yes | |
| owner_raw | String | yes | source owner cell, stripped, not cleaned |
| transaction_date | Date | no | |
| description | String | no | stripped |
| amount | Numeric(12, 2) | no | signed as imported |
| transaction_type | String | no | `TransactionType` value |
| is_spend | Boolean | no | `transaction_type == SPEND` |
| raw_type | String | yes | type cell stripped; None for sign-derived |
| category_raw | String | yes | stripped, not lowercased |
| category_normalized | String | yes | lookup canonical or None |
| category_override | String | yes | PATCH only; reclassify never writes |
| merchant_raw | String | yes | column or extracted; whitespace-collapsed |
| merchant_normalized | String | yes | lookup canonical or None |
| merchant_override | String | yes | PATCH only; reclassify never writes |
| dedupe_hash | String | no | sha256 identity |
| raw | JSON | no | original row dict, untouched |

Existing DBs: `app/database.py::_ensure_merchant_columns` `ALTER TABLE transactions ADD COLUMN {merchant_raw,merchant_normalized,merchant_override} VARCHAR` if missing.

### 3.6 Computed SQL (verbatim) — `app/models.py`

```
effective_category = case(
    (Transaction.category_override.isnot(None), Transaction.category_override),
    (Transaction.category_normalized.isnot(None), Transaction.category_normalized),
    else_=Transaction.category_raw,
)

effective_merchant = case(
    (Transaction.merchant_override.isnot(None), Transaction.merchant_override),
    (Transaction.merchant_normalized.isnot(None), Transaction.merchant_normalized),
    else_=Transaction.merchant_raw,
)
```

Python equivalent for merchant: `app/domain/merchant.py::resolved_merchant` — override > normalized > raw (strip; empty → skip).

### 3.7 Enums (verbatim value sets)

`app/domain/classification.py::TransactionType`: `SPEND`, `REFUND`, `PAYMENT`, `ADJUSTMENT`, `UNKNOWN`.

`app/domain/classification.py::NormalizationKind`: `transaction_type`, `category`, `owner`, `merchant`.

`app/domain/mapping.py::SignConvention`: `negative_is_spend`, `positive_is_spend`.

`app/schemas.py::MappingKind`: same four strings as `NormalizationKind`.

### 3.8 Cleaning

`app/domain/classification.py::clean_raw_value` — `raw_value.strip().lower()`.

Applied at write: `app/routers/mappings.py::create_mapping` (raw_value + merchant), `app/services/mapping_preview_service.py::_create_spec` / `_cleaned_merchant`. Canonical values are **not** cleaned.

Applied at read: `classify_transaction_type`, `classify_category` (raw + merchant), `classify_owner`, `classify_merchant` — all in `app/domain/classification.py`. Lookups assume already-cleaned keys (`DbNormalizationLookup.resolve`, `MergedNormalizationLookup.resolve`).

Not cleaned: `category_raw` / `owner_raw` / `raw_type` / `description` as stored on transactions (stripped only). Merchant extraction collapses whitespace (`extract_merchant` / merchant_col `" ".join(str.split())`).

### 3.9 ER

```mermaid
erDiagram
    Owner ||--o{ Account : default_owner
    Owner ||--o{ Transaction : owns
    Account ||--o{ Transaction : has
    Account ||--o{ ImportBatch : imports
    Account ||--o{ NormalizationMapping : scopes
    ImportBatch ||--o{ Transaction : batch

    Owner {
        int id PK
        string name UK
    }
    Account {
        int id PK
        string name
        string last4
        int default_owner_id FK
        string source_format
        json default_mapping
    }
    NormalizationMapping {
        int id PK
        string kind
        string raw_value
        string canonical_value
        int account_id FK
        string merchant
    }
    ImportBatch {
        int id PK
        int account_id FK
        string filename
        datetime imported_at
        int row_count
    }
    Transaction {
        int id PK
        int account_id FK
        int import_batch_id FK
        int owner_id FK
        string owner_raw
        date transaction_date
        string description
        numeric amount
        string transaction_type
        bool is_spend
        string raw_type
        string category_raw
        string category_normalized
        string category_override
        string merchant_raw
        string merchant_normalized
        string merchant_override
        string dedupe_hash UK
        json raw
    }
```

---

## 4. HTTP API

App: `app/main.py::create_app` (title `"Family Finance Tracker"`). All routers use `Depends(app/database.py::get_session)` — session closed after request; **commit is the callee's job** (router or service). Pydantic/query validation → **422** (FastAPI). Status `HTTP_422_UNPROCESSABLE_CONTENT` is used for domain 422s.

Shared analytics query params (unless noted): `date_from: date | None = None`, `date_to: date | None = None`, `account_id: int | None = None`, `owner_id: int | None = None`, `merchant: str | None = None`, `spend_only: bool = True`. Service layer **always** upper-bounds `transaction_date` at `date.today()` when `date_to` is omitted (`app/services/analytics_service.py::_resolved_date_to` via `_apply_filters`). `merchant` matches **effective_merchant**. There is **no category filter** on analytics.

### 4.1 `app/main.py`

| Method | Path | Params | Body | Response | Status | Delegates | Quirks |
|---|---|---|---|---|---|---|---|
| GET | `/health` | none | — | `{status: "ok"}` | 200 | `app/main.py::health` | |

### 4.2 `app/routers/accounts.py` (2)

| Method | Path | Params | Body | Response | Status | Delegates | Quirks |
|---|---|---|---|---|---|---|---|
| POST | `/accounts` | — | `AccountCreate` | `AccountOut` | 201; 404 unknown owner; 422 invalid mapping / `sign_convention` | inline insert; mapping via `mapping_from_payload` | `AccountOut` **omits** `default_mapping`. Commits in router. |
| GET | `/accounts` | — | — | `list[AccountOut]` | 200 | inline select | ordered by id |

### 4.3 `app/routers/owners.py` (2)

| Method | Path | Params | Body | Response | Status | Delegates | Quirks |
|---|---|---|---|---|---|---|---|
| POST | `/owners` | — | `OwnerCreate` | `OwnerOut` | 201; 409 duplicate name | inline insert | `name.strip()`; IntegrityError → 409. Commits in router. |
| GET | `/owners` | — | — | `list[OwnerOut]` | 200 | inline select | ordered by id |

### 4.4 `app/routers/mappings.py` (6)

| Method | Path | Params | Body | Response | Status | Delegates | Quirks |
|---|---|---|---|---|---|---|---|
| POST | `/mappings` | — | `NormalizationMappingCreate` | `NormalizationMappingOut` | 201; 404 unknown account; 409 identity exists; 422 bad kind / type canonical / merchant-on-non-category | inline insert | **Does not reclassify.** Cleans raw_value + merchant. Commits in router. |
| GET | `/mappings` | `kind=None`, `account_id=None` | — | `list[NormalizationMappingOut]` | 200; 422 bad kind | inline select | `account_id` **excludes** globals (`IS NULL` not included). |
| POST | `/mappings/preview` | — | `MappingPlanIn` | `MappingPreview` | 200 | `preview_mappings` | Pure read. Invalid ops listed in `validation_errors`; valid ops still scored. |
| POST | `/mappings/apply` | — | `MappingPlanIn` | `ApplyResult` | 200; 422 `MappingPlanValidationError.errors`; 404 unknown `plan.account_id` | `apply_mapping_plan` | Writes + reclassify one txn; idempotent re-apply. |
| PATCH | `/mappings/{mapping_id}` | path id | `MappingPatchIn` | `MappingPatchOut` | 200; 404 missing; 422/404 from apply | `apply_mapping_plan` with one `UpdateMappingOp` | Reclassifies (no account scope). Returns mapping + reclass counts. |
| DELETE | `/mappings/{mapping_id}` | path id | — | `MappingDeleteOut` | **200** (not 204); 404 missing | `apply_mapping_plan` with one `DeleteMappingOp` | Reclassifies. Body: `deleted_id`, `reclass_scanned`, `reclass_updated`. |

### 4.5 `app/routers/imports.py` (1)

| Method | Path | Params | Body | Response | Status | Delegates | Quirks |
|---|---|---|---|---|---|---|---|
| POST | `/imports` | `account_id: int` (query) | multipart `file: UploadFile` | `ImportResult` | **200** (not 201); 404 unknown account | `ingest_from_source` (`CsvSource`, `fetch_kwargs={"file_path": file.file}`) | Always creates an `ImportBatch`. |

### 4.6 `app/routers/transactions.py` (3)

| Method | Path | Params | Body | Response | Status | Delegates | Quirks |
|---|---|---|---|---|---|---|---|
| POST | `/transactions/reclassify` | `account_id=None` | — | `ReclassifyResultOut` | 200; 404 unknown account | `reclassify_transactions` | Does not re-import. Commits in service. |
| GET | `/transactions` | `date_from`, `date_to`, `owner_id`, `category`, `merchant`, `account_id` all optional | — | `list[TransactionOut]` | 200 | inline select | **No** `spend_only`. **No** `date_to` default to today. `category`/`merchant` = **effective** values. No limit. |
| PATCH | `/transactions/{transaction_id}` | path id | `TransactionPatch` | `TransactionOut` | 200; 404 txn or owner | inline | Only fields in `model_fields_set`. Never writes `category_raw` / `merchant_raw`. Commits in router. |

### 4.7 `app/routers/analytics.py` (9)

All except `/unmapped` and `/search` default `spend_only=True`. All except `/unmapped` go through `_apply_filters` → **`date_to` defaults to today**. `spend_only` filters `transaction_type == SPEND` and sums `abs(amount)`.

| Method | Path | Extra params | Response | Delegates |
|---|---|---|---|---|
| GET | `/analytics/summary` | required `group_by: category\|owner\|month\|account\|merchant`; 422 if invalid | `list[GroupSummary]` | `summarize` |
| GET | `/analytics/by-category` | — | `list[GroupSummary]` | `summarize(..., "category")` |
| GET | `/analytics/by-owner` | — | `list[GroupSummary]` | `summarize(..., "owner")` |
| GET | `/analytics/by-month` | — | `list[GroupSummary]` | `summarize(..., "month")` |
| GET | `/analytics/total` | — | `TotalOut` | `get_total` |
| GET | `/analytics/top-merchants` | `limit: int = 10` (`ge=1`) | `list[MerchantSummary]` | `top_merchants` |
| GET | `/analytics/largest` | `limit: int = 10` (`ge=1`) | `list[TransactionOut]` | `largest_transactions` |
| GET | `/analytics/search` | required `query: str`; `limit: int = 50` (`ge=1`); **no `spend_only`** | `list[TransactionOut]` | `search_transactions` (`spend_only=False`) |
| GET | `/analytics/unmapped` | none | `UnmappedValuesOut` | `unmapped_summary` |

`by-*` are aliases of `summary` with a fixed `group_by`. Proven: `tests/routers/test_analytics.py::test_by_category_alias_matches_summary`.

---

## 5. Schemas / DTOs

### 5.1 `app/schemas.py`

| Name | Fields (summary) | Consumed by |
|---|---|---|
| `OwnerCreate` | `name: str` | `POST /owners` |
| `OwnerOut` | `id`, `name` | owners HTTP; `list_owners` tool |
| `NormalizationMappingCreate` | `kind`, `raw_value`, `canonical_value`, `account_id=None`, `merchant=None` | `POST /mappings` |
| `NormalizationMappingOut` | `id`, `kind`, `raw_value`, `canonical_value`, `account_id`, `merchant=None` | mappings HTTP GET/POST; `list_mappings` tool |
| `MappingKind` | Literal of four kinds | `CreateMappingOp.kind`; `SampleChange.field` |
| `CreateMappingOp` | `op="create"`, `kind`, `raw_value`, `canonical_value`, `account_id=None`, `merchant=None` | plan ops |
| `UpdateMappingOp` | `op="update"`, `mapping_id`, `canonical_value` | plan ops; PATCH wrapper |
| `DeleteMappingOp` | `op="delete"`, `mapping_id` | plan ops; DELETE wrapper |
| `MappingOp` | discriminated union on `op` | plans, preview, apply, tools, steward state |
| `MappingPlanIn` | `ops: list[MappingOp]`, `account_id=None` (scan/reclass scope) | preview/apply HTTP + services + steward |
| `MappingPatchIn` | `canonical_value` | `PATCH /mappings/{id}` |
| `SampleChange` | `transaction_id`, `description`, `field`, `current_effective`, `new_effective` | `OpImpact.samples` |
| `FallbackCount` | `mapping_id`, `count` | delete impact |
| `OpImpact` | `index`, `op`, `would_change=0`, `suppressed_by_override=0`, `shadowed_by_existing=0`, `duplicate_of_existing_id`, `conflicts_with_existing_id`, `existing_canonical`, `old_canonical`, `new_canonical`, `falls_back_to`, `would_become_unmapped=0`, `samples` | `MappingPreview.ops` |
| `MappingPreview` | `scanned`, `total_would_change`, `ops`, `validation_errors` | preview HTTP/tool; interrupt payload |
| `MappingPatchOut` | mapping fields + `reclass_scanned`, `reclass_updated` | PATCH mapping |
| `MappingDeleteOut` | `deleted_id`, `reclass_scanned`, `reclass_updated` | DELETE mapping |
| `ImportMappingIn` | `date_col`, `description_col`, `amount_col`, optional `category_col`, `owner_col`, `type_col`, `merchant_col`, `sign_convention` | `AccountCreate.default_mapping` |
| `AccountCreate` | `name`, `last4`, `default_owner_id=None`, `source_format="csv"`, `default_mapping` | `POST /accounts` |
| `AccountOut` | `id`, `name`, `last4`, `default_owner_id`, `source_format` | accounts HTTP; `list_accounts` tool (**no mapping**) |
| `UnmappedValuesOut` | `transaction_types`, `categories`, `owners`, `merchants=[]` | import/reclass/apply/unmapped |
| `SkippedOp` | `op: MappingOp`, `reason: "duplicate" \| "missing"` | `ApplyResult.skipped` |
| `ApplyResult` | `created_ids`, `updated_ids`, `deleted_ids`, `skipped`, `reclass_scanned`, `reclass_updated`, `unmapped_after` | apply HTTP; steward `apply_result` |
| `ImportResult` | `account_id`, `import_batch_id`, `total_rows_read`, `inserted`, `duplicates_skipped`, `unmapped`, `errors` | `POST /imports` |
| `TransactionOut` | `id`, `account_id`, `transaction_date`, `description`, `amount`, `transaction_type`, `is_spend`, category triple, `owner_id`, merchant triple | list/patch/largest/search tools. **Omits** `owner_raw`, `raw_type`, `dedupe_hash`, `raw`, `import_batch_id` |
| `ReclassifyResultOut` | `scanned`, `updated`, `unmapped` | `POST /transactions/reclassify` |
| `TransactionPatch` | `category_override=None`, `owner_id=None`, `merchant_override=None` | PATCH txn |
| `GroupSummary` | `group_value`, `total`, `count` | summarize |
| `TotalOut` | `total`, `count`, `average` | get_total |
| `MerchantSummary` | `merchant`, `total`, `count` | top_merchants |

**Discriminated union:** `MappingOp = Annotated[Union[CreateMappingOp, UpdateMappingOp, DeleteMappingOp], Field(discriminator="op")]`. Parser: `app/schemas.py::parse_mapping_op` (passthrough if already a model; else `TypeAdapter`). **No `rules` field and no alias** on `MappingPlanIn`.

### 5.2 `app/agent/schemas.py`

| Name | Fields | Consumed by |
|---|---|---|
| `StewardState` | extends `langchain.agents.AgentState`; extras: `proposed_ops: list[dict]`, `account_scope: int \| None`, `pending_preview: dict \| None`, `apply_result: dict \| None`, `rationale: str \| None` (all `NotRequired`) | `build_steward_builder` `state_schema`; human_approval/execute |

`AgentState` (library): `messages: list[AnyMessage]` with `add_messages` reducer; `jump_to` ephemeral/private; `structured_response`. Steward extras have **no custom reducer** (last write wins).

### 5.3 Domain dataclasses (`app/domain/transaction.py`) — not Pydantic

`CanonicalTransaction`, `UnmappedValues`, `ReclassifyResult`, `IngestResult`. Pipeline currency; mapped to ORM in `ingest_from_source`.

---

## 6. Domain layer

`app/domain/` is mostly pure. Exception: `db_lookup.py` uses SQLAlchemy `Session` + `NormalizationMapping`.

### 6.1 `transaction.py`

- `CanonicalTransaction` — frozen dataclass; `dedupe_hash=""` until `dedupe.py`; `raw` original row.
- `UnmappedValues` — sorted unique raw values that failed lookup during a pass.
- `ReclassifyResult` — `scanned`, `updated`, `unmapped`.
- `IngestResult` — ingest summary including `errors`.

### 6.2 `mapping.py`

- `SignConvention` — see §3.7.
- `ImportMapping(date_col, description_col, amount_col, category_col=None, owner_col=None, type_col=None, merchant_col=None, sign_convention=None)` — `__post_init__` raises `ValueError("ImportMapping requires type_col or sign_convention")` if both missing.
- `resolve_mapping(account_default_mapping, override=None) -> ImportMapping` — **always returns `account_default_mapping`**. `override` ignored. Proven: `tests/domain/test_mapping.py::test_resolve_mapping_ignores_override_for_now`.

### 6.3 `sources.py`

- `TransactionSource.fetch(**kwargs) -> list[dict]` — ABC; raw rows, no transform.
- `CsvSource.fetch` — requires `file_path` (or `file`); `pandas.read_csv`; NaN → None. Proven: `tests/domain/test_sources.py`.
- Commented placeholders: `PdfSource`, `ApiSource`.

### 6.4 `normalize.py`

Date formats tried in order: `%Y-%m-%d`, `%m/%d/%Y`, `%m/%d/%y`, `%Y/%m/%d`, `%d/%m/%Y`.

Amount: Decimal; strip `$` and `,`; `(12.00)` → negative; bool rejected.

- `normalize_row(row, mapping, account_id, default_owner, lookup) -> CanonicalTransaction` — extract cells; type via lookup if `type_col` else sign; merchant from `merchant_col` (whitespace-collapsed) or `extract_merchant(description)`; category via lookup using `resolved_merchant(raw, normalized)`; owner via lookup if owner cell present else `default_owner`. Empty owner cell → default owner, `owner_raw is None`.
- `normalize_rows(...) -> (list[CanonicalTransaction], UnmappedValues, list[str])` — row failures collected (`row {i}: ...`); unmapped sets for UNKNOWN type / None category / None owner (only if owner_col) / None merchant.

Sign fallback: `NEGATIVE_IS_SPEND` → amount `< 0` SPEND else PAYMENT; `POSITIVE_IS_SPEND` opposite. `raw_type` is None.

### 6.5 `dedupe.py`

- `compute_dedupe_hash(txn) -> str` — sha256 of `f"{account_id}|{date.isoformat()}|{amount.quantize(Decimal('0.01'))}|{description}"`.
- `DedupeSplit(new, duplicates)`.
- `split_new_and_duplicates(candidates, existing_hashes)` — membership plus within-batch: first hash wins, later copies are duplicates.

### 6.6 `classification.py`

- `NormalizationLookup.resolve(kind, raw_value, account_id, merchant=None) -> str | None` — keys already cleaned; None → caller fallback.
- `clean_raw_value` — see §3.8.
- `classify_transaction_type` — empty → UNKNOWN; lookup miss or invalid canonical → UNKNOWN.
- `classify_category` — empty → None; cleans merchant then lookup.
- `classify_owner` / `classify_merchant` — empty → None; miss → None (passthrough to raw at display).

### 6.7 `NormalizationLookup` implementations — precedence

**Category** (both DB and merged): account+merchant → account (merchant NULL) → global+merchant → global. Proven: `tests/domain/test_merged_lookup.py::test_category_precedence_account_merchant_to_global`, `tests/fakes.py::InMemoryNormalizationLookup`, `tests/routers/test_mappings.py::test_category_mapping_merchant_scope`.

**Other kinds:** account (merchant ignored) → global. Proven: `test_non_category_account_then_global_ignores_merchant`.

#### `app/domain/db_lookup.py::DbNormalizationLookup`

`resolve` equality-matches stored cleaned `raw_value` / `merchant` via `_find`. No overlay refs.

`merged_lookup_from_db(db, proposed, kinds) -> MergedNormalizationLookup` — loads DB rows as `RuleSpec(..., ref=f"db:{row.id}")` filtered by `kind IN kinds` (empty `kinds` → all), then concatenates `proposed`.

#### `app/domain/merged_lookup.py`

`RuleSpec`: `kind`, `raw_value` (cleaned), `canonical_value`, `account_id`, `merchant`, `ref` (`"db:<id>"`, `"create:<index>"`, `"update:<index>"`, `"proposed:<index>"`).

`RuleMatch`: `value`, `ref`.

Exact-scope index key: `(kind, raw_value, account_id, merchant)`. Same-scope tie: **keep `db:` over overlay**. Overlay refs = `proposed:` or `create:` only (`_is_overlay_ref`). `update:` is not overlay. Proven: `test_same_scope_prefers_db_rule`, `test_same_scope_prefers_db_even_if_proposed_listed_first`.

`resolve_with_ref` returns `RuleMatch` for preview attribution.

### 6.8 `merchant.py`

- `extract_merchant(description)` — collapse whitespace; strip trailing `(STORE \d+)`, `(#\d+)`, `(*\d+)` case-insensitive; if strip empties, keep trimmed original; empty/None → None.
- `resolved_merchant(raw, normalized, override=None)` — first non-empty of override, normalized, raw.

### 6.9 Reclassification gates (domain used by service)

`app/services/ingest_service.py::run_reclassification` (not domain, but the gate lives there):

| Field | Recomputed when | Never touched |
|---|---|---|
| `transaction_type` + `is_spend` | `raw_type` present | sign-derived rows (`raw_type` empty) |
| `owner_id` | `owner_raw` present | account-default-only rows |
| `merchant_raw` | currently empty: backfill from `default_mapping.merchant_col` in `raw`, else `raw["Merchant"]`, else `extract_merchant(description)` (`_backfill_merchant_raw`) | non-empty `merchant_raw` |
| `merchant_normalized` | always from current/backfilled raw | `merchant_override` |
| `category_normalized` | always from `category_raw` + `resolved_merchant(raw, new_normalized, merchant_override)` | `category_raw`, `category_override` |
| others | — | amount, date, description, hash, `raw`, overrides |

Proven: `tests/routers/test_reclassify.py::*`, `tests/services/test_mapping_preview.py::test_preview_gates`.

---

## 7. Services

### 7.1 `ingest_service.py`

**`AccountNotFoundError`** — `ValueError` subclass.

**`mapping_from_stored(data: dict) -> ImportMapping`** — rebuilds dataclass; `sign_convention` string → enum or None.

**`ingest_from_source(db, account_id, source, fetch_kwargs, filename) -> IngestResult`**

Order: load Account → `resolve_mapping(mapping_from_stored(...))` → default owner name → `source.fetch` → `DbNormalizationLookup` → `normalize_rows` → hash each → existing hashes for account → split → resolve owner names to ids → create `ImportBatch` (`flush`) → insert `split.new` → **`db.commit()`**. Rolls back only if that commit/session fails (no explicit try/rollback). Side effect: batch + rows. Idempotent on re-import: duplicates skipped, new batch still inserted with `inserted=0`. Proven: `tests/routers/test_imports.py::test_import_pipeline_dedupe_filter_and_patch`.

**`run_reclassification(db, account_id=None) -> ReclassifyResult`** — **does not commit**. Caller owns the transaction. 404-equivalent: `AccountNotFoundError` if account_id set and missing. Mutates ORM objects in the session.

**`reclassify_transactions(db, account_id=None) -> ReclassifyResult`** — `run_reclassification` then **`db.commit()`**.

**`_backfill_merchant_raw(txn, account)`** — see §6.9. Also used by preview.

### 7.2 `analytics_service.py`

All read-only; no commit.

**`_apply_filters`** — `date_to` → today; optional date_from/account/owner/merchant; optional spend_only (`transaction_type == SPEND`).

**`summarize(db, date_from, date_to, account_id, owner_id, group_by, spend_only=True, merchant=None) -> list[dict]`** — keys: category=`coalesce(effective_category, "(unassigned)")`; owner=`coalesce(Owner.name, "(unassigned)")`; account=`Account.name`; month=`YYYY-MM` (`strftime` sqlite / `to_char` else); merchant=`coalesce(effective_merchant, "(unassigned)")`. Month ordered by key; others `total desc, key`. `UNASSIGNED = "(unassigned)"`.

**`get_total(...)`** — total, count, average quantized 0.01; zero rows → all `0.00`.

**`top_merchants(..., limit=10, merchant=None)`** — group effective merchant, limit.

**`largest_transactions(...)`** — order `abs(amount) desc, id`, limit.

**`search_transactions(..., query, limit=50, merchant=None)`** — `_apply_filters(..., spend_only=False)` so **still date_to=today**; `description ILIKE %query%`; order date, id.

**`unmapped_summary(db) -> dict[str, list[str]]`** — distinct: UNKNOWN+raw_type; category_raw with **both** override and normalized NULL; owner_raw with owner_id NULL; merchant_raw with **both** override and normalized NULL.

### 7.3 `mapping_preview_service.py`

**Purity of `preview_mappings(db, plan) -> MappingPreview`:** builds current + virtual merged lookups in memory; **no add/update/delete/flush/commit**. Proven: `tests/services/test_mapping_preview.py::test_preview_is_pure` (no `db.new/dirty/deleted`; row snapshot unchanged).

Virtual rule set `_virtual_specs`: drop deleted mapping ids; replace updated ids with `update:{i}` specs; append creates that are not duplicate/conflict (`create:{i}`). Then `MergedNormalizationLookup`.

**`parse_plan_ops(db, plan, allow_missing_delete=False)`** — 1-based error labels; 0-based `index` on impacts. Duplicate `mapping_id` across update/delete → error. Preview: missing id → error, omit op. Apply: missing **delete** skipped later; missing **update** → error.

**`preview_mappings`** vs **`apply_mapping_plan` validation:** preview records `validation_errors` and still scores remaining ops (`test_preview_validation_excludes_invalid_and_continues`). Apply raises `MappingPlanValidationError` and writes nothing (`test_apply_rejects_invalid_plan`).

Identity collision on create: same canonical → `duplicate_of_existing_id` (preview skip; apply skip `reason="duplicate"`). Different canonical → `conflicts_with_existing_id` (preview would_change=0; **apply rejects whole plan**). Proven: `test_preview_conflict_split`, `test_apply_rejects_conflict_and_missing_id`.

**`apply_mapping_plan(db, plan) -> ApplyResult`**

Order inside one try: **deletes** (missing → skip `missing`) + flush; **updates** (missing → error) + flush; **creates** (duplicate skip / conflict raise) + flush; `run_reclassification(db, plan.account_id)` + flush; `unmapped_summary`; **`db.commit()`**. `except: db.rollback(); raise`.

Idempotent re-apply of mixed plan: creates skip duplicate, deletes skip missing, updates re-applied (canonical already new → reclass_updated 0). Proven: `test_apply_idempotent`, `test_apply_mixed_plan_atomic_and_reapply`. Reclass failure rolls back mapping writes: `test_apply_transactional`, `test_apply_mixed_plan_rolls_back_on_reclass_failure`.

Other helpers: `find_mapping_by_identity`, `validate_create_op` (mirrors POST /mappings checks; 1-based index in messages).

---

## 8. Agent system

### 8.1 Topology

```mermaid
flowchart TB
  CLI["cli.py::main"]
  CP["open_checkpointer()"]
  CLI --> CP
  CP -->|"default"| COORD["coordinator.py::build_coordinator\ncreate_agent + checkpointer required"]
  CP -->|"--steward"| STEW_CP["steward_graph.py::build_steward_graph\ncompile(checkpointer=...)"]

  COORD --> LO["list_owners"]
  COORD --> LA["list_accounts"]
  COORD --> GT["get_total"]
  COORD --> SUM["summarize"]
  COORD --> AA["subagents.py::ask_analyst"]
  COORD --> RDS["subagents.py::run_data_steward"]

  AA -->|"invoke messages=[task] only"| ANALYST["analyst.py::build_analyst\nno checkpointer"]
  RDS -->|"invoke messages=[task] only"| STEW["build_steward_graph(checkpointer=None)\ninherits interrupt to parent"]

  ANALYST --> AT["ANALYST_TOOLS\nlist_* / search / analytics"]
  STEW --> SN["node steward\ncreate_agent STEWARD_AGENT_TOOLS"]
  SN -->|"proposed_ops nonempty"| HA["node human_approval\nrecompute preview, interrupt()"]
  SN -->|"else"| END([END])
  HA -->|"decision != approve"| SN
  HA -->|"approve"| EX["node execute\napply_mapping_plan"]
  EX --> SN
```

Nested graphs are **tools**, not StateGraph subgraph nodes. Interrupt still appears on the coordinator: `tests/agent/test_coordinator.py::test_steward_interrupt_propagates_to_coordinator`.

### 8.2 Graphs

| Graph | Builder | State | Nodes / edges | End | Recursion | Checkpointer |
|---|---|---|---|---|---|---|
| Coordinator | `app/agent/coordinator.py::build_coordinator` | default `AgentState` (`messages` + add_messages) | `create_agent` internals; tools listed below | model stops calling tools | CLI `recursion_limit=25` | CLI: **required** (always passed). Studio (`studio.py`): `None` at compile; API server injects persistence. Docstring documents invariant. |
| Analyst | `app/agent/analyst.py::build_analyst` | default `AgentState` | `create_agent`; `ANALYST_TOOLS` | same | inherit invoke config if passed; wrappers pass none | **None** (per-invocation) |
| Steward | `build_steward_builder` → `build_steward_graph` | `StewardState` | START→`steward`; conditional `_route_after_steward` → `human_approval` or END; `human_approval`/`execute` route via `Command(goto=...)` | no `proposed_ops` after steward node | CLI/tests 25 | Standalone CLI/tests: passed in. Coordinator path: `checkpointer=None` so `interrupt()` bubbles. Proven: `test_steward_standalone_compile_still_uses_checkpointer`, `test_steward_compiles_without_checkpointer_for_subagent_use` |

`create_agent(..., name="coordinator"|"analyst"|"steward")`. Steward inner agent uses `state_schema=StewardState`. Middleware: coordinator + analyst get `CurrentDateMiddleware`; **steward `create_agent` does not**.

### 8.3 Tools inventory

Read = no DB writes. Gate = graph-state only. Delegate = nested invoke. Descriptions in the next subsection are the runtime `tool.description` strings, copied verbatim.

**`app/agent/tools/read.py`**

| Tool name | Args | Wraps | Class | Defined |
|---|---|---|---|---|
| `list_owners` | none | select Owner → `OwnerOut` | read | `app/agent/tools/read.py::list_owners` |
| `list_accounts` | none | select Account → `AccountOut` | read | `app/agent/tools/read.py::list_accounts` |
| `get_unmapped_values` | none | `app/services/analytics_service.py::unmapped_summary` | read | `app/agent/tools/read.py::get_unmapped_values` |
| `list_mappings` | `kind=None`, `account_id=None` | select NormalizationMapping | read | `app/agent/tools/read.py::list_mappings` |
| `list_transactions` | `account_id`, `owner_id`, `category`, `merchant`, `date_from`, `date_to`, `limit=25` | inline select (not `_apply_filters`) | read | `app/agent/tools/read.py::list_transactions` |
| `search_transactions` | `query`, `account_id`, `owner_id`, `date_from`, `date_to`, `merchant`, `limit=25` | `app/services/analytics_service.py::search_transactions` | read | `app/agent/tools/read.py::search_transactions_tool` |
| `summarize` | `group_by`, dates, `account_id`, `owner_id`, `spend_only=True`, `merchant` | `app/services/analytics_service.py::summarize` | read | `app/agent/tools/read.py::summarize` |
| `get_total` | dates, ids, `spend_only=True`, `merchant` | `app/services/analytics_service.py::get_total` | read | `app/agent/tools/read.py::get_total` |
| `top_merchants` | `limit=10`, dates, ids, `spend_only=True`, `merchant` | `app/services/analytics_service.py::top_merchants` | read | `app/agent/tools/read.py::top_merchants` |
| `largest_transactions` | `limit=10`, dates, ids, `spend_only=True`, `merchant` | `app/services/analytics_service.py::largest_transactions` | read | `app/agent/tools/read.py::largest_transactions` |

Lists: `READ_TOOLS` (first six), `ANALYTICS_TOOLS` (last four), `ANALYST_TOOLS` = owners, accounts, list_transactions, search, + analytics.

**`app/agent/tools/steward.py`** — module docstring: *There is no tool that applies mappings.*

| Tool name | Args | Wraps | Class | Defined |
|---|---|---|---|---|
| `preview_mapping_rules` | `ops: list[dict]`, `account_id=None` | `app/services/mapping_preview_service.py::preview_mappings` | read | `app/agent/tools/steward.py::preview_mapping_rules` |
| `submit_plan` | `ops`, `rationale: str`, `runtime: ToolRuntime`, `account_id=None`; `return_direct=True` | `preview_mappings` then `Command(update=...)` | gate (graph state; **no apply**) | `app/agent/tools/steward.py::submit_plan` |

**`app/agent/tools/subagents.py::make_subagent_tools`**

| Tool name | Args | Wraps | Class | Defined |
|---|---|---|---|---|
| `ask_analyst` | `task: str` | `analyst.invoke({"messages":[{"role":"user","content": task}]})` → last text | read delegate | nested in `make_subagent_tools` |
| `run_data_steward` | `task: str` | `steward.invoke({...})` → `_steward_summary` | write-path delegate (apply only after interrupt resume) | nested in `make_subagent_tools` |

Wrappers: **no DB/session before invoke**. History control: parent sees only returned string. Proven: `tests/agent/test_coordinator.py::test_coordinator_history_excludes_analyst_internals`, `tests/agent/test_analyst.py::test_analyst_two_summarize_calls_wrapper_returns_final_only`.

Coordinator tools: `list_owners`, `list_accounts`, `get_total`, `summarize`, `ask_analyst`, `run_data_steward`.

**Counts:** 14 tools; 12 read; 2 non-read (`submit_plan` gate, `run_data_steward` delegate). **0 apply tools.**

Every tool that hits the DB uses `app/agent/config.py::tool_session` (open/close per call).

#### Tool descriptions (verbatim)

`list_owners`:

```
List registered owners (id and name).
```

`list_accounts`:

```
List accounts (id, name, last4).
```

`get_unmapped_values`:

```
Distinct raw type/category/owner/merchant values that still need mapping rules.
```

`list_mappings`:

```
List normalization mapping rules.

Filtering by account_id excludes global rules (account_id IS NULL).
To see the full effective ruleset, call this twice: once with the account_id
and once without (global rules).
```

`list_transactions`:

```
List stored transactions.

Does not apply spend_only and does not default date_to.
category/merchant filters match the effective value
(override → normalized → raw). limit must be >= 1.
```

`search_transactions`:

```
Search transaction descriptions (case-insensitive substring).

Does not apply spend_only. The analytics service still upper-bounds
transaction_date at today when date_to is omitted.
merchant matches the effective value (override → normalized → raw).
limit must be >= 1.
```

`summarize`:

```
Group transaction totals.

group_by is category, owner, month, account, or merchant.
Month buckets are YYYY-MM, ascending; other groupings sort by total desc.
"(unassigned)" is the bucket for missing groups.
Defaults: spend_only=true (only SPEND rows; totals use abs(amount)) and
date_to=today. spend_only=false sums signed amounts as stored.
merchant matches the effective value (override → normalized → raw).
Amounts are decimal strings. There is no category filter; group_by=category
to break down by category.
```

`get_total`:

```
Return total, count, and average for matching transactions.

Defaults: spend_only=true (only SPEND rows; totals use abs(amount)) and
date_to=today. spend_only=false sums signed amounts as stored.
merchant matches the effective value (override → normalized → raw).
Amounts are decimal strings.
```

`top_merchants`:

```
Top merchants by total.

Defaults: spend_only=true (only SPEND rows; totals use abs(amount)),
date_to=today, limit=10. spend_only=false sums signed amounts as stored.
merchant matches the effective value. Amounts are decimal strings.
limit must be >= 1.
```

`largest_transactions`:

```
Largest transactions by absolute amount.

Defaults: spend_only=true (only SPEND rows), date_to=today, limit=10.
Does not default to a category filter. limit must be >= 1.
```

`preview_mapping_rules` (`app/agent/tools/steward.py::_PREVIEW_DESCRIPTION`):

```
Preview a mapping plan against stored transactions. Performs no writes.

ops is a list of create / update / delete operations:
- create: {op: "create", kind, raw_value, canonical_value, account_id?, merchant?}
- update: {op: "update", mapping_id, canonical_value}  (changes an existing rule)
- delete: {op: "delete", mapping_id}

Identity of a create is (kind, cleaned raw_value, account_id, merchant).
If that identity exists with the same canonical, preview sets duplicate_of_existing_id.
If it exists with a different canonical, preview sets conflicts_with_existing_id —
do not resubmit the create; submit an update on that mapping_id instead.

Domain quirks you must respect:
- Raw values are matched trimmed + lowercased.
- Category precedence is account+merchant → account → global+merchant → global,
  so a proposed global rule can be shadowed by an existing account rule
  (check shadowed_by_existing).
- Reclassify never touches category_override or merchant_override.
- Transaction type is recalculated only for rows with raw_type.
- Owner is recalculated only for rows with owner_raw.
```

`submit_plan`:

```
Submit a mapping plan for human approval. Does not apply anything.

Always preview first. The submitted ops are paused for approval; applying
happens only after a human resumes the graph.
```

`ask_analyst`:

```
Delegate spending analysis: comparisons across months/owners/accounts/merchants/categories, trends, largest or unusual transactions, description search. Include all relevant scope in the task: exact date ranges, owner/account ids, whether refunds should be included.
```

`run_data_steward`:

```
Delegate normalization cleanup for types, categories, owners, and merchants — including account- or merchant-scoped rules when the user asks. Reviews unmapped values, proposes and previews mapping changes, and pauses for human approval before anything is applied.
```

### 8.4 Prompts (verbatim)

**Coordinator** — `app/agent/coordinator.py::COORDINATOR_PROMPT`

```
You are the conversational entrypoint for a personal finance ledger.

Resolve people and account names to ids (list_owners, list_accounts) and relative dates such as "last month" to concrete YYYY-MM-DD ranges *before* delegating. Put those ids and dates in the task text; subagents do not see this conversation.
A current calendar date is attached to each turn; use it to resolve relative dates. Never guess the calendar. Do not treat that date as something the user said or confirmed.

Answer single-number questions (a total, one summary) yourself with get_total or summarize.
Delegate multi-step analysis (comparisons, trends, top merchants, unusual transactions, description search) to ask_analyst.
Delegate anything touching mappings, unmapped values, or overrides to run_data_steward.
When delegating mapping work, include any account, kind (type, category, owner, or merchant), or merchant scope the user asked for in the task text.

Never fabricate numbers. If the steward pauses for approval, tell the user what is pending.
When relaying steward outcomes, repeat the steward's created_ids, updated_ids, deleted_ids, and reclass_updated exactly; never paraphrase counts into vague success claims.
```

**Analyst** — `app/agent/analyst.py::ANALYST_PROMPT`

```
You answer analysis questions over a personal transaction ledger.
Comparisons take multiple tool calls (two summarize calls with different date ranges, or one group_by=month); compute deltas yourself.
Report only numbers that appear in tool results — never estimate.
State the filters you used (dates, owner, account, spend_only) in the answer.
Amounts are decimal strings.
The task text should already contain resolved owner/account ids and concrete YYYY-MM-DD ranges; use list_owners/list_accounts only to confirm.
A current calendar date is attached to each turn; use it if a task still uses relative dates. Do not treat that date as something the user said or confirmed.
```

**Steward** — `app/agent/steward_graph.py::STEWARD_PROMPT`

```
You clean up normalization mappings.
Workflow: fetch unmapped values → list_mappings for the kind (global, plus the account scope if relevant) → inspect examples → propose ops → always preview before submitting → submit the plan with the preview attached.
Rules may be global or scoped to an account; category rules may also be scoped to a merchant. Propose ops and submit plans that match the scope the user requested.
Never claim anything was applied; applying happens only after a human approves.

If preview reports conflicts_with_existing_id, submit an update on that mapping_id — never resubmit the create. Collapsing near-duplicate canonicals (e.g. Grocery/Groceries) is an update on the existing rule plus creates for other raw keys.

After execute, report created_ids, updated_ids, deleted_ids, and reclass_updated verbatim. If reclass_updated is 0 when changes were expected, say so explicitly; do not claim rows were updated.
```

**Middleware fragments** — `app/agent/middleware.py`

- `DATE_CONTEXT_PREFIX = "Current date:"`
- `format_current_date(today) -> f"Current date: {today.isoformat()} ({today.strftime('%A')})."`

Reject HumanMessage from execute (verbatim content): `"Plan rejected. Nothing was applied. Propose a different plan if needed."`

Execute summary template: `"Plan executed. created_ids=... updated_ids=... deleted_ids=... skipped=... reclass_scanned=... reclass_updated=.... Nothing else will be applied unless a new plan is submitted. Report created_ids, updated_ids, deleted_ids, and reclass_updated verbatim. If reclass_updated is 0, say so explicitly; do not claim rows were updated."`

### 8.5 Interrupt / approval contract

**Payload** (`app/agent/steward_graph.py::human_approval`) — recomputed **from `state["proposed_ops"]`**, not `pending_preview`:

```
{
  "ops": submitted,          # list[dict] as stored by submit_plan
  "preview": preview,        # MappingPreview.model_dump(mode="json")
  "rationale": state.get("rationale"),
}
```

Session for preview is closed **before** `interrupt()` (`_recompute_preview` uses `tool_session`).

**Resume value**

- Non-dict → `{"decision": decision}`.
- `decision != "approve"` (including `"reject"`) → Command to `steward`; clear `proposed_ops`, `pending_preview`, `rationale`; inject reject HumanMessage. **Nothing applied.**
- `decision == "approve"`: `ops = decision.get("ops")` or original submitted; `_recompute_preview(ops, account_scope)` stored as `pending_preview`; goto `execute`.

CLI (`app/agent/cli.py::_decision`): `reject*` → `{"decision":"reject","ops":[]}`; `edit 0,2` → `{"decision":"approve","ops":[ops[i] for i in idxs]}` (0-based); else `{"decision":"approve","ops": ops}` (full list). There is **no** `"edit"` decision key — CLI maps edit to approve+subset. Proven: `tests/agent/test_cli.py::test_cli_edit_indexes_ops`.

**Tests that pin this**

| Behavior | Test |
|---|---|
| Interrupt payload has preview+rationale+ops; subset resume applies only those ops | `tests/agent/test_steward_graph.py::test_steward_interrupt_flow` |
| Preview exists even if preview tool never called | `test_interrupt_preview_without_preview_tool` |
| Interrupt preview matches **submitted** ops, not last preview tool args | `test_interrupt_preview_matches_submitted_not_last_tool` |
| Edited subset recomputes preview before execute | `test_edited_subset_recomputes_preview_for_execute` |
| SQLite file saver survives process rebuild; resume applies | `test_sqlite_file_checkpointer_survives_rebuild` |
| Coordinator surfaces same interrupt; subset apply; relay counts | `test_steward_interrupt_propagates_to_coordinator` |
| Conflict preview then update+create; execute summary has ApplyResult numbers | `test_steward_conflict_then_update_create_reports_counts` |
| Duplicate create apply: `reclass_updated=0`, `created_ids=[]` in summary | `test_steward_noop_apply_reports_reclass_updated_zero` |

**After resume approve:** `execute` calls `apply_mapping_plan` (commits inside), writes `apply_result` dump, clears plan fields, HumanMessage with verbatim ids/counts, goto `steward` for a spoken wrap-up.

`run_data_steward` return: if `apply_result` present, `"applied created_ids=... reclass_updated=..."`; else if a message contains `"nothing was applied"` → `"rejected"`; else last text or `"nothing unmapped"`.

### 8.6 Checkpointer

`app/agent/config.py::open_checkpointer(*, in_memory=False)`

| Condition | Saver |
|---|---|
| `in_memory=True` | `InMemorySaver` (tests; CLI does not pass this) |
| `settings.database_url.startswith("postgresql")` | `PostgresSaver.from_conn_string(checkpoint_conn_string(url))` then `setup()` |
| else | `SqliteSaver` at `AGENT_CHECKPOINT_PATH` (default `.agent_checkpoints.sqlite`) + `setup()` |

`checkpoint_conn_string`: replace `postgresql+psycopg2://` and `postgresql+psycopg://` with `postgresql://` (first occurrence each).

`in_memory_checkpointer()` — tests. `set_session_factory` — tests inject pytest engine.

**Thread id:** CLI arg or `uuid.uuid4()`. Config includes `configurable.thread_id`, `recursion_limit=25`, `run_name` (`coordinator-turn` / `steward-turn`), `tags` (`cli` / `steward-cli`), `metadata.thread_id` and `metadata.entrypoint` — on invoke **and** resume. Restart: `graph.get_state(config)` if `snapshot.interrupts` → print payload and resume before the chat loop.

### 8.7 Middleware

`app/agent/middleware.py::CurrentDateMiddleware` — prefixes **last HumanMessage** of **this model call** with `format_current_date(clock())` + blank line (or a leading text block for list content). Skips if already prefixed. Does **not** change `system_message` or earlier humans. Attached on coordinator and analyst `create_agent(..., middleware=[CurrentDateMiddleware()])`. Proven: `tests/agent/test_middleware.py::*`. Coordinator prompt contains no ISO date: `test_coordinator_prompt_has_no_interpolated_date`.

---

## 9. Cross-cutting invariants

| # | Statement | Why | Enforced | Test |
|---|---|---|---|---|
| 1 | Mapping **DB writes that apply plans** go through `apply_mapping_plan` / interrupt `execute`. There is **no apply tool**. | Human gate | `app/agent/tools/steward.py` (module docstring + tools); `execute` | steward graph tests; grep-equivalent: only `execute` + HTTP apply/patch/delete call apply |
| 2 | One checkpointer at the outermost graph (coordinator CLI, or standalone steward CLI). Nested steward compiles without one. Studio compiles coordinator without one; API server injects in-memory persistence. | Interrupt bubbles; no split thread | CLI always passes checkpointer; `studio.py` passes `None`; `build_steward_graph(checkpointer=None)` from coordinator | `test_steward_compiles_without_checkpointer_for_subagent_use`, `test_steward_interrupt_propagates_to_coordinator`, `test_studio_entrypoints` |
| 3 | Sessions are per tool call / preview / execute; never held across `interrupt()` | Pause can last hours | `tool_session`; `_recompute_preview` docstring | `test_sqlite_file_checkpointer_survives_rebuild` |
| 4 | No side effects before subagent `invoke` in wrappers | History isolation | `make_subagent_tools` | `test_coordinator_history_excludes_analyst_internals` |
| 5 | Approval payload preview recomputed from submitted `proposed_ops` | Model may skip preview or change ops | `human_approval` | `test_interrupt_preview_without_preview_tool`, `test_interrupt_preview_matches_submitted_not_last_tool` |
| 6 | Every mapping **plan** mutation reclassifies in the same transaction | No stale classified rows | `apply_mapping_plan` order | `test_apply_transactional`, `test_apply_mixed_plan_atomic_and_reapply`, `test_patch_and_delete_reclassify` |
| 7 | Approved plans re-apply idempotently (dup create skip, missing delete skip, conflict still rejected) | Resume / double submit | `apply_mapping_plan` | `test_apply_idempotent`, `test_apply_mixed_plan_atomic_and_reapply` |
| 8 | Canonical-diff identity collisions are conflicts, rejected on apply | No silent overwrite | `parse_plan_ops` + `_conflict_errors` | `test_apply_rejects_conflict_and_missing_id`, `test_preview_conflict_split` |
| 9 | Execute summaries carry ApplyResult numbers verbatim | Coordinator must not paraphrase | `execute` summary string; steward prompt | `test_steward_conflict_then_update_create_reports_counts`, `test_steward_noop_apply_reports_reclass_updated_zero` |
| 10 | Subagents receive scope via **task strings**, never parent history | Wrapper invoke is a fresh user message | `ask_analyst` / `run_data_steward` | `test_coordinator_history_excludes_analyst_internals` (parent names `== ["ask_analyst"]`) |
| 11 | Tests never emit LangSmith traces | Dev shell may have tracing on | `conftest.py` sets tracing env to `false` before import + autouse fixture; legacy `LANGCHAIN_*` aliases too | `tests/test_tracing_isolation.py` |
| 12 | `preview_mappings` is pure | Agent can preview freely | no session dirty | `test_preview_is_pure` |
| 13 | Reclassify never writes overrides / raw columns (except merchant_raw backfill when empty) | Preserve import + user fixes | `run_reclassification` | `test_reclassify_applies_new_type_and_category_mappings`, `test_reclassify_backfills_merchant_from_description` |
| 14 | Type/owner recompute gated on `raw_type` / `owner_raw` | Sign-derived and default-owner rows stay | `run_reclassification`; preview `_rule_in_scope` | `test_preview_gates` |
| 15 | `spend_only` totals use `abs(amount)` and SPEND only | Mixed-sign CSVs | `_amount_expr`, `_apply_filters` | `test_spend_only_excludes_payments_and_refunds`, `test_mixed_sign_spends_use_abs` |
| 16 | Effective category/merchant coalesce override > normalized > raw | Analytics + list filters | SQL case + `resolved_merchant` | `test_summarize_category_coalesce_override_wins`, `test_merchant_filter_and_group_by_use_effective_value` |
| 17 | Dedupe identity is account+date+quantized amount+description; within-batch too | Monthly re-import | `compute_dedupe_hash`, `split_new_and_duplicates` | `tests/domain/test_dedupe.py::*`, import re-import test |
| 18 | Raw mapping keys stored cleaned; lookup uses cleaned keys | `" Sale "` hits `sale` | `clean_raw_value` at write+classify | `test_create_mapping_cleans_raw_value_and_collapses_duplicates`, `test_classify_transaction_type_uses_cleaned_raw_value` |
| 19 | Same-scope overlay loses to `db:` | Preview shadowing | `MergedNormalizationLookup` | `test_same_scope_prefers_db_rule` |
| 20 | `search` is not spend_only but still date_to=today | Shared `_apply_filters` | `search_transactions` | `test_search_is_case_insensitive_and_includes_all_types`; tool docstring |
| 21 | `POST /mappings` does **not** reclassify | Setup vs plan path | `create_mapping` | `test_create_mapping_cleans_raw_value_and_collapses_duplicates` (no txn change); QA flow uses `/transactions/reclassify` |
| 22 | Invalid preview ops are skipped; invalid apply aborts all | Preview is advisory | `parse_plan_ops` vs apply errors | `test_preview_validation_excludes_invalid_and_continues`, `test_apply_rejects_invalid_plan` |
| 23 | Coordinator prompt has no interpolated calendar date | Prompt cache stability | `COORDINATOR_PROMPT` + middleware | `test_coordinator_prompt_has_no_interpolated_date` |
| 24 | `submit_plan` is `return_direct=True` so the steward node yields to routing with `proposed_ops` set | Reach `human_approval` without another model turn | decorator | interrupt flow tests |
| 25 | Date middleware does not rewrite system prompt or prior humans | Cache + history | `CurrentDateMiddleware._with_date` | `test_wrap_model_call_prefixes_last_human_and_leaves_system_untouched` |
| 26 | Identity merchant map not required to filter/group by raw merchant | Unmapped merchants still queryable | `effective_merchant` fallback to raw | `test_identity_merchant_map_not_required` |
| 27 | `resolve_mapping` ignores per-import override | Placeholder | `resolve_mapping` | `test_resolve_mapping_ignores_override_for_now` |
| 28 | Delete type mapping previews UNKNOWN | Fallback | preview delete | `test_preview_delete_type_reverts_to_unknown` |

---

## 10. Configuration and environment

Never read `.env` values into this document. Names from `.env.example` and code:

| Name | Purpose | Default | Consumed |
|---|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL for app DB; if `postgresql*`, also Postgres checkpointer | **required** (`Settings.database_url`, no default) | `app/config.py::Settings`; tests `setdefault("sqlite:///:memory:")` then use a separate StaticPool engine |
| `STEWARD_MODEL` | Model id for coordinator, analyst, steward | `anthropic:claude-sonnet-4-6` (`DEFAULT_MODEL`) | `app/agent/config.py::model_name` |
| `AGENT_CHECKPOINT_PATH` | SQLite checkpoint file when DB is not Postgres | `.agent_checkpoints.sqlite` | `checkpoint_sqlite_path` |
| `ANTHROPIC_API_KEY` | Provider SDK (comment in `.env.example`) | none | not referenced in app code |
| `OPENAI_API_KEY` | Provider SDK if `STEWARD_MODEL` is `openai:...` | none | not referenced in app code |
| `LANGSMITH_TRACING` | Enable LangSmith tracing (SDK reads; app code does not) | unset / false | LangChain/LangGraph/LangSmith SDK |
| `LANGSMITH_API_KEY` | Authenticate trace uploads | none | SDK |
| `LANGSMITH_PROJECT` | Trace project name | `default` if unset | SDK |
| `LANGSMITH_ENDPOINT` | LangSmith API region | `https://api.smith.langchain.com` | SDK |
| `LANGSMITH_HIDE_INPUTS` | Hide all span inputs | false | SDK |
| `LANGSMITH_HIDE_OUTPUTS` | Hide all span outputs | false | SDK |

Optional `LANGSMITH_*` vars documented in `.env.example`; `Settings` uses `extra="ignore"`. Legacy `LANGCHAIN_*` aliases honored by SDK; tests disable both namespaces.

`load_dotenv()` in `app/config.py` and `app/agent/config.py`. `Settings`: `env_file=".env"`, `extra="ignore"`.

**`docker-compose.yml`:** `db` = `postgres:16`, user/password/db `finance`, host port **5433**→5432, volume `pgdata`. `api` build `.`, `DATABASE_URL=postgresql+psycopg://finance:finance@db:5432/finance`, port 8000, bind-mount `./app`, `uvicorn --reload`. No agent env on the api service (CLI is host-side per AGENT-QA).

**`Dockerfile`:** install requirements, copy `app/` only, `uvicorn app.main:app --host 0.0.0.0 --port 8000` (no reload).

**`pytest.ini`:** `pythonpath = .`, `testpaths = tests`.

**`langgraph.json`:** repo root; three graphs via `app/agent/studio.py` factories; `"dependencies": ["."]`; `"env": ".env"`. Studio Python guard: ≥3.11, &lt;3.14.

---

## 11. Observability

**Env-driven LangSmith tracing.** Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env` (see `.env.example`). No app code reads these; LangChain/LangGraph emit traces automatically. CLI adds `run_name`, `tags`, and `metadata` (including `thread_id`) on every invoke and resume.

**Service spans (`@traceable`, `process_inputs` omits `db`):**

| Function | Module |
|---|---|
| `preview_mappings` | `app/services/mapping_preview_service.py` |
| `apply_mapping_plan` | same |
| `run_reclassification` | `app/services/ingest_service.py` |

**First trace to read:** steward approval — `preview_mappings` span → interrupt gap → `apply_mapping_plan` + `run_reclassification` with `reclass_updated`.

**Studio:** `make studio` → `langgraph dev` on `:2024`. Graphs: `coordinator`, `steward`, `analyst`. Dev server uses in-memory persistence; CLI checkpointer unaffected. Chrome: allow local network access for `smith.langchain.com`.

**Privacy:** traces contain transaction data and upload to LangSmith cloud. `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` hide payloads including task strings.

---

## 12. Testing strategy

Run: `.venv/bin/python -m pytest` (119 passed). `scripts/verify_api.py` is a separate HTTP walkthrough, not pytest.

| Suite | Covers | Fixtures / fakes |
|---|---|---|
| `tests/test_smoke.py` | `/health`, `/docs`, five tables exist | `client`, `db_session` |
| `tests/domain/` | mapping validation, resolve placeholder, CSV source, parse/classify/merchant/dedupe, merged vs DB lookup | `InMemoryNormalizationLookup` (`tests/fakes.py`) |
| `tests/routers/` | HTTP contracts, import+dedupe+patch, reclassify gates, analytics aliases/filters, mapping CRUD/preview/apply | TestClient + shared SQLite |
| `tests/services/` | preview purity/gates/shadow/conflict; apply txn/idempotency/conflicts | direct service calls |
| `tests/agent/` | scripted graphs, interrupt/resume, CLI parse/config, middleware, coordinator routing, Studio entrypoints | `ScriptedChatModel`, `agent_sessions`, `seed_coffee`, `capture_apply` |

**`tests/conftest.py`:** `os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")` **before** app import; tracing env vars set to `false` (LANGSMITH_* and LANGCHAIN_* aliases) before import + autouse fixture. Actual tables on `sqlite://` + `StaticPool` + FK pragma. `get_session` overridden to `db_session`. Drop_all after each test.

**Tracing isolation:** `tests/test_tracing_isolation.py` asserts env state and optionally `tracing_is_enabled()`.

**`tests/agent/helpers.py::ScriptedChatModel`:** `FakeMessagesListChatModel`; `bind_tools` returns `self`; `_generate` stays on the **last** scripted `AIMessage` once exhausted. Drives graphs **without an LLM**. `set_session_factory` points tools at the pytest engine.

**Deliberately not covered:** live-model behavior, Postgres checkpointer, provider auth, concurrent imports, Alembic, chat UI. Studio smoke-tested via factory import (`test_studio_entrypoints`); full `langgraph dev` boot not in pytest.

---

## 13. Extension points and deferred work

| Deferred | What exists to plug into |
|---|---|
| New file/API sources | `TransactionSource.fetch(**kwargs)`; ingest already source-agnostic. Commented `PdfSource` / `ApiSource` in `sources.py`. `Account.source_format` string. |
| Per-import mapping override | `resolve_mapping(..., override=)` currently ignores override. HTTP import has no override field. |
| Alternate lookups | `NormalizationLookup` + fake in tests; preview uses `MergedNormalizationLookup`. |
| Research agent | none. Would wrap like `ask_analyst` (task string, return last text, no parent history). Coordinator prompt would need a new tool. |
| Import dry-run agent | `normalize_rows` already returns errors+unmapped without persist; ingest always commits. A dry-run would stop before `ImportBatch` / insert. |
| Chat UI on thread/interrupt | CLI already: `interrupt` payload `{ops, preview, rationale}`, resume `{decision, ops}`, `thread_id` + durable saver. Same `Command(resume=...)`. |
| Batch HTTP beyond plans | `POST /mappings/apply` **is** the batch endpoint (`ops` list). No batch import of multiple files. No batch PATCH transactions. |
| Alembic | `init_db` + two ALTER helpers only. |

---

## 14. Deliberate oddities and historical decisions

Verified against code:

1. **`DELETE /mappings/{id}` returns 200 + reclass counts**, not 204. Same for PATCH. Wired through `apply_mapping_plan`. Proven: `test_list_and_delete_mapping`, `test_patch_and_delete_reclassify`.
2. **`POST /mappings` does not reclassify.** After create-via-HTTP you still `POST /transactions/reclassify` (QA.md is right). Plan/apply/patch/delete do reclassify.
3. **`search_transactions` shares `_apply_filters`** → `date_to` defaults to today though `spend_only=False`. Agent `list_transactions` does **not** share that helper (no date_to default). HTTP `GET /transactions` also has no date_to default.
4. **Analytics filters by merchant, not category.** Category questions: `summarize(group_by="category")` or HTTP `GET /transactions?category=` (exact effective match).
5. **Mapping plans use `ops` only.** No `rules` alias on `MappingPlanIn`.
6. **`langgraph-supervisor` is absent** — not in requirements, not imported. Coordinator is a `create_agent` with two delegate tools, not a supervisor package.
7. **`AccountOut` omits `default_mapping`** — agents cannot see column maps via `list_accounts`.
8. **`TransactionOut` omits `owner_raw` / `raw_type`** — unmapped worklist is `get_unmapped_values`, not list_transactions.
9. **Import HTTP is 200, not 201.** ImportBatch created even when `inserted=0`.
10. **Unique constraint with NULLs is not the real uniqueness guarantee** for global rules; app-layer identity is.
11. **Steward has no `CurrentDateMiddleware`** — dates must arrive in the task string from the coordinator.
12. **`submit_plan` still runs preview** (and stores `pending_preview`) but `human_approval` **discards** that preview and recomputes.
13. **JSON columns, not JSONB**, despite comments in `models.py`.
14. **InMemorySaver** exists for tests (`open_checkpointer(in_memory=True)` / `in_memory_checkpointer`); CLI never uses it.
15. **Owner canonical in mappings is the `Owner.name` string**, not `owner_id`; ingest maps name→id (`_owner_ids_by_name`). Unmapped owner names stay `owner_id=None`.
16. **Coordinator answers totals itself** (has `get_total`/`summarize`) but **not** top merchants / search / largest — those are analyst-only among analysis tools.

### Doc vs code discrepancy list (ground rule 1)

| Claim location | Claim | Code |
|---|---|---|
| `STRUCTURE.md` tree | Domain files listed without `merchant.py`; agent without `middleware.py`, `schemas.py`, `tools/__init__.py` | Those files exist and are load-bearing |
| `STRUCTURE.md` | “Analyst and steward compile without a checkpointer so they inherit it at runtime” | Analyst is a separate `create_agent` invoked as a tool with **no config/checkpointer**. Steward nested compile has `checkpointer=None` so `interrupt()` raises into the parent; that is not LangGraph subgraph-checkpointer inheritance |
| `app/models.py` comment | ImportMapping stored as JSONB | `mapped_column(JSON)` |
| `README.md` Agent CLI | “needs `ANTHROPIC_API_KEY`” | `STEWARD_MODEL` may be `openai:...` (`.env.example`); app never reads the key name |
| README / STRUCTURE | Studio and tracing documented | Implemented: `langgraph.json`, `studio.py`, README §Observability |
| `requirements.txt` | no `python-dotenv` | imported in `app/config.py` and `app/agent/config.py` (transitive via pydantic-settings) |
| `Dockerfile` | Python 3.12 | Local venv used for this map is 3.14.5 |
| `QA.md` import write-down | unmapped types/categories/owners | `UnmappedValues` / HTTP also include `merchants` |
| `AGENT-QA.md` fail list | CLI cannot start without `ANTHROPIC_API_KEY` | Failure mode depends on `STEWARD_MODEL` provider |
| Observability | (not claimed in README/STRUCTURE) | Implemented: env-driven tracing, CLI metadata, `@traceable` service spans, Studio entrypoints, test isolation |

Stale docs were **not** copied forward except as this discrepancy list.
