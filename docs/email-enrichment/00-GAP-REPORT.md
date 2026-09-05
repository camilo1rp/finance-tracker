# 00 — Email-enrichment gap report

Verified against code on `main` (post-observability slice, 119 tests passing).
References to `docs/PROJECT-MAP.md` cite section numbers; all claims verified against source.

## Resolutions

| DC | Decision |
|---|---|
| DC-1 | **Option A** — write overrides to `Transaction.category_override`. `transaction_overrides` is provenance only. |
| DC-2 | Parallel `preview_override_ops`; additive `overrides` key on the existing preview payload. |
| DC-3 | Free-form `category_hint` plus `snap_category` against stored canonicals — not the dynamic-enum suggestion. |
| DC-4 | `resolve_mapping` untouched (it is the CSV column-map resolver). |
| DC-5 | `EnricherState` mirrors `StewardState` (`NotRequired` extras, no custom reducers). No `evidence_ids` accumulator; evidence ids are validated at submit. The enricher ends via `submit_recommendation`, not structured output. |
| DC-6 | `ReceiptExtractor` ABC plus `FakeExtractor` for tests. |
| DC-7 | `@traceable` strippers on enrichment/proposal spans (`_enrichment_trace_inputs` / `_enrichment_trace_outputs`). |
| DC-8 | No middleware on the enricher. Dates arrive through transaction data and task strings. |

---

## 1. Summary table

| # | Topic | Verdict | Note |
|---|---|---|---|
| 1 | Port pattern (`TransactionSource`, `NormalizationLookup`) | **Holds** | ABC with `abstractmethod`; fakes in `tests/fakes.py`. |
| 2 | Lookup injection into services | **Holds** | Lookup constructed inside the service (`DbNormalizationLookup(db)`); `merged_lookup` built inline in preview. |
| 3 | Mapping-op discriminated union | **Holds** | Discriminator `"op"`, three variants; adding two new variants requires only extending the `Union`. |
| 4 | `apply_mapping_plan` op ordering | **Holds** | Fixed order: deletes → updates → creates → reclassify. New op type needs a new stage. |
| 5 | Conflict detection for identity collisions | **Does not hold** | Current conflict detection is keyed on `(kind, raw_value, account_id, merchant)` identity — no concept of transaction-scoped override identity. |
| 6 | Idempotency mechanics | **Partially** | Create-duplicate skip and delete-missing skip reusable; update re-apply reusable. Override ops need new idempotency: same-category no-op, different-category conflict. |
| 7 | `resolve_mapping` and transaction-scoped precedence | **Does not hold** | `resolve_mapping` resolves `ImportMapping` column maps, not category precedence. Category precedence is SQL `effective_category` + `run_reclassification` — neither has access to a transaction-override table. |
| 8 | `merged_lookup` virtual override layer | **Does not hold** | `MergedNormalizationLookup` indexes by `(kind, raw_value, account_id, merchant)`. An override keyed by `transaction_id` is structurally incompatible. |
| 9 | Reclassification gates — override on category only | **Partially** | Reclassify writes `category_normalized` / `merchant_normalized` and never touches `*_override`. A transaction override that writes `category_override` would survive reclassify, but `effective_category` SQL already reads `category_override` first. |
| 10 | Computed SQL for effective values | **Does not hold** | `effective_category` is a Python-side `case()` expression on three Transaction columns. No join to an override table; adding one changes the expression and every query that uses it. |
| 11 | Enum declaration style | **Holds** | Python `str, Enum` (`TransactionType`, `NormalizationKind`, `SignConvention`). New enums should follow this pattern. |
| 12 | Migration mechanism | **Holds** | No Alembic; `init_db` → `Base.metadata.create_all` + additive ALTER helpers. Three new tables require adding ORM models to `models.py`. |
| 13 | Cleaned-value join convention | **Holds** | `clean_raw_value` (trim+lowercase) applied at write and lookup. `merchant_senders.effective_merchant` must store cleaned values to join correctly. |
| 14 | Category value set | **Does not hold** | Categories are free-form strings, not a closed enum. `canonical_value` for category kind is unconstrained. |
| 15 | Session/commit convention | **Holds** | `apply_mapping_plan` commits; `run_reclassification` does not. `tool_session` opens/closes per call. |
| 16 | One-commit-per-item loop pattern | **Partially** | `ingest_from_source` commits once per call (one file = one commit). No existing "loop of commits" pattern exists; `enrich_range` would be novel. |
| 17 | CLI entrypoint declaration | **Holds** | `python -m app.agent.cli`; `__main__` guard. CLI uses `argparse`; new subcommands straightforward. |
| 18 | Analyst builder state/shape | **Partially** | Analyst uses default `AgentState` (`messages` with `add_messages` reducer only). Design's enricher state adds `evidence_ids` (set-union) and `recommendation` (last-write) — these need custom reducers which `AgentState` does not provide out of the box. |
| 19 | Coordinator invokes analyst as a tool | **Holds** | `ask_analyst(task: str)` → `analyst.invoke({"messages":[{"role":"user","content": task}]})` → `_last_text`. No side effects before invoke. |
| 20 | Steward tool set / `load_proposal` precedent | **Does not hold** | Steward has no "load by id" read tool. Tools are `preview_mapping_rules` and `submit_plan`. A `load_proposal` tool is new infrastructure; no proposals table exists. |
| 21 | Interrupt payload and resume shapes | **Holds** | Payload: `{ops, preview, rationale}`. Resume: `{decision, ops?}`. New op variants in `ops` list flow through unchanged — `edit` indexing is positional. |
| 22 | `middleware.py` automatic attachment | **Does not hold** | Middleware is passed explicitly to `create_agent`. Enricher would need explicit `middleware=[CurrentDateMiddleware()]` — or intentionally omit it (steward omits it today). |
| 23 | Tool session and model provider | **Partially** | `tool_session()` per call ✓. Model is `model_name()` from `STEWARD_MODEL` env var — single model for all agents. No mechanism for a per-agent model override via env. |
| 24 | Structured-output / tool-less model calls | **Does not hold** | No existing tool-less structured-output call exists anywhere in the codebase. All model invocations go through `create_agent` with bound tools. |
| 25 | Config loading mechanism | **Holds** | `pydantic-settings` `BaseSettings` in `app/config.py`; `load_dotenv()` in `config.py` and `agent/config.py`. `.env.example` at repo root. New vars: add to `Settings` or read via `os.environ.get` (current pattern for agent vars). |
| 26 | Tracing hide-inputs toggle | **Holds** | `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` — SDK-level, global per process, not per-run. |
| 27 | `langgraph.json` pattern | **Holds** | Three graphs via `app/agent/studio.py` factories. Enricher needs a new factory + entry in `langgraph.json`. |
| 28 | Scripted-model mechanism | **Partially** | `ScriptedChatModel` replays `AIMessage` list; `bind_tools` returns `self`. A `finalize` node using structured output (not tool calls) would need the scripted model to return a message with `additional_kwargs` or a parseable content block — possible but no existing precedent. |
| 29 | Fake lookup pattern for `FakeEmailSource` | **Holds** | `InMemoryNormalizationLookup` in `tests/fakes.py` — dict-based, no DB. `FakeEmailSource` and `FakeExtractor` can follow the same file and pattern. |
| 30 | Trace-isolation fixture | **Holds** | `autouse=True` `_disable_tracing_env` fixture + module-level env vars in `conftest.py`. New test suites inherit automatically. |
| 31 | Design vs PROJECT-MAP invariants | **Partially** | Invariant 1 ("no apply tool") holds — enricher is read-only. Invariant 13 ("reclassify never writes overrides") is partially threatened: `set_transaction_category` writes `category_override` or a new column, sidestepping the gate. |
| 32 | Silent breakage risk | **Does not hold** | `effective_category` SQL expression is used in analytics queries and transaction filters. Adding an override table without updating this expression silently breaks category reporting. No test asserts the SQL expression matches a join. |
| 33 | Reusable symbols the design doesn't mention | **Partially** | `_backfill_merchant_raw`, `resolved_merchant`, `clean_raw_value`, `find_mapping_by_identity`, `tool_session`, `_service_trace_inputs`, `MappingPlanValidationError`, `@traceable` decorator. |

