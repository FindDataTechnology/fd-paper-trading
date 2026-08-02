## 1. Frontend project setup (`web/`)

- [x] 1.1 Scaffold `web/` package: `package.json`, `tsconfig.json`, `vite.config.ts`, `index.html`, `src/main.tsx`; pin React 18, TypeScript, Vite, and `lightweight-charts` to exact versions
- [x] 1.2 Add runtime deps: `react`, `react-dom`, `react-router-dom`, `@tanstack/react-query`, `zustand`, `axios` (or fetch wrapper), `lightweight-charts`
- [x] 1.3 Configure Vite dev proxy: forward `/api` and `/ws` to the FastAPI backend at `http://localhost:8000`; set dev server port to 5173
- [x] 1.4 Add npm scripts: `dev`, `build` (outputs `web/dist`), `lint`, `test`; add `web/dist` and `web/node_modules` to `.gitignore`
- [x] 1.5 Add a minimal `App.tsx` smoke route that renders, and verify `npm run dev` + `npm run build` succeed

## 2. Backend: `MarketDataProvider` extensions

- [x] 2.1 Add abstract `search_instruments(self, q: str) -> list[InstrumentSearchResult]` to `MarketDataProvider` (return `{symbol, name, venue}`); return empty list for empty/whitespace `q`
- [x] 2.2 Add abstract `get_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]` to `MarketDataProvider`; define a supported `timeframe` set and a default `limit`
- [x] 2.3 Implement `search_instruments` in `SimulatedMarketDataProvider` over a curated static symbol list (filter by symbol/name contains `q`, case-insensitive)
- [x] 2.4 Implement `get_bars` in `SimulatedMarketDataProvider` as a deterministic, symbol-seeded OHLCV generator (stable per symbol/timeframe so charts are reproducible)
- [x] 2.5 Tests: provider search (prefix, empty query, no match) and bars (count <= limit, ordering oldest-first, determinism)

## 3. Backend: instruments & bars REST endpoints

- [x] 3.1 Add `GET /api/v1/instruments/search?q=` route returning `{symbol, name, venue}[]`; require auth; empty `q` returns `[]`
- [x] 3.2 Add `GET /api/v1/instruments/{symbol}/bars?timeframe=&limit=` route returning OHLCV bars oldest-first; reject unknown `timeframe` with `400`; default `limit`
- [x] 3.3 Add Pydantic response schemas for instrument search results and bars; register routers in `app.py` under `/api/v1`
- [x] 3.4 Tests: search auth (401 without token), search results, bars happy path, invalid timeframe 400, default limit

## 4. Backend: WebSocket streaming endpoint (`/ws`)

- [x] 4.1 Add a `WS /ws` route; on connect require a valid JWT in the first message within a short window, else close the connection
- [x] 4.2 Implement subscribe/unsubscribe message protocol for symbol quotes; track per-connection subscriptions scoped to the authenticated user
- [x] 4.3 Emit quote ticks for subscribed symbols from the `MarketDataProvider`; for the simulated provider, emit on a regular interval
- [x] 4.4 Push order-status and fill events to the owning user's connection (wire into `PaperExecutionService`/repositories); ensure events are user-scoped (never leak across users)
- [x] 4.5 Tests: auth-accepts valid token, rejects missing/invalid token; subscribe then receive a tick; unsubscribe stops ticks; order/fill event delivered only to the owning user

## 5. Backend: serve the built SPA from FastAPI

- [x] 5.1 In `app.py`, mount API and WS routers first, then conditionally mount `StaticFiles` for `web/dist` at `/` only when the directory exists
- [x] 5.2 Add an SPA fallback: unmatched non-`/api`, non-`/ws` GET paths serve `index.html` for client-side routing
- [x] 5.3 Test: `/api/v1/*` and `/ws` are not shadowed by the static mount; a deep link returns `index.html` (or 404 when `web/dist` absent)

## 6. Frontend: app shell & auth (`web-shell`)

- [x] 6.1 Set up React Router with protected routes and a `/login` public route; redirect unauthenticated users to `/login` and preserve the intended destination
- [x] 6.2 Build app layout: top nav + account switcher slot + main outlet; default route redirects to dashboard (auth) or `/login` (unauth)
- [x] 6.3 Implement a Zustand auth store backed by `sessionStorage` holding the JWT; API client attaches `Authorization: Bearer <token>` to every `/api/v1` request
- [x] 6.4 Implement Login screen against `POST /api/v1/auth/login`; implement Register screen against `POST /api/v1/auth/register`; surface API errors inline
- [x] 6.5 On any `401` from REST, clear the token and redirect to `/login`
- [x] 6.6 Configure TanStack Query client; centralize API base URL (`/api/v1`) and typed response interfaces mirroring backend schemas

