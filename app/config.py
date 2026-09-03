"""
Environment-driven settings (12-factor style) -- required for Docker/Postgres
since the DB host/credentials differ between local dev and the container.
"""
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str  # e.g. postgresql+psycopg://user:pass@db:5432/finance


settings = Settings()  # type: ignore[call-arg]
