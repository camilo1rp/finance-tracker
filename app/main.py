from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.config import open_checkpointer
from app.agent.coordinator import build_coordinator
from app.database import init_db
from app.routers import accounts, agent, analytics, artifacts, imports, mappings, owners, transactions


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with open_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer
        app.state.coordinator_graph = build_coordinator(checkpointer=checkpointer)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Family Finance Tracker", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(accounts.router)
    app.include_router(owners.router)
    app.include_router(mappings.router)
    app.include_router(imports.router)
    app.include_router(transactions.router)
    app.include_router(analytics.router)
    app.include_router(artifacts.router)
    app.include_router(agent.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
