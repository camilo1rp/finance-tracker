from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers import accounts, analytics, artifacts, imports, mappings, owners, transactions


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Family Finance Tracker", lifespan=lifespan)
    app.include_router(accounts.router)
    app.include_router(owners.router)
    app.include_router(mappings.router)
    app.include_router(imports.router)
    app.include_router(transactions.router)
    app.include_router(analytics.router)
    app.include_router(artifacts.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
