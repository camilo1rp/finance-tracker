# Project structure

finance-tracker/
├── docker-compose.yml       # postgres + api services
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt     # langgraph-cli[inmem] for Studio
├── Makefile                 # make studio → langgraph dev
├── langgraph.json           # Studio graph entrypoints (coordinator, steward, analyst)
├── .env.example
├── README.md                # API + agent CLI
├── QA.md                    # import QA pass (Chase + Apple)
├── AGENT-QA.md              # live agent CLI QA pass
├── app/
│   ├── main.py              # FastAPI app instantiation, router registration
│   ├── config.py            # Settings (env-driven: DB url, etc.)
│   ├── database.py          # SQLAlchemy engine/session (Postgres)
│   ├── models.py            # SQLAlchemy ORM models, effective expressions, evidence/sender/override tables
│   ├── schemas.py            # Pydantic request/response models (API boundary)
│   │
│   ├── domain/               # Pure logic layer -- no FastAPI, no SQLAlchemy imports
│   │   ├── transaction.py    # CanonicalTransaction dataclass (the pipeline's common currency)
│   │   ├── mapping.py        # ImportMapping dataclass + resolve_mapping()
│   │   ├── sources.py        # TransactionSource interface (ABC) + CsvSource impl
│   │   ├── email_source.py   # EmailSource ABC, allowlist enforcement, fixture-backed fake source
│   │   ├── receipts.py       # Receipt domain types, matcher, extractor port, regex fallback
│   │   ├── normalize.py      # raw rows + mapping -> CanonicalTransaction list
│   │   ├── dedupe.py         # canonical rows + existing hashes -> new vs duplicate
│   │   ├── classification.py # TransactionType + NormalizationLookup + classify_*
│   │   ├── db_lookup.py      # DbNormalizationLookup (the one domain file that queries the DB)
│   │   └── merged_lookup.py  # In-memory lookup: db:/create:/update: refs, same precedence
│   │
│   ├── services/              # Orchestration layer -- coordinates domain + persistence
│   │   ├── ingest_service.py
│   │   ├── analytics_service.py
│   │   ├── enrichment_service.py       # email candidate search, receipt match, evidence persistence
│   │   └── mapping_preview_service.py  # preview_mappings (read-only) + apply_mapping_plan
│   │
│   ├── routers/               # API layer -- thin, delegates to services
│   │   ├── accounts.py
│   │   ├── owners.py
│   │   ├── mappings.py
│   │   ├── imports.py
│   │   ├── transactions.py
│   │   └── analytics.py
│   │
│   └── agent/                 # LangGraph CLI agents (no chat UI)
│       ├── cli.py             # python -m app.agent.cli [--steward] [thread_id]
│       ├── studio.py          # LangGraph Studio factories (no compile-time checkpointer)
│       ├── coordinator.py     # outer create_agent; checkpointer required for CLI
│       ├── analyst.py         # read-only analysis subagent
│       ├── steward_graph.py   # propose → preview → interrupt → apply
│       ├── config.py          # model name, tool sessions, checkpointer factory
│       └── tools/
│           ├── read.py        # owners/accounts/unmapped/mappings/txns + analytics
│           ├── steward.py     # preview_mapping_rules, submit_plan (rule ops + txn override ops; still no apply tool)
│           └── subagents.py   # ask_analyst, run_data_steward
│
└── tests/
    ├── conftest.py
    ├── fakes.py              # lookup fake + enrichment fakes
    ├── domain/
    ├── enrichment/           # allowlist, receipts, enrichment service, tracing, CLI
    ├── routers/
    ├── services/
    └── agent/

## Agent notes

The coordinator compiles **with** a checkpointer for the CLI. `checkpointer=None` is valid only when served by the LangGraph API server (see `studio.py`). Analyst and steward compile **without** one so steward `interrupt()` bubbles to the outer graph on the same thread.

`python -m app.agent.cli <thread_id>` is the coordinator. `--steward` compiles the steward graph with the checkpointer for direct use. `--enrich --from YYYY-MM-DD --to YYYY-MM-DD` runs the deterministic email-enrichment pipeline; `--dry-run` only counts candidates, `--seed-senders` seeds domain sender patterns. `make studio` starts LangGraph Studio for visual debugging. Approval UX is `approve` / `reject` / `edit 0,2` over plan ops. Checkpoints persist on Postgres or on the SQLite file at `AGENT_CHECKPOINT_PATH`.
