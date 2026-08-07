"""Application configuration loaded from environment variables."""

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Database ----
    # Postgres in production; sqlite+aiosqlite for tests/dev.
    database_url: str = "postgresql+asyncpg://localhost:5432/fdtrade"

    # ---- Auth ----
    jwt_secret: str = "dev-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    jwt_issuer: str = "fd-trade"

    # ---- Accounts ----
    default_starting_balance: Decimal = Decimal("100000.0000")
    account_currency: str = "USD"

    # ---- Market data / execution ----
    market_data_provider: str = "simulated"
    market_data_api_key: str = ""
    per_user_symbol_limit: int = 20



@lru_cache
def get_settings() -> Settings:
    return Settings()
