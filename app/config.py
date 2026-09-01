"""
Environment-driven settings (12-factor style) -- required for Docker/Postgres
since the DB host/credentials differ between local dev and the container.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str  # e.g. postgresql+psycopg://user:pass@db:5432/finance

    class Config:
        env_file = ".env"


settings = Settings()  # type: ignore[call-arg]