---

## 2. Findings

### B1. Port pattern

#### 1. `TransactionSource` and `NormalizationLookup` declaration

**Verdict: Holds.**

- `app/domain/sources.py::TransactionSource` — ABC with `@abstractmethod def fetch(self, **kwargs)`. `CsvSource` is the only implementation.
- `app/domain/classification.py::NormalizationLookup` — ABC with `@abstractmethod def resolve(self, kind, raw_value, account_id, merchant=None)`.
- `tests/fakes.py::InMemoryNormalizationLookup` — dict-based fake implementing `NormalizationLookup`.

**Convention:** Python `ABC` + `@abstractmethod`. Fakes live in `tests/fakes.py`. `EmailSource` should be an ABC in `app/domain/` with a fake in `tests/fakes.py`.

#### 2. Lookup construction and injection

**Verdict: Holds.**

- `app/services/ingest_service.py::ingest_from_source` — constructs `DbNormalizationLookup(db)` inline.
- `app/services/mapping_preview_service.py::preview_mappings` — constructs `MergedNormalizationLookup(_virtual_specs(...))` inline.
- `app/domain/db_lookup.py::merged_lookup_from_db` — loads DB rows, appends `proposed`, returns `MergedNormalizationLookup`.

Lookups are never injected as constructor args to a service class; they are built fresh inside service functions. A virtual override layer for `preview_mappings` must be composed the same way — but see item 8 for structural issues.

---

### B2. Mapping ops and plans

#### 3. Discriminated union

**Verdict: Holds.**

`app/schemas.py::MappingOp` — `Annotated[Union[CreateMappingOp, UpdateMappingOp, DeleteMappingOp], Field(discriminator="op")]`. Discriminator field: `op` (Literal string per variant: `"create"`, `"update"`, `"delete"`).

Adding `SetTransactionCategoryOp(op="set_transaction_category")` and `RemoveTransactionOverrideOp(op="remove_transaction_override")` extends the `Union` and the `Literal` discriminator values. The interrupt payload passes `ops` as `list[dict]` — new dict shapes with a new `op` value flow through without touching the payload shape.

#### 4. `apply_mapping_plan` op ordering

