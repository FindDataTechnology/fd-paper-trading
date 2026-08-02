## Why

The fd-trade project is greenfield - there is no trading infrastructure yet. We need a paper trading subsystem so users can place stock orders through an API with **no real capital at risk**, against **real-time market data**, with **simulated fills**. A multi-user, multi-account service lets us both validate the AI-signal-to-execution pipeline and offer paper trading to end users before any live capital is involved. Building it now establishes the execution half of fd-trade on Nautilus Trader and gives the AI quant subsystem a concrete downstream consumer for its signals.

## What Changes

- Introduce a multi-tenant paper trading service built on **Nautilus Trader**, exposed via a **FastAPI REST + WebSocket API**.
- Add **multi-user authentication** (JWT access tokens + API tokens) so each user is isolated and can call the API programmatically.
- Add **multi-account support**: each user owns one or more paper trading accounts, each with its own cash balance, positions, and order history, fully isolated from other accounts.
- Add a **real-time stock market data feed** (e.g. Polygon / Alpaca / Databento) supplying live prices.
- Add a **simulated execution/matching engine** that fills paper orders (market and limit) against live market data - **no broker account and no real capital required**.
- Persist users, accounts, orders, positions, and fills in **PostgreSQL**.
- Bootstrap the repo (`uv init`, `pyproject.toml`, project layout) per CLAUDE.md.

**BREAKING**: This is the first implementation in a greenfield repo, so there is no existing API to break. The initial public API surface (REST + WebSocket) is established here.

## Capabilities

### New Capabilities
- `user-auth`: Multi-user registration, authentication (JWT access tokens + API tokens), and per-request authorization/identity.
- `paper-accounts`: Multi-account paper trading accounts owned by users - creation, cash balance management, reset, and strict per-account isolation of positions and orders.
- `market-data`: Real-time stock market data ingestion from an external provider feed and distribution to the execution engine and API clients (quotes/trades/bars).
- `order-execution`: Simulated order matching and fill engine (market and limit orders) that fills paper orders against live market data via Nautilus Trader, with no real capital.
- `trading-api`: REST + WebSocket API exposing order placement/cancellation, order and position queries, account management, and live market data streaming.

### Modified Capabilities
<!-- None - this is the first change in a greenfield repo. No existing specs in openspec/specs/. -->

## Impact

- **New code**: FastAPI API layer; auth middleware; user/account/position/order domain models and persistence; market data client; simulated execution engine; Nautilus `TradingNode` integration; WebSocket streaming.
- **New dependencies**: `nautilus_trader`, `fastapi`, `uvicorn[standard]`, an async SQL layer (`sqlalchemy` + `asyncpg` or `sqlmodel`), JWT/password libs (`python-jose`/`passlib`), a market data provider SDK, `python-dotenv`, `pytest`.
- **Infrastructure**: A PostgreSQL database; credentials for a market data feed provider; secrets kept in environment variables (never committed).
- **Bootstrap**: `uv init` + `pyproject.toml` (Python 3.12+, managed with uv); source layout and `tests/`.
- **Scope**: Establishes the paper-trading half of fd-trade. The AI quant subsystem remains separate and will feed signals into these paper accounts in a later change; this change focuses on API-driven (manual/programmatic) order placement, multi-user, and multi-account support.
