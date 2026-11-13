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
    # MARKET_DATA_SOURCE_ID is the registry id from fd-open-data-mcp's
    # price_sources declarations; it takes precedence over the legacy
    # MARKET_DATA_PROVIDER below. An unregistered id is a startup error, never a
    # silent fallback (add-price-supply-and-caliber task 3.1).
    market_data_source_id: str = ""
    # 旧选择器 MARKET_DATA_PROVIDER 已在 task 6.4 删除：它的映射层一个版本的生命期
    # 到期，且生产只剩 MARKET_DATA_SOURCE_ID。若仍设旧变量，启动期直接失败（见
    # legacy_env_guard）——弃用窗口不该永久存在，也不该悄悄继续生效。
    market_data_api_key: str = ""

    # ---- astock provider (spec: astock-market-data) ----
    # Read-only DSN over the platform store's federated view
    # (semantic_observations_all). 由注册表声明的 ``access.dsn_env`` 指名，
    # ``selection._dsn_for`` 以 ``getattr(settings, "astock_dsn")`` 动态读取——
    # 所以这个字段名是凭据槽的契约，不能改名。
    astock_dsn: str = ""

    # 2026-11-13 删掉两个"声明了但没人读"的字段（都是 task 6.4 的残件）：
    #
    # * ``per_user_symbol_limit``：全仓零执行点。用户限额早已统一到组配置
    #   （``DEFAULT_GROUP_CONFIG`` 的 ``max_universe_size`` / ``allowed_symbols``），
    #   而 user-groups 规格明写「per-user customization via a one-person group,
    #   SHALL NOT maintain a parallel per-user override layer」——留着它正是那条
    #   规格禁止的"第二层覆盖"。孤儿身份 2026-11-06 就在 astock spike 报告里记过账，
    #   今天连文档里的那一行一起清掉。
    # * ``astock_calendar_refs``：日历参照篮的真源是注册表声明的
    #   ``watermark_probe.reference_basket``（``selection`` 取它，注入 provider）。
    #   本字段无人读，却带着一份**看起来像真值**的默认篮——而 index-seed 声明的篮
    #   是 ``000300,000905,000001,399001``，两者不同：留着它就是一个会分叉的第二真源。

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

    # ---- Resting stop orders ----
    # How often the sweeper re-evaluates resting STOP/STOP_LIMIT orders of
    # accounts that no strategy run is driving. A stop's trigger point is "a new
    # quote arrives" (stop-order change D1), and a manually traded account
    # produces no such arrival on its own — without this, a stop only wakes up
    # when its owner places another order on the same symbol. The underlying
    # price is the daily close, so a 60 s cadence reads the same value the
    # placement path would; it is not an intraday evaluation. 0 disables it.
    stop_sweep_seconds: float = 60.0



@lru_cache
def get_settings() -> Settings:
    return Settings()
