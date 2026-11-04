"""Runtime settings, read from environment variables or the repo-root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://cloudopt:cloudopt@localhost:5433/cloudopt"
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:5173"

    replay_enabled: bool = True
    replay_seconds_per_slot: float = 5.0
    replay_start_day: int = 11  # first replayed day; days 1-10 are model training data
    demo_tenants: int = 8

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_max_calls_per_hour: int = 120  # spend cap; beyond it explanations use the template

    seed_admin_email: str = "admin@cloudopt.dev"
    seed_admin_password: str = "change-me"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