## 7. Frontend: accounts dashboard (`web-accounts`)

- [x] 7.1 Implement account list + switcher consuming `GET /api/v1/accounts`; persist the active account id in the Zustand UI store
- [x] 7.2 Implement account summary consuming `GET /api/v1/accounts/{id}` (cash, equity, open positions/orders counts); compute equity as cash + Σ position market value when quotes available (indicative)
- [x] 7.3 Implement positions table consuming `GET /api/v1/accounts/{id}/positions`; compute unrealized P&L client-side from the live quote; empty state
- [x] 7.4 Implement deposit (`POST /accounts/{id}/deposit`) and reset (`POST /accounts/{id}/reset`) actions with client-side amount validation; refresh summary on success
- [x] 7.5 Tests: summary render, positions render with P&L, account switch refreshes views, deposit/reset invalid-amount guard

## 8. Frontend: order entry (`web-order-entry`)

- [x] 8.1 Build the order ticket (account, symbol, side BUY/SELL, type MARKET/LIMIT, quantity, limit price) posting to `POST /api/v1/orders`
- [x] 8.2 Client-side validation: quantity > 0; LIMIT requires limit price; BUY cost (qty × ask) vs available cash warning with confirm; disable submit until valid
- [x] 8.3 Build orders list consuming `GET /api/v1/accounts/{id}/orders` with status filter (OPEN/FILLED/CANCELLED) and empty state
- [x] 8.4 Build order detail consuming `GET /api/v1/orders/{id}` showing fills; cancel action via `DELETE /api/v1/orders/{id}` only for open orders; invalidate/refetch on mutation
- [x] 8.5 Surface API rejection reasons (e.g. insufficient funds) from the error response
- [x] 8.6 Tests: market/limit submission, validation guards, cancel only for open orders, rejection surfacing

## 9. Frontend: stock search (`web-market-search`)

- [x] 9.1 Build typeahead search box querying `GET /api/v1/instruments/search?q=` with debounce (>= 2 chars); cancel/skip stale requests
- [x] 9.2 Render result list with symbol, name, and bid/ask quote preview; "no instruments found" empty state
- [x] 9.3 On result select, navigate to the instrument's chart/quote view
- [x] 9.4 Tests: debounced requests, empty/none states, navigation on select

## 10. Frontend: charts (`web-charts`)

- [x] 10.1 Integrate `lightweight-charts`; fetch bars from `GET /api/v1/instruments/{symbol}/bars?timeframe=&limit=` and render a candlestick chart
- [x] 10.2 Add candlestick/line toggle (re-render without refetch) and timeframe selector (refetch on change)
- [x] 10.3 Overlay current quote (last price, bid, ask) near the chart
- [x] 10.4 On quote ticks from the WS client, update the latest bar in place (`series.update`)
- [x] 10.5 Show a visible "simulated data" indicator when the source is the simulated provider
- [x] 10.6 Tests: chart renders bars, timeframe switch refetches, line/candle toggle, tick updates latest bar

## 11. Frontend: realtime client (`web-realtime`)

- [x] 11.1 Implement a single WebSocket hook/store connecting to `/ws`; send the JWT in the first message; expose connection status
- [x] 11.2 Implement subscribe/unsubscribe for symbol quotes; re-establish subscriptions after reconnect
- [x] 11.3 Auto-reconnect with exponential backoff on drop; re-auth and re-subscribe on reconnect
- [x] 11.4 Dispatch quote ticks to chart and positions views; dispatch order/fill events to the orders list and detail view (in-place update, no full refetch)
- [x] 11.5 Tests: connect + auth, reconnect + resubscribe, tick dispatch to subscribers, order/fill in-place update

## 12. Integration, build & docs

- [x] 12.1 End-to-end manual pass: login → dashboard → search a symbol → view chart → place order → see it in orders list → receive fill over WS → see positions update
- [x] 12.2 Verify production serving: `npm run build`, then run FastAPI and confirm SPA + API + WS work from a single origin with no CORS
- [x] 12.3 Add a CI step: `npm ci && npm run build && npm test` alongside `uv run pytest`
- [x] 12.4 Update README and `.env.example` with `web/` dev/build instructions and the dev proxy ports
- [x] 12.5 Run `uv run pytest` and `openspec status --change trading-web-ui`; ensure all new backend tests pass and the change is apply-ready
