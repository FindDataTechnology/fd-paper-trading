## 1. Trading Dashboard Layout

- [x] 1.1 Create `SelectedSymbolContext` using React Context API to share selected symbol between watchlist, chart, and order panel
- [x] 1.2 Create `Watchlist` component displaying a hardcoded list of symbols (AAPL, TSLA, NVDA, SPY, QQQ) with live bid/ask quotes from WebSocket
- [x] 1.3 Create `OrderPanel` component that wraps the existing `OrderTicket` but reads the symbol from `SelectedSymbolContext` instead of props
- [x] 1.4 Refactor `Dashboard.tsx` to use CSS Grid layout with three columns: watchlist (left, ~250px), chart (center, flex), order panel (right, ~350px)
- [x] 1.5 Add click handler to watchlist rows that updates `SelectedSymbolContext`, causing chart and order panel to switch to that symbol
- [x] 1.6 Verify the existing `PriceChart` component works when passed the selected symbol from context

## 2. Position Actions

- [x] 2.1 Add "Close" button to each row in `PositionsTable` component
- [x] 2.2 Implement close logic: calculate opposite-side market order (sell for long, buy for short) with quantity equal to full position size
- [x] 2.3 Add confirmation dialog before submitting close order, showing symbol, side, quantity, and "Market" order type
- [x] 2.4 Submit close order via existing `usePlaceOrder` mutation hook
- [x] 2.5 Add "Reduce" button to each row in `PositionsTable` component
- [x] 2.6 Implement reduce UI: inline input field for quantity, validate it's <= position size
- [x] 2.7 Add confirmation dialog before submitting reduce order
- [x] 2.8 Submit reduce order via existing `usePlaceOrder` mutation hook

## 3. Daily P&L Tracking

- [x] 3.1 Create `DailyPnl` component that calculates total daily P&L from account summary's `realized_pnl` + positions' unrealized P&L at live quotes
- [x] 3.2 Add color-coded display (green for positive, red for negative) with currency formatting
- [x] 3.3 Place `DailyPnl` component prominently in the dashboard header or summary panel
- [x] 3.4 Verify real-time updates: when new quotes arrive via WebSocket, daily P&L recalculates automatically
- [x] 3.5 Document that daily P&L is indicative (frontend-calculated), not authoritative

## 4. Integration & Testing

- [x] 4.1 Ensure existing `InstrumentPage` still works (chart + order ticket for deep-dive analysis)
- [x] 4.2 Test the full flow: open dashboard → click symbol in watchlist → place order → see position appear → close position
- [x] 4.3 Verify WebSocket quote updates propagate to watchlist, chart, order panel, positions table, and daily P&L simultaneously
- [x] 4.4 Add Vitest component tests for `Watchlist`, `OrderPanel`, and position close/reduce actions
- [x] 4.5 Update Playwright e2e test to cover the new dashboard flow (watchlist click → order placement)

## 5. Documentation & Cleanup

- [x] 5.1 Update README.md to describe the new trading dashboard layout and features
- [x] 5.2 Add screenshot of the new dashboard to README (optional, if time permits)
- [x] 5.3 Document that mobile-responsive layout is a future enhancement
- [x] 5.4 Document that watchlist customization (user-configurable symbols) is a future enhancement
