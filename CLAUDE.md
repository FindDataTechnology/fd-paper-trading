# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Bootstrapped and functional. The repository is a **multi-tenant stock paper-trading service**: a FastAPI REST + WebSocket backend (`src/fd_trade/`) on SQLAlchemy 2.0 async (Postgres in prod, aiosqlite in tests) plus a React + TypeScript + Vite SPA (`web/`). The full flow - register/login (JWT + revocable API tokens), multi-account management, market/limit orders with simulated fills, positions + cash ledgering, `/ws` streaming, and the web UI - is implemented and tested.

**v1 execution model:** fills are computed by Nautilus Trader's matching engine via a *stateless* `BacktestEngine`-based fill calculator (`execution/fill_engine.py`) - one engine per order, `bypass_logging=True`. Account state lives in the database, isolated by `account_id` (no per-account Nautilus venues). The live `TradingNode` + `SandboxLiveExecClientFactory` + async command-queue bridge remain **deferred** (see archived changes' `design.md`, decision D10).

Tests live in `tests/unit/` (pure logic), `tests/e2e/` (full ASGI flows), and `tests/` (feature tests); the frontend has Vitest component tests and a Playwright e2e (`web/e2e/`). See `openspec/specs/` for capability specs and `openspec/changes/` for history.

## Goal

A trading system with two cooperating subsystems, both built on **Nautilus Trader** (`nautilus_trader` on PyPI):

1. **AI quant system** — model training/inference and signal generation (feature pipelines, alpha research, forecasts). Produces trading signals.
2. **Paper trading** — Nautilus `Strategy`(s) that consume those signals against a sandbox/testnet via a live `TradingNode`, with no real capital at risk.

Intended data flow: **AI signals → Nautilus Strategy → TradingNode (paper/sandbox)**.

## Technology baseline

- Python 3.12+, managed with **uv** (Nautilus itself uses uv). `nautilus_trader` is a dependency in `pyproject.toml`.
- Tests: **pytest**.
- The prebuilt `nautilus_trader` wheel covers macOS — only build from source (Rust/Cython) if a needed adapter or version lacks a wheel.

Once bootstrapped, standard commands:
```bash
uv sync                                              # install deps into .venv
uv run pytest                                        # all tests
uv run pytest tests/test_foo.py::test_bar            # single test
uv run python <entrypoint>                           # run a backtest / paper node (scripts TBD)
```

## Architecture: Nautilus Trader mental model

Nautilus is an **event-driven** engine. These pieces (spread across `live/`, `backtest/`, `trading/`, `adapters/`, `model/`) must be understood before writing strategies:

- **Strategy** (`nautilus_trader.trading.strategy.Strategy`): the unit of trading logic. Subclass it, accept a `StrategyConfig` in `__init__` (call `super().__init__(config)`), and override lifecycle handlers: `on_start`, `on_bar`, `on_event`, `on_stop`, `on_reset`. State lives on `self`; config is read via `self.config`.
- **StrategyConfig** (`nautilus_trader.config.StrategyConfig`): typed (Pydantic-style) config. Parameterize instrument, bar type, sizes, periods here — never hardcode in the strategy body.
- **BacktestEngine / BacktestNode**: historical replay. Add venues, data, instruments, then `add_strategy(...)` and `run()`.
- **TradingNode** (`nautilus_trader.live.node.TradingNode`): the live/paper runtime. Register per-venue data + execution client factories (`add_data_client_factory`, `add_exec_client_factory`), then `build()` + `run()`. **Paper trading is a TradingNode pointed at a sandbox/testnet** (e.g. Binance testnet, AX sandbox) — not a separate engine.
- **Adapters** (`nautilus_trader.adapters.*`): per-venue data + execution clients (Binance, BitMEX, …). Each brings `*DataClientFactory` / `*ExecClientFactory` and its own config.
- **Model identifiers**: `InstrumentId` like `"ETHUSDT-PERP.BINANCE"`; `BarType` like `"ETHUSDT-PERP.BINANCE-15-MINUTE[LAST]-EXTERNAL"`. Parse with `InstrumentId.from_str` / `BarType.from_str`.
- **Cache**: instruments must be loaded into the cache before subscribing; fetch via `self.cache.instrument(id)`. If it returns `None`, the instrument hasn't loaded — `stop()` rather than trade blind.
- **Indicators**: register with `self.register_indicator_for_bars(bar_type, indicator)` so the engine updates them; read values in `on_bar`.

