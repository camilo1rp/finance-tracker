# Finance tracker

Personal ledger API plus a conversational agent for analytics and mapping cleanup.

## Run the API

```bash
cp .env.example .env
docker compose up --build
```

Swagger is at `http://localhost:8000/docs`. See `QA.md` for a first import pass.

## Agent CLI

One entrypoint. The coordinator resolves names and dates, answers simple totals itself, delegates comparisons to the analyst, and delegates mapping cleanup to the steward.

```bash
# Live model (needs ANTHROPIC_API_KEY). Reuse thread_id after kill/restart.
python -m app.agent.cli <thread_id>

# Steward only (propose → preview → approve → apply)
python -m app.agent.cli --steward <thread_id>
```

See `AGENT-QA.md` for a live-model sitting that checks routing, approval, kill/restart, and API cross-checks.

When a mapping plan is submitted, the same conversation pauses with a preview computed from the ops actually submitted. Type:

- `approve` — apply every listed op
- `reject` — apply nothing
- `edit 0,2` — apply those indexes only

Plans are CRUD: `create`, `update` (change a rule's canonical by `mapping_id`), and `delete`. A create whose identity already exists with a **different** canonical is a conflict, not a skip — submit an `update` on that id instead. After apply, the steward reports `created_ids` / `updated_ids` / `deleted_ids` and `reclass_updated` verbatim.

`PATCH /mappings/{id}` updates a canonical and reclassifies in one transaction. `DELETE /mappings/{id}` now reclassifies in the same transaction and returns `200` with reclass counts (not `204`).

The conversation then continues on the same thread.

### Checkpoints

Kill/restart with the same `thread_id` resumes a paused approval.

| `DATABASE_URL` | Checkpointer |
|---|---|
| Postgres | `PostgresSaver` in that database |
| anything else | SQLite file at `AGENT_CHECKPOINT_PATH` (default `.agent_checkpoints.sqlite`) |

`InMemorySaver` is tests-only and does not survive process exit.

### Env

See `.env.example`:

- `DATABASE_URL` — app database (and Postgres checkpoints when this is Postgres)
- `STEWARD_MODEL` — model id for coordinator, analyst, and steward (default `anthropic:claude-sonnet-4-6`)
- `AGENT_CHECKPOINT_PATH` — SQLite checkpoint file when not using Postgres

## Observability

### LangSmith tracing

Tracing is optional and env-driven — no LangSmith key is required to run the CLI, API, or tests.

1. Sign up at [smith.langchain.com](https://smith.langchain.com) and create an API key.
2. Uncomment the observability block in `.env.example` and copy into `.env`:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your-key>
LANGSMITH_PROJECT=finance-agent
# LANGSMITH_ENDPOINT=https://api.smith.langchain.com   # US default; use https://eu.api.smith.langchain.com for EU
```

Each CLI turn produces one trace named `coordinator-turn` or `steward-turn`, tagged `cli` or `steward-cli`, with `thread_id` in metadata. Expand the tree to see:

- **Coordinator run** → `ask_analyst` tool call (full task string in tool input) → analyst's `summarize` calls
- **Steward approval** → `preview_mappings` span → interrupt gap → post-resume `apply_mapping_plan` and `run_reclassification` with `reclass_updated`

Traces include transaction data and upload to LangSmith cloud. Optional `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` hide all payloads — including the task strings tracing exists to show. Use tracing in dev only when that trade-off is acceptable.

### LangGraph Studio

Local graph debugging via [LangGraph Studio](https://smith.langchain.com/studio/). Requires Python ≥ 3.11 and &lt; 3.14, plus dev deps:

```bash
pip install -r requirements-dev.txt
make studio
```

Open [Studio](https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024) (or use the URL printed when the server starts). Verify the API is up at `http://127.0.0.1:2024/docs`.

**Chrome:** Studio runs on HTTPS and talks to your local HTTP server. On first connect, allow **Local network access** for `smith.langchain.com` — click the site icon left of the address bar → **Site settings** → **Local network access** → **Allow**, then reload Studio. If the option is missing, add `https://smith.langchain.com` at `chrome://settings/content/localNetworkAccess`.

Three graphs are registered in `langgraph.json`: `coordinator`, `steward`, `analyst`. Studio uses an in-memory checkpointer; threads vanish on server restart. The CLI's Postgres/SQLite checkpointer is separate and unaffected.

Run a mapping-cleanup message on the steward or coordinator graph to hit the `human_approval` interrupt and resume from the Studio UI.
