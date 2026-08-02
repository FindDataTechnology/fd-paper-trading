## Why

fd-trade today exposes its paper-trading functionality only through a REST API (`/api/v1/*`). There is no browser UI, so the only way to place orders, view positions, or watch prices is to call the API programmatically. A web UI makes the system usable by humans for manual paper trading, demoing the AI-signal-to-execution pipeline, and inspecting account/order state at a glance - directly serving the "orders list, order logic, stock search, stock charts" workflow the project needs.

## What Changes

- Add a **single-page web application** (React 18 + TypeScript + Vite) in a new `web/` package that consumes the existing `/api/v1` REST API and a new WebSocket streaming surface.
- Add an **order management UI**: an order ticket (market/limit, buy/sell, quantity, limit price, client-side validation against the live quote and account buying power), an orders list with status/filter, order detail, cancel, and per-order fills.
- Add a **stock search** module: a symbol/instrument search endpoint + typeahead search UI with live quote preview.
- Add a **stock charts** module: a historical OHLCV bars endpoint + interactive charts (TradingView `lightweight-charts`) with candlestick/line views, timeframe selection, and a live quote overlay.
- Add an **accounts dashboard** module: account summary (cash, equity, open positions/orders), positions table with unrealized P&L, account list and switcher, deposit/reset actions.
- Add a **real-time streaming** module: a backend WebSocket endpoint (`/ws`) plus a frontend client pushing live quotes, order-status changes, and fill events to the UI.
- Add the **app shell**: routing, navigation, session/auth integration (login + register against `/api/v1/auth`, token storage, protected route guards), and FastAPI `StaticFiles` serving of the built SPA in production (Vite dev server proxied to the API in development).
- **Backend additions** (small, in service of the UI): symbol search endpoint, historical bars endpoint, and a WebSocket streaming endpoint. These extend the existing FastAPI app and `MarketDataProvider` interface; no changes to existing order/account/auth behavior.

**BREAKING**: None. The existing `/api/v1` REST contract is unchanged; all backend additions are new endpoints. The SPA is a new surface mounted at `/` and served alongside the existing API.

## Capabilities

### New Capabilities
- `web-shell`: SPA application shell - routing, layout, navigation, FastAPI static serving, and auth/session integration (login/register UI, token storage, protected route guards) wired to the existing `/api/v1/auth` endpoints.
- `web-order-entry`: Order ticket (market/limit, buy/sell, validation against live quote and buying power), orders list with status filter, order detail, cancel, and fills display - consuming `/api/v1/orders` and `/api/v1/accounts/{id}/orders`.
- `web-market-search`: Symbol/instrument search endpoint (`/api/v1/instruments/search`) plus a typeahead search UI with live quote preview from the market-data provider.
- `web-charts`: Historical OHLCV bars endpoint (`/api/v1/instruments/{symbol}/bars`) plus interactive charts (TradingView `lightweight-charts`) with candlestick/line views, timeframe selection, and a live quote overlay.
- `web-accounts`: Account dashboard - summary (cash, equity, open positions/orders), positions table with unrealized P&L, account list/switcher, and deposit/reset actions - consuming `/api/v1/accounts*`.
- `web-realtime`: Backend WebSocket endpoint (`/ws`) and frontend client streaming live quotes, order-status changes, and fill events to subscribed UI components.

### Modified Capabilities
<!-- None. openspec/specs/ is empty (the paper-trading-api change has not been consolidated into specs/), so there are no existing specs to delta. The new backend endpoints extend the existing FastAPI app and MarketDataProvider interface but introduce no requirement changes to the already-implemented REST contract. -->

## Impact

- **New code**: a `web/` package (React + Vite + TypeScript SPA) with app shell, auth, order entry, search, charts, accounts, and a WebSocket client; backend additions under `src/fd_trade/api/routes/` (instruments search, bars) and a WebSocket router, plus `MarketDataProvider` extensions for instrument search and historical bars.
- **New dependencies (backend)**: none expected beyond what `pyproject.toml` already declares (FastAPI/Starlette already provide WebSocket support); the bars endpoint may need a small bar-aggregation helper.
- **New dependencies (frontend)**: `react`, `react-dom`, `react-router-dom`, `typescript`, `vite`, `@tanstack/react-query`, `zustand`, `lightweight-charts`, and an HTTP client (`axios` or `fetch`). Managed by a separate `web/package.json` (npm/pnpm), not `uv`.
- **API surface**: adds `GET /api/v1/instruments/search`, `GET /api/v1/instruments/{symbol}/bars`, and `WS /ws`; existing endpoints unchanged.
- **Serving**: production serves the built SPA from FastAPI `StaticFiles` (SPA fallback to `index.html`); development runs the Vite dev server with API/WS requests proxied to the FastAPI backend.
- **Scope**: frontend + minimal backend endpoints needed for the UI. The AI quant subsystem, Nautilus `TradingNode` live wiring, and any real-broker work remain out of scope. The simulated `MarketDataProvider` is the data source for v1; a real feed arrives in a later change.