## Conventions to follow

- One `Strategy` + one `StrategyConfig` pair per trading idea. Keep AI/ML **out of the strategy hot path** — strategies consume precomputed signals; inference/feature work happens upstream.
- Match Nautilus's own patterns (config object, lifecycle handlers, `self.config` access) rather than inventing parallel abstractions.
- Secrets/API keys in environment variables (e.g. `python-dotenv`), never in config files or commits.
- Prefer the prebuilt wheel; reach for Rust/source builds only when an adapter requires it.

## MCP Server: fd-trade-mcp

Claude can place/cancel orders via MCP tools (FastMCP-based). Configure in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "fd-trade": {
      "command": "uv",
      "args": ["run", "src.fd_trade.mcp.server"],
      "env": {
        "TRADE_API_URL": "http://localhost:8000",
        "TRADE_API_TOKEN": "<your-jwt-token>"
      }
    }
  }
}
```

**Tools available:**
- `list_accounts()` → discover account IDs
- `get_account(account_id)` → check cash balance
- `place_order(account_id, symbol, side, order_type, quantity, limit_price?)` → submit orders
- `cancel_order(order_id)` → cancel pending orders
- `list_orders(account_id, status?)` → query order history

Get your JWT token via web UI login or `POST /api/v1/auth/token`.

## Web Chat Assistant (CopilotKit)

The web UI includes a CopilotKit-powered chat assistant that can manage orders via the MCP server.

### Architecture

```
Browser (CopilotKit UI) → Vite proxy (/copilotkit) → Node.js runtime (port 4100)
    ↓
MCP server (port 8100, SSE transport) → FastAPI backend (port 8000)
```

### Running the chat

```bash
# Terminal 1: FastAPI backend
uv run uvicorn fd_trade.api.app:create_app --factory --reload

# Terminal 2: MCP server (HTTP/SSE mode for the runtime)
TRADE_API_TOKEN=<jwt> uv run python -m fd_trade.mcp.http_server

# Terminal 3: CopilotKit runtime
cd web/copilot-runtime && npm install && node server.js

# Terminal 4: Frontend
cd web && npm run dev
```

### Configuration

The runtime loads the repo-root `.env` file (the same one the Python backend
reads) via `dotenv`, so LLM config lives in `.env` alongside the backend
settings. The relevant vars (defaults in `web/copilot-runtime/server.js`):
- `OPENAI_API_KEY` — API key for the LLM provider (required)
- `OPENAI_API_BASE` — Base URL (must end with `/v1` for OpenAI-compatible APIs)
- `COPILOT_MODEL` — Model name (e.g., `glm-5.2`, `doubao-seed-2-0-pro`)
- `MCP_SERVER_URL` — MCP server URL (default: `http://127.0.0.1:8100/sse`)

See the `# ---- AI Chat Assistant (CopilotKit + MCP) ----` section in `.env.example`.

### Available MCP tools

The chat assistant has access to the same tools as the Claude Code MCP server:
- `list_accounts()` — discover account IDs
- `get_account(account_id)` — check cash balance
- `place_order(...)` — submit orders
- `cancel_order(order_id)` — cancel pending orders
- `list_orders(account_id, status?)` — query order history

---

## References

- Docs: https://nautilustrader.io · Source: https://github.com/nautechsystems/nautilus_trader
- For current Nautilus API details, use the Context7 MCP (`/nautechsystems/nautilus_trader`) — the Cython/Rust surface changes between versions.
