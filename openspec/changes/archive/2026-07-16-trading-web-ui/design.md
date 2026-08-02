## Context

fd-trade already ships a paper-trading **REST API** under `/api/v1` (auth, accounts, orders) backed by SQLAlchemy/Postgres, a `MarketDataProvider` interface with a `SimulatedMarketDataProvider` (`get_quote`, `get_instrument`), and a `PaperExecutionService`. There is no browser surface: placing orders or viewing positions requires calling the API programmatically. This change adds a **web UI** for human-driven paper trading and the minimal backend endpoints the UI needs (symbol search, historical bars, a WebSocket stream).

User-confirmed decisions for this change:
- **Frontend**: React 18 + TypeScript + Vite SPA, located in a new `web/` package.
- **Charts**: TradingView `lightweight-charts`.
- **State**: TanStack Query (server state) + Zustand (client state).
- **Real-time**: native WebSocket to a new `/ws` endpoint, first-class in this change.
- **Serving**: FastAPI `StaticFiles` in production; Vite dev server (proxied) in development.

Constraints from the existing codebase:
- Auth is JWT access tokens (and API tokens) via `Authorization: Bearer`; the SPA must use the same contract - no new auth scheme.
- `MarketDataProvider` currently exposes only `get_quote`/`get_instrument`; search and bars require interface extensions implemented first by the simulated provider.
- The existing REST contract is frozen (no breaking changes); all backend additions are new endpoints.

## Goals / Non-Goals

**Goals:**
- A browser SPA for manual paper trading: login/register, account dashboard, order entry, orders list, stock search, charts, and live streaming.
- Order entry that validates against the live quote and account buying power before submission, with market and limit support.
- Interactive financial charts (candlestick + line) with timeframe selection and a live quote overlay.
- Real-time updates over WebSocket for quotes, order-status changes, and fills.
- Production served from FastAPI (single origin, no CORS); dev via Vite proxy.
- Backend additions (search, bars, WS) extend the existing provider interface and FastAPI app without touching existing behavior.

**Non-Goals:**
- Real-money / live-broker trading (paper only).
- The AI quant subsystem and signal-driven strategies (separate; this UI is manual/programmatic order entry against the existing API).
- Advanced order types beyond market and limit (stop, trailing, OCO deferred).
- Native mobile apps; responsive web only.
- A real market-data feed in this change - v1 uses the `SimulatedMarketDataProvider`; a real feed arrives later behind the same interface.
- Server-side mark-to-market of positions (v1 computes unrealized P&L client-side from the live quote).
- Backtesting or strategy-authoring UI.

## Decisions

### D1. Frontend: React 18 + TypeScript + Vite
A client-side SPA consuming the REST/WebSocket API. **Why over alternatives**: richest financial-charting ecosystem (see D2), mature real-time/WS patterns, and the largest pool of contributors for a trading dashboard. Alternative considered: Vue 3 + Vite (rejected - comparable effort, markedly smaller financial-charting ecosystem). Alternative considered: server-rendered Jinja2 + HTMX (rejected - interactive candlestick charts and bidirectional streaming are awkward without a full client runtime; the user explicitly wants charts and real-time).

### D2. Charts: TradingView `lightweight-charts`
MIT-licensed, ~45KB, purpose-built for financial candlestick/line charts with streaming updates. **Why over alternatives**: performant OHLC rendering and a first-class real-time update API (`series.update`) that fits the WebSocket feed. Alternative considered: ECharts/Chart.js (rejected - general-purpose, weaker OHLC ergonomics and streaming). Alternative considered: Recharts (rejected - not candlestick-first, slower for high-frequency updates). Alternative considered: the commercial TradingView widget (rejected - embedding/branding/limits and less control).

### D3. State management: TanStack Query + Zustand
TanStack Query owns **server state** (accounts, orders, positions, search results, bars) with caching, background refetch, and post-mutation invalidation (e.g. invalidate the orders query after placing/cancelling). Zustand owns **client UI state** (selected account id, active symbol, auth token, WS connection status). **Why over alternatives**: separates the two concerns cleanly with minimal boilerplate. Alternative considered: Redux Toolkit (rejected - heavier, more ceremony for this scope). Alternative considered: Zustand for everything (rejected - reinvents server-cache/invalidation logic). Alternative considered: SWR (viable; TanStack Query chosen for its richer mutation/invalidation API).

### D4. Real-time: native WebSocket to `/ws`, auto-reconnecting client
A new FastAPI `WebSocket` endpoint at `/ws` broadcasts quote, order-status, and fill events; the SPA runs a single connection in a Zustand-backed hook with exponential-backoff reconnect and re-subscription. **Why over alternatives**: FastAPI supports WebSockets natively (no extra dependency), and bidirectional WS lets the client send subscribe/unsubscribe messages. Alternative considered: Server-Sent Events (rejected - unidirectional; we want client-controlled subscriptions). Alternative considered: Socket.IO (rejected - adds a protocol/dependency; native WS is sufficient). v1 sources quote ticks from the simulated provider on a short interval, which proves the streaming contract end-to-end before a real feed lands.

