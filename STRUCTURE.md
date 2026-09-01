# Project structure

finance-tracker/
├── docker-compose.yml       # postgres + api services
├── Dockerfile
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py              # FastAPI app instantiation, router registration
│   ├── config.py            # Settings (env-driven: DB url, etc.)
│   ├── database.py          # SQLAlchemy engine/session (Postgres)
│   ├── models.py            # SQLAlchemy ORM models (Owner, Account, Transaction, ImportBatch, NormalizationMapping)
│   ├── schemas.py            # Pydantic request/response models (API boundary)
│   │
│   ├── domain/               # Pure logic layer -- no FastAPI, no SQLAlchemy imports
│   │   ├── transaction.py    # CanonicalTransaction dataclass (the pipeline's common currency)
│   │   ├── mapping.py        # ImportMapping dataclass + resolve_mapping()
│   │   ├── sources.py        # TransactionSource interface (ABC) + CsvSource impl
│   │   ├── normalize.py      # raw rows + mapping -> CanonicalTransaction list
│   │   ├── dedupe.py         # canonical rows + existing hashes -> new vs duplicate
│   │   ├── classification.py # TransactionType + NormalizationLookup + classify_*
│   │   └── db_lookup.py      # DbNormalizationLookup (the one domain file that queries the DB)
│   │
│   ├── services/              # Orchestration layer -- coordinates domain + persistence
│   │   ├── ingest_service.py
│   │   └── analytics_service.py  # stub; not implemented yet
│   │
│   └── routers/               # API layer -- thin, delegates to services
│       ├── accounts.py
│       ├── owners.py
│       ├── mappings.py
│       ├── imports.py
│       ├── transactions.py
│       └── analytics.py       # stub; not implemented yet
│
└── tests/
    ├── conftest.py
    ├── fakes.py
    ├── domain/
    └── routers/