**Verdict: Holds.**

`app/services/mapping_preview_service.py::apply_mapping_plan` — hard-coded order: deletes → flush → updates → flush → creates → flush → `run_reclassification` → flush → `unmapped_summary` → commit.

A transaction-scoped op (`set_transaction_category`) does not create/update/delete a `NormalizationMapping` row. It needs its own stage. **Suggestion:** add a new stage after creates and before reclassify that writes `transaction_overrides`, then reclassify picks up the override.

#### 5. Conflict detection

**Verdict: Does not hold.**

`app/services/mapping_preview_service.py::_conflict_errors` checks `conflicts_with_existing_id` which is set by `find_mapping_by_identity` — identity is `(kind, raw_value, account_id, merchant)` on the `normalization_mappings` table. There is no concept of "same transaction already has an override with a different category." The design's conflict rule ("different category on an already-overridden transaction is a conflict rejected on apply") requires a new check against the `transaction_overrides` table, not the existing `_conflict_errors` path.

**Suggestion:** add a dedicated `_override_conflict_errors` function that queries `transaction_overrides` for each `set_transaction_category` op's `transaction_id` and rejects if a different category is already stored.

#### 6. Idempotency mechanics

**Verdict: Partially.**

Existing idempotency in `apply_mapping_plan`:
- Duplicate create: `find_mapping_by_identity` finds existing with same canonical → `skipped.append(SkippedOp(..., reason="duplicate"))`. Reusable pattern for `set_transaction_category` (same txn+same category → skip).
- Missing delete: `db.get(NormalizationMapping, id)` returns None → skip. Reusable for `remove_transaction_override` (no override → skip).
- Update re-apply: canonical already equals new value → reclassify finds 0 changes. Reusable concept.

New: `set_transaction_category` with a *different* category on an already-overridden transaction has no parallel — this is the conflict from item 5.

---

### B3. Resolution and preview

#### 7. `resolve_mapping` and transaction-scoped precedence (HIGH RISK)

**Verdict: Does not hold.**

The design says: "`resolve_mapping` category precedence becomes: transaction override → per-import placeholder → merchant/category rules → raw."

**This conflates two different `resolve_mapping` functions:**

- `app/domain/mapping.py::resolve_mapping(account_default_mapping, override=None) -> ImportMapping` — resolves **column mapping** (which CSV columns map to which fields). Currently always returns `account_default_mapping`; `override` is ignored. This has nothing to do with category precedence.
- Category precedence is implemented in two places:
  1. **SQL:** `app/models.py::effective_category` — `case(override, normalized, raw)`. Used by analytics queries and transaction list filters.
  2. **Python:** `app/services/ingest_service.py::run_reclassification` — writes `category_normalized` from lookup; never touches `category_override`.

Neither has access to a `transaction_overrides` table or `transaction_id`-scoped lookup. `run_reclassification` iterates transactions and calls `classify_category(raw, lookup, account_id, merchant=...)` — lookup is `(kind, raw_value, account_id, merchant)` keyed, not transaction-keyed.

**To insert a transaction-override level:**

1. The SQL `effective_category` expression must be changed — either to join `transaction_overrides` or to read a new column on `transactions` (e.g., reusing existing `category_override`).
2. `run_reclassification` must be aware that a transaction with an active override should not have its `category_normalized` change clobber the effective value — but this already holds because `category_override` already takes precedence in the SQL expression.
3. `preview_mappings` operates via `MergedNormalizationLookup` which is rule-keyed, not transaction-keyed. A transaction override cannot be represented as a `RuleSpec`.

**Suggestion:** use the existing `category_override` column on `Transaction` as the write target for `set_transaction_category`. This avoids changing `effective_category` SQL and reclassify gates entirely. The `transaction_overrides` table then becomes a provenance/audit table, not a precedence table.

#### 8. `merged_lookup` virtual override layer

**Verdict: Does not hold.**

`app/domain/merged_lookup.py::MergedNormalizationLookup` indexes rules by `(kind, raw_value, account_id, merchant)`. Resolution: given a raw value and context, find the best-matching rule.

A transaction override is keyed by `transaction_id`, not `(kind, raw_value, ...)`. It cannot be expressed as a `RuleSpec` or indexed in `_by_scope`. The design says "`merged_lookup` must compose virtual overrides the same way it composes virtual rules so `preview_mappings` stays pure. Ref label: `override:<transaction_id>`." This is a structural change, not a mechanical extension.

**Suggestion:** `preview_mappings` for override ops should bypass the lookup entirely. For a `set_transaction_category` op, preview just needs to report the current `effective_category` vs. the proposed category for that `transaction_id` — a direct query, not a lookup-based classification. Add a separate preview path for override ops alongside the existing rule-based path.

#### 9. Reclassification gates

**Verdict: Partially.**

`app/services/ingest_service.py::run_reclassification` recomputes:
- `transaction_type` + `is_spend` (only if `raw_type` present)
- `owner_id` (only if `owner_raw` present)
- `merchant_raw` (backfill if empty), `merchant_normalized` (always)
- `category_normalized` (always from `category_raw` + resolved merchant)

