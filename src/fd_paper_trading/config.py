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
    database_url: str = "postgresql+asyncpg://localhost:5432/paper_trading"
    # Connection pool sizing (asyncpg). Defaults are small; raise for higher
    # concurrent load (e.g. 1000 users/bots).
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---- Order execution / rate limiting ----
    # Per-account order rate limit (token bucket). 0 disables the limiter.
    order_rate_per_second: float = 0
    order_rate_burst: float = 20

    # ---- API tokens ----
    # Only rewrite last_used_at every N minutes per token (it's a write per
    # request otherwise).
    api_token_touch_minutes: int = 15

    # ---- Auth ----
    jwt_secret: str = "dev-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    jwt_issuer: str = "fd-paper-trading"

    # ---- Accounts ----
    default_starting_balance: Decimal = Decimal("100000.0000")
    account_currency: str = "USD"

    # ---- Market data / execution ----
    market_data_provider: str = "simulated"
    market_data_api_key: str = ""
    per_user_symbol_limit: int = 20

    # ---- fd-open-data concept family ----
    # The concept the fd-open-data provider reads, as (code, entity_type,
    # frequency). Defaults to fund NAV, the family with real production data
    # (17k+ rows, updated daily). CRYPTO_*/IDX_* concepts are live in the
    # catalog but carry no observations (price crawling stopped 2026-10-07).
    # The stock price.* family stays deprecated by policy and must not be
    # re-registered.
    fd_open_data_concept: str = "nav.unit"
    fd_open_data_entity_type: str = "fund"
    fd_open_data_frequency: str = "daily"

    # ---- Live strategy runner (paper) ----
    # Poll interval for the supervised per-run task (design D5: default 30 min).
    # Tests override via ``app.state.strategy_runner.poll_seconds``.
    strategy_poll_seconds: float = 1800.0
    # Bars of history handed to ``on_bar(ctx)`` each live tick (enough for any
    # v1 indicator window; MACD needs ~35, SMA-200 needs 200).
    strategy_lookback_bars: int = 250

    # ---- User groups / admin bootstrap ----
    # Comma-separated emails promoted to admin on registration (design D7).
    fd_admin_emails: str = ""



@lru_cache
def get_settings() -> Settings:
    return Settings()
