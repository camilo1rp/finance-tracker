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

When a mapping plan is submitted, the same conversation pauses with a preview computed from the rules actually submitted. Type:

- `approve` — apply every listed rule
- `reject` — apply nothing
- `edit 0,2` — apply those indexes only

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