Never touches: `category_override`, `merchant_override`, `category_raw`, `merchant_raw` (unless backfill). An override on `category_override` alone would correctly leave `effective_merchant` and other fields untouched — `category_override` is not written by reclassify (invariant 13, PROJECT-MAP §9).

However, if the design writes to `category_override`, then `reclassify_transactions` called afterward would still show the override winning in `effective_category` (SQL precedence). This is the correct behavior, but it means `category_override` is now dual-purpose: manual PATCH and enrichment override. The design may want to distinguish these.

#### 10. Computed SQL expressions

**Verdict: Does not hold.**

`app/models.py::effective_category` and `effective_merchant` are Python-side SQLAlchemy `case()` expressions over `Transaction` columns — `category_override`, `category_normalized`, `category_raw`. They are not views or computed columns. They are used inline in `select()` statements in analytics service and transaction routers.

If the design adds a `transaction_overrides` table as a separate join target for precedence, every query using `effective_category` must add a join or subquery. This is a high-impact change.

If instead the design writes to `Transaction.category_override` directly, no SQL expression change is needed — the existing `case()` already reads `category_override` first.

---

### B4. Data model

#### 11. Enum declaration style

**Verdict: Holds.**

- `app/domain/classification.py::TransactionType(str, Enum)` — `SPEND`, `INCOME`, `TRANSFER`, `REFUND`, `FEE`, `ADJUSTMENT`, `UNKNOWN` (legacy `PAYMENT` retained for migrated rows only).
- `app/domain/classification.py::NormalizationKind(str, Enum)` — `transaction_type`, `category`, `owner`, `merchant`.
- `app/domain/mapping.py::SignConvention(str, Enum)` — `negative_is_spend`, `positive_is_spend`.

All are Python `str, Enum`. DB stores the string value; no SQLAlchemy `Enum` type or CHECK constraint. New enums (`EvidenceKind`, `MatchKind`, `SenderOrigin`) should follow the same `(str, Enum)` pattern with string values stored in `String` columns.

#### 12. Migration mechanism

**Verdict: Holds.**

`app/database.py::init_db` — `Base.metadata.create_all(bind=engine)` plus additive `ALTER TABLE` helpers (`_ensure_merchant_columns`, `_ensure_mapping_merchant_scope`). No Alembic.

Adding three tables (`transaction_evidence`, `merchant_senders`, `transaction_overrides`) requires: define ORM classes inheriting `Base` in `app/models.py`; `create_all` will create them on next startup. Existing `ALTER TABLE` helpers show the pattern for additive column additions on existing tables if needed.

#### 13. Cleaned-value join convention

**Verdict: Holds.**

`app/domain/classification.py::clean_raw_value` — `raw_value.strip().lower()`. Applied at write time (`app/routers/mappings.py::create_mapping`, `app/services/mapping_preview_service.py::_create_spec`/`_cleaned_merchant`). Applied at read time (all `classify_*` functions). Lookups assume already-cleaned keys.

`merchant_senders.effective_merchant` must store values cleaned via `clean_raw_value` to join correctly against `effective_merchant` (which is override > normalized > raw, where normalized is the cleaned canonical — but canonicals are **not** cleaned). The join is on the merchant's resolved effective value, which may be a canonical (not cleaned) or a raw (not cleaned). **Correction:** effective_merchant values are not necessarily cleaned. The design should either:
- Clean the join key at query time, or
- Store `merchant_senders.effective_merchant` as the **canonical** merchant name (not cleaned), matching how `merchant_normalized` is stored.

#### 14. Category value set

**Verdict: Does not hold.**

Categories are free-form strings. `canonical_value` for `kind="category"` is unconstrained — no enum, no CHECK, no validation beyond "not empty." The only constrained kind is `transaction_type` (must be a `TransactionType` enum value). Tests use arbitrary strings like `"Dining"`, `"Shopping"`, `"Groceries"`.

This means `LineItem.category_hint` cannot be constrained to an "existing category value set" because no such closed set exists. The design must either:
1. Accept free-form category hints (extraction model proposes any string), or
2. Dynamically query distinct `canonical_value WHERE kind='category'` at extraction time and constrain the model's output to that set plus `"unknown"`.

**Suggestion:** option 2 — query distinct canonicals as a dynamic enum for structured output.

---

### B5. Services and transactions

#### 15. Session/commit convention

**Verdict: Holds.**

- `app/services/ingest_service.py::run_reclassification` — does **not** commit. Caller owns the transaction.
- `app/services/ingest_service.py::reclassify_transactions` — calls `run_reclassification` then `db.commit()`.
- `app/services/mapping_preview_service.py::apply_mapping_plan` — wraps all writes + `run_reclassification` + `unmapped_summary` in a single try/commit/except-rollback block.
- `app/agent/config.py::tool_session` — context manager, opens and closes session per call.

`enrich_transaction` must follow `apply_mapping_plan`'s pattern: try → writes → reclassify → commit; except → rollback.

#### 16. One-commit-per-item loop pattern

**Verdict: Partially.**

`ingest_from_source` does one commit per source file (not per row). No existing code commits inside a loop. `enrich_range` looping over transactions with one commit per transaction would be a new pattern.

**Suggestion:** model after `apply_mapping_plan`'s atomic pattern but called in a loop, with each iteration getting its own session via `tool_session()`.

