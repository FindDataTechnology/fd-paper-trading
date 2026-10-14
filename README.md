<div align="center">

**[English](./README.md)** | **[中文](./README_zh.md)**

</div>

# fd-paper-trading

Multi-tenant **stock paper-trading** service built on [Nautilus Trader](https://nautilustrader.io), exposed via a FastAPI REST + WebSocket API. Users can own multiple paper trading accounts and place orders through the API against real-time market data with **simulated fills** — no broker, no real capital.

## Features

- **Multi-user auth** — JWT + revocable API tokens (`fdt_` prefix)
- **Multi-account management** — each user can create and manage multiple isolated paper accounts
- **Order execution** — market and limit orders with simulated fills via Nautilus Trader's matching engine
- **Position & cash ledgering** — real-time P&L, average cost, realized gains
- **Real-time streaming** — WebSocket endpoint (`/ws`) pushes live quotes, order-status, and fill events
- **Market data** — `SimulatedMarketDataProvider` (deterministic, for dev/tests), `PolygonMarketDataProvider` (real prices, end-of-day on free tier), and `FdOpenDataMarketDataProvider` (real daily bars + close-derived quotes from the [fd-open-data-mcp](https://github.com/FindDataTechnology/fd-open-data-mcp) ontology)
- **Quant strategies** - dual-track authoring: fixed templates (SMA cross, RSI reversion, Bollinger breakout, MACD cross, DCA) and sandboxed custom code (`on_bar(ctx)`); backtest with equity-curve stats; live paper runs that place attributed signals on your account. Per-user group limits (active strategies, daily orders, custom-code gate, symbol allowlist)
- **Web UI** — React + TypeScript SPA with professional trading dashboard featuring:
  - **Three-column workspace**: watchlist (live quotes), price chart, and order panel
  - **Click-to-trade**: select any symbol from watchlist to instantly update chart and order form
  - **Position management**: quick close/reduce actions with confirmation dialogs
  - **Daily P&L tracking**: real-time calculation from realized + unrealized components
  - **Strategies pages**: create/edit strategies (template param form or code editor), run backtests with equity curves, and monitor live paper runs + history
  - **AI Chat Assistant**: conversational interface for placing orders, checking accounts, and managing trades
  - **Typeahead symbol search**, interactive charts (TradingView `lightweight-charts`), and live streaming

## Tech Stack

- **Backend:** Python 3.12+, FastAPI + Uvicorn, SQLAlchemy 2.0 async, Alembic
- **Database:** PostgreSQL (prod) / SQLite (tests)
- **Auth:** JWT (`python-jose`) + bcrypt + revocable API tokens
- **Execution:** Nautilus Trader (matching engine as stateless fill calculator)
- **Frontend:** React 18 + TypeScript + Vite, TanStack Query + Zustand, TradingView `lightweight-charts`
- **Testing:** pytest (backend), Vitest + React Testing Library (frontend), Playwright (e2e)

## Quick Start

```bash
# 1. Install Python dependencies
uv sync

# 2. Configure environment
cp .env.example .env   # edit DATABASE_URL, JWT_SECRET, market-data keys

# 3. Provision PostgreSQL and apply migrations
brew services start postgresql@14
createdb paper_trading
uv run alembic upgrade head

# 4. (Optional) Build the web UI so FastAPI serves it at /
cd web && npm install && npm run build && cd ..

# 5. Run the API (OpenAPI docs at http://localhost:8000/docs)
uv run fd-paper-trading
```

> **Note:** On macOS with Homebrew, `postgresql@14` may run on port 5433 if 5432 is occupied. Adjust `DATABASE_URL` in `.env` accordingly.

> **Admin bootstrap:** to manage user groups (custom-code access, symbol allowlists, limits), set `FD_ADMIN_EMAILS=you@example.com` in `.env` and register with that email - the account is promoted to admin. Admins manage groups at `POST /api/v1/groups` and assign users at `POST /api/v1/users/{id}/group`.

### fd-open-data market data (optional)

To trade/backtest on real daily bars from the [fd-open-data-mcp](https://github.com/FindDataTechnology/fd-open-data-mcp) ontology instead of simulated quotes:

```bash
# One-time bootstrap of the ontology DB (migrate, catalog import, concepts,
# bindings, entity seeds). Set FD_OPEN_DATA_MCP_DATABASE_URL to override the
# default sqlite location.
uv run python scripts/setup_fd_open_data.py

# Then select the provider
MARKET_DATA_PROVIDER=fd-open-data uv run fd-paper-trading
```

The bootstrap prints a coverage report mapping the curated symbol universe to ontology entity IDs; unresolved symbols return no bars/quotes until the ontology seeds them (e.g. via the library's `migrate-astock-daily` or entity import commands).

**Concept family** — the provider reads one concept, configured as code/entity-type/frequency (default `nav.unit`/`fund`/`daily` — fund NAV, the family with real production data, updated daily). `CRYPTO_*` and `IDX_*` concepts are live in the catalog but currently carry no observations:

```bash
FD_OPEN_DATA_CONCEPT=CRYPTO_PRICE FD_OPEN_DATA_ENTITY_TYPE=symbol MARKET_DATA_PROVIDER=fd-open-data uv run fd-paper-trading
```

A deprecated concept degrades to a data gap (no bars, no quotes), never an error. The stock `price.*` family is deprecated by curation policy and must not be re-registered.

### Development

```bash
# Terminal 1: FastAPI backend on :8000
uv run fd-paper-trading

# Terminal 2: Vite dev server on :5173 (proxies /api and /ws to backend)
cd web && npm install && npm run dev
```

### Production

```bash
# Build the SPA and serve everything from FastAPI on a single origin
cd web && npm run build
uv run fd-paper-trading   # SPA + API + /ws all at http://localhost:8000/
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Web UI (React + Vite + TypeScript)                     │
│  - TanStack Query for data fetching                     │
│  - Zustand for state management                         │
│  - TradingView lightweight-charts for charting          │
│  - WebSocket client for real-time streaming             │
└────────────────────┬────────────────────────────────────┘
                     │ REST (/api/v1) + WebSocket (/ws)
┌────────────────────▼────────────────────────────────────┐
│  FastAPI Backend                                        │
│  - Auth (JWT + API tokens)                              │
│  - Account management                                   │
│  - Order placement & cancellation                       │
│  - Position & cash ledgering                            │
│  - Market data API                                      │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│PostgreSQL│  │  Nautilus │  │  Market  │
│(ledger)  │  │  (fills)  │  │  Data    │
└──────────┘  └──────────┘  └──────────┘
```

**Execution model (v1):** Fills are computed by Nautilus Trader's matching engine via a stateless `BacktestEngine`-based fill calculator — one engine per order, `bypass_logging=True`. Account state lives in the database, isolated by `account_id`. The live `TradingNode` + `SandboxLiveExecClientFactory` are deferred.

## Trading Dashboard

The web UI provides a professional trading workspace optimized for active trading:

```
┌───────────────────────────────────────────────────────────────┐
│ Watchlist (left) │ Price Chart  │ Order Ticket (right) │
├──────────────────┼──────────────┼─────────────────────────────┤
│ AAPL    $175.50▲│              │ Side: [Buy ▼] Sell          │
│ TSLA    $250.20▼│ │ Type: [Market ▼] Limit       │
│ NVDA    $890.10▲│   [graph]    │ Qty: [100]       Limit:$175  │
│ SPY     $450.00▶│              │                             │
│ QQQ     $400.25▶│              │ [BUY AAPL] [SELL AAPL] │
│              Total PnL: (+$127)│                             │
├──────────────────┴──────────────┴─────────────────────────────┤
│ Positions Table (quick close/reduce buttons on each row)      │
└───────────────────────────────────────────────────────────────┘
```

**Key features:**
- **Watchlist**: Live quotes for tracked symbols (AAPL, TSLA, NVDA, SPY, QQQ), updated via WebSocket
- **Click-to-trade**: Click any symbol in the watchlist to instantly update the chart and order panel
- **Order panel**: Always-visible order entry form bound to the selected symbol
- **Position actions**: Quick close/reduce buttons with confirmation dialogs
- **Daily P&L**: Real-time calculation from realized gains + unrealized mark-to-market

**Note:** Daily P&L is indicative (frontend-calculated) and may differ slightly from backend calculations. Mobile-responsive layout and user-configurable watchlists are planned for future releases.

## AI Chat Assistant

The web UI includes an AI-powered chat assistant that allows you to interact with the trading system using natural language. Powered by OpenAI and CopilotKit, the assistant can:

- **Place orders**: "Buy 100 shares of AAPL at market price"
- **Check account balance**: "What's my cash balance?"
- **View positions**: "Show my open positions"
- **Cancel orders**: "Cancel my TSLA order"
- **Get market insights**: "What's the current price of NVDA?"

### Using the Chat Assistant

1. Click the 💬 **Chat** button in the top navigation bar
2. Type your request in the chat input
3. The AI will process your request and execute the appropriate trading operation
4. You'll see a confirmation with the results

### Configuration

The chat assistant requires an OpenAI API key. Set it in your `.env` file:

```bash
OPENAI_API_KEY=your_openai_api_key_here
```

You can also configure the model (default is `gpt-3.5-turbo`):

```bash
OPENAI_MODEL=gpt-4  # or gpt-3.5-turbo
```

**Note:** The chat assistant uses your active account context automatically. All operations are authenticated and authorized - you can only trade your own accounts.

## Commands

```bash
# Backend
uv run pytest                    # all backend tests (in-memory SQLite)
uv run pytest tests/unit/        # unit tests only
uv run pytest tests/e2e/         # end-to-end flows only
uv run alembic upgrade head      # apply database migrations
uv run alembic downgrade -1      # rollback last migration

# Frontend
cd web && npm test               # Vitest component tests
cd web && npx playwright test    # Playwright e2e tests
cd web && npm run build          # production build
cd web && npm run dev            # dev server with hot reload
```

## API Overview

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/auth/register` | Register a user |
| POST | `/api/v1/auth/login` | Obtain a JWT access token |
| POST | `/api/v1/tokens` | Create a revocable API token |
| GET | `/api/v1/tokens` | List API tokens |
| DELETE | `/api/v1/tokens/{id}` | Revoke an API token |
| GET | `/api/v1/accounts` | List your accounts |
| POST | `/api/v1/accounts` | Create a paper account |
| GET | `/api/v1/accounts/{id}` | Account summary (balance, equity, counts) |
| POST | `/api/v1/accounts/{id}/deposit` | Deposit / withdraw (signed amount) |
| POST | `/api/v1/accounts/{id}/reset` | Reset account (cancel orders, flatten, restore balance) |
| GET | `/api/v1/accounts/{id}/positions` | List open positions |
| GET | `/api/v1/accounts/{id}/orders` | List orders (optional `?status=`) |
| POST | `/api/v1/orders` | Place a market/limit order (simulated fill, returns 202) |
| DELETE | `/api/v1/orders/{id}` | Cancel an open order |
| GET | `/api/v1/orders/{id}` | Query a single order |
| GET | `/api/v1/instruments/search?q=` | Search instruments by symbol/name |
| GET | `/api/v1/instruments/{symbol}/bars` | Historical OHLCV bars (`?timeframe=&limit=`) |
| WS | `/ws` | Stream quotes + order/fill events (auth in first message) |

**Authentication:** `Authorization: Bearer <jwt>` or `Authorization: Bearer <api-token>` (tokens start with `fdt_`).

**WebSocket:** Send `{"type":"auth","token":"<jwt>"}` as the first message, then `{"type":"subscribe","symbol":"AAPL"}` / `{"type":"unsubscribe","symbol":"AAPL"}`.

**Errors:** Standardized format: `{"error": {"code": "...", "message": "...", "fields": [...]}}`.

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes and add tests
4. Run the test suite: `uv run pytest && cd web && npm test`
5. Commit with a descriptive message: `git commit -m "Add your feature"`
6. Push to your fork: `git push origin feature/your-feature`
7. Open a Pull Request

Please ensure your code follows the existing style and all tests pass before submitting.

## License

This project is proprietary software. All rights reserved.
