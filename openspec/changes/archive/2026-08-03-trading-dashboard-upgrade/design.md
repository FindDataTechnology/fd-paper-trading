## Context

The current web UI has a fragmented trading experience: users must navigate to individual instrument pages to place orders, and the dashboard only shows account summary and positions. This creates friction for active traders who need to monitor multiple symbols and execute trades quickly.

The backend already provides all necessary data via WebSocket (real-time quotes) and REST API (positions, orders). The frontend has the building blocks (OrderTicket, PositionsTable, SymbolSearch) but they're not integrated into a cohesive trading workspace.

## Goals / Non-Goals

**Goals:**
- Create a single-page trading workspace where users can monitor multiple symbols and execute trades without navigation
- Add quick position management (close/reduce) directly from the positions table
- Display daily P&L prominently with real-time updates
- Maintain existing functionality (instrument pages, orders list) as optional deep-dive destinations

**Non-Goals:**
- Hotkey support (future enhancement)
- Advanced order types (stop-loss, take-profit, OCO) — these require backend changes
- Mobile-responsive layout (initial focus on desktop)
- Custom watchlist persistence (initial implementation uses hardcoded or session-based symbol list)

## Decisions

### 1. Dashboard layout: CSS Grid vs. Flexbox

**Decision:** Use CSS Grid for the three-column layout (watchlist | chart | order panel) with fixed column widths and a responsive breakpoint at 1200px.

**Rationale:** Grid provides precise control over column sizing and alignment. The trading workspace has a fixed structure that doesn't need the flexibility of flexbox. The 1200px breakpoint ensures usability on smaller desktop screens.

**Alternatives considered:**
- Flexbox with `flex: 1` — harder to control exact column widths, watchlist might shrink too much
- Absolute positioning — breaks responsive design, harder to maintain

### 2. Symbol selection state: Context API vs. Zustand

**Decision:** Use React Context API for the selected symbol state, since it's a simple string value shared between watchlist, chart, and order panel.

**Rationale:** The selected symbol is read by multiple components but written by only one (watchlist click handler). Context API is simpler than adding another Zustand store for a single value. If the state grows (e.g., selected timeframe, order form pre-fill), migrate to Zustand.

**Alternatives considered:**
- Zustand store — overkill for a single value, adds dependency
- Prop drilling — would require passing symbol through Layout → Dashboard → all panels, messy

### 3. Position close/reduce: Backend endpoint vs. frontend orchestration

**Decision:** Implement close/reduce as frontend orchestration — calculate the opposite-side market order and submit via the existing `POST /orders` endpoint. No new backend endpoints needed.

**Rationale:** The backend already supports market orders with simulated fills. Close/reduce is just a UI convenience that submits a market order in the opposite direction. Adding a dedicated endpoint would duplicate logic and require backend changes.

**Alternatives considered:**
- New `POST /positions/{id}/close` endpoint — cleaner API but requires backend work, duplicates order placement logic
- WebSocket command to close position — overkill, backend doesn't have position-level commands yet

### 4. Daily P&L calculation: Frontend vs. backend

**Decision:** Calculate daily P&L entirely on the frontend using existing data: realized P&L from account summary + unrealized P&L from positions marked to live quotes.

**Rationale:** The backend already provides `realized_pnl` per position and the frontend has live quotes via WebSocket. Calculating daily P&L on the frontend avoids a new API endpoint and leverages existing data. The backend's `realized_pnl` is already filtered to today's fills (or should be — verify in implementation).

**Alternatives considered:**
- New `GET /accounts/{id}/daily-pnl` endpoint — requires backend work, adds latency
- Store daily P&L in database — overkill, frontend can compute it

### 5. Watchlist symbol list: Hardcoded vs. user-configurable

**Decision:** Start with a hardcoded list of popular symbols (AAPL, TSLA, NVDA, SPY, QQQ) stored in a constant. Future enhancement: persist user's watchlist to backend or localStorage.

**Rationale:** Hardcoded list is simplest to implement and provides immediate value. User-configurable watchlists require backend storage or localStorage management, which adds complexity. The hardcoded list can be easily changed to a configurable list later without breaking the UI.

**Alternatives considered:**
- User-configurable watchlist with backend storage — requires new API endpoint, database schema
- localStorage-based watchlist — adds frontend complexity, doesn't sync across devices
- Empty watchlist with "add symbol" button — poor UX, users have to manually build their list

## Risks / Trade-offs

- **[Realized P&L scope]** → Backend may not filter `realized_pnl` to today's fills only. Mitigation: verify in implementation, adjust calculation if needed.
- **[Mobile layout]** → Initial implementation focuses on desktop. Mitigation: document that mobile is out of scope, add responsive breakpoint later.
- **[Watchlist customization]** → Hardcoded symbol list may not match user preferences. Mitigation: document as future enhancement, provide easy way to change the constant.
- **[Position close latency]** → Market order submission + fill calculation takes time. Mitigation: show loading state on close/reduce buttons, disable until order completes.
- **[Daily P&L accuracy]** → Frontend calculation may diverge from backend's view. Mitigation: document that daily P&L is indicative (like unrealized P&L), not authoritative.