#### 17. CLI entrypoint declaration

**Verdict: Holds.**

`app/agent/cli.py` — `python -m app.agent.cli [thread_id]` with `--steward` flag. Uses `argparse`. The module has an `if __name__ == "__main__": main()` guard. New CLI subcommands (e.g., `--enrich`) can be added as argparse arguments in the same module, or a separate `enrichment_cli.py` entry.

---

### B6. Agent system

#### 18. Analyst builder state shape

**Verdict: Partially.**

`app/agent/analyst.py::build_analyst` — uses `create_agent` with default `AgentState`:
- `messages: list[AnyMessage]` with `add_messages` reducer
- `jump_to` (ephemeral/private)
- `structured_response`

The enricher's proposed state adds:
- `evidence_ids` (set-union reducer) — `AgentState` has no set-union reducer. Requires a custom `TypedDict` with `Annotated[set, operator.or_]` or similar.
- `recommendation` (last-write) — `NotRequired` fields on `AgentState` subclass use last-write by default (same pattern as `StewardState`).

The analyst has **no custom state** — it uses plain `AgentState`. The enricher's state is closer to `StewardState` (which extends `AgentState` with `NotRequired` extras). **Clone `StewardState`'s pattern**, not the analyst's.

`create_agent` accepts `state_schema` — the steward uses `state_schema=StewardState`. The enricher should do the same.

Recursion limit: analyst inherits from invoke config (no explicit limit). Coordinator CLI sets `recursion_limit=25`. The enricher, compiled without a checkpointer and invoked as a tool, would inherit the parent's config — same as the analyst.

#### 19. Coordinator → analyst invocation

**Verdict: Holds.**

`app/agent/tools/subagents.py::make_subagent_tools` — `ask_analyst(task: str)` invokes `analyst.invoke({"messages": [{"role": "user", "content": task}]})`, returns `_last_text(result)`. No DB access or side effects before invoke. `run_data_steward` follows the same pattern.

`run_enricher(task)` would follow this exactly: `@tool("run_enricher") def run_enricher(task: str) -> str: result = enricher.invoke(...); return _enricher_summary(result)`.

#### 20. Steward tool set / `load_proposal`

**Verdict: Does not hold.**

Steward tools (`STEWARD_AGENT_TOOLS` in `app/agent/tools/__init__.py`): `READ_TOOLS` + `STEWARD_TOOLS` = `[list_owners, list_accounts, get_unmapped_values, list_mappings, list_transactions, search_transactions]` + `[preview_mapping_rules, submit_plan]`.

No "load by id" read tool exists. `load_proposal(id)` would be new. For storage: proposals could be stored in coordinator graph state (via the checkpointer) or in a new `proposals` table. The checkpointer stores full graph state snapshots — adding a `proposal` field to coordinator state is possible but would serialize the full proposal into every checkpoint. A `proposals` table is cleaner and avoids bloating checkpoints.

**Suggestion:** add a `proposals` table (id, enricher_run_id, recommendation JSON, status, created_at). The steward gets `load_proposal` as a new read tool.

#### 21. Interrupt payload and resume shapes

**Verdict: Holds.**

Interrupt payload (`app/agent/steward_graph.py::human_approval`):
```python
{"ops": submitted, "preview": preview, "rationale": state.get("rationale")}
```

Resume value: non-dict → `{"decision": decision}`. `decision == "approve"` → goto execute with ops (original or edited subset). CLI `edit 0,2` → `{"decision":"approve","ops":[ops[i] for i in idxs]}` — 0-based positional indexing.

New op variants (dicts with `"op": "set_transaction_category"`) in the `ops` list flow through the interrupt/resume payload unchanged. The `edit` indexing is positional (by list index), not by op type. No changes needed.

#### 22. Middleware attachment

**Verdict: Does not hold.**

`app/agent/middleware.py::CurrentDateMiddleware` is passed explicitly:
- Coordinator: `create_agent(..., middleware=[CurrentDateMiddleware()])` in `app/agent/coordinator.py::build_coordinator`.
- Analyst: `create_agent(..., middleware=[CurrentDateMiddleware()])` in `app/agent/analyst.py::build_analyst`.
- Steward: `create_agent(...)` in `app/agent/steward_graph.py::build_steward_builder` — **no middleware**.

The enricher would not get middleware automatically. Must explicitly pass `middleware=[CurrentDateMiddleware()]` if date context is needed, or omit it (like the steward) if dates arrive via the task string.

#### 23. Tool sessions and model provider

**Verdict: Partially.**

- Sessions: `app/agent/config.py::tool_session()` — context manager, session per call. All tools use it. ✓
- Model: `app/agent/config.py::model_name()` returns `os.environ.get("STEWARD_MODEL", DEFAULT_MODEL)`. One model for all agents. `build_analyst` and `build_steward_builder` accept `model=None` (default) which resolves to `model_name()`. `build_coordinator` resolves model per sub-agent: `analyst_model`, `steward_model` kwargs.

For a separate extraction model (`EXTRACTION_MODEL`), the current pattern is `os.environ.get(...)` — add another env var reader in `config.py`. The extraction model would not be a LangGraph agent model but a direct `langchain` model call. No existing mechanism for per-tool model selection; a new `extraction_model_name()` function in `config.py` is straightforward.