### D5. Auth/session: JWT in memory + sessionStorage, Bearer on REST and WS
The SPA obtains a JWT via `POST /api/v1/auth/login`, keeps it in a Zustand store backed by `sessionStorage`, and attaches `Authorization: Bearer <token>` to REST calls. For the WebSocket, the client sends the token in the **first message** (not the URL) to authenticate. Protected routes redirect to `/login` when no token is present or a 401 is received. **Why over alternatives**: reuses the existing JWT/API-token contract with no backend auth changes. Alternative considered: httpOnly cookies (rejected - the API is Bearer-token based and multi-client; cookies add CSRF/CORS complexity and diverge from the existing contract). API tokens remain supported for programmatic/power users.

### D6. Build & serving: separate `web/` package; FastAPI StaticFiles in prod, Vite proxy in dev
The SPA lives in `web/` with its own `package.json` (npm/pnpm) and builds to `web/dist`. In production, FastAPI mounts `StaticFiles` at `/` with an SPA fallback to `index.html` (so client-side routes deep-link correctly); `/api/*` and `/ws` continue to the API. In development, the Vite dev server (default :5173) proxies `/api` and `/ws` to the FastAPI backend (:8000), avoiding CORS entirely. **Why over alternatives**: clean separation of Python (uv) and JS (npm) toolchains; single-origin in production (no CORS). Alternative considered: monorepo single build (rejected - entangles uv and npm). Alternative considered: serve from a separate static host (rejected - loses single-origin simplicity and adds deployment surface).

### D7. Backend endpoints extend `MarketDataProvider` and the FastAPI app
Three additions, all new routes:
- `GET /api/v1/instruments/search?q=<query>` -> `{ symbol, name, venue }[]`; provider gains `search_instruments(q)`.
- `GET /api/v1/instruments/{symbol}/bars?timeframe=<tf>&limit=<n>` -> OHLCV bars; provider gains `get_bars(symbol, timeframe, limit)`.
- `WS /ws` -> authenticated stream of quote/order/fill events scoped to the caller's accounts and subscriptions.

`SimulatedMarketDataProvider` implements `search_instruments` (a curated static symbol list filtered by `q`) and `get_bars` (a deterministic, symbol-seeded bar generator so charts render reproducibly). **Why over alternatives**: keeps the provider interface as the single seam for market data, so a real feed swaps in later by implementing the same methods. Alternative considered: a separate read-model service (rejected - overkill for v1; one provider interface is enough).

### D8. P&L and equity computed client-side for v1
Positions come from the existing `GET /api/v1/accounts/{id}/positions` (avg price, quantity, realized P&L). The SPA computes **unrealized P&L** from the live quote (WS or a quote fetch) and **equity = cash + Σ(position market value)**. **Why over alternatives**: the server `PositionOut` has no live market value, and wiring the streaming/quote layer into `AccountService` for server-side mark-to-market is a larger change; computing on the client delivers the dashboard now and is clearly labeled as indicative. Alternative considered: server-side mark-to-market (deferred to a later change that integrates the feed into `AccountService`).

## Risks / Trade-offs

- **[JWT in sessionStorage is XSS-exposable]** -> short token expiry; document the trade-off; offer API tokens for programmatic use. Accepted for SPA simplicity in v1; refresh-token rotation deferred.
- **[WS auth via URL query string would be logged by proxies]** -> pass the token in the first WebSocket message instead of the URL; close the socket if the first message is not a valid auth.
- **[Simulated bars/quotes look unrealistic]** -> deterministic generator clearly labeled "simulated" in the UI; identical interface for a real feed later.
- **[Two toolchains (uv + npm)]** -> documented in README; CI runs both `uv run pytest` and `npm run build && test`. `web/dist` gitignored, built in CI.
- **[`lightweight-charts` API churn between versions]** -> pin an exact version in `web/package.json`.
- **[Client-side P&L can drift from server]** -> recompute on each quote tick and on position refetch; show a "last updated" timestamp; server remains source of truth for realized P&L and cash.
- **[SPA fallback must not shadow `/api` and `/ws`]** -> mount API/WS routers first, then `StaticFiles` with a catch-all that serves `index.html` only for non-API paths.

## Migration Plan

- **Backend**: add `search_instruments`/`get_bars` to the provider interface + simulated impl; add the instruments and WS routers to `app.py`. No DB migration; existing endpoints unchanged. Rollback = remove the routers and provider methods.
- **Frontend**: add `web/` package; in `app.py`, mount `StaticFiles` for `web/dist` only when the directory exists (so the app still runs without a built UI in dev/tests). Rollback = unmount StaticFiles; the API is unaffected.
- **Deploy**: CI builds `web/dist`, then runs the backend; the same FastAPI process serves API + SPA on one origin.

## Open Questions

- Persist `web/dist` in the repo or build only in CI? **Lean**: build in CI, gitignore `dist`.
- Refresh-token rotation for longer sessions? **Lean**: defer; v1 re-login on token expiry.
- Should the `/ws` stream also push account equity updates, or only quotes/orders/fills? **Lean**: quotes/orders/fills for v1; equity derived client-side from the position + quote stream.