#### 24. Structured-output / tool-less model calls

**Verdict: Does not hold.**

No tool-less structured-output model call exists in the codebase. Every model interaction goes through `create_agent` with `bind_tools`. The `ReceiptExtractor` using structured output (e.g., `model.with_structured_output(ReceiptExtraction)`) would be a first-of-kind pattern.

**Suggestion:** use `langchain`'s `with_structured_output` on a `ChatModel` instance. This is well-supported by `langchain-anthropic` and `langchain-openai` (both in `requirements.txt`). Define a Pydantic model for `ReceiptExtraction` and call `model.with_structured_output(ReceiptExtraction).invoke(messages)`.

---

### B7. Configuration and observability

#### 25. Config loading

**Verdict: Holds.**

- `app/config.py::Settings` — `pydantic_settings.BaseSettings` with `env_file=".env"`, `extra="ignore"`. Only `database_url` today.
- Agent config: `app/agent/config.py` — `load_dotenv()` + `os.environ.get(...)` for `STEWARD_MODEL` and `AGENT_CHECKPOINT_PATH`.
- `.env.example` at repo root lists `DATABASE_URL`, `STEWARD_MODEL`, `AGENT_CHECKPOINT_PATH`, plus commented `LANGSMITH_*`.

New variables must follow the same split: DB-related → add to `Settings`; agent/enrichment-related → `os.environ.get` in `agent/config.py` (or a new `enrichment/config.py`). Add all ten to `.env.example` with comments.

#### 26. Tracing hide-inputs toggle

**Verdict: Holds.**

`LANGSMITH_HIDE_INPUTS` and `LANGSMITH_HIDE_OUTPUTS` are SDK-level, global (per-process), not per-run. Email content in extraction calls would appear in traces unless hidden globally.

The design requirement that "email content must never reach traces" cannot be achieved with the existing per-process toggle without hiding **all** inputs. **Suggestion:** use `@traceable(process_inputs=...)` on enrichment service functions (same pattern as `_service_trace_inputs` which strips `db`) to strip email body content from trace inputs while preserving other span data.

#### 27. `langgraph.json` pattern

**Verdict: Holds.**

`langgraph.json` — three graphs via `app/agent/studio.py` factories:
```json
{"coordinator": "./app/agent/studio.py:coordinator_graph",
 "steward": "./app/agent/studio.py:steward_graph",
 "analyst": "./app/agent/studio.py:analyst_graph"}
```

Add `"enricher": "./app/agent/studio.py:enricher_graph"` and a corresponding `enricher_graph()` factory in `studio.py`.

---

### B8. Tests

#### 28. Scripted-model mechanism

**Verdict: Partially.**

`tests/agent/helpers.py::ScriptedChatModel` — extends `FakeMessagesListChatModel`. `responses` is a list of `AIMessage`. `_generate` stays on the last response once exhausted. `bind_tools` returns `self` (tools are accepted but ignored — the scripted responses must include tool calls as `AIMessage(content=..., tool_calls=[...])` to drive the graph).

For the enricher's `finalize` node: if `finalize` uses structured output (not tool calls), the scripted model must return an `AIMessage` whose content is parseable by `with_structured_output`. This works if the node uses `model.invoke()` and the fake returns a pre-built message — but `with_structured_output` typically wraps the model in a chain that expects specific output parsing. The `ScriptedChatModel` would need to return content matching the Pydantic schema's JSON, and the `with_structured_output` wrapper must not call `bind_tools` in a way that breaks the fake.

**Suggestion:** make `ReceiptExtractor` a protocol with a `FakeExtractor` that returns canned `ReceiptExtraction` objects, bypassing the model entirely in tests.

#### 29. Fake pattern for `FakeEmailSource` / `FakeExtractor`

**Verdict: Holds.**

`tests/fakes.py::InMemoryNormalizationLookup` — dict-based, constructor takes pre-built dicts, `resolve()` does lookups. Same file, same pattern. `FakeEmailSource` would implement the `EmailSource` ABC with canned `EmailRef`/`EmailMessage` returns. `FakeExtractor` implements the `ReceiptExtractor` protocol with canned `ReceiptExtraction`.

#### 30. Trace-isolation fixture

**Verdict: Holds.**

`tests/conftest.py` — module-level `os.environ[key] = value` for all `LANGSMITH_*` and `LANGCHAIN_*` tracing vars, plus `autouse=True` `_disable_tracing_env` fixture that re-sets them. New test suites in `tests/` inherit both automatically.

---

### B9. Discrepancies and risks

#### 31. Design vs PROJECT-MAP invariants

**Verdict: Partially.**

- **Invariant 1** ("no apply tool; all writes through `apply_mapping_plan` / interrupt `execute`"): The enricher persists evidence and proposals as an "observation cache" — this is a write path outside `apply_mapping_plan`. This is acceptable if evidence/proposals are not effective-value mutations, but the invariant's spirit is "no unreviewed writes." Evidence upserts (read-only observation data) are a reasonable exception, but should be documented.
- **Invariant 13** ("reclassify never writes overrides"): If `set_transaction_category` writes `category_override`, then `apply_mapping_plan` (which calls `run_reclassification`) would need a new stage that writes overrides before reclassify. Reclassify itself still doesn't write overrides — the new op does. Invariant technically holds but the boundary is subtle.
- **Invariant 3** ("sessions per tool call, never across interrupt"): Holds — enricher tools use `tool_session()`.
- **Invariant 10** ("subagents receive scope via task strings"): Holds for enricher.

#### 32. Silent breakage risk

**Verdict: Does not hold.**

1. If a `transaction_overrides` table is added as a **join** target for `effective_category`, every analytics query, transaction list filter, and reclassification gate that uses `app/models.py::effective_category` must be updated. If the join is omitted from even one query, that query silently ignores overrides. No test asserts that all queries consistently use the same `effective_category` definition.

2. If `set_transaction_category` writes to `Transaction.category_override` (reusing the existing column), PATCH `/transactions/{id}` with `category_override=None` would silently clear an enrichment-set override. No test distinguishes "user-set override" from "enrichment-set override."

#### 33. Reusable symbols

**Verdict: Partially.**

Symbols the design should explicitly reuse but doesn't mention:

| Symbol | Location | Use |
|---|---|---|
| `clean_raw_value` | `app/domain/classification.py` | Cleaning merchant names for `merchant_senders` |
| `resolved_merchant` | `app/domain/merchant.py` | Getting effective merchant for sender matching |
| `_backfill_merchant_raw` | `app/services/ingest_service.py` | Already used in preview; needed for enrichment matching |
| `tool_session` | `app/agent/config.py` | All enricher tools |
| `_service_trace_inputs` | `app/services/mapping_preview_service.py` (and `ingest_service.py`) | Stripping `db` from trace inputs |
| `@traceable` | `langsmith` (used in `mapping_preview_service.py`, `ingest_service.py`) | Enrichment service functions |
| `MappingPlanValidationError` | `app/services/mapping_preview_service.py` | Validation errors in override ops |
| `find_mapping_by_identity` | `app/services/mapping_preview_service.py` | Not directly, but pattern for `find_override_by_identity` |
| `set_session_factory` | `app/agent/config.py` | Test injection for enricher tools |
| `StewardState` pattern | `app/agent/schemas.py` | Model for `EnricherState` |
| `make_subagent_tools` | `app/agent/tools/subagents.py` | Adding `run_enricher` |
| `build_coordinator` kwargs | `app/agent/coordinator.py` | Adding `enricher_model` kwarg |

---

## 3. Design changes required

Ordered by impact (most design-altering first).

### DC-1. Transaction-override storage mechanism (items 7, 8, 9, 10, 32)

**The design's assumption that `resolve_mapping` controls category precedence and that `merged_lookup` can compose transaction-scoped overrides is incorrect.** Category precedence is SQL-side (`effective_category` case expression) and Python-side (`run_reclassification`), neither of which operates on transaction-keyed lookups.

**Decision required:** Write overrides to `Transaction.category_override` (reusing the existing column) vs. adding a `transaction_overrides` table with a join into `effective_category`.

- **Option A (reuse `category_override`):** Zero SQL expression changes, zero analytics query changes. Downside: conflates user manual overrides (PATCH) with enrichment overrides; no provenance on the override itself (provenance lives in `transaction_evidence`).
- **Option B (new table + join):** Requires changing `effective_category` expression, every analytics query, and `run_reclassification`. Much higher risk and scope.

### DC-2. Preview path for transaction-scoped ops (items 5, 8)

`preview_mappings` uses `MergedNormalizationLookup` which is rule-keyed. Transaction-scoped ops cannot be previewed through this path. A separate preview mechanism is needed: for each `set_transaction_category` op, directly query the transaction's current `effective_category` and report the proposed change. This is a new code path in `preview_mappings` (or a parallel function).

### DC-3. Category value set is open, not closed (item 14)

`LineItem.category_hint` cannot be constrained to a compile-time enum. Categories are free-form strings. The extraction model must either accept free-form hints or dynamically query distinct category canonicals at extraction time.

### DC-4. `resolve_mapping` naming collision (item 7)

The design uses `resolve_mapping` to mean "determine category precedence," but `app/domain/mapping.py::resolve_mapping` resolves CSV column mappings. The design should use a different name for the category-precedence function to avoid confusion (e.g., `resolve_effective_category`).

### DC-5. Enricher state uses `StewardState` pattern, not analyst (item 18)

The analyst has no custom state. The enricher's `evidence_ids` (set-union) and `recommendation` (last-write) require a custom `TypedDict` extending `AgentState`, following `StewardState`'s pattern. `evidence_ids` with a set-union reducer needs `Annotated[set, operator.or_]` or equivalent — verify LangGraph's reducer support.

### DC-6. No existing structured-output precedent (item 24)

`ReceiptExtractor` using `with_structured_output` is a new pattern. The `ReceiptExtractor` should be a protocol so tests can use a `FakeExtractor` that bypasses the model entirely, avoiding scripted-model complications with structured output parsing.

### DC-7. Tracing privacy for email content (item 26)

Global `LANGSMITH_HIDE_INPUTS` hides all inputs, not just email content. Use `@traceable(process_inputs=...)` to selectively strip email bodies from enrichment service spans while keeping other trace data visible.

### DC-8. Middleware is explicit, not automatic (item 22)

The enricher won't inherit `CurrentDateMiddleware` automatically. Decide whether the enricher needs date context (probably not — dates come from transaction data) and wire accordingly.

---

## 4. Reuse list (by phase)

### Phase 1 — Port + fake + tables + deterministic matcher + CLI

| Symbol | Path | Use |
|---|---|---|
| `TransactionSource` (ABC pattern) | `app/domain/sources.py::TransactionSource` | Model for `EmailSource` ABC |
| `InMemoryNormalizationLookup` (fake pattern) | `tests/fakes.py::InMemoryNormalizationLookup` | Model for `FakeEmailSource`, `FakeExtractor` |
| `Base` | `app/models.py::Base` | New ORM models inherit from it |
| `clean_raw_value` | `app/domain/classification.py::clean_raw_value` | Clean merchant names for `merchant_senders` |
| `resolved_merchant` | `app/domain/merchant.py::resolved_merchant` | Effective merchant for sender matching |
| `tool_session` | `app/agent/config.py::tool_session` | Session management in enrichment service |
| `_service_trace_inputs` | `app/services/mapping_preview_service.py::_service_trace_inputs` | Strip `db` from trace inputs |
| `@traceable` | `langsmith.traceable` | Decorate enrichment service functions |
| `settings` | `app/config.py::settings` | Access `DATABASE_URL` |
| CLI `argparse` pattern | `app/agent/cli.py::main` | Add `--enrich` subcommand |

### Phase 2 — Real extractor + golden tests

| Symbol | Path | Use |
|---|---|---|
| `model_name` / `os.environ.get` | `app/agent/config.py::model_name` | Pattern for `extraction_model_name()` |
| `ScriptedChatModel` | `tests/agent/helpers.py::ScriptedChatModel` | Reference (but prefer `FakeExtractor` protocol) |
| `conftest.py` fixtures | `tests/conftest.py` | `db_session`, `_disable_tracing_env` inherited |
| `set_session_factory` | `app/agent/config.py::set_session_factory` | Test injection |

### Phase 3 — Gmail MCP adapter

| Symbol | Path | Use |
|---|---|---|
| (no direct reuse — MCP client is new infrastructure) | — | — |

### Phase 4 — Override ops through steward gate

| Symbol | Path | Use |
|---|---|---|
| `MappingOp` union | `app/schemas.py::MappingOp` | Extend with new op variants |
| `parse_mapping_op` | `app/schemas.py::parse_mapping_op` | Parsing new op dicts |
| `apply_mapping_plan` | `app/services/mapping_preview_service.py::apply_mapping_plan` | Add override stages |
| `preview_mappings` | `app/services/mapping_preview_service.py::preview_mappings` | Add override preview path |
| `MappingPlanValidationError` | `app/services/mapping_preview_service.py::MappingPlanValidationError` | Validation errors |
| `find_mapping_by_identity` pattern | `app/services/mapping_preview_service.py::find_mapping_by_identity` | Pattern for override identity checks |
| `_conflict_errors` pattern | `app/services/mapping_preview_service.py::_conflict_errors` | Pattern for override conflict checks |
| `run_reclassification` | `app/services/ingest_service.py::run_reclassification` | Called after override writes |

### Phase 5 — Enricher subagent + coordinator routing

| Symbol | Path | Use |
|---|---|---|
| `StewardState` | `app/agent/schemas.py::StewardState` | Pattern for `EnricherState` |
| `build_steward_builder` / `build_steward_graph` | `app/agent/steward_graph.py` | Pattern for enricher graph builder |
| `make_subagent_tools` | `app/agent/tools/subagents.py::make_subagent_tools` | Add `run_enricher` tool |
| `build_coordinator` | `app/agent/coordinator.py::build_coordinator` | Add `enricher_model` kwarg, add enricher tool |
| `_last_text` / `_steward_summary` | `app/agent/tools/subagents.py` | Pattern for `_enricher_summary` |
| `CurrentDateMiddleware` | `app/agent/middleware.py::CurrentDateMiddleware` | Optional explicit wiring |
| `studio.py` factories | `app/agent/studio.py` | Add `enricher_graph()` factory |
| `seed_coffee` / `agent_sessions` | `tests/agent/helpers.py` | Test fixtures for enricher tests |
| `capture_apply` | `tests/agent/helpers.py::capture_apply` | Pattern for mocking enrichment writes |

---

## Counts

- **Holds:** 16
- **Partially:** 9
- **Does not hold:** 8

## Top three design changes required

1. **DC-1 — Transaction-override storage mechanism:** The core assumption that `resolve_mapping` and `merged_lookup` handle transaction-scoped category precedence is incorrect. Decide between reusing `Transaction.category_override` (low risk, no SQL changes) vs. a new `transaction_overrides` table with join (high risk, pervasive query changes).

2. **DC-2 — Preview path for transaction-scoped ops:** `preview_mappings` via `MergedNormalizationLookup` cannot express transaction-keyed overrides. A parallel preview path is needed for `set_transaction_category` ops.

3. **DC-3 — Category value set is open:** Categories are free-form strings, not a closed enum. `LineItem.category_hint` constraints and extraction model prompting must account for this.
